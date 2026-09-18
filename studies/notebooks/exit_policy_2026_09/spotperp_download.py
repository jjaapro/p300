"""Spot-vs-perp stage, raw archive download only (no build, no computation on any trade).

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\spotperp_download.py

Fetches, checksum-verified, from data.binance.vision for BTCUSDT and ETHUSDT, 2020-01-01 -> 2026-09-14 (exclusive):
  spot 1-minute klines (monthly zips through 2026-08, daily zips 2026-09-01 .. 2026-09-13)
  USD-M premiumIndexKlines 1-minute (monthly through 2026-08, daily 2026-09-01 .. 2026-09-13)
Files land under data/raw/spotperp/<kind>/ (gitignored) with a manifest. Re-running verifies present files and only
downloads what is missing or corrupt. Nothing here reads prod.db or any trade population.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "spotperp"
BASE = "https://data.binance.vision/data"
START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END_EXCL = datetime(2026, 9, 14, tzinfo=timezone.utc)      # last daily file 2026-09-13
LAST_MONTHLY = (2026, 8)
# symbol -> (first month, kinds). BTCFDUSD (zero-fee pair listed 2023-08) serves only the reported composition
# robustness line of the pre-registration (section 5); it has no premium index of its own.
SYMBOL_SPECS = {"BTCUSDT": (START, ("spot_1m", "premium_1m")),
                "ETHUSDT": (START, ("spot_1m", "premium_1m")),
                "BTCFDUSD": (datetime(2023, 8, 1, tzinfo=timezone.utc), ("spot_1m",))}
SYMBOLS = tuple(SYMBOL_SPECS)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _get(url: str, timeout: float = 180.0, retries: int = 5) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "p300-exit-policy-study/1.0"}), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2.0 * 2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


def file_list() -> list[dict]:
    files = []
    for sym, (first, kinds) in SYMBOL_SPECS.items():
        for kind, prefix in (("spot_1m", f"{BASE}/spot"), ("premium_1m", f"{BASE}/futures/um")):
            if kind not in kinds:
                continue
            sub = "klines" if kind == "spot_1m" else "premiumIndexKlines"
            y, m = first.year, first.month
            while (y, m) <= LAST_MONTHLY:
                files.append({"symbol": sym, "kind": kind,
                              "url": f"{prefix}/monthly/{sub}/{sym}/1m/{sym}-1m-{y}-{m:02d}.zip"})
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            d = datetime(LAST_MONTHLY[0], LAST_MONTHLY[1] + 1, 1, tzinfo=timezone.utc)
            while d < END_EXCL:
                files.append({"symbol": sym, "kind": kind,
                              "url": f"{prefix}/daily/{sub}/{sym}/1m/{sym}-1m-{d:%Y-%m-%d}.zip"})
                d += timedelta(days=1)
    for f in files:
        f["path"] = RAW / f["kind"] / f["url"].rsplit("/", 1)[1]
    return files


def fetch_verified(f: dict) -> dict:
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
    return {"symbol": f["symbol"], "kind": f["kind"], "url": f["url"],
            "file": str(path.relative_to(STUDY)).replace("\\", "/"), "bytes": path.stat().st_size,
            "sha256": expected, "status": status}


def main() -> None:
    files = file_list()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(fetch_verified, files))
    manifest = {"source": "Binance public data archive", "symbols": list(SYMBOLS),
                "span": [START.isoformat(), END_EXCL.isoformat()],
                "symbol_spans": {s: [f.isoformat(), END_EXCL.isoformat()] for s, (f, _) in SYMBOL_SPECS.items()},
                "retrieved_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "files": entries}
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    by = {}
    for e in entries:
        k = (e["symbol"], e["kind"])
        by.setdefault(k, {"n": 0, "bytes": 0, "downloaded": 0})
        by[k]["n"] += 1
        by[k]["bytes"] += e["bytes"]
        by[k]["downloaded"] += e["status"] == "downloaded"
    for k, v in by.items():
        print(f"{k[0]} {k[1]}: {v['n']} files, {v['bytes'] / 1e6:.1f} MB, {v['downloaded']} downloaded")
    print(f"manifest: {RAW / 'manifest.json'} ({len(entries)} files, {time.time() - t0:.0f} s)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
