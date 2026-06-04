"""Bulk historical klines backfill for the screener universe.

Pulls daily + 1h candles for every coin in `screener_universe` and writes to
`screener_klines_daily` / `screener_klines_1h` with (asset, ts) PK so the
tables are multi-asset from day 1.

Usage:
    python data/sources/binance_klines_bulk.py [--interval 1d] [--days 1100]
    python data/sources/binance_klines_bulk.py --interval 1h --days 365
    python data/sources/binance_klines_bulk.py --all   # both 1d (1100d) + 1h (365d)
    python data/sources/binance_klines_bulk.py --top-n 50   # limit universe slice

Re-runs are idempotent — INSERT OR REPLACE on (asset, ts). Resumes from the
most recent ts already in the table per asset.

Rate limits: Binance FAPI allows 2400 weight/min, each klines call = 1 weight.
We pace 6 req/sec (≈360/min) to leave headroom for other jobs running on the
same IP. A 1100d × 150-coin backfill is ~150 calls (1 per coin, 1000 candles
each) → completes in ~25 seconds. The 1h × 365d × 150-coin job is ~1,300 calls
(9 per coin) → ~4 minutes.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
FAPI = "https://fapi.binance.com/fapi/v1"
SLEEP_SEC = 1.0 / 6.0  # 6 req/sec

log = logging.getLogger("binance_klines_bulk")


# ─── HTTP with retry ─────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None,
         timeout: float = 20.0, retries: int = 3):
    if params:
        url = f"{url}?{urlencode(params)}"
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
            with urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (HTTPError, URLError, TimeoutError) as e:
            last_err = e
            # Binance 418/429 = rate-limit; back off harder
            wait = 2.0 ** attempt * 2.0
            log.warning(f"  retry {attempt + 1}/{retries} after {wait:.1f}s: {e}")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {retries} retries: {last_err}")


# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS screener_klines_daily (
    asset        TEXT,
    ts           INTEGER,
    open         REAL,
    high         REAL,
    low          REAL,
    close        REAL,
    volume       REAL,
    quote_volume REAL,
    PRIMARY KEY (asset, ts)
);
CREATE INDEX IF NOT EXISTS ix_skd_ts ON screener_klines_daily(ts);

CREATE TABLE IF NOT EXISTS screener_klines_1h (
    asset        TEXT,
    ts           INTEGER,
    open         REAL,
    high         REAL,
    low          REAL,
    close        REAL,
    volume       REAL,
    quote_volume REAL,
    PRIMARY KEY (asset, ts)
);
CREATE INDEX IF NOT EXISTS ix_sk1h_ts ON screener_klines_1h(ts);
"""

INTERVAL_TABLE = {"1d": "screener_klines_daily", "1h": "screener_klines_1h"}
INTERVAL_SEC = {"1d": 86_400, "1h": 3_600}


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


# ─── Backfill core ──────────────────────────────────────────────────────────

def _latest_ts_for_asset(con: sqlite3.Connection, table: str,
                         asset: str) -> int:
    row = con.execute(
        f"SELECT MAX(ts) FROM {table} WHERE asset = ?", (asset,)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_one_pass(con: sqlite3.Connection, asset: str, interval: str,
                   start_ms: int, end_ms: int) -> int:
    """Fetch one batch (≤1000 bars) of klines for asset/interval.

    Returns count inserted in this batch.
    """
    table = INTERVAL_TABLE[interval]
    params: dict = {
        "symbol": asset,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    rows = _get(f"{FAPI}/klines", params)
    n = 0
    for r in rows:
        ts_s = int(r[0]) // 1000
        con.execute(
            f"INSERT OR REPLACE INTO {table} "
            "(asset, ts, open, high, low, close, volume, quote_volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset, ts_s,
             float(r[1]), float(r[2]), float(r[3]), float(r[4]),
             float(r[5]), float(r[7])),
        )
        n += 1
    con.commit()
    return n


def backfill_asset(con: sqlite3.Connection, asset: str, interval: str,
                   target_start_ms: int, now_ms: int) -> int:
    """Backfill `asset` at `interval` from target_start_ms forward to now_ms.

    Resumes from the latest ts already stored (so re-runs are cheap).
    Walks in 1000-bar windows. Returns total bars inserted.
    """
    table = INTERVAL_TABLE[interval]
    interval_ms = INTERVAL_SEC[interval] * 1000
    latest_ts_s = _latest_ts_for_asset(con, table, asset)
    if latest_ts_s > 0:
        cursor_ms = (latest_ts_s + INTERVAL_SEC[interval]) * 1000
    else:
        cursor_ms = target_start_ms

    if cursor_ms >= now_ms:
        return 0  # already current

    total = 0
    while cursor_ms < now_ms:
        window_end_ms = min(cursor_ms + 1000 * interval_ms, now_ms)
        try:
            n = fetch_one_pass(con, asset, interval, cursor_ms, window_end_ms)
        except RuntimeError as e:
            log.warning(f"  {asset}: aborting at cursor {cursor_ms}: {e}")
            break
        total += n
        if n == 0:
            # Exchange returned no rows — coin not yet listed or gap. Step forward.
            cursor_ms = window_end_ms
        else:
            cursor_ms = (int(_latest_ts_for_asset(con, table, asset)) + 1) * 1000
        time.sleep(SLEEP_SEC)
    return total


def run_backfill(interval: str, days_back: int, top_n: int | None = None,
                 asset_filter: list[str] | None = None) -> dict[str, int]:
    """Backfill all universe coins at `interval` for `days_back` days.

    If top_n given, only process the top-N coins by 24h quote volume
    (ranked at universe-refresh time).
    Returns asset -> bars-inserted dict.
    """
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        sql = ("SELECT asset, median_quote_volume_30d FROM screener_universe "
               "ORDER BY median_quote_volume_30d DESC")
        rows = con.execute(sql).fetchall()
        if top_n is not None:
            rows = rows[:top_n]
        if asset_filter is not None:
            rows = [r for r in rows if r[0] in set(asset_filter)]

        now_ms = int(time.time() * 1000)
        target_start_ms = now_ms - days_back * 86_400_000
        log.info(f"backfilling {interval} for {len(rows)} coins, "
                 f"{days_back} days back")

        results: dict[str, int] = {}
        for i, (asset, qvol) in enumerate(rows, 1):
            t0 = time.time()
            try:
                n = backfill_asset(con, asset, interval, target_start_ms, now_ms)
            except Exception as e:
                log.error(f"  [{i:3d}/{len(rows)}] {asset}: {e}")
                results[asset] = -1
                continue
            elapsed = time.time() - t0
            results[asset] = n
            log.info(f"  [{i:3d}/{len(rows)}] {asset:<14s} +{n:>5d} bars  "
                     f"({elapsed:.1f}s)  qvol={qvol/1e6:.0f}M")
    finally:
        con.close()
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", choices=["1d", "1h"],
                   help="Single interval to backfill")
    p.add_argument("--days", type=int, default=None,
                   help="Days of history (default: 1100 for 1d, 365 for 1h)")
    p.add_argument("--top-n", type=int, default=None,
                   help="Limit to top-N coins by 24h quote volume")
    p.add_argument("--asset", action="append", default=None,
                   help="Restrict to specific asset (repeatable)")
    p.add_argument("--all", action="store_true",
                   help="Run both 1d (1100d) and 1h (365d) backfills")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not args.all and args.interval is None:
        p.error("must give --interval or --all")

    intervals: list[tuple[str, int]] = []
    if args.all:
        intervals = [("1d", args.days or 1100), ("1h", args.days or 365)]
    else:
        default_days = 1100 if args.interval == "1d" else 365
        intervals = [(args.interval, args.days or default_days)]

    for interval, days in intervals:
        log.info(f"=== bulk klines: interval={interval} days={days} ===")
        results = run_backfill(interval, days, top_n=args.top_n,
                                asset_filter=args.asset)
        ok = sum(1 for n in results.values() if n >= 0)
        bars = sum(n for n in results.values() if n > 0)
        failures = [a for a, n in results.items() if n < 0]
        log.info(f"=== done: {ok}/{len(results)} OK, {bars:,} bars inserted, "
                 f"{len(failures)} failures ===")
        if failures:
            log.info(f"failed: {', '.join(failures[:10])}"
                     f"{'...' if len(failures) > 10 else ''}")


if __name__ == "__main__":
    main()
