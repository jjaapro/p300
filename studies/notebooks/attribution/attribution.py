#!/usr/bin/env python3
"""Reusable per-trade attribution: how much of a trade list's R is market
drift (beta) vs decision alpha?

For each trade: passive_r = hold the same symbol over the same window with the
same stop distance (stop hit -> -1R, else close-to-close move in R units).
alpha_r = actual_r - passive_r. A sleeve whose alpha ~ 0 is charging fees to
deliver beta. Born from Paladin H14; applied here to our own books.

    python attribution.py     # chento backward-only pools + live paper trades

Read-only everywhere.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PROD = os.path.join(ROOT, 'data', 'databases', 'prod.db')
BACKONLY = os.path.join(ROOT, 'studies', 'notebooks', 'overlay_study', 'results_backonly')

BARS_TABLE = {'BTCUSDT': 'cd_futures_15m', 'ETHUSDT': 'cd_futures_eth_15m'}
_cache: dict[str, pd.DataFrame] = {}


def bars(symbol: str) -> pd.DataFrame:
    if symbol not in _cache:
        con = sqlite3.connect(f'file:{PROD}?mode=ro', uri=True)
        try:
            df = pd.read_sql(f'SELECT timestamp, high, low, close FROM {BARS_TABLE[symbol]} '
                             'ORDER BY timestamp', con)
        finally:
            con.close()
        df.index = pd.to_datetime(df.pop('timestamp'), unit='s', utc=True)
        _cache[symbol] = df[~df.index.duplicated(keep='last')]
    return _cache[symbol]


def passive_r(symbol: str, start, end, entry: float, stop: float, sign: int) -> float | None:
    risk = abs(entry - stop)
    if not risk or pd.isna(start) or pd.isna(end):
        return None
    b = bars(symbol)
    w = b[(b.index >= start) & (b.index <= end)]
    if len(w) < 2:
        return None
    hit = (w['low'] <= stop) if sign > 0 else (w['high'] >= stop)
    if hit.any():
        return -1.0
    return float((w['close'].iloc[-1] - entry) * sign / risk)


RNG = np.random.default_rng(14)
HOLD_H = 72
N_RAND = 20


def _hold_r(symbol: str, ts, sign: int, risk_frac: float) -> float | None:
    """No-stop hold over HOLD_H hours, in R units of risk_frac x start price."""
    b = bars(symbol)
    ts = pd.Timestamp(ts)
    ts = (ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC'))
    ts = ts.floor('s').as_unit(b.index.unit)
    i0 = b.index.searchsorted(ts)
    if i0 >= len(b):
        return None
    j = min(b.index.searchsorted(ts + pd.Timedelta(hours=HOLD_H)), len(b) - 1)
    if j <= i0:
        return None
    p0, p1 = b['close'].iloc[i0], b['close'].iloc[j]
    return float((p1 - p0) / (risk_frac * p0)) * sign


def attribute(trades: pd.DataFrame, label: str) -> dict:
    """Three-way decomposition per trade:
       actual = regime beta (random-time hold) + timing alpha (same-time hold
       minus random) + exit alpha (actual minus same-time hold).
    trades: symbol, side_sign, entry, stop, actual_r, ts_entry."""
    rows = []
    for _, t in trades.iterrows():
        risk_frac = abs(t['entry'] - t['stop']) / t['entry']
        if not risk_frac or not np.isfinite(t['actual_r']):
            continue
        same = _hold_r(t['symbol'], t['ts_entry'], int(t['side_sign']), risk_frac)
        if same is None:
            continue
        b = bars(t['symbol'])
        rand_pos = RNG.integers(0, len(b) - HOLD_H * 4 - 2, N_RAND)
        rands = [r for p in rand_pos
                 if (r := _hold_r(t['symbol'], b.index[int(p)],
                                  int(t['side_sign']), risk_frac)) is not None]
        if not rands:
            continue
        regime = float(np.mean(rands))
        rows.append({'actual': t['actual_r'], 'regime': regime,
                     'timing': same - regime, 'exit': t['actual_r'] - same})
    d = pd.DataFrame(rows)
    if not len(d):
        print(f'{label}: no attributable trades')
        return {}
    print(f'{label:<38} n={len(d):>4}  actual {d.actual.mean():+6.2f}R = '
          f'regime {d.regime.mean():+6.2f} + timing {d.timing.mean():+6.2f} '
          f'+ exit {d.exit.mean():+6.2f}')
    return {'label': label, 'n': len(d), 'actual': d.actual.mean(),
            'regime': d.regime.mean(), 'timing': d.timing.mean(),
            'exit': d.exit.mean()}


def _walk(symbol: str, ts, entry: float, stop: float, target: float,
          sign: int) -> tuple[float, pd.Timestamp] | None:
    """Re-replay one trade with the study-consistent engine (stop-first,
    fixed target, TIF 72h, gross) and return (r, exit_time)."""
    b = bars(symbol)
    w = b[(b.index > ts) & (b.index <= ts + pd.Timedelta(hours=72))]
    if len(w) < 2:
        return None
    for t_, row in w.iterrows():
        if (row['low'] <= stop) if sign > 0 else (row['high'] >= stop):
            return -1.0, t_
        if (row['high'] >= target) if sign > 0 else (row['low'] <= target):
            return float((target - entry) * sign / abs(entry - stop)), t_
    return float((w['close'].iloc[-1] - entry) * sign / abs(entry - stop)), w.index[-1]


def chento_backonly() -> None:
    for asset, sym in (('BTC', 'BTCUSDT'), ('ETH', 'ETHUSDT')):
        t = pd.read_csv(os.path.join(BACKONLY, f'trades_{asset}.csv'), parse_dates=['ts'])
        aligned = (((t.direction == 'long') & (t.okx_delta_z >= 0))
                   | ((t.direction == 'short') & (t.okx_delta_z <= 0)))
        t = t[aligned].copy()
        recs = []
        for _, r in t.iterrows():
            sign = 1 if r.direction == 'long' else -1
            res = _walk(sym, r.ts, r.entry, r.stop, r.target, sign)
            if res is None:
                continue
            actual, ts_exit = res
            recs.append({'symbol': sym, 'side_sign': sign, 'entry': r.entry,
                         'stop': r.stop, 'ts_entry': r.ts, 'ts_exit': ts_exit,
                         'actual_r': actual})
        attribute(pd.DataFrame(recs), f'chento_backonly_{asset} (72h engine)')


def live_paper() -> None:
    import re
    con = sqlite3.connect(f'file:{PROD}?mode=ro', uri=True)
    try:
        rows = con.execute(
            "SELECT t.strategy, t.direction, t.entry_time, t.entry_price, "
            "t.exit_time, t.exit_price, t.status, t.notes FROM trades t "
            "WHERE t.execution_mode='paper' AND t.strategy_variant LIKE 'bot_%' "
            "AND t.entry_time >= '2026-07-21'").fetchall()
    finally:
        con.close()
    recs = []
    now = pd.Timestamp.now(tz='UTC')
    for strat, direction, et, ep, xt, xp, status, notes in rows:
        try:
            meta = json.loads(notes) if notes else {}
        except (TypeError, json.JSONDecodeError):
            m = re.search(r'"_stop_price":\s*([0-9.eE+-]+)', notes or '')
            meta = {'_stop_price': float(m.group(1))} if m else {}
        stop = meta.get('_stop_price')
        if not stop or not ep:
            continue
        sign = 1 if str(direction).upper() == 'LONG' else -1
        ts_entry = pd.Timestamp(et).tz_convert('UTC')
        risk = abs(ep - stop)
        b = bars('BTCUSDT')
        if status == 'open' or xp is None or pd.Timestamp(xt).year >= 2099:
            ts_exit = now
            w = b[b.index <= now]
            xpx = float(w['close'].iloc[-1])
        else:
            ts_exit = pd.Timestamp(xt).tz_convert('UTC')
            xpx = xp
        recs.append({'strategy': strat, 'symbol': 'BTCUSDT', 'side_sign': sign,
                     'entry': ep, 'stop': stop, 'ts_entry': ts_entry,
                     'ts_exit': ts_exit,
                     'actual_r': (xpx - ep) * sign / risk if risk else np.nan})
    t = pd.DataFrame(recs)
    if not len(t):
        print('live paper: no attributable trades (missing stops in notes?)')
        return
    print(f'\nlive paper fleet since 2026-07-21 ({len(t)} trades incl. open @ mark):')
    attribute(t, 'fleet_all')
    for strat, g in t.groupby('strategy'):
        attribute(g, f'  {strat}')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    print('=== chento Triple, backward-only pools (base exits) ===')
    chento_backonly()
    live_paper()
