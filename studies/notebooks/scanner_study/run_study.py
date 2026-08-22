#!/usr/bin/env python3
"""Run the scanner-study grid over every symbol and write per-variant results.

    python run_study.py            # full grid -> results/

Walk-forward: IS = 2024-01 .. 2025-08, OOS = 2025-09 .. 2026-08 (12 months
held out). Metrics are net of the 18bp round-trip cost model in scanner_lib.
"""
from __future__ import annotations

import itertools
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scanner_lib as sl

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')
OOS_START = pd.Timestamp('2025-09-01', tz='UTC')
MIN_QV_30D = 10e6            # $10M median daily quote volume
TIF_BARS = 48 * 4            # 48h

GRID_A = list(itertools.product([1, 3, 7], [1.5, 2.5], [1.0, 1.5],
                                [None, 'skip_up30d']))
GRID_B = list(itertools.product([0.01], [0.01], [1.5, 2.5], [1.0, 1.5],
                                [None]))


def summarize(trades: pd.DataFrame, label: str) -> dict:
    d: dict = {'variant': label, 'n': len(trades)}
    if not len(trades):
        return d
    r = trades['net_r']
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    days = max(1.0, (trades['time'].max() - trades['time'].min()).days)
    d.update({'net_exp_r': r.mean(), 'gross_exp_r': trades['gross_r'].mean(),
              'wr_pct': (r > 0).mean() * 100, 'total_r': r.sum(),
              'max_dd_r': dd, 'mar_like': (r.sum() / abs(dd)) if dd < 0 else np.inf,
              'trades_per_day': len(trades) / days,
              'med_cost_r': trades['cost_r'].median(),
              'med_risk_pct': trades['risk_pct'].median()})
    return d


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    symbols = sl.list_symbols()
    print(f'{len(symbols)} symbols')

    btc = sl.load_symbol('BTCUSDT')
    btc_1h = btc['close'].resample('1h', label='left', closed='left').last().dropna()

    # detect events once per (symbol, n_days); backtest per variant
    all_trades_a: dict[tuple, list] = {k: [] for k in GRID_A}
    all_trades_b: dict[tuple, list] = {k: [] for k in GRID_B}
    for si, sym in enumerate(symbols, 1):
        df = sl.load_symbol(sym)
        if df is None:
            continue
        df = sl.add_features(df, btc_1h)
        for n_days in (1, 3, 7):
            ev = sl.sweep_fade_short_events(df, n_days)
            if not ev.any():
                continue
            for (n, am, tr, gate) in GRID_A:
                if n != n_days:
                    continue
                t = sl.backtest_events(df, ev, side=-1, atr_mult=am, target_r=tr,
                                       tif_bars=TIF_BARS, min_qv_30d=MIN_QV_30D,
                                       regime_gate=gate)
                for x in t:
                    x['symbol'] = sym
                all_trades_a[(n, am, tr, gate)].extend(t)
        if sym != 'BTCUSDT':
            evb = sl.rs_dump_long_events(df, 0.01, 0.01)
            if evb.any():
                for (bt_, rs_, am, tr, gate) in GRID_B:
                    t = sl.backtest_events(df, evb, side=1, atr_mult=am, target_r=tr,
                                           tif_bars=TIF_BARS, min_qv_30d=MIN_QV_30D,
                                           regime_gate=gate)
                    for x in t:
                        x['symbol'] = sym
                    all_trades_b[(bt_, rs_, am, tr, gate)].extend(t)
        if si % 25 == 0:
            print(f'  [{si}/{len(symbols)}] {sym}', flush=True)

    rows = []
    for grid, trades_map, tag in ((GRID_A, all_trades_a, 'A_sweepfade'),
                                  (GRID_B, all_trades_b, 'B_rsdump')):
        for key, tl in trades_map.items():
            if not tl:
                continue
            tdf = pd.DataFrame(tl).sort_values('time')
            label = f'{tag}_' + '_'.join(str(k) for k in key)
            tdf.to_csv(os.path.join(OUT, f'trades_{label}.csv'), index=False)
            is_, oos = tdf[tdf.time < OOS_START], tdf[tdf.time >= OOS_START]
            for split, part in (('IS', is_), ('OOS', oos), ('ALL', tdf)):
                s = summarize(part, label)
                s['split'] = split
                rows.append(s)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, 'variant_summary.csv'), index=False)

    for split in ('IS', 'OOS'):
        sub = (res[(res.split == split) & (res.n > 50)]
               .sort_values('net_exp_r', ascending=False).head(10))
        cols = ['variant', 'n', 'net_exp_r', 'gross_exp_r', 'wr_pct', 'total_r',
                'max_dd_r', 'trades_per_day', 'med_cost_r', 'med_risk_pct']
        print(f'\n=== top by net expectancy, {split} (n>50) ===')
        print(sub[cols].round(3).to_string(index=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
