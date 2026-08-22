#!/usr/bin/env python3
"""Step 1 sanity gate: join his 981 quoted prices to our Binance bars and measure
the venue offset. The pack warns to expect 0.05-0.3% (he screenshots Blofin /
MEXC / Bybit / Yubit / Binance); if we see more, our symbol mapping or
timestamps are wrong and nothing downstream can be trusted.

Writes results/venue_offset.csv and prints the per-symbol summary.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from paladin_data import load_ohlcv, load_pack

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    obs = load_pack()['price_observations']
    obs = obs.rename(columns={'observed_price': 'price'})
    obs = obs[obs['price'].notna() & obs['time_utc'].notna()].copy()

    rows = []
    for sym, g in obs.groupby('symbol'):
        try:
            bars = load_ohlcv(sym, g['time_utc'].min() - pd.Timedelta('30min'),
                              g['time_utc'].max() + pd.Timedelta('30min'), '15m')
        except Exception:
            continue
        if not len(bars):
            continue
        for _, r in g.iterrows():
            # bar containing the observation time
            ts = r['time_utc'].floor('15min')
            if ts not in bars.index:
                continue
            b = bars.loc[ts]
            inside = b['low'] <= r['price'] <= b['high']
            # distance to the bar's range (0 if inside), and to close, in %
            dist_range = 0.0 if inside else min(abs(r['price'] - b['low']),
                                                abs(r['price'] - b['high'])) / r['price'] * 100
            rows.append({'symbol': sym, 'time_utc': r['time_utc'], 'price': r['price'],
                         'field': r.get('field', ''), 'inside_bar': inside,
                         'dist_to_range_pct': dist_range,
                         'dist_to_close_pct': abs(r['price'] - b['close']) / r['price'] * 100})

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'venue_offset.csv'), index=False)
    print(f'joined {len(df)} of {len(obs)} observations')
    print(f"inside-bar share: {df.inside_bar.mean() * 100:.1f}%")
    print(f"dist-to-range pct: median {df.dist_to_range_pct.median():.4f}  "
          f"p90 {df.dist_to_range_pct.quantile(0.9):.4f}  max {df.dist_to_range_pct.max():.3f}")
    print(f"dist-to-close pct: median {df.dist_to_close_pct.median():.4f}  "
          f"p90 {df.dist_to_close_pct.quantile(0.9):.4f}")
    worst = (df.groupby('symbol')
               .agg(n=('inside_bar', 'size'), inside=('inside_bar', 'mean'),
                    med_range=('dist_to_range_pct', 'median'))
               .sort_values('inside').head(12))
    print('\nworst symbols by inside-bar share:')
    print(worst.to_string(float_format=lambda x: f'{x:.3f}'))


if __name__ == '__main__':
    main()
