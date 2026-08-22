#!/usr/bin/env python3
"""H12 + H11 + the H3 stop-quality check.

H12  Resolve the 35 positions that vanish from the channel: scan forward from
     signal for the first touch of stop or TP1. Turns the 77% reconstructed
     win rate into a measured one.
H11  For his manual closes: how much MFE was left after his exit? If price kept
     running, a trailing rule beats him; if it collapsed, his exits are the edge.
H3b  For backtestable losses: did a 4h candle actually CLOSE beyond the stop,
     or only wick through it? A high wick-only share means the stop he traded
     is wider than the stop he published.

Writes results/unresolved_resolved.csv, results/manual_exit_mfe.csv,
results/loss_wick_analysis.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, '..', '..', 'material', 'paladin', 'analysis')
sys.path.insert(0, PACK)
from load import replay_plan  # noqa: E402

from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')


def h12_unresolved(d: dict) -> None:
    un = d['unresolved_positions']
    rows = []
    for _, r in un.iterrows():
        entry, stop = r.get('entry_ref'), r.get('planned_stop')
        tp = r.get('planned_tp1')
        sign = {'long': 1, 'short': -1}.get(str(r.get('side')).lower())
        ts = r['signal_time_utc']
        if pd.isna(entry) or pd.isna(stop) or sign is None or pd.isna(ts):
            rows.append({'position_id': r['position_id'], 'resolution': 'unresolvable_missing_fields'})
            continue
        if abs(entry - stop) / entry > 0.25:   # decimal-typo stops (e.g. 0.0525 vs 0.606)
            rows.append({'position_id': r['position_id'], 'resolution': 'stop_implausible'})
            continue
        try:
            bars = load_ohlcv(r['symbol'], ts, ts + pd.Timedelta(hours=700), '15m')
        except Exception as exc:  # noqa: BLE001
            rows.append({'position_id': r['position_id'], 'resolution': f'no_data: {exc}'})
            continue
        if len(bars) < 2:
            rows.append({'position_id': r['position_id'], 'resolution': 'no_bars'})
            continue
        res = replay_plan(bars, entry, stop, tp if pd.notna(tp) else None, sign)
        rows.append({'position_id': r['position_id'], 'symbol': r['symbol'], 'side': r.get('side'),
                     'resolution': res.get('exit_reason'), 'r': res.get('r'),
                     'exit_time_utc': res.get('exit_time_utc')})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'unresolved_resolved.csv'), index=False)
    res = df[df.resolution.isin(['stop', 'target', 'timeout'])]
    print(f'H12: resolved {len(res)} of {len(df)} vanished positions')
    print(res.resolution.value_counts().to_string())
    if len(res):
        print(f"  mean R of the vanished: {res.r.mean():+.2f}   "
              f"wins {(res.r > 0).sum()}  losses {(res.r < 0).sum()}")


def h11_manual_exits(d: dict) -> None:
    pos = d['positions']
    man = pos[pos.is_backtestable & (pos.exit_type == 'manual_close')
              & pos.exit_time_utc.notna() & pos.exit_price.notna()].copy()
    rows = []
    for _, r in man.iterrows():
        risk = abs(r['entry_ref'] - r['planned_stop'])
        if not risk:
            continue
        sign = int(r['side_sign'])
        try:
            after = load_ohlcv(r['symbol'], r['exit_time_utc'],
                               r['exit_time_utc'] + pd.Timedelta(hours=72), '15m')
        except Exception:  # noqa: BLE001
            continue
        if len(after) < 2:
            continue
        # from his exit forward: how far did it keep going in his direction,
        # and would the published stop have been hit first?
        fav = ((after['high'] - r['exit_price']) if sign > 0
               else (r['exit_price'] - after['low'])) / risk
        hit_stop = ((after['low'] <= r['planned_stop']) if sign > 0
                    else (after['high'] >= r['planned_stop']))
        first_stop = after.index[hit_stop][0] if hit_stop.any() else None
        fav_before_stop = fav[:first_stop] if first_stop is not None else fav
        rows.append({'position_id': r['position_id'], 'symbol': r['symbol'], 'side': r['side'],
                     'his_r': r['r_from_prices'],
                     'mfe_after_exit_r_72h': float(fav.max()),
                     'mfe_after_exit_r_before_stop': float(fav_before_stop.max()) if len(fav_before_stop) else np.nan,
                     'stop_hit_after_exit': first_stop is not None})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'manual_exit_mfe.csv'), index=False)
    print(f'\nH11: {len(df)} manual closes with post-exit data')
    print(f"  median extra MFE within 72h after his exit: {df.mfe_after_exit_r_72h.median():+.2f}R")
    print(f"  median extra MFE before the stop would hit: {df.mfe_after_exit_r_before_stop.median():+.2f}R")
    print(f"  share where published stop was hit within 72h after exit: {df.stop_hit_after_exit.mean():.2f}")
    print(f"  share leaving >0.5R on the table (pre-stop): "
          f"{(df.mfe_after_exit_r_before_stop > 0.5).mean():.2f}")


def h3b_loss_wicks(d: dict) -> None:
    pos = d['positions']
    losses = pos[pos.is_backtestable & (pos.outcome == 'loss')].copy()
    rows = []
    for _, r in losses.iterrows():
        sign = int(r['side_sign'])
        stop = r['planned_stop']
        start = r['signal_time_utc']
        end = r['exit_time_utc'] if pd.notna(r['exit_time_utc']) else start + pd.Timedelta(hours=336)
        try:
            h4 = load_ohlcv(r['symbol'], start, end + pd.Timedelta(hours=4), '4h')
        except Exception:  # noqa: BLE001
            continue
        if not len(h4):
            continue
        wicked = ((h4['low'] <= stop) if sign > 0 else (h4['high'] >= stop)).any()
        closed_beyond = ((h4['close'] < stop) if sign > 0 else (h4['close'] > stop)).any()
        rows.append({'position_id': r['position_id'], 'symbol': r['symbol'], 'side': r['side'],
                     'wicked_through_stop': bool(wicked),
                     'closed_4h_beyond_stop': bool(closed_beyond)})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'loss_wick_analysis.csv'), index=False)
    touched = df[df.wicked_through_stop]
    print(f'\nH3b: {len(df)} backtestable losses with data; {len(touched)} touched the stop')
    if len(touched):
        print(f"  4h-closed beyond stop: {touched.closed_4h_beyond_stop.sum()}  "
              f"wick-only: {(~touched.closed_4h_beyond_stop).sum()}")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    d = load_pack()
    h12_unresolved(d)
    h11_manual_exits(d)
    h3b_loss_wicks(d)


if __name__ == '__main__':
    main()
