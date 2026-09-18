"""Top anatomy (stage A) data: BTCUSDT open interest, long/short ratios and premium index, plus the SJ-4250 case panel.

Two steps, run from the repository root:

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\anatomy_data.py download
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\anatomy_data.py build

`download` fetches, checksum-verified, the daily `metrics` archive (5-minute open interest and long/short ratios) and
the 1-minute `premiumIndexKlines` archive for 2022-01-01 → 2026-09-14, and for the case the 2026-09-14 1-minute klines
and bookDepth day files. It then saves verbatim Binance public REST responses from 2026-09-14 00:00 UTC to the time of
the call (1-minute klines, premium index, 5-minute open interest and ratios). `build` writes dense grids to cache/ with
JSON sidecars. Nothing here reads or writes prod.db.

Only BTCUSDT: the ETH holdout of stage B is not downloaded here (PROTOCOL_TOP_ANATOMY.md section 7).
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
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

import micro_data as MD

STUDY = Path(__file__).resolve().parent
RAW = STUDY / "data" / "raw" / "anatomy"
CACHE = STUDY / "cache"
SYMBOL = "BTCUSDT"
ARCHIVE = "https://data.binance.vision/data/futures/um"
FAPI = "https://fapi.binance.com"
START = datetime(2022, 1, 1, tzinfo=timezone.utc)
ARCHIVE_END = datetime(2026, 9, 15, tzinfo=timezone.utc)          # exclusive: last archive day 2026-09-14
LAST_MONTHLY = (2026, 8)
CASE_FROM = datetime(2026, 9, 14, tzinfo=timezone.utc)           # REST span starts here (overlaps the archive's last day)
METRIC_COLUMNS = {"sum_open_interest": "oi", "sum_open_interest_value": "oi_value",
                  "count_toptrader_long_short_ratio": "top_account_lsr", "sum_toptrader_long_short_ratio": "top_position_lsr",
                  "count_long_short_ratio": "account_lsr", "sum_taker_long_short_vol_ratio": "taker_ratio"}
KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
                 "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
# The REST ratio and open-interest endpoints stamp a value 5 minutes later than the archive's create_time for the same
# value (all 289 overlapping slots on 2026-09-14 match exactly after this shift).
REST_METRIC_STAMP_OFFSET_S = 300


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get(url: str, timeout: float = 120.0, retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "p300-exit-policy-study/1.0"}), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # network errors are retried, then raised
            last = e
            time.sleep(2.0 * 2 ** attempt)
    raise RuntimeError(f"GET failed: {url}: {last}")


# --- file lists ---------------------------------------------------------------------

def archive_files() -> list[dict]:
    files = []
    d = START
    while d < ARCHIVE_END:
        files.append({"kind": "metrics", "url": f"{ARCHIVE}/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{d:%Y-%m-%d}.zip"})
        d += timedelta(days=1)
    y, m = START.year, START.month
    while (y, m) <= LAST_MONTHLY:
        files.append({"kind": "premium_1m", "url": f"{ARCHIVE}/monthly/premiumIndexKlines/{SYMBOL}/1m/{SYMBOL}-1m-{y}-{m:02d}.zip"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = datetime(LAST_MONTHLY[0], LAST_MONTHLY[1] + 1, 1, tzinfo=timezone.utc)
    while d < ARCHIVE_END:
        files.append({"kind": "premium_1m", "url": f"{ARCHIVE}/daily/premiumIndexKlines/{SYMBOL}/1m/{SYMBOL}-1m-{d:%Y-%m-%d}.zip"})
        d += timedelta(days=1)
    files.append({"kind": "case_klines_1m", "url": f"{ARCHIVE}/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-2026-09-14.zip"})
    files.append({"kind": "case_bookDepth", "url": f"{ARCHIVE}/daily/bookDepth/{SYMBOL}/{SYMBOL}-bookDepth-2026-09-14.zip"})
    for f in files:
        f["path"] = RAW / f["kind"] / f["url"].rsplit("/", 1)[1]
    return files


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
    return {"kind": f["kind"], "url": f["url"], "file": str(path.relative_to(STUDY)).replace("\\", "/"),
            "bytes": path.stat().st_size, "sha256": expected, "status": status}


# --- REST (the case's last day) -----------------------------------------------------------

REST_SERIES = {
    "klines_1m": ("/fapi/v1/klines", {"interval": "1m", "limit": 1500}, 60_000 * 1500),
    "premium_1m": ("/fapi/v1/premiumIndexKlines", {"interval": "1m", "limit": 1500}, 60_000 * 1500),
    "open_interest_5m": ("/futures/data/openInterestHist", {"period": "5m", "limit": 500}, 300_000 * 500),
    "top_position_lsr_5m": ("/futures/data/topLongShortPositionRatio", {"period": "5m", "limit": 500}, 300_000 * 500),
    "top_account_lsr_5m": ("/futures/data/topLongShortAccountRatio", {"period": "5m", "limit": 500}, 300_000 * 500),
    "account_lsr_5m": ("/futures/data/globalLongShortAccountRatio", {"period": "5m", "limit": 500}, 300_000 * 500),
}


def fetch_rest(now: datetime) -> dict:
    out = {}
    for name, (path, params, span_ms) in REST_SERIES.items():
        rows, start = [], ms(CASE_FROM)
        end = ms(now)
        while start < end:
            q = "&".join(f"{k}={v}" for k, v in {"symbol": SYMBOL, **params, "startTime": start,
                                                 "endTime": min(end, start + span_ms) - 1}.items())
            batch = json.loads(_get(f"{FAPI}{path}?{q}"))
            rows.extend(batch)
            start = min(end, start + span_ms)
        body = json.dumps(rows, indent=1).encode()
        target = RAW / "rest" / f"{SYMBOL}-{name}-from-2026-09-14.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        out[name] = {"file": str(target.relative_to(STUDY)).replace("\\", "/"), "rows": len(rows), "sha256": _sha256(body),
                     "url": f"{FAPI}{path}"}
    return out


def download() -> Path:
    files = archive_files()
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(_fetch_verified, files))
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    manifest = {"source": "Binance public data archive + public REST", "symbol": SYMBOL,
                "archive_span": [START.isoformat(), ARCHIVE_END.isoformat()], "rest_retrieved_utc": now.isoformat(),
                "files": entries, "rest": fetch_rest(now)}
    out = RAW / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"manifest: {out} ({len(entries)} archive files; REST rows "
          f"{ {k: v['rows'] for k, v in manifest['rest'].items()} })")
    return out


# --- build --------------------------------------------------------------------------------

def _read_zip_csv(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != 1:
            raise RuntimeError(f"{path}: expected one member, found {names}")
        raw = z.read(names[0])
    has_header = not raw[:1].isdigit()
    return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)


def build_metrics(manifest: dict) -> dict:
    frames = [_read_zip_csv(STUDY / e["file"]) for e in manifest["files"] if e["kind"] == "metrics"]
    m = pd.concat(frames, ignore_index=True)
    m["ts_s"] = [int(pd.Timestamp(x, tz="UTC").timestamp()) for x in m["create_time"]]
    rows = len(m)
    m = m.drop_duplicates(subset=["ts_s"] + list(METRIC_COLUMNS))
    identical_dropped = rows - len(m)
    conflicting = int(m.duplicated("ts_s", keep=False).sum())
    m = m.sort_values("ts_s", kind="mergesort").drop_duplicates("ts_s", keep="last")
    t0 = int(START.timestamp())
    n = (int(ARCHIVE_END.timestamp()) - t0) // 300
    off_grid = int(((m["ts_s"] - t0) % 300 != 0).sum())
    idx = ((m["ts_s"] - t0) // 300).to_numpy()
    keep = ((m["ts_s"] - t0) % 300 == 0).to_numpy() & (idx >= 0) & (idx < n)
    arrays = {}
    for col, name in METRIC_COLUMNS.items():
        a = np.full(n, np.nan)
        a[idx[keep]] = m[col].to_numpy(float)[keep]
        arrays[name] = a
    out = CACHE / f"{SYMBOL}_metrics_5m.npz"
    np.savez_compressed(out, t0_s=np.array([t0], dtype=np.int64), **arrays)
    meta = {"symbol": SYMBOL, "grid": "5-minute create_time stamps, UTC s", "t0_utc": START.isoformat(), "slots": n,
            "rows_read": rows, "identical_duplicates_dropped": identical_dropped,
            "conflicting_duplicate_rows": conflicting, "off_grid_rows": off_grid,
            "slots_present": int(np.isfinite(arrays["oi"]).sum()), "logical_sha256": MD._logical_hash(arrays),
            "file_sha256": _sha256(out.read_bytes())}
    (CACHE / f"{SYMBOL}_metrics_5m.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"metrics: {meta['slots_present']}/{n} slots, {identical_dropped} identical duplicates, {conflicting} conflicting")
    return meta


def build_premium(manifest: dict) -> dict:
    t0_ms = ms(START)
    n = (ms(ARCHIVE_END) - t0_ms) // 60_000
    close = np.full(n, np.nan)
    rows = 0
    for e in manifest["files"]:
        if e["kind"] != "premium_1m":
            continue
        f = _read_zip_csv(STUDY / e["file"])
        if "open_time" not in f.columns:
            f.columns = KLINE_COLUMNS
        ot = f["open_time"].to_numpy(np.int64)
        idx = (ot - t0_ms) // 60_000
        ok = ((ot - t0_ms) % 60_000 == 0) & (idx >= 0) & (idx < n)
        close[idx[ok]] = f["close"].to_numpy(float)[ok]
        rows += len(f)
    out = CACHE / f"{SYMBOL}_premium_1m.npz"
    np.savez_compressed(out, t0_s=np.array([t0_ms // 1000], dtype=np.int64), close=close)
    meta = {"symbol": SYMBOL, "value": "premium index kline close (fraction)", "t0_utc": START.isoformat(), "minutes": n,
            "rows_read": rows, "minutes_present": int(np.isfinite(close).sum()),
            "logical_sha256": MD._logical_hash({"close": close}), "file_sha256": _sha256(out.read_bytes())}
    (CACHE / f"{SYMBOL}_premium_1m.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"premium: {meta['minutes_present']}/{n} minutes")
    return meta


def build_case(manifest: dict) -> dict:
    """SJ-4250 panel, 2026-09-01 00:00 → the REST retrieval minute: klines, premium, metrics and book on one minute grid."""
    t0 = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    now = int(pd.Timestamp(manifest["rest_retrieved_utc"]).timestamp())
    n = (now - t0) // 60
    orb = np.load(STUDY.parent / "orb_study" / "cache" / f"{SYMBOL}_perp_1m.npz")
    o0 = (t0 - int(orb["t0_ms"][0]) // 1000) // 60
    fields = ("open", "high", "low", "close", "volume", "taker_buy_volume")
    panel = {k: np.full(n, np.nan) for k in fields}
    source = np.zeros(n, dtype=np.int8)                 # 1 ORB panel, 2 archive day file, 3 REST
    m_orb = min(n, len(orb["close"]) - o0)
    for k in fields:
        panel[k][:m_orb] = orb[k][o0:o0 + m_orb]
    source[:m_orb] = 1
    day = _read_zip_csv(STUDY / next(e["file"] for e in manifest["files"] if e["kind"] == "case_klines_1m"))
    if "open_time" not in day.columns:
        day.columns = KLINE_COLUMNS
    idx = (day["open_time"].to_numpy(np.int64) // 1000 - t0) // 60
    for k in fields:
        panel[k][idx] = day[k].to_numpy(float)
    source[idx] = 2
    rest = json.loads((STUDY / manifest["rest"]["klines_1m"]["file"]).read_text())
    rest = [r for r in rest if int(r[6]) < now * 1000]                 # closed bars only
    ridx = np.array([(int(r[0]) // 1000 - t0) // 60 for r in rest])
    rvals = {k: np.array([float(r[KLINE_COLUMNS.index(k)]) for r in rest]) for k in fields}
    overlap = source[ridx] == 2
    kline_overlap_mismatch = int(sum((~np.isclose(panel[k][ridx[overlap]], rvals[k][overlap], rtol=0, atol=1e-9)).sum()
                                     for k in fields))
    new = ~overlap
    for k in fields:
        panel[k][ridx[new]] = rvals[k][new]
    source[ridx[new]] = 3
    prem = np.full(n, np.nan)
    with np.load(CACHE / f"{SYMBOL}_premium_1m.npz") as z:
        p0 = (t0 - int(z["t0_s"][0])) // 60
        seg = z["close"][p0:p0 + n]
        prem[:len(seg)] = seg
    rp = json.loads((STUDY / manifest["rest"]["premium_1m"]["file"]).read_text())
    rp = [r for r in rp if int(r[6]) < now * 1000]
    pidx = np.array([(int(r[0]) // 1000 - t0) // 60 for r in rp])
    pval = np.array([float(r[4]) for r in rp])
    have = np.isfinite(prem[pidx])
    premium_overlap_max_diff = float(np.max(np.abs(prem[pidx[have]] - pval[have]))) if have.any() else None
    prem[pidx[~have]] = pval[~have]
    metrics = case_metrics(manifest, t0, now)
    bid, ask = np.full(n, np.nan), np.full(n, np.nan)
    with np.load(CACHE / f"{SYMBOL}_book1pct_1m.npz") as b:
        b0 = (t0 - int(b["t0_s"][0])) // 60
        seg_b, seg_a = b["bid_notional_1pct"][b0:b0 + n], b["ask_notional_1pct"][b0:b0 + n]
        bid[:len(seg_b)], ask[:len(seg_a)] = seg_b, seg_a
    snaps, _ = MD.read_day(STUDY / next(e["file"] for e in manifest["files"] if e["kind"] == "case_bookDepth"))
    MD.last_in_minute(snaps["ts_s"].to_numpy(np.int64), snaps["bid"].to_numpy(), snaps["ask"].to_numpy(), t0, n, bid, ask,
                      np.zeros(n, dtype=np.uint16))
    arrays = {**panel, "premium": prem, "bid_notional_1pct": bid, "ask_notional_1pct": ask, "source": source}
    out = CACHE / f"{SYMBOL}_case_SJ-4250.npz"
    np.savez_compressed(out, t0_s=np.array([t0], dtype=np.int64), **arrays, **{f"m_{k}": v for k, v in metrics["arrays"].items()},
                        m_t0_s=np.array([metrics["t0_s"]], dtype=np.int64))
    meta = {"t0_utc": datetime.fromtimestamp(t0, tz=timezone.utc).isoformat(), "minutes": n,
            "rest_retrieved_utc": manifest["rest_retrieved_utc"],
            "minutes_by_source": {"orb_panel": int((source == 1).sum()), "archive_day": int((source == 2).sum()),
                                  "rest": int((source == 3).sum()), "missing": int((source == 0).sum())},
            "rest_vs_archive_kline_mismatches_2026_09_14": kline_overlap_mismatch,
            "rest_vs_archive_premium_max_abs_diff": premium_overlap_max_diff,
            "metrics": metrics["facts"], "file_sha256": _sha256(out.read_bytes())}
    (CACHE / f"{SYMBOL}_case_SJ-4250.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"case panel: {meta['minutes_by_source']}, kline overlap mismatches {kline_overlap_mismatch}")
    return meta


def case_metrics(manifest: dict, t0: int, now: int) -> dict:
    """5-minute grid for the case: archive through 2026-09-14, REST after; the overlap day is compared."""
    with np.load(CACHE / f"{SYMBOL}_metrics_5m.npz") as z:
        a0 = (t0 - int(z["t0_s"][0])) // 300
        n = (now - t0) // 300 + 1
        arrays = {name: np.full(n, np.nan) for name in METRIC_COLUMNS.values()}
        for name in arrays:
            seg = z[name][a0:a0 + n]
            arrays[name][:len(seg)] = seg
    rest_map = {"open_interest_5m": [("sumOpenInterest", "oi"), ("sumOpenInterestValue", "oi_value")],
                "top_position_lsr_5m": [("longShortRatio", "top_position_lsr")],
                "top_account_lsr_5m": [("longShortRatio", "top_account_lsr")],
                "account_lsr_5m": [("longShortRatio", "account_lsr")]}
    facts = {}
    for series, pairs in rest_map.items():
        rows = json.loads((STUDY / manifest["rest"][series]["file"]).read_text())
        for key, name in pairs:
            idx = np.array([(int(r["timestamp"]) // 1000 - REST_METRIC_STAMP_OFFSET_S - t0) // 300 for r in rows])
            val = np.array([float(r[key]) for r in rows])
            ok = (idx >= 0) & (idx < len(arrays[name]))
            idx, val = idx[ok], val[ok]
            have = np.isfinite(arrays[name][idx])
            rel = np.abs(arrays[name][idx[have]] - val[have]) / np.abs(val[have])
            facts[name] = {"overlap_slots": int(have.sum()), "max_rel_diff": float(rel.max()) if have.any() else None}
            arrays[name][idx[~have]] = val[~have]
    return {"t0_s": t0, "arrays": arrays, "facts": facts}


def build() -> None:
    manifest = json.loads((RAW / "manifest.json").read_text(encoding="utf-8"))
    build_metrics(manifest)
    build_premium(manifest)
    build_case(manifest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "build"])
    args = ap.parse_args()
    download() if args.step == "download" else build()


if __name__ == "__main__":
    main()
