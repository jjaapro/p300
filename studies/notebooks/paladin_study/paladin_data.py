#!/usr/bin/env python3
"""Data layer for the Paladin study.

Implements the load_ohlcv() contract from the analysis pack's load.py:
returns a DataFrame indexed by tz-aware UTC DatetimeIndex with columns
open, high, low, close, volume, bars fully inside [start, end], ascending.

Sources (all read-only):
  - paladin_ohlcv.db  klines_15m  — Binance USDT-M futures (RAY = spot),
    every symbol he traded, 2026-03-01 -> fetch time. Base source.
  - prod.db btc_1m / eth_1m       — 1m precision for BTC/ETH excursion work
    (interval='1m' only, those two symbols only).

15m is the base interval; 1h/4h/1d are resampled from it, so a "4h close"
here is the UTC-aligned 00/04/08/12/16/20 close, matching how exchanges draw it.
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY_DB = os.path.join(HERE, 'paladin_ohlcv.db')
PROD_DB = os.path.join(HERE, '..', '..', '..', 'data', 'databases', 'prod.db')
PACK = os.path.join(HERE, '..', '..', 'material', 'paladin', 'analysis')

_RESAMPLE = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
_MIN_1M = {'BTCUSDT': 'btc_1m', 'ETHUSDT': 'eth_1m'}


def _to_utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def load_ohlcv(symbol: str, start, end, interval: str = '1h') -> pd.DataFrame:
    start, end = _to_utc(start), _to_utc(end)
    if interval == '1m':
        if symbol not in _MIN_1M:
            raise ValueError(f'1m only available for {sorted(_MIN_1M)}, not {symbol}')
        con = sqlite3.connect(PROD_DB)
        try:
            df = pd.read_sql(
                f'SELECT open_time, open, high, low, close, volume FROM {_MIN_1M[symbol]} '
                'WHERE open_time >= ? AND open_time < ? ORDER BY open_time',
                con, params=(int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
        finally:
            con.close()
        df.index = pd.to_datetime(df.pop('open_time'), unit='ms', utc=True)
        return df

    con = sqlite3.connect(STUDY_DB)
    try:
        df = pd.read_sql(
            'SELECT open_time_ms, open, high, low, close, volume FROM klines_15m '
            'WHERE symbol = ? AND open_time_ms >= ? AND open_time_ms < ? ORDER BY open_time_ms',
            con, params=(symbol, int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
    finally:
        con.close()
    df.index = pd.to_datetime(df.pop('open_time_ms'), unit='ms', utc=True)
    if interval == '15m':
        return df
    rule = {'1h': '1h', '4h': '4h', '1d': '1D'}[interval]
    out = df.resample(rule, label='left', closed='left').agg(_RESAMPLE).dropna(subset=['open'])
    return out


def load_pack(fmt: str = 'csv') -> dict[str, pd.DataFrame]:
    """The analysis pack, with *_utc columns parsed. Thin wrapper so notebooks
    don't need sys.path tricks to reach the pack's own load.py."""
    names = ['positions', 'actions', 'events', 'price_observations', 'unresolved_positions']
    out = {}
    for n in names:
        path = os.path.join(PACK, f'{n}.{fmt}')
        df = pd.read_parquet(path) if fmt == 'parquet' else pd.read_csv(path)
        for col in df.columns:
            if col.endswith('_utc'):
                df[col] = pd.to_datetime(df[col], utc=True, errors='coerce')
        out[n] = df
    return out


if __name__ == '__main__':
    bars = load_ohlcv('BTCUSDT', '2026-08-10', '2026-08-18', '4h')
    print(bars.head())
    print(f'{len(bars)} bars, {bars.index.min()} -> {bars.index.max()}')
