"""Fetch alt-coin OHLCV into prod.db for cross-referencing chento's trade book.

The chento journal is alt-heavy in the bootstrap era — OPUSDT dominates June-Aug
2024. This script pulls Binance USDT-margined perpetual klines for assets he's
known to trade and writes them into prod.db with a schema mirroring btc_1m /
eth_1m. Run once for backfill; re-run to top up.

Standalone — does not modify data/sources/binance.py. If alts become permanent
fixtures of the live bot, fold the fetch_one_alt_perp() helper into
binance.refresh_all().

    python studies/notebooks/chento_journal/fetch_alts.py        # fetch defaults
    python studies/notebooks/chento_journal/fetch_alts.py SOL XRP # specific symbols
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / 'data' / 'databases' / 'prod.db'
FAPI = 'https://fapi.binance.com/fapi/v1'

# Symbols he's been seen trading + the perp launch date on Binance (for backfill
# floor — fetching from before the launch returns nothing and wastes API calls).
# Launch dates from Binance's announcements (https://www.binance.com/en/support/announcement).
ASSETS = {
    'OPUSDT':  '2022-08-03',  # primary bootstrap asset in 2024
    'SOLUSDT': '2020-09-14',
    'AVAXUSDT':'2020-09-22',
    'DOGEUSDT':'2020-07-10',
    'ARBUSDT': '2023-03-23',
    'INJUSDT': '2020-10-22',
    'WIFUSDT': '2024-01-26',
}

CADENCE_MS = 60_000


def get(url: str, params: dict | None = None, timeout: float = 20.0):
    if params:
        url = f'{url}?{urlencode(params)}'
    req = Request(url, headers={'User-Agent': 'p300-bot/1.0'})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ensure_table(con: sqlite3.Connection, table: str) -> None:
    """Mirror the btc_1m schema."""
    con.execute(
        f'CREATE TABLE IF NOT EXISTS {table} ('
        f'  open_time INTEGER PRIMARY KEY,'
        f'  open REAL, high REAL, low REAL, close REAL,'
        f'  volume REAL, num_trades INTEGER)'
    )


def latest_open_time(con: sqlite3.Connection, table: str) -> int:
    r = con.execute(f'SELECT MAX(open_time) FROM {table}').fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def fetch_one_alt_perp(symbol: str, since: str) -> int:
    """Backfill USDT-margined perp 1-minute klines for `symbol` into
    `{symbol_lower_minus_usdt}_perp_1m`. Idempotent.
    Returns rows inserted (approximate)."""
    table = f'{symbol.lower().replace("usdt","")}_perp_1m'
    con = sqlite3.connect(str(DB))
    ensure_table(con, table)
    try:
        last_ms = latest_open_time(con, table)
        floor_ms = int(datetime.fromisoformat(f'{since}T00:00:00+00:00').timestamp() * 1000)
        cursor = max(last_ms or 0, floor_ms)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        total = 0
        empty_streak = 0
        while cursor <= now_ms:
            params = {'symbol': symbol, 'interval': '1m',
                      'startTime': cursor, 'limit': 1000}
            try:
                rows = get(f'{FAPI}/klines', params)
            except Exception as e:
                print(f'  {symbol} fetch failed at {cursor}: {e}')
                break
            if not rows:
                empty_streak += 1
                if empty_streak >= 3:
                    break
                cursor += 1000 * CADENCE_MS  # skip ~16h ahead
                continue
            empty_streak = 0
            con.executemany(
                f'INSERT OR REPLACE INTO {table} '
                f'(open_time, open, high, low, close, volume, num_trades) '
                f'VALUES (?, ?, ?, ?, ?, ?, ?)',
                [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                  float(r[4]), float(r[5]), int(r[8])) for r in rows],
            )
            con.commit()
            total += len(rows)
            next_cursor = int(rows[-1][0]) + CADENCE_MS
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(rows) < 1000:
                # caught up; last partial bar will refresh next run
                break
            time.sleep(0.15)  # rate-limit politeness
        if total and total % 10_000:
            print(f'  {symbol}: +{total:,} rows')
        return total
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('symbols', nargs='*', default=list(ASSETS.keys()),
                    help='Override the default symbol list (e.g. OPUSDT SOLUSDT)')
    args = ap.parse_args()

    if not DB.exists():
        print(f'prod.db not found at {DB}', file=sys.stderr); return 2

    if 'PYTHONIOENCODING' not in sys.modules:
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    print(f'target db: {DB}')
    summary = {}
    for sym in args.symbols:
        since = ASSETS.get(sym, '2020-01-01')
        print(f'\n=== {sym} (perp launch {since}) ===')
        t0 = time.time()
        n = fetch_one_alt_perp(sym, since)
        dt = time.time() - t0
        summary[sym] = n
        print(f'{sym}: {n:,} rows in {dt:.1f}s')

    print('\n=== summary ===')
    for sym, n in summary.items():
        print(f'  {sym}: {n:,} rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
