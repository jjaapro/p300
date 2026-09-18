"""Microstructure stage 1 data: Binance USD-M bookDepth (order-book depth within +-1 % of mid), study-local only.

Two steps, run from the repository root:

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_data.py download
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\micro_data.py build

`download` fetches the daily bookDepth archive (data.binance.vision) for BTCUSDT and ETHUSDT into data/raw/bookDepth/
and checks every zip against Binance's published SHA-256 CHECKSUM file; a day the archive does not serve is recorded
as missing, never guessed. `build` keeps only the +-1 % rows and writes, per symbol, a dense UTC minute grid
(cache/<SYMBOL>_book1pct_1m.npz) holding the bid and ask notional of the LAST snapshot taken inside each minute, the
number of snapshots in the minute, and a JSON sidecar with per-file facts and a logical hash of every array.

A snapshot stamped inside minute [t, t+60 s) is known by t+60 s, the close of the 1-minute bar opened at t, so the
minute value is causal for an event evaluated at that bar's close. Nothing here reads or writes prod.db.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "bookDepth"
CACHE = STUDY / "cache"

SYMBOLS = ("BTCUSDT", "ETHUSDT")
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/bookDepth"
BOOK_START = datetime(2023, 1, 1, tzinfo=timezone.utc)      # first day the archive serves
CUTOFF = datetime(2026, 9, 14, tzinfo=timezone.utc)          # exclusive; equals the ORB panel cutoff
MINUTE_S = 60
LEVEL = 1.0                                                  # percent from mid; the only level used


def n_minutes() -> int:
    return int((CUTOFF - BOOK_START).total_seconds()) // MINUTE_S


def days():
    d = BOOK_START
    while d < CUTOFF:
        yield d
        d += timedelta(days=1)


def day_files(symbol: str) -> list[dict]:
    out = []
    for d in days():
        name = f"{symbol}-bookDepth-{d:%Y-%m-%d}.zip"
        out.append({"symbol": symbol, "day": f"{d:%Y-%m-%d}", "url": f"{ARCHIVE}/{symbol}/{name}",
                    "path": RAW / symbol / name})
    return out


# --- download -----------------------------------------------------------------------

def _get(url: str, timeout: float = 120.0, retries: int = 4) -> bytes | None:
    """Body, or None when the archive answers 404 (a day it does not serve)."""
    last = None
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "p300-exit-policy-study/1.0"}), timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            if e.code == 404:
                return None
            last = e
        except Exception as e:  # network errors are retried, then raised
            last = e
        time.sleep(2.0 * 2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_verified(f: dict) -> dict:
    path: Path = f["path"]
    base = {"symbol": f["symbol"], "day": f["day"], "url": f["url"],
            "file": str(path.relative_to(STUDY)).replace("\\", "/")}
    checksum = _get(f["url"] + ".CHECKSUM")
    if checksum is None:
        return {**base, "status": "missing_in_archive", "bytes": 0, "sha256": None, "checksum_ok": None}
    expected = checksum.decode().split()[0]
    if path.exists() and _sha256(path.read_bytes()) == expected:
        status = "present"
    else:
        data = _get(f["url"])
        if data is None:
            return {**base, "status": "missing_in_archive", "bytes": 0, "sha256": expected, "checksum_ok": None}
        if _sha256(data) != expected:
            raise RuntimeError(f"checksum mismatch for {f['url']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        status = "downloaded"
    return {**base, "status": status, "bytes": path.stat().st_size, "sha256": expected, "checksum_ok": True,
            "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def download(workers: int = 8) -> Path:
    files = [f for s in SYMBOLS for f in day_files(s)]
    entries = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, entry in enumerate(pool.map(_fetch_verified, files), 1):
            entries.append(entry)
            if i % 100 == 0 or i == len(files):
                print(f"  {i}/{len(files)} day files handled", flush=True)
    manifest = {"source": "Binance public data archive, futures/um/daily/bookDepth",
                "book_start_utc": BOOK_START.isoformat(), "cutoff_utc_exclusive": CUTOFF.isoformat(),
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status_counts": pd.Series([e["status"] for e in entries]).value_counts().to_dict(),
                "files": entries}
    out = RAW / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {out} ({len(entries)} entries, {manifest['status_counts']})")
    return out


# --- build --------------------------------------------------------------------------

def _logical_hash(arrays: dict[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(name.encode())
        h.update(str(a.dtype).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def read_day(path: Path) -> tuple[pd.DataFrame, dict]:
    """The +-1 % rows of one day file as (ts_s, bid_notional, ask_notional), one row per snapshot, plus facts."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise RuntimeError(f"{path}: expected one member, found {names}")
        raw = z.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw))
    if list(frame.columns) != ["timestamp", "percentage", "depth", "notional"]:
        raise RuntimeError(f"{path}: unexpected columns {list(frame.columns)}")
    ts = pd.to_datetime(frame["timestamp"], format="%Y-%m-%d %H:%M:%S", utc=True)
    frame["ts_s"] = (ts - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    pct = frame["percentage"].astype(float)
    facts = {"rows": int(len(frame)), "snapshots": int(frame["ts_s"].nunique()),
             "levels": sorted(set(pct.round(2).tolist()))}
    keep = frame[np.isclose(pct.abs(), LEVEL)].copy()
    keep["side"] = np.where(keep["percentage"].astype(float) < 0, "bid", "ask")
    dup = keep.duplicated(["ts_s", "side"], keep="last")
    facts["duplicate_level_rows_dropped"] = int(dup.sum())
    keep = keep[~dup]
    wide = keep.pivot(index="ts_s", columns="side", values="notional").sort_index()
    facts["snapshots_missing_a_side"] = int(wide.isna().any(axis=1).sum())
    wide = wide.dropna()
    return pd.DataFrame({"ts_s": wide.index.to_numpy(np.int64), "bid": wide["bid"].to_numpy(float),
                         "ask": wide["ask"].to_numpy(float)}), facts


def last_in_minute(ts_s: np.ndarray, bid: np.ndarray, ask: np.ndarray, t0: int, n: int,
                   out_bid: np.ndarray, out_ask: np.ndarray, counts: np.ndarray) -> None:
    """Write each minute's LAST snapshot (by timestamp) into out_bid / out_ask and add the minute's snapshot count."""
    m = (ts_s - t0) // MINUTE_S
    ok = (m >= 0) & (m < n)
    order = np.argsort(ts_s[ok], kind="mergesort")
    m, b, a = m[ok][order], bid[ok][order], ask[ok][order]
    np.add.at(counts, m, 1)
    last = np.r_[m[1:] != m[:-1], True] if len(m) else np.zeros(0, dtype=bool)
    out_bid[m[last]] = b[last]
    out_ask[m[last]] = a[last]


def build_symbol(symbol: str, manifest: dict) -> dict:
    t0 = int(BOOK_START.timestamp())
    n = n_minutes()
    bid = np.full(n, np.nan)
    ask = np.full(n, np.nan)
    n_snap = np.zeros(n, dtype=np.uint16)
    files = []
    for e in (x for x in manifest["files"] if x["symbol"] == symbol):
        if e["status"] == "missing_in_archive":
            files.append({"day": e["day"], "status": "missing_in_archive"})
            continue
        path = STUDY / e["file"]
        if _sha256(path.read_bytes()) != e["sha256"]:
            raise RuntimeError(f"{path}: file changed since download")
        snaps, facts = read_day(path)
        day0 = int(datetime.fromisoformat(e["day"]).replace(tzinfo=timezone.utc).timestamp())
        outside = (snaps["ts_s"] < day0) | (snaps["ts_s"] >= day0 + 86400)
        facts["snapshots_outside_day"] = int(outside.sum())
        last_in_minute(snaps["ts_s"].to_numpy(np.int64), snaps["bid"].to_numpy(), snaps["ask"].to_numpy(), t0, n,
                       bid, ask, n_snap)
        gaps = np.diff(np.sort(snaps["ts_s"].to_numpy()))
        facts.update({"day": e["day"], "status": "parsed", "sha256": e["sha256"],
                      "max_gap_s": int(gaps.max()) if len(gaps) else None,
                      "gaps_over_120s": int((gaps > 120).sum()) if len(gaps) else 0})
        files.append(facts)
    arrays = {"bid_notional_1pct": bid, "ask_notional_1pct": ask, "snapshots_in_minute": n_snap}
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{symbol}_book1pct_1m.npz"
    np.savez_compressed(out, t0_s=np.array([t0], dtype=np.int64), **arrays)
    present = np.isfinite(bid)
    meta = {"symbol": symbol, "product": "Binance USD-M perpetual, bookDepth archive",
            "value": "notional within 1 % of mid, last snapshot inside the minute", "bar_label": "minute open, UTC s",
            "book_start_utc": BOOK_START.isoformat(), "cutoff_utc_exclusive": CUTOFF.isoformat(), "minutes": n,
            "minutes_with_snapshot": int(present.sum()), "minutes_without_snapshot": int((~present).sum()),
            "days_missing_in_archive": [f["day"] for f in files if f["status"] == "missing_in_archive"],
            "minutes_with_snapshot_by_year": {str(y): int(present[_year_slice(t0, y, n)].sum())
                                              for y in range(BOOK_START.year, CUTOFF.year + 1)},
            "logical_sha256": _logical_hash(arrays), "file_sha256": _sha256(out.read_bytes()),
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files}
    (CACHE / f"{symbol}_book1pct_1m.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{symbol}: {meta['minutes_with_snapshot']}/{n} minutes with a snapshot; "
          f"{len(meta['days_missing_in_archive'])} archive days missing")
    return meta


def _year_slice(t0: int, year: int, n: int) -> slice:
    lo = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    hi = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    return slice(max(0, (lo - t0) // MINUTE_S), min(n, max(0, (hi - t0) // MINUTE_S)))


def build() -> None:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    for s in SYMBOLS:
        build_symbol(s, manifest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "build"])
    args = ap.parse_args()
    download() if args.step == "download" else build()


if __name__ == "__main__":
    main()
