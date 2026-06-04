"""Ingest Binance USD-M perp aggTrades from Binance Vision public archive,
bucketed by 15-minute bin × $-notional tier × side, and write to
`binance_agg_trades_15m` table.

Source: https://data.binance.vision/data/futures/um/daily/aggTrades/<SYMBOL>/<SYMBOL>-aggTrades-YYYY-MM-DD.zip

Each daily ZIP contains a single CSV with columns:
  agg_trade_id, price, quantity, first_trade_id, last_trade_id,
  transact_time (ms), is_buyer_maker

is_buyer_maker semantics:
  True  -> the buyer was the maker (resting limit). Trade is a market SELL.
  False -> the seller was the maker. Trade is a market BUY (taker buy).

We aggregate per 15m bin into:
  whale_buy_qty   : sum of BTC qty for taker-buy trades with notional >= $100k
  whale_sell_qty  : same for taker-sell
  mid_buy_qty / mid_sell_qty   : $1k–$100k
  retail_buy_qty / retail_sell_qty : < $1k
  USD-notional equivalents
  n_trades        : total aggregated trades in the bin

Idempotent: INSERT OR REPLACE keyed on (timestamp, asset).

Usage:
    python data/sources/binance_agg_trades.py --asset BTCUSDT --start 2026-02-25 --end 2026-05-25
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import sqlite3
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
BASE_URL = "https://data.binance.vision/data/futures/um/daily/aggTrades"

# $-notional tier thresholds (USD)
WHALE_USD = 100_000
MID_USD = 1_000
# trades < MID_USD are RETAIL

# Default bar resolutions written on each backfill run. Each resolution maps
# to a separate table; the parser streams the raw aggTrades CSV once and
# writes to all three in a single pass.
BAR_RESOLUTIONS = {
    60: "binance_agg_trades_1m",
    300: "binance_agg_trades_5m",
    900: "binance_agg_trades_15m",
}

log = logging.getLogger("p300.binance_agg_trades")


_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    timestamp        INTEGER NOT NULL,
    asset            TEXT NOT NULL,
    whale_buy_qty    REAL,
    whale_sell_qty   REAL,
    mid_buy_qty      REAL,
    mid_sell_qty     REAL,
    retail_buy_qty   REAL,
    retail_sell_qty  REAL,
    whale_buy_usd    REAL,
    whale_sell_usd   REAL,
    mid_buy_usd      REAL,
    mid_sell_usd     REAL,
    retail_buy_usd   REAL,
    retail_sell_usd  REAL,
    n_trades         INTEGER,
    PRIMARY KEY (timestamp, asset)
);
CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);
"""


def ensure_schema(con: sqlite3.Connection, tables: list[str]) -> None:
    for table in tables:
        con.executescript(_SCHEMA_TEMPLATE.format(table=table))
    con.commit()


def fetch_day_zip(asset: str, day: date, timeout: int = 60) -> bytes:
    """Download a single day's aggTrades ZIP from Binance Vision.
    Returns the raw zip bytes."""
    url = f"{BASE_URL}/{asset}/{asset}-aggTrades-{day.isoformat()}.zip"
    r = requests.get(url, timeout=timeout)
    if r.status_code == 404:
        raise FileNotFoundError(f"No archive for {asset} on {day}: {url}")
    r.raise_for_status()
    return r.content


def _new_bin(ts: int, asset: str) -> dict:
    return {
        'timestamp': ts, 'asset': asset,
        'whale_buy_qty': 0.0, 'whale_sell_qty': 0.0,
        'mid_buy_qty': 0.0, 'mid_sell_qty': 0.0,
        'retail_buy_qty': 0.0, 'retail_sell_qty': 0.0,
        'whale_buy_usd': 0.0, 'whale_sell_usd': 0.0,
        'mid_buy_usd': 0.0, 'mid_sell_usd': 0.0,
        'retail_buy_usd': 0.0, 'retail_sell_usd': 0.0,
        'n_trades': 0,
    }


def parse_and_aggregate(zip_bytes: bytes, asset: str,
                          bar_seconds_list: list[int]) -> dict[int, dict[int, dict]]:
    """Stream-parse the daily aggTrades CSV ONCE and aggregate into multiple
    bar resolutions simultaneously.

    Returns: {bar_seconds: {bin_unix_seconds: row_dict}}.
    """
    out: dict[int, dict[int, dict]] = {bs: {} for bs in bar_seconds_list}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as fh:
            text = io.TextIOWrapper(fh, encoding='utf-8', newline='')
            reader = csv.reader(text)
            first_row = next(reader)
            is_header = True
            try:
                int(first_row[0])
                is_header = False
            except (ValueError, IndexError):
                is_header = True
            rows_iter = iter([first_row] + list(reader)) if not is_header else reader

            for row in rows_iter:
                if len(row) < 7:
                    continue
                try:
                    price = float(row[1])
                    qty = float(row[2])
                    transact_ms = int(row[5])
                    is_buyer_maker = row[6].strip().lower() in ('true', '1')
                except (ValueError, IndexError):
                    continue
                ts_sec = transact_ms // 1000
                notional = price * qty
                if notional >= WHALE_USD:
                    tier = 'whale'
                elif notional >= MID_USD:
                    tier = 'mid'
                else:
                    tier = 'retail'
                side = 'sell' if is_buyer_maker else 'buy'
                qty_col = f'{tier}_{side}_qty'
                usd_col = f'{tier}_{side}_usd'

                for bs in bar_seconds_list:
                    bin_unix = (ts_sec // bs) * bs
                    bins = out[bs]
                    b = bins.get(bin_unix)
                    if b is None:
                        b = _new_bin(bin_unix, asset)
                        bins[bin_unix] = b
                    b[qty_col] += qty
                    b[usd_col] += notional
                    b['n_trades'] += 1
    return out


def upsert_bins(con: sqlite3.Connection, bins: dict[int, dict],
                  table: str) -> int:
    """Upsert aggregated bins into the named table. Returns row count."""
    if not bins:
        return 0
    cur = con.cursor()
    cols = ['timestamp', 'asset',
             'whale_buy_qty', 'whale_sell_qty',
             'mid_buy_qty', 'mid_sell_qty',
             'retail_buy_qty', 'retail_sell_qty',
             'whale_buy_usd', 'whale_sell_usd',
             'mid_buy_usd', 'mid_sell_usd',
             'retail_buy_usd', 'retail_sell_usd',
             'n_trades']
    placeholders = ','.join(['?'] * len(cols))
    rows = [[b[c] for c in cols] for b in bins.values()]
    cur.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
        f"VALUES ({placeholders})", rows)
    con.commit()
    return len(rows)


def backfill(asset: str, start: date, end: date,
              sleep_between: float = 0.5,
              skip_existing: bool = True,
              resolutions: dict[int, str] | None = None) -> dict:
    """Backfill [start, end] inclusive. Writes to ALL resolutions in
    `resolutions` map ({bar_seconds: table_name}) in a single parse pass per
    daily ZIP. Defaults to BAR_RESOLUTIONS (1m / 5m / 15m)."""
    if resolutions is None:
        resolutions = dict(BAR_RESOLUTIONS)
    bar_seconds_list = sorted(resolutions.keys())
    tables = [resolutions[bs] for bs in bar_seconds_list]
    # Use the smallest bar (most rows/day) to decide if a day is already covered
    finest_bs = bar_seconds_list[0]
    finest_table = resolutions[finest_bs]
    expected_bins_per_day = 86400 // finest_bs

    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con, tables)
        stats: dict[str, tuple[int, int]] = {}
        day = start
        while day <= end:
            day_str = day.isoformat()
            if skip_existing:
                day_start = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp())
                day_end = day_start + 86400
                existing = con.execute(
                    f"SELECT COUNT(*) FROM {finest_table} "
                    f"WHERE asset=? AND timestamp >= ? AND timestamp < ?",
                    (asset, day_start, day_end)).fetchone()[0]
                if existing >= 0.9 * expected_bins_per_day:
                    log.info(f"  {day_str}: already have {existing}/{expected_bins_per_day} "
                              f"{finest_table} bins, skip")
                    day += timedelta(days=1)
                    continue
            try:
                log.info(f"  {day_str}: fetching...")
                t0 = time.time()
                zb = fetch_day_zip(asset, day)
                t1 = time.time()
                bins_by_bs = parse_and_aggregate(zb, asset, bar_seconds_list)
                t2 = time.time()
                written_summary = []
                for bs in bar_seconds_list:
                    table = resolutions[bs]
                    n = upsert_bins(con, bins_by_bs[bs], table)
                    written_summary.append(f"{table.split('_')[-1]}={n}")
                t3 = time.time()
                size_mb = len(zb) / (1024 * 1024)
                log.info(f"  {day_str}: {size_mb:.1f}MB zip, "
                          f"{','.join(written_summary)}  "
                          f"(fetch {t1-t0:.1f}s, parse {t2-t1:.1f}s, write {t3-t2:.1f}s)")
                stats[day_str] = (len(bins_by_bs[finest_bs]), 1)
            except FileNotFoundError as e:
                log.warning(f"  {day_str}: archive missing ({e})")
                stats[day_str] = (0, 0)
            except Exception as e:
                log.error(f"  {day_str}: ERROR {e}")
                stats[day_str] = (0, 0)
            if sleep_between > 0:
                time.sleep(sleep_between)
            day += timedelta(days=1)

        # Summary
        for bs in bar_seconds_list:
            table = resolutions[bs]
            total = con.execute(
                f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                f"FROM {table} WHERE asset=?", (asset,)).fetchone()
            if total[0]:
                log.info(
                    f"\n{table}: {total[0]:,} bins  "
                    f"{datetime.fromtimestamp(total[1], UTC).date()} -> "
                    f"{datetime.fromtimestamp(total[2], UTC).date()}")
    finally:
        con.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--asset", default="BTCUSDT",
                    help="Binance perp symbol (default BTCUSDT)")
    p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    p.add_argument("--end", required=True, help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--sleep", type=float, default=0.5,
                    help="Seconds between requests (default 0.5)")
    p.add_argument("--force", action="store_true",
                    help="Re-fetch days already in DB")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(message)s")
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    backfill(args.asset, start, end,
              sleep_between=args.sleep,
              skip_existing=not args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
