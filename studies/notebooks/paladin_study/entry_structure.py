#!/usr/bin/env python3
"""Do Paladin's entries sit on structure his words never name?

The corpus contains zero mentions of fibonacci, volume profile, or liquidity
(the method analysis verified the absence) — but he could be reading them
implicitly. Tested here, entries vs the same matched controls used in
entry_context.py (same symbol, same time-of-day, random dates):

  VP   volume-profile position — trailing-14d price-volume histogram (50 bins);
       distance from entry to the nearest high-volume node (top-5 bins) and
       nearest low-volume node (bottom-5 in-range), % of price
  FIB  distance to the nearest retracement level (0.382/0.5/0.618/0.786) of
       the trailing-14d swing range, normalized by the range
  LIQ  liquidity-shelf proxy — touch density: share of trailing-14d bar lows
       (longs; highs for shorts) within ±0.10% of the entry price

All features use bars strictly before the timestamp. Outcome split included:
if a structure predicts his wins, it matters even if selection rates match.

Writes results/entry_structure.csv (+ _controls) and prints the comparison.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')
LOOKBACK = pd.Timedelta(days=14)
FIBS = (0.382, 0.5, 0.618, 0.786)


def features_at(df: pd.DataFrame, ts: pd.Timestamp, price: float,
                side_sign: int) -> dict | None:
    w = df[(df.index >= ts - LOOKBACK) & (df.index + pd.Timedelta('15min') <= ts)]
    if len(w) < 600 or not np.isfinite(price) or price <= 0:
        return None
    lo, hi = w['low'].min(), w['high'].max()
    if hi <= lo:
        return None

    # VP: volume-weighted histogram over typical price
    typ = (w['high'] + w['low'] + w['close']) / 3
    bins = np.linspace(lo, hi, 51)
    hist, edges = np.histogram(typ, bins=bins, weights=w['volume'])
    centers = (edges[:-1] + edges[1:]) / 2
    order = np.argsort(hist)
    hvn = centers[order[-5:]]
    inrange = hist > 0
    lvn = centers[inrange][np.argsort(hist[inrange])[:5]] if inrange.sum() >= 5 else hvn
    f = {'dist_hvn_pct': float(np.abs(hvn - price).min() / price * 100),
         'dist_lvn_pct': float(np.abs(lvn - price).min() / price * 100)}

    # FIB: retracements of the 14d swing (direction from where the extreme is)
    rng = hi - lo
    fib_dn = [hi - f_ * rng for f_ in FIBS]   # retracement down from high
    fib_up = [lo + f_ * rng for f_ in FIBS]   # retracement up from low
    all_fibs = np.array(fib_dn + fib_up)
    f['dist_fib_norm'] = float(np.abs(all_fibs - price).min() / rng)
    f['dist_fib_pct'] = float(np.abs(all_fibs - price).min() / price * 100)

    # LIQ: touch density of same-side extremes near the price
    ref = w['low'] if side_sign > 0 else w['high']
    f['touch_density_pct'] = float((np.abs(ref - price) / price <= 0.001).mean() * 100)
    return f


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pos = load_pack()['positions']
    bt = pos[pos.is_backtestable].copy()
    ctrl = pd.read_csv(os.path.join(OUT, 'control_features.csv'),
                       parse_dates=['control_time_utc'])

    cache: dict[str, pd.DataFrame | None] = {}
    def bars(sym):
        if sym not in cache:
            try:
                cache[sym] = load_ohlcv(sym, '2026-03-01', '2026-08-22', '15m')
            except Exception:
                cache[sym] = None
        return cache[sym]

    his_rows = []
    for _, r in bt.iterrows():
        df = bars(r['symbol'])
        if df is None:
            continue
        f = features_at(df, r['signal_time_utc'], r['entry_ref'], int(r['side_sign']))
        if f:
            his_rows.append({'position_id': r['position_id'], 'symbol': r['symbol'],
                             'side_sign': int(r['side_sign']), 'outcome': r['outcome']} | f)
    his = pd.DataFrame(his_rows)
    his.to_csv(os.path.join(OUT, 'entry_structure.csv'), index=False)

    ctl_rows = []
    for _, r in ctrl.iterrows():
        df = bars(r['symbol'])
        if df is None:
            continue
        ts = r['control_time_utc']
        w = df[df.index + pd.Timedelta('15min') <= ts]
        if not len(w):
            continue
        price = float(w['close'].iloc[-1])
        f = features_at(df, ts, price, int(r['side_sign']))
        if f:
            ctl_rows.append({'symbol': r['symbol'], 'side_sign': int(r['side_sign'])} | f)
    ctl = pd.DataFrame(ctl_rows)
    ctl.to_csv(os.path.join(OUT, 'entry_structure_controls.csv'), index=False)

    print(f'his n={len(his)}   controls n={len(ctl)}\n')
    print(f'{"feature":<20} {"his med":>9} {"ctrl med":>9}   {"his mean":>9} {"ctrl mean":>9}')
    for c in ('dist_hvn_pct', 'dist_lvn_pct', 'dist_fib_norm', 'touch_density_pct'):
        print(f'{c:<20} {his[c].median():9.3f} {ctl[c].median():9.3f}   '
              f'{his[c].mean():9.3f} {ctl[c].mean():9.3f}')
    res = his[his.outcome.isin(['win', 'loss'])]
    print('\noutcome split (his trades, win vs loss medians):')
    for c in ('dist_hvn_pct', 'dist_lvn_pct', 'dist_fib_norm', 'touch_density_pct'):
        wm = res[res.outcome == 'win'][c].median()
        lm = res[res.outcome == 'loss'][c].median()
        print(f'  {c:<20} win {wm:7.3f}   loss {lm:7.3f}')


if __name__ == '__main__':
    main()
