"""diag_h_b_distribution: how are the 116 H_B trades distributed across
months and quarters?

Production CHENTO_TRIPLE_V3 mirrors the H_B variant from
validation_adaptive_hybrid.py (T3 inside-VA + T1 outside-VA). The
adaptive_hybrid_results.json only stored aggregate metrics (n, per_yr,
IS/OOS split) — never per-quarter counts. This script reconstructs the
exact H_B ledger, bucks by quarter and month, and prints distribution
diagnostics so we can see whether multi-month droughts are mechanically
expected or anomalous.

Output:
  - per-quarter table: count, cumR, maxDD, mean R
  - per-month table: count
  - largest gaps between consecutive trades
  - JSON dump for reproducibility
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m,
)
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (
    build_optimized, replay_with_mae, apply_filters, compute_volume_profile,
)
from studies.notebooks.chento_journal.validation_adaptive_hybrid import (
    TIER_PARAMS, replay_all_tiers, attach_va, hybrid,
)


def per_period(t: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Bucket trades by period (freq='QE' / 'ME'); return count, sum R,
    maxDD-within-period, cumR-at-end."""
    if t.empty:
        return pd.DataFrame()
    t = t.sort_values('ts').reset_index(drop=True).copy()
    t['period'] = t['ts'].dt.to_period(freq)
    rows = []
    cum_R_running = 0.0
    for p, sub in t.groupby('period'):
        in_period_cum = sub['r_outcome'].cumsum().values
        period_peak = np.maximum.accumulate(in_period_cum) if len(in_period_cum) else np.array([0.0])
        period_dd = (in_period_cum - period_peak).min() if len(in_period_cum) else 0.0
        cum_R_running += float(sub['r_outcome'].sum())
        rows.append({
            'period': str(p),
            'n': int(len(sub)),
            'sum_R': round(float(sub['r_outcome'].sum()), 2),
            'mean_R': round(float(sub['r_outcome'].mean()), 2),
            'wins': int((sub['r_outcome'] > 0).sum()),
            'cum_R_running': round(cum_R_running, 2),
            'period_maxDD_R': round(float(period_dd), 2),
        })
    return pd.DataFrame(rows)


def gap_analysis(t: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Largest gaps between consecutive trades, in days."""
    t = t.sort_values('ts').reset_index(drop=True).copy()
    t['gap_days'] = t['ts'].diff().dt.total_seconds() / 86400
    largest = t.nlargest(top_n, 'gap_days')[['ts', 'gap_days']].copy()
    largest['gap_days'] = largest['gap_days'].round(1)
    largest['prev_ts'] = t['ts'].shift().loc[largest.index]
    return largest[['prev_ts', 'ts', 'gap_days']]


def main():
    print('Reconstructing H_B ledger (T3 inside-VA + T1 outside-VA) ...')
    print('  Step 1: build_optimized (triggers + features) ...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    print(f'    Triple triggers: {len(triple_w):,}')

    print('  Step 2: replay each tier under ladder ...')
    replays_raw = replay_all_tiers(triple_w, df_smc, df_atr, fvgs, obs)
    for tier, rep in replays_raw.items():
        print(f'    {tier}: {len(rep)} rows')

    print('  Step 3: filters per tier ...')
    filtered = {tier: apply_filters(rep, delta_df, df_smc, fvgs, obs)
                for tier, rep in replays_raw.items()}
    ts_intersect = set(filtered['T1']['ts'])
    for tier in ('T2', 'T3'):
        ts_intersect &= set(filtered[tier]['ts'])
    for tier in TIER_PARAMS:
        filtered[tier] = filtered[tier][filtered[tier]['ts'].isin(ts_intersect)].copy()
        filtered[tier] = filtered[tier].sort_values('ts').reset_index(drop=True)
    print(f'    intersected: {len(ts_intersect)} triggers')

    print('  Step 4: volume profile + VA attach ...')
    vp = compute_volume_profile(load_btc_15m(), window_days=7, n_price_bins=50)
    for tier in TIER_PARAMS:
        attach_va(filtered[tier], vp)

    inside = filtered['T1']['in_va']
    h_b = hybrid(filtered['T3'], filtered['T1'], inside)
    h_b['ts'] = pd.to_datetime(h_b['ts'], utc=True)
    print(f'\n  H_B ledger reconstructed: n={len(h_b)} trades')

    # Headline span
    span_days = (h_b['ts'].max() - h_b['ts'].min()).total_seconds() / 86400
    print(f'  Span: {h_b["ts"].min().date()} → {h_b["ts"].max().date()} '
          f'({span_days:.0f} days = {span_days/365.25:.2f} years)')
    print(f'  Implied base rate: {len(h_b) / (span_days/365.25):.1f} trades/yr')

    # Per-quarter
    print('\n' + '=' * 86)
    print('Per-quarter distribution')
    print('=' * 86)
    pq = per_period(h_b, 'Q')
    print(f'  {"quarter":<12s} {"n":>4s} {"sumR":>7s} {"meanR":>7s} {"wins":>5s} '
          f'{"cumR":>8s} {"maxDD":>7s}')
    for _, row in pq.iterrows():
        print(f'  {row["period"]:<12s} {row["n"]:>4d} {row["sum_R"]:>+7.2f} '
              f'{row["mean_R"]:>+7.2f} {row["wins"]:>5d} '
              f'{row["cum_R_running"]:>+8.2f} {row["period_maxDD_R"]:>+7.2f}')

    # Headline stats on distribution
    n_quarters_zero = int((pq['n'] == 0).sum())
    n_quarters_total = len(pq)
    print(f'\n  zero-trade quarters: {n_quarters_zero}/{n_quarters_total} '
          f'({n_quarters_zero/n_quarters_total*100:.0f}%)')
    print(f'  median trades/quarter: {pq["n"].median():.1f}')
    print(f'  max trades/quarter:    {pq["n"].max()}')
    print(f'  min trades/quarter:    {pq["n"].min()}')

    # Per-month (compact view)
    print('\n' + '=' * 86)
    print('Per-month distribution (counts only)')
    print('=' * 86)
    pm = per_period(h_b, 'M')
    by_year = {}
    for _, row in pm.iterrows():
        p = pd.Period(row['period'])
        by_year.setdefault(p.year, {})[p.month] = int(row['n'])
    print(f'  {"year":<6s} ' + ' '.join(f'{m:>3s}' for m in
          ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']) +
          f' {"total":>6s}')
    for year in sorted(by_year):
        counts = [by_year[year].get(m, 0) for m in range(1, 13)]
        total = sum(counts)
        print(f'  {year:<6d} ' + ' '.join(f'{c:>3d}' if c > 0 else '  .' for c in counts) +
              f' {total:>6d}')
    n_months_zero = int((pm['n'] == 0).sum())
    # Account for months that exist in the span but have no trades — we need
    # the full month range, not just months that appear in the ledger.
    full_months = pd.period_range(h_b['ts'].min().to_period('M'),
                                   h_b['ts'].max().to_period('M'), freq='M')
    months_with_trades = set(pm['period'].tolist())
    zero_months_full = sum(1 for m in full_months if str(m) not in months_with_trades)
    print(f'\n  zero-trade months: {zero_months_full}/{len(full_months)} '
          f'({zero_months_full/len(full_months)*100:.0f}%)')

    # Top gaps
    print('\n' + '=' * 86)
    print('Top-10 longest gaps between consecutive trades')
    print('=' * 86)
    gaps = gap_analysis(h_b, top_n=10)
    print(f'  {"prev trade":<28s} {"next trade":<28s} {"gap":>8s}')
    for _, row in gaps.iterrows():
        prev = str(row['prev_ts'])[:19] if pd.notna(row['prev_ts']) else 'n/a'
        nxt = str(row['ts'])[:19]
        print(f'  {prev:<28s} {nxt:<28s} {row["gap_days"]:>7.1f}d')

    # Side split (use 'direction' column — research replay uses 'long'/'short' strings)
    print('\n' + '=' * 86)
    print('Per-direction (long vs short) per quarter')
    print('=' * 86)
    if 'direction' in h_b.columns:
        pq_dir = h_b.groupby([h_b['ts'].dt.to_period('Q'), 'direction']).size().unstack(fill_value=0)
        print(f'  {"quarter":<12s} {"long":>5s} {"short":>6s}')
        for q, row in pq_dir.iterrows():
            l = int(row.get('long', 0))
            s = int(row.get('short', 0))
            print(f'  {str(q):<12s} {l:>5d} {s:>6d}')
    else:
        print(f'  (no direction column — available: {list(h_b.columns)})')

    # JSON dump
    out_path = OUT_DIR / 'h_b_distribution.json'
    payload = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'n': int(len(h_b)),
        'span_start': str(h_b['ts'].min()),
        'span_end': str(h_b['ts'].max()),
        'span_years': round(span_days / 365.25, 3),
        'base_rate_per_year': round(len(h_b) / (span_days / 365.25), 2),
        'per_quarter': pq.to_dict(orient='records'),
        'per_month': pm.to_dict(orient='records'),
        'zero_quarters_count': n_quarters_zero,
        'zero_quarters_pct': round(n_quarters_zero / n_quarters_total * 100, 1),
        'zero_months_count': zero_months_full,
        'zero_months_pct': round(zero_months_full / len(full_months) * 100, 1),
        'top_gaps_days': gaps.assign(
            prev_ts=lambda d: d['prev_ts'].astype(str),
            ts=lambda d: d['ts'].astype(str),
        ).to_dict(orient='records'),
    }
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
