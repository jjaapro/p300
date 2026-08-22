#!/usr/bin/env python3
"""Refinement pass A2: extended-pump-only sweep fades, first rejection per
episode. Filters pre-registered from priors (Paladin selectivity + the
wider-TP-same-stop memory), not mined from the v1 grid.

    python run_study_v2.py
"""
from __future__ import annotations

import itertools
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner_lib as sl
from run_study import MIN_QV_30D, OOS_START, TIF_BARS, summarize

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')

GRID = list(itertools.product([1, 3], [1.5, 2.5], [1.0, 1.5, 2.0],
                              [None, 'skip_up30d']))


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    symbols = sl.list_symbols()
    btc = sl.load_symbol('BTCUSDT')
    btc_1h = btc['close'].resample('1h', label='left', closed='left').last().dropna()

    all_trades: dict[tuple, list] = {k: [] for k in GRID}
    for si, sym in enumerate(symbols, 1):
        df = sl.load_symbol(sym)
        if df is None:
            continue
        df = sl.add_features(df, btc_1h)
        for n_days in (1, 3):
            ev = sl.sweep_fade_short_events_v2(df, n_days)
            if not ev.any():
                continue
            for (n, am, tr, gate) in GRID:
                if n != n_days:
                    continue
                t = sl.backtest_events(df, ev, side=-1, atr_mult=am, target_r=tr,
                                       tif_bars=TIF_BARS, min_qv_30d=MIN_QV_30D,
                                       regime_gate=gate)
                for x in t:
                    x['symbol'] = sym
                all_trades[(n, am, tr, gate)].extend(t)
        if si % 50 == 0:
            print(f'  [{si}/{len(symbols)}]', flush=True)

    rows = []
    for key, tl in all_trades.items():
        if not tl:
            continue
        tdf = pd.DataFrame(tl).sort_values('time')
        label = 'A2_' + '_'.join(str(k) for k in key)
        tdf.to_csv(os.path.join(OUT, f'trades_{label}.csv'), index=False)
        for split, part in (('IS', tdf[tdf.time < OOS_START]),
                            ('OOS', tdf[tdf.time >= OOS_START]),
                            ('ALL', tdf)):
            s = summarize(part, label)
            s['split'] = split
            rows.append(s)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, 'variant_summary_v2.csv'), index=False)

    cols = ['variant', 'n', 'net_exp_r', 'gross_exp_r', 'wr_pct', 'total_r',
            'max_dd_r', 'trades_per_day', 'med_cost_r', 'med_risk_pct']
    for split in ('IS', 'OOS'):
        sub = (res[(res.split == split) & (res.n > 50)]
               .sort_values('net_exp_r', ascending=False).head(12))
        print(f'\n=== A2 top by net expectancy, {split} (n>50) ===')
        print(sub[cols].round(3).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
