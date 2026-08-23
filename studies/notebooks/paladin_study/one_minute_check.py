#!/usr/bin/env python3
"""Does 1-minute granularity change the Paladin conclusions? (BTC/ETH subset,
77 backtestable positions — alts have no 1m history on our side.)

  A  H0 replay at 1m vs 15m: entry at signal, published stop/TP1, 72h cap.
     If stop/target touch ORDER at 1m flips outcomes, 15m replays were wrong.
  B  Wick-exit ladder at 1m vs 5m vs 15m (same pre-registered rule:
     armed >=0.3R, wick >=0.4 range at round level/TP, exit at close).
  C  Entry microstructure: in the 5 minutes before his signal, is there a 1m
     sweep-and-reclaim (bar low takes out the prior 30m low, closes back
     above) more often than at matched control times? The 1m version of
     "he watches the tape for the flush".

Read-only; prints verdicts, writes results/one_minute_check.csv.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exit_wick_study import replay_with_wick_exit, round_levels, TOL  # noqa: E402
from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')
CAP_H = 72
AGG = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
       'volume': 'sum'}


def replay_plan(bars, entry, stop, tp, sign):
    for ts, b in bars.iterrows():
        if (b['low'] <= stop) if sign > 0 else (b['high'] >= stop):
            return -1.0, 'stop'
        if tp is not None and np.isfinite(tp) and (
                (b['high'] >= tp) if sign > 0 else (b['low'] <= tp)):
            return (tp - entry) * sign / abs(entry - stop), 'target'
    return (bars.iloc[-1]['close'] - entry) * sign / abs(entry - stop), 'timeout'


def main():
    pos = load_pack()['positions']
    bt = pos[pos.is_backtestable & pos.symbol.isin(['BTCUSDT', 'ETHUSDT'])]
    print(f'{len(bt)} BTC/ETH backtestable positions\n')

    rows = []
    for _, r in bt.iterrows():
        try:
            m1 = load_ohlcv(r['symbol'], r['signal_time_utc'],
                            r['signal_time_utc'] + pd.Timedelta(hours=CAP_H), '1m')
        except Exception:
            continue
        if len(m1) < 120:
            continue
        frames = {'1m': m1,
                  '5m': m1.resample('5min', label='left', closed='left').agg(AGG).dropna(subset=['open']),
                  '15m': m1.resample('15min', label='left', closed='left').agg(AGG).dropna(subset=['open'])}
        sign, entry = int(r['side_sign']), r['entry_ref']
        stop = r['planned_stop']
        tp = r['planned_tp1'] if pd.notna(r['planned_tp1']) else None
        rec = {'position_id': r['position_id']}
        for tf, bars in frames.items():
            rec[f'plan_r_{tf}'], rec[f'plan_end_{tf}'] = replay_plan(bars, entry, stop, tp, sign)
            w = replay_with_wick_exit(bars, entry, stop, tp, sign, 0.3, 0.4)
            rec[f'wick_r_{tf}'] = w.get('r', np.nan)
            rec[f'wick_end_{tf}'] = w.get('exit_reason', '?')
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'one_minute_check.csv'), index=False)

    print('A) H0 plan replay by granularity:')
    for tf in ('1m', '5m', '15m'):
        print(f'   {tf:>3}: mean {df[f"plan_r_{tf}"].mean():+.3f}R   '
              f'stops {(df[f"plan_end_{tf}"]=="stop").sum()}  '
              f'targets {(df[f"plan_end_{tf}"]=="target").sum()}')
    flip = (df.plan_end_1m != df.plan_end_15m).sum()
    print(f'   outcome flips 1m vs 15m: {flip}/{len(df)}')

    print('\nB) wick-exit by granularity:')
    for tf in ('1m', '5m', '15m'):
        wr = (df[f'wick_r_{tf}'] > 0).mean() * 100
        print(f'   {tf:>3}: mean {df[f"wick_r_{tf}"].mean():+.3f}R   WR {wr:.0f}%   '
              f'wick-exits {(df[f"wick_end_{tf}"]=="wick").sum()}')

    # C) 1m sweep-and-reclaim in the 5 min before signal, his vs controls
    ctrl = pd.read_csv(os.path.join(OUT, 'control_features.csv'),
                       parse_dates=['control_time_utc'])
    ctrl = ctrl[ctrl.symbol.isin(['BTCUSDT', 'ETHUSDT'])]

    def sweep_reclaim(sym, ts, sign):
        try:
            m = load_ohlcv(sym, ts - pd.Timedelta('40min'), ts, '1m')
        except Exception:
            return None
        if len(m) < 36:
            return None
        recent, prior = m.iloc[-5:], m.iloc[:-5]
        if sign > 0:
            return bool(((recent['low'] < prior['low'].min())
                         & (recent['close'] > prior['low'].min())).any())
        return bool(((recent['high'] > prior['high'].max())
                     & (recent['close'] < prior['high'].max())).any())

    his = [v for _, r in bt.iterrows()
           if (v := sweep_reclaim(r['symbol'], r['signal_time_utc'],
                                  int(r['side_sign']))) is not None]
    ctl = [v for _, r in ctrl.iterrows()
           if (v := sweep_reclaim(r['symbol'], r['control_time_utc'],
                                  int(r['side_sign']))) is not None]
    print(f'\nC) 1m sweep-and-reclaim in the 5min before entry:')
    print(f'   his {np.mean(his):.0%} (n={len(his)})   controls {np.mean(ctl):.0%} (n={len(ctl)})')


if __name__ == '__main__':
    main()
