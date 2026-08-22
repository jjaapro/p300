#!/usr/bin/env python3
"""Backfill Binance OHLCV for every symbol Paladin traded into a study-local DB.

Study-local by design: prod.db is live (bots read it) and its screener klines
stopped 2026-05-24. This DB is throwaway research data, never read by anything
in production.

    python fetch_ohlcv.py          # fetches 15m USDT-M futures klines, 2026-03-01 -> now

Writes paladin_ohlcv.db, table klines_15m(symbol, open_time_ms, open, high, low,
close, volume, quote_volume, n_trades) with a (symbol, open_time_ms) PK.
RAYUSDT is not on Binance futures; it is fetched from spot instead and flagged
in the source column.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, '..', '..', 'material', 'paladin', 'analysis')
DB = os.path.join(HERE, 'paladin_ohlcv.db')

FUT = 'https://fapi.binance.com/fapi/v1/klines'
SPOT = 'https://api.binance.com/api/v3/klines'
START = pd.Timestamp('2026-03-01', tz='UTC')   # lookback for 4h EMA200 / ATR warmup
INTERVAL = '15m'
STEP_MS = 15 * 60 * 1000


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS klines_15m (
        symbol TEXT NOT NULL, open_time_ms INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, quote_volume REAL, n_trades INTEGER,
        source TEXT NOT NULL,
        PRIMARY KEY (symbol, open_time_ms))""")
    con.commit()


def fetch_symbol(con: sqlite3.Connection, symbol: str, url: str, source: str) -> int:
    end_ms = int(time.time() * 1000)
    cur = con.execute('SELECT MAX(open_time_ms) FROM klines_15m WHERE symbol=?', (symbol,))
    row = cur.fetchone()
    start_ms = (row[0] + STEP_MS) if row and row[0] else int(START.timestamp() * 1000)
    n = 0
    while start_ms < end_ms:
        r = requests.get(url, params={'symbol': symbol, 'interval': INTERVAL,
                                      'startTime': start_ms, 'limit': 1500}, timeout=30)
        if r.status_code == 429:
            time.sleep(30)
            continue
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        con.executemany(
            'INSERT OR IGNORE INTO klines_15m VALUES (?,?,?,?,?,?,?,?,?,?)',
            [(symbol, k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]),
              float(k[5]), float(k[7]), int(k[8]), source) for k in rows])
        con.commit()
        n += len(rows)
        start_ms = rows[-1][0] + STEP_MS
        time.sleep(0.15)   # ~7 req/s, far under Binance weight limits
    return n


def main() -> int:
    pos = pd.read_csv(os.path.join(PACK, 'positions.csv'))
    symbols = sorted(set(pos[pos.asset_class == 'crypto'].symbol))
    info = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=30).json()
    on_fut = {s['symbol'] for s in info['symbols'] if s['status'] == 'TRADING'}

    con = sqlite3.connect(DB)
    ensure_schema(con)
    missing = []
    for i, sym in enumerate(symbols, 1):
        if sym in on_fut:
            n = fetch_symbol(con, sym, FUT, 'binance_futures')
        else:
            try:
                n = fetch_symbol(con, sym, SPOT, 'binance_spot')
            except requests.HTTPError:
                missing.append(sym)
                print(f'[{i}/{len(symbols)}] {sym}: NOT ON BINANCE', flush=True)
                continue
        print(f'[{i}/{len(symbols)}] {sym}: +{n} bars', flush=True)
    got = con.execute('SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines_15m').fetchone()
    print(f'done: {got[0]} symbols, {got[1]} bars, missing entirely: {missing}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
