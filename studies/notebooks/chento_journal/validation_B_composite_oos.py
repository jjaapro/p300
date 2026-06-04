"""validation_B_composite_oos: out-of-sample test for B1 ∩ B5 composite.

In-sample window: 2019-09-08 to 2024-12-31 (≈5.3 years)
Out-of-sample window: 2025-01-01 to 2026-05-24 (≈1.4 years)

Hypothesis to test:
  B1 ∩ B5 (BTC, same direction, ±24h) produces:
    - Stable per-trade R (~+0.25R) across IS and OOS
    - Stable WR (~43%)
    - Stable frequency (~280/yr)

If OOS metrics collapse vs IS, the +0.252R was probably overfit/coincidence.
If OOS metrics hold (within ±30%), the composite is genuine signal-stream
edge worth pursuing.

We also report per-period direction split and per-year breakdown to detect
regime sensitivity.
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

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import (
    intersect_triggers, WINDOW_HOURS,
)


IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
OOS_START = pd.Timestamp('2025-01-01', tz='UTC')


def stats_for_subset(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {'label': label, 'n': 0}
    span_y = (df['ts'].max() - df['ts'].min()).total_seconds() / (365.25 * 86400)
    per_y = len(df) / max(span_y, 0.01)
    r_vals = df['r_outcome'].dropna()
    if r_vals.empty:
        return {'label': label, 'n': len(df), 'note': 'no R outcomes'}
    mean_r = float(r_vals.mean())
    median_r = float(r_vals.median())
    wr = float((r_vals > 0).mean())
    # Std of R for sharpe-like ratio
    r_std = float(r_vals.std(ddof=1)) if len(r_vals) > 1 else 0
    sharpe = mean_r / r_std if r_std > 0 else 0
    exit_counts = df['exit_kind'].value_counts().to_dict() if 'exit_kind' in df.columns else {}
    direction = df['direction'].value_counts().to_dict() if 'direction' in df.columns else {}
    ann = ((1 + 0.02 * mean_r) ** per_y - 1) if per_y > 0 else 0
    return {
        'label': label,
        'n': int(len(df)),
        'span_years': round(span_y, 2),
        'trades_per_year': round(per_y, 1),
        'mean_R': round(mean_r, 3),
        'median_R': round(median_r, 3),
        'r_std': round(r_std, 3),
        'r_sharpe': round(sharpe, 3),
        'win_rate': round(wr, 3),
        'exit_kinds': exit_counts,
        'direction_split': direction,
        'implied_annual_pct': round(ann * 100, 1),
    }


def by_year_stats(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    d = df.copy()
    d['year'] = pd.to_datetime(d['ts']).dt.year
    g = d.groupby('year').agg(
        n=('r_outcome', 'size'),
        mean_R=('r_outcome', lambda s: round(float(s.mean()), 3)),
        wr=('r_outcome', lambda s: round(float((s > 0).mean()), 3)),
        n_long=('direction', lambda s: int((s == 'long').sum())),
        n_short=('direction', lambda s: int((s == 'short').sum())),
    )
    return g


def main():
    print('Loading BTC 15m + signals...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')

    print('\nGenerating B1 triggers (cvd>±0.5, vel<1.0)...')
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    print(f'  B1: {len(b1)} triggers')

    print('\nGenerating B5 triggers (long_pct vs rolling p10/p90)...')
    lsr = load_lsr('BTC')
    lsr_sig = compute_lsr_extremes(lsr)
    b5 = b5_triggers(df, lsr_sig)
    print(f'  B5: {len(b5)} triggers')

    print(f'\nIntersecting B1 ∩ B5 (same dir, ±{WINDOW_HOURS}h)...')
    composite = intersect_triggers(b1, b5)
    print(f'  Composite: {len(composite)} triggers')

    print('\nMeasuring R outcomes...')
    composite_r = measure_r_outcomes(composite, df)

    # Split IS / OOS
    is_mask = composite_r['ts'] <= IS_END
    oos_mask = composite_r['ts'] >= OOS_START
    is_set = composite_r[is_mask].copy()
    oos_set = composite_r[oos_mask].copy()

    print(f'\nIS  ({IS_END.date()} and earlier): {len(is_set)} triggers')
    print(f'OOS ({OOS_START.date()} onward):   {len(oos_set)} triggers')

    # Compute stats
    full = stats_for_subset(composite_r, 'FULL_PERIOD')
    is_s = stats_for_subset(is_set, 'IN_SAMPLE')
    oos_s = stats_for_subset(oos_set, 'OUT_OF_SAMPLE')

    def fmt(s):
        if s.get('n', 0) == 0:
            return f'  {s["label"]:<16s}: 0 triggers'
        return (f'  {s["label"]:<16s}: n={s["n"]:>5d} ({s["trades_per_year"]:>5.1f}/yr)  '
                f'meanR={s["mean_R"]:+.3f}  medianR={s["median_R"]:+.3f}  '
                f'WR={s["win_rate"]:.0%}  Sharpe={s["r_sharpe"]:+.2f}  '
                f'annual={s["implied_annual_pct"]:+.1f}%')

    print('\n=== IS vs OOS comparison ===')
    print(fmt(full))
    print(fmt(is_s))
    print(fmt(oos_s))

    # Decay metric: how much did per-trade R drop OOS vs IS?
    if is_s.get('n', 0) > 0 and oos_s.get('n', 0) > 0:
        r_decay = oos_s['mean_R'] - is_s['mean_R']
        r_decay_pct = (oos_s['mean_R'] / is_s['mean_R'] - 1) * 100 if is_s['mean_R'] != 0 else 0
        freq_ratio = oos_s['trades_per_year'] / is_s['trades_per_year']
        wr_decay = oos_s['win_rate'] - is_s['win_rate']
        print(f'\n  R decay (OOS - IS):      {r_decay:+.3f}R  ({r_decay_pct:+.1f}%)')
        print(f'  Freq ratio (OOS / IS):   {freq_ratio:.2f}')
        print(f'  WR decay (OOS - IS):     {wr_decay:+.0%}')
        if oos_s['mean_R'] > 0 and r_decay > -0.10:
            verdict = 'HOLDS (R decay within tolerance)'
        elif oos_s['mean_R'] > 0:
            verdict = 'DECAYED (positive but materially weaker OOS)'
        else:
            verdict = 'FAILED (negative R OOS — signal does not generalize)'
        print(f'  VERDICT: {verdict}')

    print('\n=== Per-year breakdown (full period) ===')
    yearly = by_year_stats(composite_r)
    print(yearly.to_string())

    # Direction sub-stats per period
    print('\n=== Per-direction within IS / OOS ===')
    for label, sub in (('IS', is_set), ('OOS', oos_set)):
        for d in ('long', 'short'):
            ds = sub[sub['direction'] == d]
            if ds.empty: continue
            r = ds['r_outcome'].dropna()
            print(f'  {label} {d:<5s}: n={len(ds):>4d}  mean R={r.mean():+.3f}  '
                   f'WR={(r>0).mean():.0%}')

    out_path = OUT_DIR / 'B_composite_oos_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_question': (
                'Does B1 ∩ B5 (+0.252R per trade in-sample) hold out-of-sample? '
                'If yes -> genuine signal-stream edge worth pursuing. If R '
                'collapses OOS -> overfit / regime artifact.'
            ),
            'is_end': str(IS_END), 'oos_start': str(OOS_START),
            'full_period': full,
            'in_sample': is_s,
            'out_of_sample': oos_s,
            'yearly_breakdown': yearly.reset_index().to_dict(orient='records') if not yearly.empty else [],
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
