"""CoinMetrics community-tier ingestion for BTC.

Base URL: https://community-api.coinmetrics.io/v4 (NO auth required for these)
Rate limit: 10 req per 6 seconds per IP (~100 req/min). Free.

Community tier offers 32 BTC asset-metrics (no market/derivatives data).
This module ingests the metrics we actually want:

  Hourly:
    - ReferenceRateUSD — institutional-grade BTC USD price (only hourly metric)

  Daily:
    - FlowInExUSD / FlowOutExUSD — exchange inflows/outflows in USD (chento-relevant
      whale movement signal we don't have from other sources)
    - SplyExUSD / SplyExNtv — exchange-held supply (positioning context)
    - CapMVRVCur — MVRV ratio (cycle gate)
    - HashRate — daily mean hashrate
    - AdrActCnt — daily active addresses
    - TxCnt / TxTfrCnt — daily transactions / transfers
    - CapMrktCurUSD / CapMrktEstUSD — market cap variants
    - ROI1yr / ROI30d — return-on-investment context

Tables (all keyed on (asset, timestamp) since CoinMetrics serves multi-asset
even though we only ingest BTC for now):
    cm_reference_rate_1h(asset, timestamp, ref_rate_usd)
    cm_daily_metrics(asset, date, [all daily metric columns ...])

Backfill range: 2018-01-01 onward by default (covers BTC institutional era).

Usage:
    python data/sources/coinmetrics.py --backfill
    python data/sources/coinmetrics.py --backfill --start 2020-01-01
    python data/sources/coinmetrics.py --refresh         # tail-update
    python data/sources/coinmetrics.py --catalog         # print available metrics
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
BASE_URL = "https://community-api.coinmetrics.io/v4"
RATE_LIMIT_DELAY = 0.7  # ~85 req/min, well under the 100/min ceiling
HTTP_TIMEOUT = 60

log = logging.getLogger("p300.coinmetrics")

# Daily metrics we want (community-tier list, filtered to chento-relevant ones)
DAILY_METRICS = [
    "FlowInExUSD", "FlowOutExUSD", "SplyExUSD", "SplyExNtv",
    "CapMVRVCur", "HashRate", "AdrActCnt", "TxCnt", "TxTfrCnt",
    "CapMrktCurUSD", "CapMrktEstUSD", "ROI1yr", "ROI30d",
    "IssTotUSD", "FeeTotNtv",
]


def _parse_cm_ts(ts_iso: str) -> int | None:
    """Parse CoinMetrics ISO timestamp (with nanoseconds) to Unix epoch seconds.

    Format examples:
      "2026-05-20T01:00:00.000000000Z" (hourly)
      "2024-01-01T00:00:00.000000000Z" (daily)
    """
    if not ts_iso:
        return None
    # Strip the nanosecond fractional and the trailing Z
    if "." in ts_iso:
        ts_iso = ts_iso.split(".", 1)[0]
    ts_iso = ts_iso.rstrip("Z")
    try:
        return int(datetime.fromisoformat(ts_iso).replace(
            tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


# ─── HTTP layer ──────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None,
         retries: int = 4) -> dict:
    url = BASE_URL + path
    if params:
        url += "?" + urlencode(params)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
            with urlopen(req, timeout=HTTP_TIMEOUT) as r:
                time.sleep(RATE_LIMIT_DELAY)
                return json.loads(r.read())
        except HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = 2 ** attempt * 3
                log.warning(f"  rate-limited; sleeping {wait}s")
                time.sleep(wait)
                continue
            # 4xx other than 429 is bad params — don't retry
            body = e.read().decode(errors='replace')[:200]
            raise RuntimeError(f"HTTP {e.code} on {url[:120]}: {body}")
        except (URLError, TimeoutError) as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries}: {last_err}")


# ─── Schema ──────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS cm_reference_rate_1h (
    asset      TEXT NOT NULL,
    timestamp  INTEGER NOT NULL,
    ref_rate_usd REAL,
    PRIMARY KEY (asset, timestamp)
);
CREATE INDEX IF NOT EXISTS ix_cmref1h_ts ON cm_reference_rate_1h(timestamp);

CREATE TABLE IF NOT EXISTS cm_daily_metrics (
    asset            TEXT NOT NULL,
    date             TEXT NOT NULL,
    FlowInExUSD      REAL,
    FlowOutExUSD     REAL,
    SplyExUSD        REAL,
    SplyExNtv        REAL,
    CapMVRVCur       REAL,
    HashRate         REAL,
    AdrActCnt        REAL,
    TxCnt            REAL,
    TxTfrCnt         REAL,
    CapMrktCurUSD    REAL,
    CapMrktEstUSD    REAL,
    ROI1yr           REAL,
    ROI30d           REAL,
    IssTotUSD        REAL,
    FeeTotNtv        REAL,
    PRIMARY KEY (asset, date)
);
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


# ─── Reference rate hourly ───────────────────────────────────────────────

def fetch_reference_rate_1h(asset: str, start_iso: str, end_iso: str | None = None) -> int:
    """Fetch ReferenceRateUSD at 1h for `asset` from start_iso to end_iso.

    Paginates through all results. Returns rows inserted.
    """
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        total = 0
        params: dict = {
            "assets": asset, "metrics": "ReferenceRateUSD",
            "frequency": "1h", "start_time": start_iso,
            "page_size": 10000,
        }
        if end_iso:
            params["end_time"] = end_iso
        next_token = None
        while True:
            if next_token:
                params["next_page_token"] = next_token
            r = _get("/timeseries/asset-metrics", params)
            data = r.get("data", []) or []
            for row in data:
                ts_iso = row.get("time", "")
                ts = _parse_cm_ts(ts_iso)
                if ts is None:
                    continue
                price = row.get("ReferenceRateUSD")
                if price is None:
                    continue
                con.execute(
                    "INSERT OR REPLACE INTO cm_reference_rate_1h "
                    "(asset, timestamp, ref_rate_usd) VALUES (?, ?, ?)",
                    (asset.upper(), ts, float(price)),
                )
                total += 1
            con.commit()
            next_token = r.get("next_page_token")
            if not next_token:
                break
        return total
    finally:
        con.close()


# ─── Daily metrics (multi-metric bulk fetch) ─────────────────────────────

def fetch_daily_metrics(asset: str, start_iso: str,
                         end_iso: str | None = None,
                         metrics: list[str] | None = None) -> int:
    """Fetch daily metrics in bulk (CoinMetrics supports comma-separated
    metrics in one call). Upserts into cm_daily_metrics with one row per
    (asset, date)."""
    metrics = metrics or DAILY_METRICS
    metric_str = ",".join(metrics)
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        params: dict = {
            "assets": asset, "metrics": metric_str,
            "frequency": "1d", "start_time": start_iso,
            "page_size": 10000,
        }
        if end_iso:
            params["end_time"] = end_iso
        total = 0
        next_token = None
        while True:
            if next_token:
                params["next_page_token"] = next_token
            r = _get("/timeseries/asset-metrics", params)
            data = r.get("data", []) or []
            for row in data:
                ts_iso = row.get("time", "")
                date_str = ts_iso[:10]  # YYYY-MM-DD
                if not date_str:
                    continue
                cols = ["asset", "date"]
                vals = [asset.upper(), date_str]
                for m in metrics:
                    cols.append(m)
                    v = row.get(m)
                    vals.append(float(v) if v is not None else None)
                placeholders = ",".join(["?"] * len(cols))
                con.execute(
                    f"INSERT OR REPLACE INTO cm_daily_metrics ({','.join(cols)}) "
                    f"VALUES ({placeholders})", vals,
                )
                total += 1
            con.commit()
            next_token = r.get("next_page_token")
            if not next_token:
                break
        return total
    finally:
        con.close()


# ─── Catalog probe ───────────────────────────────────────────────────────

def print_catalog() -> None:
    r = _get("/catalog/asset-metrics")
    metrics = r.get("data", [])
    print(f"CoinMetrics community asset-metrics: {len(metrics)} available")
    by_cat: dict = {}
    for m in metrics:
        by_cat.setdefault(m.get("category", "?"), []).append(m)
    for cat, items in sorted(by_cat.items()):
        print(f"\n[{cat}]")
        for m in sorted(items, key=lambda x: x.get("metric", "")):
            freqs = ",".join(f.get("frequency", "?") for f in m.get("frequencies", []))
            print(f"  {m.get('metric'):<28s} [{freqs}]")


# ─── CLI ─────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--asset", default="btc",
                   help="Asset (default: btc; CoinMetrics uses lowercase)")
    p.add_argument("--start", default="2018-01-01",
                   help="Backfill start date YYYY-MM-DD (default 2018-01-01)")
    p.add_argument("--end", default=None, help="Optional end date YYYY-MM-DD")
    p.add_argument("--backfill", action="store_true",
                   help="Pull full history from --start to --end")
    p.add_argument("--refresh", action="store_true",
                   help="Pull only since most recent stored row (incremental)")
    p.add_argument("--catalog", action="store_true",
                   help="Print community-available metrics and exit")
    p.add_argument("--hourly-only", action="store_true",
                   help="Skip daily metrics, only fetch hourly reference rate")
    p.add_argument("--daily-only", action="store_true",
                   help="Skip hourly, only fetch daily metrics")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.catalog:
        print_catalog()
        return 0

    if not (args.backfill or args.refresh):
        p.error("Must pass --backfill or --refresh")

    start = args.start
    if args.refresh:
        # find latest in DB
        con = sqlite3.connect(str(DB_PATH))
        try:
            ensure_schema(con)
            row = con.execute(
                "SELECT MAX(date) FROM cm_daily_metrics WHERE asset=?",
                (args.asset.upper(),)).fetchone()
            if row and row[0]:
                start = row[0]
        finally:
            con.close()

    # Hourly reference rate
    if not args.daily_only:
        print(f"Fetching hourly reference rate for {args.asset} from {start}...")
        n = fetch_reference_rate_1h(args.asset, start, args.end)
        print(f"  -> {n:,} rows upserted")

    # Daily metrics bulk
    if not args.hourly_only:
        print(f"Fetching daily metrics ({len(DAILY_METRICS)} metrics) "
              f"for {args.asset} from {start}...")
        n = fetch_daily_metrics(args.asset, start, args.end)
        print(f"  -> {n:,} rows upserted")

    return 0


if __name__ == "__main__":
    sys.exit(main())
