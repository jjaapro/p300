"""Spot-vs-perp stage: build the spot and premium-index minute panels from the downloaded archive.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_data.py build

The download is `spotperp_download.py` and is already done; this step only parses. Every zip listed in
`data/raw/spotperp/manifest.json` is re-hashed against the manifest (the archive's own CHECKSUM value) before it is
read. Rows are placed on the ORB perpetual panel's minute grid (t0 2020-01-01 00:00 UTC, 3,525,120 minutes, cutoff
2026-09-14 00:00 UTC exclusive), so the spot, premium and perpetual arrays share one index.

Outputs, each with a `.meta.json` sidecar (PREREGISTRATION_SPOT_PERP.md section 4, precondition P7):

    cache/BTCUSDT_spot_1m_panel.npz      cache/BTCUSDT_premium_1m_panel.npz
    cache/ETHUSDT_spot_1m_panel.npz      cache/ETHUSDT_premium_1m_panel.npz
    cache/BTCFDUSD_spot_1m_panel.npz

The `_panel` suffix is required: `cache/<SYMBOL>_spot_1m.npz` and `cache/<SYMBOL>_metrics_5m_full.npz` belong to the
liquidation-map study running in this folder and are neither written nor read here.

Archive quirks handled, all recorded rather than silently repaired: header rows in later files, spot timestamps in
microseconds from the 2025-01 files, rows whose `close_time - open_time` is not one minute less one unit (16 spot rows,
each the truncated last bar before an exchange outage; kept like any other row), absent minutes (NaN, never filled),
carried zero-volume minutes (kept with their prices; dead for flow). Nothing here reads prod.db or any trade.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import anatomy_data as AD  # noqa: E402
import micro_data as MD  # noqa: E402

RAW = HERE / "data" / "raw" / "spotperp"
CACHE = HERE / "cache"
ORB_META = HERE.parents[1] / "notebooks" / "orb_study" / "cache" / "BTCUSDT_perp_1m.meta.json"
ANATOMY_PREMIUM = CACHE / "BTCUSDT_premium_1m.npz"

T0_S = 1_577_836_800                     # 2020-01-01 00:00 UTC, the ORB panel's first minute
N_MINUTES = 3_525_120                    # cutoff 2026-09-14 00:00 UTC exclusive
SPOT_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume")
US_THRESHOLD = 10 ** 14                  # 10^14 ms is the year 5138, so the unit test is unambiguous
IDENTITY = {"ms": 59_999, "us": 59_999_999}
UNIT_DIVISOR = {"ms": 1_000, "us": 1_000_000}          # per second
MINUTE = {"ms": 60_000, "us": 60_000_000}
FDUSD_FIRST_UTC = "2023-08-04T08:00:00+00:00"          # the pair's first archive row (format_scan.json)
PREMIUM_OVERLAP_FROM = 1_640_995_200                   # 2022-01-01 00:00 UTC, the anatomy cache's t0
MAX_RUNS_LISTED = 50


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(ts_s: int) -> str:
    return datetime.fromtimestamp(int(ts_s), tz=timezone.utc).isoformat()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=_plain), encoding="utf-8")


def _plain(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(f"not JSON serialisable: {type(o)}")


# --- one file ---------------------------------------------------------------------------------------

def read_kline_zip(path: Path) -> tuple[pd.DataFrame, dict]:
    """One archive file as a numeric frame with the archive's column names, plus its format facts."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise RuntimeError(f"{path}: expected one member, found {names}")
        raw = z.read(names[0])
    has_header = not raw[:1].isdigit()
    frame = AD._read_zip_csv(path)
    if not has_header:
        frame.columns = AD.KLINE_COLUMNS
    for col in frame.columns:
        if col != "ignore":
            frame[col] = pd.to_numeric(frame[col], errors="raise")
    open_time = frame["open_time"].to_numpy(np.int64)
    close_time = frame["close_time"].to_numpy(np.int64)
    if len(open_time) == 0:
        raise RuntimeError(f"{path}: no rows")
    hi, lo = int(open_time.max()), int(open_time.min())
    if (hi > US_THRESHOLD) != (lo > US_THRESHOLD):
        raise RuntimeError(f"{path}: mixed timestamp units ({lo} .. {hi})")
    unit = "us" if hi > US_THRESHOLD else "ms"
    fail = np.flatnonzero((close_time - open_time) != IDENTITY[unit])
    facts = {
        "file": path.name, "rows": int(len(frame)), "header": bool(has_header), "unit": unit,
        "first_open_utc": iso(lo // UNIT_DIVISOR[unit]), "last_open_utc": iso(hi // UNIT_DIVISOR[unit]),
        "identity_failures": int(len(fail)),
        "identity_failure_rows": [
            {"open_utc": iso(int(open_time[i]) // UNIT_DIVISOR[unit]),
             "close_minus_open_s": round(float(close_time[i] - open_time[i]) / UNIT_DIVISOR[unit], 3),
             "volume": float(frame["volume"].to_numpy(float)[i])} for i in fail],
    }
    return frame, facts


def place_on_grid(frame: pd.DataFrame, facts: dict, fields: tuple[str, ...],
                  panel: dict[str, np.ndarray], t0_s: int, n: int) -> None:
    """Place one file's rows on the grid, last row wins; every dropped or repeated row is counted into `facts`."""
    unit = facts["unit"]
    t0_u, minute = t0_s * UNIT_DIVISOR[unit], MINUTE[unit]
    open_time = frame["open_time"].to_numpy(np.int64)
    on_grid = (open_time - t0_u) % minute == 0
    idx_all = (open_time - t0_u) // minute
    inside = (idx_all >= 0) & (idx_all < n)
    keep = on_grid & inside
    facts["off_grid"] = int((~on_grid).sum())
    facts["outside_panel"] = int((on_grid & ~inside).sum())

    idx = idx_all[keep]
    values = {f: frame[f].to_numpy(float)[keep] for f in fields}
    order = pd.Series(idx).duplicated(keep="last").to_numpy()      # True on every row a later row supersedes
    facts["duplicates_in_file"] = int(order.sum())
    facts["conflicting_duplicates"] = 0
    if order.any():                                                # superseded rows must agree with the survivor
        by_idx = {}
        for pos in range(len(idx)):
            by_idx.setdefault(int(idx[pos]), []).append(pos)
        for positions in by_idx.values():
            if len(positions) == 1:
                continue
            last = positions[-1]
            for pos in positions[:-1]:
                if any(not _same(values[f][pos], values[f][last]) for f in fields):
                    facts["conflicting_duplicates"] += 1
    idx, values = idx[~order], {f: v[~order] for f, v in values.items()}

    already = np.isfinite(panel[fields[0]][idx])
    facts["duplicates_across_files"] = int(already.sum())
    for pos in np.flatnonzero(already):
        j = int(idx[pos])
        if any(not _same(values[f][pos], panel[f][j]) for f in fields):
            facts["conflicting_duplicates"] += 1
    for f in fields:
        panel[f][idx] = values[f]


def _same(a: float, b: float) -> bool:
    return bool((np.isnan(a) and np.isnan(b)) or a == b)


# --- grid facts -------------------------------------------------------------------------------------

def year_slices(t0_s: int, n: int) -> dict[str, tuple[int, int]]:
    """Year label -> [start, end) minute index."""
    first = datetime.fromtimestamp(t0_s, tz=timezone.utc).year
    last = datetime.fromtimestamp(t0_s + 60 * (n - 1), tz=timezone.utc).year
    out = {}
    for year in range(first, last + 1):
        lo = (int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()) - t0_s) // 60
        hi = (int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()) - t0_s) // 60
        out[str(year)] = (max(0, int(lo)), min(n, int(hi)))
    return out


def runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    """Maximal runs of True as (start index, length), longest first."""
    if not mask.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate([[False], mask, [False]]).astype(np.int8)))
    starts, ends = edges[0::2], edges[1::2]
    order = np.argsort(ends - starts)[::-1]
    return [(int(starts[i]), int(ends[i] - starts[i])) for i in order]


def grid_facts(present: np.ndarray, dead: np.ndarray | None, t0_s: int, n: int) -> dict:
    missing = ~present
    out = {"minutes_present": int(present.sum()), "minutes_missing": int(missing.sum()), "by_year": {}}
    for year, (lo, hi) in year_slices(t0_s, n).items():
        row = {"present": int(present[lo:hi].sum()), "missing": int(missing[lo:hi].sum())}
        if dead is not None:
            row["dead"] = int(dead[lo:hi].sum())
        out["by_year"][year] = row
    if dead is not None:
        out["minutes_dead"] = int(dead.sum())
    runs = runs_of(missing)
    out["missing_runs_total"] = len(runs)
    out["missing_runs_longest"] = [{"start_utc": iso(t0_s + 60 * s), "minutes": length}
                                   for s, length in runs[:MAX_RUNS_LISTED]]
    return out


def perp_grid_facts() -> dict:
    meta = json.loads(ORB_META.read_text())
    if meta["minutes"] != N_MINUTES or not meta["panel_start_utc"].startswith("2020-01-01"):
        raise RuntimeError(f"perp panel grid changed: {meta['panel_start_utc']}, {meta['minutes']} minutes")
    return {"t0_s": T0_S, "minutes": N_MINUTES, "cutoff_utc_exclusive": meta["cutoff_utc_exclusive"],
            "perp_panel_logical_sha256": meta["logical_sha256"]}


# --- builds -----------------------------------------------------------------------------------------

def build_one(symbol: str, kind: str, manifest: dict, t0_s: int = T0_S, n: int = N_MINUTES,
              cache: Path = CACHE, raw: Path = RAW) -> dict:
    """Build one symbol-kind panel and its sidecar. `kind` is 'spot_1m' or 'premium_1m'."""
    fields = SPOT_FIELDS if kind == "spot_1m" else ("close",)
    entries = [e for e in manifest["files"] if e["symbol"] == symbol and e["kind"] == kind]
    if not entries:
        raise RuntimeError(f"no manifest entries for {symbol} {kind}")
    panel = {f: np.full(n, np.nan) for f in fields}
    per_file, rows_read = [], 0
    for e in entries:
        path = raw / e["kind"] / Path(e["file"]).name
        if AD._sha256(path.read_bytes()) != e["sha256"]:
            raise RuntimeError(f"{path}: sha256 does not match the manifest")
        frame, facts = read_kline_zip(path)
        place_on_grid(frame, facts, fields, panel, t0_s, n)
        if kind == "spot_1m":
            facts["zero_volume_rows"] = int((frame["volume"].to_numpy(float) == 0).sum())
        rows_read += facts["rows"]
        per_file.append(facts)

    present = np.isfinite(panel[fields[0]])
    dead = (present & (panel["volume"] == 0)) if kind == "spot_1m" else None
    name = f"{symbol}_{'spot' if kind == 'spot_1m' else 'premium'}_1m_panel"
    out = cache / f"{name}.npz"
    cache.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, t0_s=np.array([t0_s], dtype=np.int64), **panel)

    us_files = [f["file"] for f in per_file if f["unit"] == "us"]
    meta = {
        "symbol": symbol, "kind": kind, "built_utc": now_utc(),
        "grid": perp_grid_facts() if (t0_s, n) == (T0_S, N_MINUTES) else {"t0_s": t0_s, "minutes": n},
        "arrays": list(fields), "rows_read": rows_read, "files": len(per_file),
        "totals": {k: int(sum(f[k] for f in per_file)) for k in
                   ("off_grid", "outside_panel", "duplicates_in_file", "duplicates_across_files",
                    "conflicting_duplicates", "identity_failures")},
        "unit_switch_first_us_file": us_files[0] if us_files else None,
        "identity_failure_rows": [{"file": f["file"], **r} for f in per_file for r in f["identity_failure_rows"]],
        **grid_facts(present, dead, t0_s, n),
        "per_file": per_file,
        "logical_sha256": MD._logical_hash(panel), "file_sha256": AD._sha256(out.read_bytes()),
        "file_bytes": out.stat().st_size,
    }
    if meta["totals"]["conflicting_duplicates"]:
        raise RuntimeError(f"{symbol} {kind}: {meta['totals']['conflicting_duplicates']} conflicting duplicate rows")
    if symbol == "BTCFDUSD":
        first = int(np.flatnonzero(present)[0])
        meta["first_present_utc"] = iso(t0_s + 60 * first)
        meta["all_nan_before_first_present"] = bool(not present[:first].any())
        if (t0_s, n) == (T0_S, N_MINUTES) and meta["first_present_utc"] != FDUSD_FIRST_UTC:
            raise RuntimeError(f"BTCFDUSD first present minute {meta['first_present_utc']}, expected {FDUSD_FIRST_UTC}")
    if symbol == "BTCUSDT" and kind == "premium_1m" and ANATOMY_PREMIUM.exists() and (t0_s, n) == (T0_S, N_MINUTES):
        meta["premium_overlap_vs_anatomy_cache"] = _premium_overlap(panel["close"], t0_s, n)
    write_json(cache / f"{name}.meta.json", meta)
    print(f"{symbol} {kind}: {meta['minutes_present']}/{n} minutes, {rows_read} rows, "
          f"{meta['totals']['identity_failures']} identity failures, {out.stat().st_size / 1e6:.0f} MB", flush=True)
    return meta


def _premium_overlap(close: np.ndarray, t0_s: int, n: int) -> dict:
    """The anatomy stage's premium cache over [2022-01-01, the new panel's end): identical, and its extra minutes."""
    with np.load(ANATOMY_PREMIUM) as z:
        old_t0, old = int(z["t0_s"][0]), z["close"]
    lo = (PREMIUM_OVERLAP_FROM - t0_s) // 60
    old_lo = (PREMIUM_OVERLAP_FROM - old_t0) // 60
    length = min(n - lo, len(old) - old_lo)
    a, b = close[lo:lo + length], old[old_lo:old_lo + length]
    return {"span": [iso(PREMIUM_OVERLAP_FROM), iso(t0_s + 60 * (lo + length))], "minutes": int(length),
            "identical": bool(np.array_equal(a, b, equal_nan=True)),
            "differing_minutes": int((~((a == b) | (np.isnan(a) & np.isnan(b)))).sum()),
            "anatomy_cache_minutes_outside_new_panel": int(max(0, (len(old) - old_lo) - length))}


def build(symbols: list[str] | None = None) -> dict:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    want = symbols or ["BTCUSDT", "ETHUSDT", "BTCFDUSD"]
    started = time.time()
    metas = {}
    for symbol in want:
        for kind in ("spot_1m", "premium_1m"):
            if any(e["symbol"] == symbol and e["kind"] == kind for e in manifest["files"]):
                metas[f"{symbol}:{kind}"] = build_one(symbol, kind, manifest)
    print(f"built {len(metas)} panels in {time.time() - started:.0f} s", flush=True)
    return metas


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["build"])
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()
    build(args.symbols)
