#!/usr/bin/env python3
"""Test A events: BTC/ETH fresh-high sweep-fades with replayed outcomes.

Reuses scanner_lib (definition + engine) so the baseline is byte-comparable to
the scanner study. Events pooled over N ∈ {1,3,7}, deduped on entry bar.
Writes results/events.csv (event_ts = sweep bar, entry at next bar open).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scanner_study'))
import scanner_lib as sl  # noqa: E402

OUT = os.path.join(HERE, 'results')
MIN_QV = 10e6
TIF = 48 * 4


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    btc = sl.load_symbol('BTCUSDT')
    btc_1h = btc['close'].resample('1h', label='left', closed='left').last().dropna()
    rows = []
    for sym in ('BTCUSDT', 'ETHUSDT'):
        df = sl.add_features(sl.load_symbol(sym), btc_1h)
        for n in (1, 3, 7):
            ev = sl.sweep_fade_short_events(df, n)
            for tr_target in (1.0, 1.5):
                trades = sl.backtest_events(df, ev, side=-1, atr_mult=2.5,
                                            target_r=tr_target, tif_bars=TIF,
                                            min_qv_30d=MIN_QV, regime_gate=None)
                for t in trades:
                    t.update(symbol=sym, n_days=n, target_r=tr_target)
                rows.extend(trades)
    t = pd.DataFrame(rows)
    t['event_ts'] = pd.to_datetime(t['time']) - pd.Timedelta('15min')
    # dedupe: same (symbol, event bar, target) can arrive via several N — keep smallest N
    t = (t.sort_values('n_days')
          .drop_duplicates(subset=['symbol', 'event_ts', 'target_r'], keep='first'))
    t.to_csv(os.path.join(OUT, 'events.csv'), index=False)
    uniq = t[t.target_r == 1.0]
    print(f'{len(t)} trade rows, {len(uniq)} unique events '
          f'({uniq.symbol.value_counts().to_dict()}), '
          f'{uniq.event_ts.dt.date.nunique()} unique days')
    print(f'baseline net R (target 1.0): {uniq.net_r.mean():+.3f} '
          f'gross {uniq.gross_r.mean():+.3f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
