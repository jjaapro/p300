#!/usr/bin/env python3
"""paladin_harvester — the mechanical extract of what tested positive in the
Paladin studies: enter on a round-level bounce with a wide ATR stop, book at
the next level's rejection wick, never let a fixed TP cap the exit.

PRE-REGISTERED (2026-08-23, before any run):
  Entry (long):  15m bar low within TOL of a round 1000-level from above,
                 close back above the level, close > open. Enter next bar open.
  Entry (short): mirror at a level from below.
  Stop:          k x ATR14(1h), k in {4, 5}  (his median stop ~4%)
  Exit:          rejection-wick booking (armed >= 0.3R, shadow >= 0.4 x range,
                 at any round level / no fixed TP), else TIF 72h close.
  Gates:         none | up30d trend gate (longs only when BTC 30d ret > 0,
                 shorts only when < 0) — 2 variants, both sides tested.
  Cooldown:      24h per side. Costs 18bp round trip, in R per trade's risk.
  Split:         IS <= 2024-12-31 < OOS. KILL: net exp < +0.05R or MAR < 2.

Data: prod cd_futures_15m (read-only), 2020-01 -> now.
Writes results/harvester_summary.csv + per-variant trades.
"""
from __future__ import annotations

import itertools
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DB = os.path.join(ROOT, 'data', 'databases', 'prod.db')
OUT = os.path.join(HERE, 'results')

TOL = 0.0015
WICK_FRAC = 0.4
ARM_R = 0.3
TIF_BARS = 72 * 4
COOLDOWN = 96
COST = 0.0018            # round trip, price fraction
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def load_btc() -> pd.DataFrame:
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        df = pd.read_sql("SELECT timestamp, open, high, low, close FROM cd_futures_15m "
                         "WHERE timestamp >= 1577836800 ORDER BY timestamp", con)
    finally:
        con.close()
    df.index = pd.to_datetime(df.pop('timestamp'), unit='s', utc=True)
    return df[~df.index.duplicated(keep='last')]


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    h1c = df['close'].resample('1h', label='left', closed='left').last().dropna()
    h1h = df['high'].resample('1h', label='left', closed='left').max().dropna()
    h1l = df['low'].resample('1h', label='left', closed='left').min().dropna()
    tr = pd.concat([h1h - h1l, (h1h - h1c.shift()).abs(),
                    (h1l - h1c.shift()).abs()], axis=1).max(axis=1)
    df = df.copy()
    df['atr1h'] = tr.ewm(alpha=1 / 14, adjust=False).mean().shift(1).reindex(
        df.index, method='ffill')
    df['ret30d'] = df['close'].pct_change(30 * 96)
    return df


def detect(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Round-1000 bounce events. Returns (long_idx, short_idx)."""
    lvl_lo = (df['low'] / 1000).round() * 1000        # nearest level to the low
    long_ev = ((np.abs(df['low'] - lvl_lo) <= TOL * df['close'])
               & (df['close'] > lvl_lo) & (df['close'] > df['open']))
    lvl_hi = (df['high'] / 1000).round() * 1000
    short_ev = ((np.abs(df['high'] - lvl_hi) <= TOL * df['close'])
                & (df['close'] < lvl_hi) & (df['close'] < df['open']))
    return np.flatnonzero(long_ev.to_numpy()), np.flatnonzero(short_ev.to_numpy())


def replay(o, h, lo, c, i: int, sign: int, risk: float) -> tuple[float, str, int]:
    entry = o[i + 1]
    stop = entry - sign * risk
    end = min(i + 1 + TIF_BARS, len(o) - 1)
    for j in range(i + 1, end + 1):
        if (lo[j] <= stop) if sign > 0 else (h[j] >= stop):
            return -1.0, 'stop', j
        ext = h[j] if sign > 0 else lo[j]
        unreal = (ext - entry) * sign / risk
        rng = h[j] - lo[j]
        if unreal >= ARM_R and rng > 0:
            lvl = round(ext / 1000) * 1000
            near = abs(ext - lvl) <= TOL * entry
            through = ((ext - lvl) * sign >= 0) and ((c[j] - lvl) * sign < 0)
            shadow = (h[j] - max(o[j], c[j])) if sign > 0 else (min(o[j], c[j]) - lo[j])
            if (near or through) and (lvl - entry) * sign > 0 and shadow >= WICK_FRAC * rng:
                return (c[j] - entry) * sign / risk, 'wick', j
    return (c[end] - entry) * sign / risk, 'timeout', end


def run_variant(df, longs, shorts, k: float, gate: bool, sides: str) -> pd.DataFrame:
    o, h, lo, c = (df[x].to_numpy() for x in ('open', 'high', 'low', 'close'))
    atr, r30 = df['atr1h'].to_numpy(), df['ret30d'].to_numpy()
    idx = df.index
    trades = []
    next_ok = {1: -1, -1: -1}
    events = sorted([(i, 1) for i in longs] + [(i, -1) for i in shorts])
    for i, sign in events:
        if sides == 'long' and sign < 0:
            continue
        if sides == 'short' and sign > 0:
            continue
        if i <= next_ok[sign] or i + 2 >= len(o) or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if gate and np.isfinite(r30[i]) and ((sign > 0) != (r30[i] > 0)):
            continue
        risk = k * atr[i]
        gross, reason, j = replay(o, h, lo, c, i, sign, risk)
        entry = o[i + 1]
        cost_r = COST * entry / risk
        trades.append({'time': idx[i + 1], 'side': sign, 'gross_r': gross,
                       'net_r': gross - cost_r, 'exit': reason,
                       'risk_pct': risk / entry * 100, 'hold_bars': j - i})
        next_ok[sign] = i + COOLDOWN
    return pd.DataFrame(trades)


def summarize(t: pd.DataFrame, label: str, split: str) -> dict:
    d = {'variant': label, 'split': split, 'n': len(t)}
    if not len(t):
        return d
    r = t['net_r']
    eq = r.cumsum()
    dd = float((eq - eq.cummax()).min())
    days = max(1.0, (t['time'].max() - t['time'].min()).days)
    d.update({'net_exp_r': r.mean(), 'gross_exp_r': t['gross_r'].mean(),
              'wr_pct': (r > 0).mean() * 100, 'total_r': r.sum(), 'max_dd_r': dd,
              'mar_like': r.sum() / abs(dd) if dd < 0 else np.inf,
              'trades_per_wk': len(t) / days * 7,
              'wick_share': (t['exit'] == 'wick').mean(),
              'med_risk_pct': t['risk_pct'].median()})
    return d


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    df = add_features(load_btc())
    longs, shorts = detect(df)
    print(f'{len(df):,} bars  {df.index.min():%Y-%m} -> {df.index.max():%Y-%m}  '
          f'raw events: {len(longs)} long / {len(shorts)} short')

    rows = []
    for k, gate, sides in itertools.product((4.0, 5.0), (False, True),
                                            ('both', 'long', 'short')):
        label = f'k{k:.0f}_{"gated" if gate else "open"}_{sides}'
        t = run_variant(df, longs, shorts, k, gate, sides)
        t.to_csv(os.path.join(OUT, f'trades_{label}.csv'), index=False)
        for split, part in (('IS', t[t.time <= IS_END]), ('OOS', t[t.time > IS_END]),
                            ('ALL', t)):
            rows.append(summarize(part, label, split))
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, 'harvester_summary.csv'), index=False)
    cols = ['variant', 'split', 'n', 'net_exp_r', 'gross_exp_r', 'wr_pct', 'total_r',
            'max_dd_r', 'mar_like', 'trades_per_wk', 'wick_share', 'med_risk_pct']
    for split in ('IS', 'OOS'):
        sub = res[(res.split == split) & (res.n > 30)].sort_values('net_exp_r',
                                                                   ascending=False)
        print(f'\n=== {split} ===')
        print(sub[cols].round(3).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
