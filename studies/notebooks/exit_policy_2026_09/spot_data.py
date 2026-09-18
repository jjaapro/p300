"""Binance SPOT 1-minute klines for BTCUSDT and ETHUSDT, 2020-01-01 → 2026-09-14 (exclusive), study-local.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spot_data.py download
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spot_data.py build

Same shape as the ORB study's perpetual panel (`orb_study/cache/<SYMBOL>_perp_1m.npz`): a dense UTC minute grid with
open, high, low, close, volume, quote_volume, count, taker_buy_volume, NaN where a minute is absent, `t0_ms`. Archive
quirks handled: header rows in later files, and spot timestamps in MICROSECONDS from 2025-01-01 (detected by magnitude).
Monthly zips through 2026-08, daily zips for 2026-09; every zip verified against its published CHECKSUM.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import anatomy_data as AD
import micro_data as MD

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "spot_1m"
CACHE = STUDY / "cache"
ARCHIVE = "https://data.binance.vision/data/spot"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
PANEL_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 15, tzinfo=timezone.utc)          # exclusive
LAST_MONTHLY = (2026, 8)
MINUTE_MS = 60_000
FIELDS = ("open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_volume")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def archive_files(symbol: str) -> list[dict]:
    files = []
    y, m = PANEL_START.year, PANEL_START.month
    while (y, m) <= LAST_MONTHLY:
        files.append({"url": f"{ARCHIVE}/monthly/klines/{symbol}/1m/{symbol}-1m-{y}-{m:02d}.zip"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = datetime(LAST_MONTHLY[0], LAST_MONTHLY[1] + 1, 1, tzinfo=timezone.utc)
    while d < CUTOFF:
        files.append({"url": f"{ARCHIVE}/daily/klines/{symbol}/1m/{symbol}-1m-{d:%Y-%m-%d}.zip"})
        d += timedelta(days=1)
    for f in files:
        f["kind"], f["symbol"] = "spot_1m", symbol
        f["path"] = RAW / symbol / f["url"].rsplit("/", 1)[1]
    return files


def download() -> Path:
    files = [f for s in SYMBOLS for f in archive_files(s)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(pool.map(AD._fetch_verified, files))
    for e, f in zip(entries, files):
        e["symbol"] = f["symbol"]
    manifest = {"source": "Binance public data archive, spot/{monthly,daily}/klines 1m",
                "panel_start_utc": PANEL_START.isoformat(), "cutoff_utc_exclusive": CUTOFF.isoformat(),
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": entries}
    out = RAW / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {out} ({len(entries)} files)")
    return out


def build_symbol(symbol: str, manifest: dict) -> dict:
    t0 = ms(PANEL_START)
    n = (ms(CUTOFF) - t0) // MINUTE_MS
    panel = {k: np.full(n, np.nan) for k in FIELDS}
    seen = np.zeros(n, dtype=bool)
    facts, duplicates, outside = [], 0, 0
    for e in manifest["files"]:
        if e["symbol"] != symbol:
            continue
        path = STUDY / e["file"]
        if AD._sha256(path.read_bytes()) != e["sha256"]:
            raise RuntimeError(f"{path}: file changed since download")
        f = AD._read_zip_csv(path)
        if "open_time" not in f.columns:
            f.columns = AD.KLINE_COLUMNS
        ot = f["open_time"].to_numpy(np.int64)
        unit = "ms"
        if ot.max() > 10 ** 14:                       # microsecond era
            unit, ot = "us", ot // 1000
        idx = (ot - t0) // MINUTE_MS
        on_grid = (ot - t0) % MINUTE_MS == 0
        inside = (idx >= 0) & (idx < n)
        keep = on_grid & inside
        outside += int((~inside).sum())
        k = idx[keep]
        duplicates += int(len(k) - len(np.unique(k))) + int(seen[k].sum())
        for name in FIELDS:
            panel[name][k] = f[name].to_numpy(np.float64)[keep]
        seen[k] = True
        facts.append({"file": path.name, "rows": int(len(f)), "unit": unit, "off_grid": int((~on_grid).sum()),
                      "outside_panel": int((~inside).sum())})
    arrays = dict(panel)
    arrays["t0_ms"] = np.array([t0], dtype=np.int64)
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{symbol}_spot_1m.npz"
    np.savez_compressed(out, **arrays)
    meta = {"symbol": symbol, "product": "Binance spot", "bar_label": "open time, UTC ms",
            "panel_start_utc": PANEL_START.isoformat(), "cutoff_utc_exclusive": CUTOFF.isoformat(), "minutes": n,
            "present_minutes": int(seen.sum()), "missing_minutes": int(n - seen.sum()), "duplicates": duplicates,
            "rows_outside_panel": outside, "logical_sha256": MD._logical_hash(arrays),
            "file_sha256": AD._sha256(out.read_bytes()),
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": facts}
    (CACHE / f"{symbol}_spot_1m.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{symbol}: {meta['present_minutes']:,}/{n:,} minutes present, {duplicates} duplicates")
    return meta


def build() -> None:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    for s in SYMBOLS:
        build_symbol(s, manifest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "build"])
    step = ap.parse_args().step
    download() if step == "download" else build()
