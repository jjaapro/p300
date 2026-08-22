#!/usr/bin/env python3
"""Backfill Binance USDT-M futures 15m klines for the scanner-study universe
into a study-local DB (scanner_ohlcv.db). Research data only — prod untouched.

Universe = the 150-symbol screener_universe snapshot (prod, frozen 2026-05-23,
read-only) ∪ the 68 Paladin-study symbols. Window: 2024-01-01 -> now, so the
study sees the 2024 chop, the 2025 trend, and the 2026 drawdown+recovery
rather than one recovering market.

Resumable: reruns continue from each symbol's last stored bar.
Symbols not on Binance futures are recorded in missing_symbols.json.

    python fetch_universe.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PROD_DB = os.path.join(REPO, 'data', 'databases', 'prod.db')
PALADIN_DB = os.path.join(HERE, '..', 'paladin_study', 'paladin_ohlcv.db')
DB = os.path.join(HERE, 'scanner_ohlcv.db')

FUT = 'https://fapi.binance.com/fapi/v1/klines'
START_MS = 1704067200000            # 2024-01-01 UTC
STEP_MS = 15 * 60 * 1000


def universe() -> list[str]:
    syms: set[str] = set()
    con = sqlite3.connect(f'file:{PROD_DB}?mode=ro', uri=True)
    try:
        syms |= {r[0] for r in con.execute('SELECT asset FROM screener_universe')}
    finally:
        con.close()
    con = sqlite3.connect(f'file:{PALADIN_DB}?mode=ro', uri=True)
    try:
        syms |= {r[0] for r in con.execute('SELECT DISTINCT symbol FROM klines_15m')}
    finally:
        con.close()
    return sorted(syms)


def main() -> int:
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS klines_15m (
        symbol TEXT NOT NULL, open_time_ms INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, quote_volume REAL, n_trades INTEGER,
        PRIMARY KEY (symbol, open_time_ms))""")
    con.commit()

    info = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=30).json()
    on_fut = {s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING'}
    syms = universe()
    missing = [s for s in syms if s not in on_fut]
    todo = [s for s in syms if s in on_fut]
    with open(os.path.join(HERE, 'missing_symbols.json'), 'w') as f:
        json.dump({'missing_from_binance_futures': missing}, f, indent=1)
    print(f'{len(todo)} symbols to fetch, {len(missing)} not on Binance futures', flush=True)

    for i, sym in enumerate(todo, 1):
        row = con.execute('SELECT MAX(open_time_ms) FROM klines_15m WHERE symbol=?',
                          (sym,)).fetchone()
        start_ms = (row[0] + STEP_MS) if row and row[0] else START_MS
        end_ms = int(time.time() * 1000)
        n = 0
        while start_ms < end_ms:
            r = requests.get(FUT, params={'symbol': sym, 'interval': '15m',
                                          'startTime': start_ms, 'limit': 1500},
                             timeout=30)
            if r.status_code in (418, 429):
                time.sleep(60)
                continue
            r.raise_for_status()
            rows = r.json()
            if not rows:
                break
            con.executemany(
                'INSERT OR IGNORE INTO klines_15m VALUES (?,?,?,?,?,?,?,?,?)',
                [(sym, k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                  float(k[5]), float(k[7]), int(k[8])) for k in rows])
            con.commit()
            n += len(rows)
            start_ms = rows[-1][0] + STEP_MS
            time.sleep(0.25)   # ~4 req/s, well under fapi weight limits
        print(f'[{i}/{len(todo)}] {sym}: +{n} bars', flush=True)

    tot = con.execute('SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines_15m').fetchone()
    print(f'done: {tot[0]} symbols, {tot[1]} bars')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
