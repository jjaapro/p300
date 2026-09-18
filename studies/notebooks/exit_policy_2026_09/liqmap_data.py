"""Liquidation-map study data: Binance USD-M 5-minute open interest for BTCUSDT (2020-09-01 →) and ETHUSDT (2021-12-01 →).

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_data.py download
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_data.py build

The daily `metrics` archive (checksum-verified, one zip per day, `data/raw/metrics/<SYMBOL>/`) is parsed into a dense
5-minute grid per symbol, `cache/<SYMBOL>_metrics_5m_full.npz`, with a JSON sidecar. Days the archive does not serve
are recorded, never filled. The top-anatomy stage's `BTCUSDT_metrics_5m.npz` (2022-01 →) is left as it is, because
its hash is in that stage's manifest; this file is the study's own input.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import numpy as np
import pandas as pd

import anatomy_data as AD
import micro_data as MD

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "metrics"
CACHE = STUDY / "cache"
ARCHIVE = "https://data.binance.vision/data/futures/um/daily/metrics"
SPANS = {"BTCUSDT": datetime(2020, 9, 1, tzinfo=timezone.utc), "ETHUSDT": datetime(2021, 12, 1, tzinfo=timezone.utc)}
END = datetime(2026, 9, 15, tzinfo=timezone.utc)                  # exclusive: last archive day 2026-09-14


def day_files(symbol: str) -> list[dict]:
    out, d = [], SPANS[symbol]
    while d < END:
        name = f"{symbol}-metrics-{d:%Y-%m-%d}.zip"
        out.append({"kind": "metrics", "symbol": symbol, "day": f"{d:%Y-%m-%d}", "url": f"{ARCHIVE}/{symbol}/{name}",
                    "path": RAW / symbol / name})
        d += timedelta(days=1)
    return out


def _fetch(f: dict) -> dict:
    try:
        return AD._fetch_verified(f)
    except RuntimeError as e:
        if "404" in str(e):
            return {"kind": "metrics", "url": f["url"], "file": str(f["path"].relative_to(STUDY)).replace("\\", "/"),
                    "status": "missing_in_archive", "bytes": 0, "sha256": None}
        raise


def download() -> Path:
    files = [f for s in SPANS for f in day_files(s)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(_fetch, files))
    for e, f in zip(entries, files):
        e["symbol"], e["day"] = f["symbol"], f["day"]
    manifest = {"source": "Binance public data archive, futures/um/daily/metrics",
                "spans": {s: [d.isoformat(), END.isoformat()] for s, d in SPANS.items()},
                "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status_counts": pd.Series([e["status"] for e in entries]).value_counts().to_dict(), "files": entries}
    out = RAW / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {out} ({len(entries)} entries, {manifest['status_counts']})")
    return out


def build_symbol(symbol: str, manifest: dict) -> dict:
    files = [e for e in manifest["files"] if e["symbol"] == symbol]
    frames, missing = [], []
    for e in files:
        if e["status"] == "missing_in_archive":
            missing.append(e["day"])
            continue
        path = STUDY / e["file"]
        if AD._sha256(path.read_bytes()) != e["sha256"]:
            raise RuntimeError(f"{path}: file changed since download")
        frames.append(AD._read_zip_csv(path))
    m = pd.concat(frames, ignore_index=True)
    m["ts_s"] = [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in m["create_time"]]
    rows = len(m)
    m = m.drop_duplicates(subset=["ts_s"] + list(AD.METRIC_COLUMNS))
    identical_dropped = rows - len(m)
    conflicting = int(m.duplicated("ts_s", keep=False).sum())
    m = m.sort_values("ts_s", kind="mergesort").drop_duplicates("ts_s", keep="last")
    t0 = int(SPANS[symbol].timestamp())
    n = (int(END.timestamp()) - t0) // 300
    idx = ((m["ts_s"] - t0) // 300).to_numpy()
    keep = ((m["ts_s"] - t0) % 300 == 0).to_numpy() & (idx >= 0) & (idx < n)
    arrays = {}
    for col, name in AD.METRIC_COLUMNS.items():
        a = np.full(n, np.nan)
        a[idx[keep]] = m[col].to_numpy(float)[keep]
        arrays[name] = a
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{symbol}_metrics_5m_full.npz"
    np.savez_compressed(out, t0_s=np.array([t0], dtype=np.int64), **arrays)
    present = np.isfinite(arrays["oi"])
    meta = {"symbol": symbol, "grid": "5-minute create_time stamps, UTC s", "t0_utc": SPANS[symbol].isoformat(),
            "end_utc_exclusive": END.isoformat(), "slots": n, "rows_read": rows,
            "identical_duplicates_dropped": identical_dropped, "conflicting_duplicate_rows": conflicting,
            "off_grid_rows": int((~((m["ts_s"] - t0) % 300 == 0)).sum()), "slots_present": int(present.sum()),
            "days_missing_in_archive": missing,
            "slots_present_by_year": {str(y): int(present[_year_slice(t0, y, n)].sum()) for y in range(SPANS[symbol].year, END.year + 1)},
            "logical_sha256": MD._logical_hash(arrays), "file_sha256": AD._sha256(out.read_bytes()),
            "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (CACHE / f"{symbol}_metrics_5m_full.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{symbol}: {meta['slots_present']}/{n} slots, {len(missing)} archive days missing, {conflicting} conflicting duplicates")
    return meta


def _year_slice(t0: int, year: int, n: int) -> slice:
    lo = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    hi = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    return slice(min(n, max(0, (lo - t0) // 300)), min(n, max(0, (hi - t0) // 300)))


def build() -> None:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    for s in SPANS:
        build_symbol(s, manifest)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "build"])
    step = ap.parse_args().step
    download() if step == "download" else build()
