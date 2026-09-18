"""ORB study data: Binance USD-M perpetual 1m klines and funding, study-local only.

Two steps, run from the repository root:

    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_data.py download
    venv\\Scripts\\python.exe studies\\notebooks\\orb_study\\orb_data.py build

`download` fetches the public archive (data.binance.vision) into data/raw/ and checks
every zip against Binance's published SHA-256 CHECKSUM file. Funding settlements after
the last monthly archive come from the public REST endpoint and are saved verbatim.
`build` parses the zips into a dense UTC minute grid (NaN where a minute is absent) and
writes cache/<SYMBOL>_perp_1m.npz plus a JSON sidecar with per-file facts and a logical
hash of every array. Nothing here reads or writes prod.db.

Bars are open-stamped: the row at minute t covers [t, t+60s) and is known at t+60s.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "binance_um"
CACHE = STUDY / "cache"

SYMBOLS = ("BTCUSDT", "ETHUSDT")
ARCHIVE = "https://data.binance.vision/data/futures/um"
FAPI = "https://fapi.binance.com/fapi/v1"

# Frozen panel bounds. The cutoff is exclusive and never advances on a rerun.
PANEL_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 14, tzinfo=timezone.utc)
LAST_MONTHLY = (2026, 8)          # months after this come from daily files / REST
MINUTE_MS = 60_000

KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
                 "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
PANEL_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume", "count",
                "taker_buy_volume")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def n_minutes() -> int:
    return (ms(CUTOFF) - ms(PANEL_START)) // MINUTE_MS


# --- file lists ---------------------------------------------------------------------

def _months():
    y, m = PANEL_START.year, PANEL_START.month
    while (y, m) <= LAST_MONTHLY:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _days_after_monthly():
    y, m = LAST_MONTHLY
    day = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    while day < CUTOFF:
        yield day
        day += timedelta(days=1)


def archive_files(symbol: str) -> list[dict]:
    files = []
    for y, m in _months():
        files.append({"kind": "klines_1m", "url": f"{ARCHIVE}/monthly/klines/{symbol}/1m/{symbol}-1m-{y}-{m:02d}.zip"})
        files.append({"kind": "funding", "url": f"{ARCHIVE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip"})
    for day in _days_after_monthly():
        files.append({"kind": "klines_1m", "url": f"{ARCHIVE}/daily/klines/{symbol}/1m/{symbol}-1m-{day:%Y-%m-%d}.zip"})
    for f in files:
        f["symbol"] = symbol
        f["path"] = RAW / f["kind"] / symbol / f["url"].rsplit("/", 1)[1]
    return files


# --- download -----------------------------------------------------------------------

def _get(url: str, timeout: float = 120.0, retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "p300-orb-study/1.0"}), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network errors are retried, then raised
            last = e
            time.sleep(2.0 * 2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_verified(f: dict) -> dict:
    path: Path = f["path"]
    expected = _get(f["url"] + ".CHECKSUM").decode().split()[0]
    if path.exists() and _sha256(path.read_bytes()) == expected:
        status = "present"
    else:
        data = _get(f["url"])
        if _sha256(data) != expected:
            raise RuntimeError(f"checksum mismatch for {f['url']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        status = "downloaded"
    return {"kind": f["kind"], "symbol": f["symbol"], "url": f["url"],
            "file": str(path.relative_to(STUDY)).replace("\\", "/"),
            "bytes": path.stat().st_size, "sha256": expected, "checksum_ok": True,
            "status": status, "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _fetch_funding_rest(symbol: str) -> dict:
    """Settlements from the end of the monthly archive to the cutoff, saved verbatim."""
    start = next(iter(_days_after_monthly()), None)
    if start is None:
        return {}
    rows, cursor = [], ms(start)
    while cursor < ms(CUTOFF):
        batch = json.loads(_get(f"{FAPI}/fundingRate?symbol={symbol}&startTime={cursor}"
                                f"&endTime={ms(CUTOFF) - 1}&limit=1000"))
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1]["fundingTime"]) + 1
        if len(batch) < 1000:
            break
    path = RAW / "funding_rest" / f"{symbol}-fundingRate-{start:%Y-%m-%d}_to_cutoff.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, indent=1).encode()
    path.write_bytes(body)
    return {"kind": "funding_rest", "symbol": symbol,
            "url": f"{FAPI}/fundingRate?symbol={symbol}&startTime={ms(start)}&endTime={ms(CUTOFF) - 1}",
            "file": str(path.relative_to(STUDY)).replace("\\", "/"), "bytes": len(body),
            "sha256": _sha256(body), "checksum_ok": None, "status": "downloaded",
            "rows": len(rows), "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def download() -> Path:
    entries = []
    files = [f for s in SYMBOLS for f in archive_files(s)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, entry in enumerate(pool.map(_fetch_verified, files), 1):
            entries.append(entry)
            if i % 20 == 0 or i == len(files):
                print(f"  {i}/{len(files)} archive files verified", flush=True)
    for s in SYMBOLS:
        entries.append(_fetch_funding_rest(s))
    manifest = {"source": "Binance public data archive + public REST fundingRate",
                "panel_start_utc": PANEL_START.isoformat(), "cutoff_utc_exclusive": CUTOFF.isoformat(),
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "files": entries}
    out = RAW / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {out} ({len(entries)} files)")
    return out


# --- build --------------------------------------------------------------------------

def _read_zip_csv(path: Path) -> tuple[pd.DataFrame, bool]:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise RuntimeError(f"{path}: expected one member, found {names}")
        raw = z.read(names[0])
    has_header = not raw[:1].isdigit()
    frame = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)
    return frame, has_header


def _logical_hash(arrays: dict[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(name.encode())
        h.update(str(a.dtype).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def build_klines(symbol: str) -> dict:
    t0, n = ms(PANEL_START), n_minutes()
    panel = {k: np.full(n, np.nan) for k in PANEL_FIELDS}
    seen = np.zeros(n, dtype=bool)
    file_facts, duplicates, out_of_range = [], 0, 0
    for f in archive_files(symbol):
        if f["kind"] != "klines_1m":
            continue
        frame, has_header = _read_zip_csv(f["path"])
        if not has_header:
            frame.columns = KLINE_COLUMNS
        ot = frame["open_time"].to_numpy(np.int64)
        unit = "ms"
        if ot.max() > 10**14:                      # microsecond era (spot archive convention)
            unit = "us"
            ot = ot // 1000
        ct = frame["close_time"].to_numpy(np.int64) // (1000 if unit == "us" else 1)
        idx = (ot - t0) // MINUTE_MS
        on_grid = (ot - t0) % MINUTE_MS == 0
        inside = (idx >= 0) & (idx < n)
        keep = on_grid & inside
        out_of_range += int((~inside).sum())
        idx_k = idx[keep]
        dup_in_file = int(len(idx_k) - len(np.unique(idx_k)))
        dup_across = int(seen[idx_k].sum())
        duplicates += dup_in_file + dup_across
        for k in PANEL_FIELDS:
            panel[k][idx_k] = frame[k].to_numpy(np.float64)[keep]
        seen[idx_k] = True
        file_facts.append({
            "file": f["path"].name, "rows": int(len(frame)), "header": has_header, "unit": unit,
            "first_open_utc": pd.Timestamp(int(ot.min()), unit="ms", tz="UTC").isoformat(),
            "last_open_utc": pd.Timestamp(int(ot.max()), unit="ms", tz="UTC").isoformat(),
            "off_grid": int((~on_grid).sum()), "outside_panel": int((~inside).sum()),
            "duplicates_in_file": dup_in_file, "duplicates_across_files": dup_across,
            "non_monotonic": int((np.diff(ot) <= 0).sum()),
            "close_time_not_open_plus_59999": int((ct - ot != MINUTE_MS - 1).sum()),
        })
    arrays = {k: v for k, v in panel.items()}
    arrays["t0_ms"] = np.array([t0], dtype=np.int64)
    out = CACHE / f"{symbol}_perp_1m.npz"
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, **arrays)
    meta = {"symbol": symbol, "product": "Binance USD-M linear perpetual", "price": "last trade",
            "bar_label": "open time, UTC ms", "panel_start_utc": PANEL_START.isoformat(),
            "cutoff_utc_exclusive": CUTOFF.isoformat(), "minutes": n,
            "present_minutes": int(seen.sum()), "missing_minutes": int(n - seen.sum()),
            "duplicates": duplicates, "rows_outside_panel": out_of_range,
            "logical_sha256": _logical_hash(arrays), "file_sha256": _sha256(out.read_bytes()),
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": file_facts}
    (CACHE / f"{symbol}_perp_1m.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{symbol}: {meta['present_minutes']:,}/{n:,} minutes present, {duplicates} duplicates -> {out.name}")
    return meta


def build_funding(symbol: str) -> dict:
    times, rates, interval, source = [], [], [], []
    for f in archive_files(symbol):
        if f["kind"] != "funding":
            continue
        frame, _ = _read_zip_csv(f["path"])
        times.extend(frame["calc_time"].astype(np.int64))
        rates.extend(frame["last_funding_rate"].astype(float))
        interval.extend(frame["funding_interval_hours"].astype(float))
        source.extend([0] * len(frame))
    rest = next((RAW / "funding_rest").glob(f"{symbol}-fundingRate-*_to_cutoff.json"), None)
    if rest is not None:
        for row in json.loads(rest.read_text()):
            times.append(int(row["fundingTime"]))
            rates.append(float(row["fundingRate"]))
            interval.append(np.nan)                 # REST rows do not carry the interval
            source.append(1)
    order = np.argsort(np.asarray(times, dtype=np.int64), kind="stable")
    arrays = {"time_ms": np.asarray(times, dtype=np.int64)[order],
              "rate": np.asarray(rates, dtype=np.float64)[order],
              "interval_hours": np.asarray(interval, dtype=np.float64)[order],
              "source": np.asarray(source, dtype=np.int8)[order]}
    keep = (arrays["time_ms"] >= ms(PANEL_START)) & (arrays["time_ms"] < ms(CUTOFF))
    arrays = {k: v[keep] for k, v in arrays.items()}
    # A settlement listed twice (archive + REST overlap) keeps its first occurrence.
    _, first = np.unique(arrays["time_ms"] // MINUTE_MS, return_index=True)
    dropped = int(len(arrays["time_ms"]) - len(first))
    arrays = {k: v[np.sort(first)] for k, v in arrays.items()}
    out = CACHE / f"{symbol}_funding.npz"
    np.savez_compressed(out, **arrays)
    gaps_h = np.diff(arrays["time_ms"]) / 3_600_000
    meta = {"symbol": symbol, "rows": int(len(arrays["time_ms"])), "duplicate_minutes_dropped": dropped,
            "first_utc": pd.Timestamp(int(arrays["time_ms"][0]), unit="ms", tz="UTC").isoformat(),
            "last_utc": pd.Timestamp(int(arrays["time_ms"][-1]), unit="ms", tz="UTC").isoformat(),
            "spacing_hours_counts": {str(round(float(v), 3)): int(c) for v, c in
                                     zip(*np.unique(np.round(gaps_h, 3), return_counts=True))},
            "logical_sha256": _logical_hash(arrays),
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (CACHE / f"{symbol}_funding.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{symbol}: {meta['rows']} funding settlements, spacing {meta['spacing_hours_counts']}")
    return meta


def build() -> None:
    for s in SYMBOLS:
        build_klines(s)
        build_funding(s)


# --- loading ------------------------------------------------------------------------

def load_panel(symbol: str) -> dict[str, np.ndarray]:
    with np.load(CACHE / f"{symbol}_perp_1m.npz") as z:
        panel = {k: z[k] for k in z.files}
    panel["t0_ms"] = int(panel["t0_ms"][0])
    return panel


def load_funding(symbol: str) -> dict[str, np.ndarray]:
    with np.load(CACHE / f"{symbol}_funding.npz") as z:
        return {k: z[k] for k in z.files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["download", "build"])
    if parser.parse_args().step == "download":
        download()
    else:
        build()
