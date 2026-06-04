"""validation_adaptive_hybrid: adaptive-sizing hybrid configurations using
C6 AMT (value area) classification to switch tier sizing per trade.

Hypothesis: outside-VA trades carry the DD risk; inside-VA trades carry
the quality. If we apply LARGER size (T3) on inside-VA setups and
SMALLER size (T1 or T2) on outside-VA setups (or skip them entirely),
we may break the previously-shown Pareto frontier where higher annual R
always came with higher DD.

Hybrids tested:
  H_A: T3 inside-VA + T2 outside-VA   (keep frequency, boost quality)
  H_B: T3 inside-VA + T1 outside-VA   (conservative outside)
  H_C: T3 inside-VA + skip outside    (same as T3 + inside-VA pure filter — already done, included for reference)
  H_D: T2 inside-VA + T1 outside-VA
  H_E: T3 within-1R-VA + T1 outside    (broader inside zone)
  H_F: T3 within-1R-VA + skip outside  (within-1R pure filter, ref)
"""
from __future__ import annotations

import json
import sys
import sqlite3
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
DB = ROOT / 'data' / 'databases' / 'prod.db'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m,
)
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (
    build_optimized, replay_with_mae, apply_filters, compute_volume_profile
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')

TIER_PARAMS = {
    'T1': {'ladder_size_frac': 0.5, 'post_ladder_stop_R': 1.5},
    'T2': {'ladder_size_frac': 1.0, 'post_ladder_stop_R': 1.4},
    'T3': {'ladder_size_frac': 1.5, 'post_ladder_stop_R': 1.5},
}


def replay_all_tiers(triple_w, df_smc, df_atr, fvgs, obs):
    """Replay each trigger under each tier's sizing. Returns dict of dfs."""
    result = {}
    for tier_name, params in TIER_PARAMS.items():
        rows = []
        for _, t in triple_w.iterrows():
            r = replay_with_mae(t, df_smc, df_atr, fvgs, obs,
                                  enable_ladder=True, ladder_at_adv_R=0.3, **params)
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        result[tier_name] = rep
    return result


def attach_va(df: pd.DataFrame, vp: pd.DataFrame):
    ts_idx = pd.DatetimeIndex(df['ts'])
    ix = vp.index.searchsorted(ts_idx, side='right') - 1
    df['vah'] = [float(vp['vah'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
    df['val'] = [float(vp['val'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
    df['above_vah'] = df['entry'] > df['vah']
    df['below_val'] = df['entry'] < df['val']
    df['in_va'] = (~df['above_vah']) & (~df['below_val'])
    df['dist_to_va_R'] = (df[['vah', 'val']].sub(df['entry'], axis=0)).abs().min(axis=1) / df['risk']


def hybrid(rep_inside_tier: pd.DataFrame, rep_outside_tier: pd.DataFrame | None,
            inside_mask: pd.Series) -> pd.DataFrame:
    """Build a hybrid trade sequence: use rep_inside_tier rows where
    inside_mask is True, and rep_outside_tier rows where it's False
    (or skip if rep_outside_tier is None).
    Both inputs must be replays of the SAME trigger set, just at different
    tier sizings."""
    inside_rows = rep_inside_tier[inside_mask].copy()
    if rep_outside_tier is None:
        return inside_rows.sort_values('ts').reset_index(drop=True)
    outside_rows = rep_outside_tier[~inside_mask].copy()
    combined = pd.concat([inside_rows, outside_rows], ignore_index=True)
    return combined.sort_values('ts').reset_index(drop=True)


def summary(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    annual_R = float(t['r_outcome'].mean()) * len(t) / max(span_y, 0.1)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 1),
        'MAR': round(annual_R / abs(float(dd.min())), 2) if dd.min() != 0 else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<55s} empty'); return
    print(f'  {s["label"]:<55s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  maxDD={s["max_dd_R"]:+5.2f}  '
           f'annual={s["annual_R"]:+.1f}R  MAR={s["MAR"]:>5.2f}  '
           f'IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


def main():
    print('Building optimized triggers and replays at all three tiers...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    replays_raw = replay_all_tiers(triple_w, df_smc, df_atr, fvgs, obs)
    # Apply filters per tier (each replay separately because losses_before
    # is path-dependent on which trades take place)
    print('Applying filters per tier...')
    filtered = {tier: apply_filters(rep, delta_df, df_smc, fvgs, obs)
                for tier, rep in replays_raw.items()}
    # ensure same trigger universe in each: take intersection by ts
    ts_intersect = set(filtered['T1']['ts'])
    for tier in ('T2', 'T3'):
        ts_intersect &= set(filtered[tier]['ts'])
    print(f'  Intersecting ts: {len(ts_intersect)} (T1={len(filtered["T1"])}, T2={len(filtered["T2"])}, T3={len(filtered["T3"])})')

    # Restrict each tier to intersection
    for tier in TIER_PARAMS:
        filtered[tier] = filtered[tier][filtered[tier]['ts'].isin(ts_intersect)].copy()
        filtered[tier] = filtered[tier].sort_values('ts').reset_index(drop=True)

    print('Computing volume profile (7d window)...')
    vp = compute_volume_profile(load_btc_15m(), window_days=7, n_price_bins=50)

    # Attach VA flags to each filtered tier (same trigger ts ⇒ same VA classification)
    for tier in TIER_PARAMS:
        attach_va(filtered[tier], vp)

    # Sanity: VA classification should be identical across tiers (same trigger ts)
    assert (filtered['T1']['in_va'].values == filtered['T2']['in_va'].values).all()
    assert (filtered['T1']['in_va'].values == filtered['T3']['in_va'].values).all()

    inside = filtered['T1']['in_va']
    within1R = filtered['T1']['dist_to_va_R'] <= 1.0
    print(f'\n  Inside-VA: {int(inside.sum())} of {len(inside)} ({inside.mean()*100:.0f}%)')
    print(f'  Within 1R of VA edge: {int(within1R.sum())} of {len(within1R)} ({within1R.mean()*100:.0f}%)')

    print('\n' + '=' * 80)
    print('=== Reference points (single-tier configurations) ===')
    print('=' * 80)
    for tier in TIER_PARAMS:
        show(summary(filtered[tier], f'{tier} alone (all trades)'))

    print()
    show(summary(filtered['T3'][inside], 'T3 + inside-VA only (no skip otherwise)'))
    show(summary(filtered['T3'][within1R], 'T3 + within-1R-VA only'))
    show(summary(filtered['T2'][inside], 'T2 + inside-VA only'))

    print('\n' + '=' * 80)
    print('=== Adaptive hybrids ===')
    print('=' * 80)

    print('\n--- H_A: T3 inside-VA + T2 outside-VA ---')
    h_a = hybrid(filtered['T3'], filtered['T2'], inside)
    show(summary(h_a, 'H_A: T3 inside + T2 outside'))

    print('\n--- H_B: T3 inside-VA + T1 outside-VA ---')
    h_b = hybrid(filtered['T3'], filtered['T1'], inside)
    show(summary(h_b, 'H_B: T3 inside + T1 outside'))

    print('\n--- H_D: T2 inside-VA + T1 outside-VA ---')
    h_d = hybrid(filtered['T2'], filtered['T1'], inside)
    show(summary(h_d, 'H_D: T2 inside + T1 outside'))

    print('\n--- H_E: T3 within-1R-VA + T1 outside-1R-VA ---')
    h_e = hybrid(filtered['T3'], filtered['T1'], within1R)
    show(summary(h_e, 'H_E: T3 within-1R + T1 outside'))

    print('\n--- H_F: T3 within-1R-VA + skip outside ---')
    h_f = hybrid(filtered['T3'], None, within1R)
    show(summary(h_f, 'H_F: T3 within-1R only (skip outside)'))

    print('\n--- H_C: T3 inside-VA + skip outside (== T3 + inside-VA filter) ---')
    h_c = hybrid(filtered['T3'], None, inside)
    show(summary(h_c, 'H_C: T3 inside only (skip outside)'))

    # Additional variants where outside-VA size is REDUCED
    # e.g., use HALF-T1 size on outside (50% of T1's 50% = T0.25)
    # Skipping for now — already shows pattern

    print('\n' + '=' * 80)
    print('=== Pareto frontier analysis ===')
    print('=' * 80)
    candidates = [
        ('T1 alone', summary(filtered['T1'], 'T1 alone')),
        ('T2 alone', summary(filtered['T2'], 'T2 alone')),
        ('T3 alone', summary(filtered['T3'], 'T3 alone')),
        ('T3 + inside-VA', summary(filtered['T3'][inside], 'T3 + inside-VA')),
        ('T3 + within-1R-VA', summary(filtered['T3'][within1R], 'T3 + within-1R-VA')),
        ('H_A: T3in+T2out', summary(h_a, 'H_A')),
        ('H_B: T3in+T1out', summary(h_b, 'H_B')),
        ('H_D: T2in+T1out', summary(h_d, 'H_D')),
        ('H_E: T3w1R+T1out', summary(h_e, 'H_E')),
    ]
    print(f'\n{"variant":<28s} {"n/yr":>5s} {"meanR":>7s} {"maxDD":>7s} {"annual":>8s} {"MAR":>5s} {"OOS":>7s}')
    for name, s in candidates:
        if s.get('n', 0) == 0: continue
        print(f'  {name:<28s} {s["per_yr"]:>5.1f} {s["mean_R"]:>+7.3f} {s["max_dd_R"]:>+7.2f} '
               f'{s["annual_R"]:>+8.1f} {s["MAR"]:>5.2f} {s["OOS_meanR"]:>+7.3f}')

    out_path = OUT_DIR / 'adaptive_hybrid_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Adaptive-sizing hybrid using C6 AMT VA classification: '
                      'higher tier on inside-VA, lower or skip on outside-VA.'),
            'results': {name: s for name, s in candidates},
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
