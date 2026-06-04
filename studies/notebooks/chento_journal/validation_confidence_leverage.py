"""validation_confidence_leverage: build a per-trade confidence score from
the signal features we already have, then test discrete leverage tiers per
confidence bucket against the H_B uniform-sized hybrid baseline.

Plan:
  1. Reconstruct H_B trades (T3 inside-VA / T1 outside-VA, 116 trades, 22/yr).
  2. For each trade attach all candidate confidence features:
       - okx_delta_z magnitude
       - dist_to_va_R (smaller is better)
       - in_va boolean
       - b7 alignment magnitude (mean abs of cvd_z across 4 timeframes)
       - cvd_z magnitude at entry (B1 strength)
       - dist_resist_OB_R (larger is better)
       - hour-of-day quality score
  3. Test each feature individually for monotonic edge with r_outcome.
  4. Build a composite confidence score (z-sum of best-performing features).
  5. Bucket into quartiles or quintiles.
  6. Test discrete NAV-risk-per-trade per bucket vs uniform baseline.
  7. Report new headline (best confidence-scaled config vs H_B uniform).
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
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    compute_lsr_extremes, b5_triggers, load_lsr,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    features_at,
)
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (
    replay_with_mae, apply_filters, compute_volume_profile, build_optimized
)
from studies.notebooks.chento_journal.validation_multi_asset import (
    derive_binance_1h_close, compute_okx_delta_z, load_okx_close_asset,
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def session_quality(hour_utc: int) -> float:
    """Per loser_profile findings: Asia hours win, NY afternoon loses."""
    table = {
        0: 0.85, 1: 1.00, 2: 0.65, 3: 1.00, 4: 0.75, 5: 1.00,
        6: 0.00, 7: 0.40, 8: 0.80, 9: 0.85, 10: 0.45, 11: 0.30,
        12: 0.80, 13: 0.65, 14: 0.50, 15: 0.00, 16: 0.60, 17: 0.50,
        18: 0.70, 19: 0.55, 20: 0.20, 21: 0.75, 22: 0.55, 23: 1.00,
    }
    return table.get(hour_utc, 0.5)


def build_h_b_with_features():
    """Build H_B (T3 inside-VA / T1 outside-VA) trades with all features attached."""
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    df_15m = load_btc_15m()
    vp = compute_volume_profile(df_15m, window_days=7, n_price_bins=50)
    multitf = compute_multitf_cvd(df_15m)

    # First pass: replay at T1 to get the trigger ts list AND the inside-VA
    # classification (we need VA membership before deciding tier).
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_mae(t, df_smc, df_atr, fvgs, obs,
                              enable_ladder=True, ladder_at_adv_R=0.3,
                              ladder_size_frac=0.5, post_ladder_stop_R=1.5)
        if r is not None:
            rows.append(r)
    rep_t1 = pd.DataFrame(rows)
    rep_t1 = apply_filters(rep_t1, delta_df, df_smc, fvgs, obs)
    rep_t1 = rep_t1.sort_values('ts').reset_index(drop=True)

    # Attach VA membership
    ts_idx = pd.DatetimeIndex(rep_t1['ts'])
    ix_vp = vp.index.searchsorted(ts_idx, side='right') - 1
    rep_t1['vah'] = [float(vp['vah'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix_vp]
    rep_t1['val'] = [float(vp['val'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix_vp]
    rep_t1['above_vah'] = rep_t1['entry'] > rep_t1['vah']
    rep_t1['below_val'] = rep_t1['entry'] < rep_t1['val']
    rep_t1['in_va'] = (~rep_t1['above_vah']) & (~rep_t1['below_val'])
    rep_t1['dist_to_va_R'] = (rep_t1[['vah','val']].sub(rep_t1['entry'], axis=0)).abs().min(axis=1) / rep_t1['risk']

    # Replay each ts with the tier indicated by in_va
    final_rows = []
    for _, row in rep_t1.iterrows():
        ts = row['ts']
        params = ({'ladder_size_frac': 1.5, 'post_ladder_stop_R': 1.5} if row['in_va']
                   else {'ladder_size_frac': 0.5, 'post_ladder_stop_R': 1.5})
        # Find the trigger row in triple_w for this ts
        tr = triple_w[triple_w['ts'] == ts]
        if tr.empty: continue
        r = replay_with_mae(tr.iloc[0], df_smc, df_atr, fvgs, obs,
                              enable_ladder=True, ladder_at_adv_R=0.3, **params)
        if r is not None:
            r['in_va'] = bool(row['in_va'])
            r['vah'] = float(row['vah']) if not pd.isna(row['vah']) else np.nan
            r['val'] = float(row['val']) if not pd.isna(row['val']) else np.nan
            r['dist_to_va_R'] = float(row['dist_to_va_R']) if not pd.isna(row['dist_to_va_R']) else np.nan
            final_rows.append(r)
    rep = pd.DataFrame(final_rows)
    rep = rep.sort_values('ts').reset_index(drop=True)

    # Apply filters fresh (no_tilt depends on order; re-apply)
    cur = 0; lb = []
    for ro in rep['r_outcome'].shift(1).fillna(0):
        if ro < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb
    # OKX delta z
    ts_idx2 = pd.DatetimeIndex(rep['ts'])
    ix = delta_df.index.searchsorted(ts_idx2, side='right') - 1
    rep['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i])
                            if 0 <= i < len(delta_df) else np.nan for i in ix]
    # dist_resist_OB_R
    dist_arr = []
    for _, r in rep.iterrows():
        idx = df_smc.index.searchsorted(r['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, r['entry'], r['direction'], r['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr

    # Filter again
    mask = ((rep['consec_losses_before'] == 0) &
             ((rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()) &
             (((rep['direction'] == 'long') & (rep['okx_delta_z'] >= 0)) |
              ((rep['direction'] == 'short') & (rep['okx_delta_z'] <= 0))))
    rep = rep[mask].copy().reset_index(drop=True)

    # Attach b7 alignment magnitude
    ix3 = multitf.index.searchsorted(ts_idx2[mask.values], side='right') - 1
    b7_cols = ['cvd_1h_z', 'cvd_4h_z', 'cvd_1d_z', 'cvd_3d_z']
    b7_strength = []
    for i in ix3:
        if 0 <= i < len(multitf):
            vs = [multitf[c].iloc[i] for c in b7_cols]
            vs = [abs(v) for v in vs if not pd.isna(v)]
            b7_strength.append(float(np.mean(vs)) if vs else np.nan)
        else:
            b7_strength.append(np.nan)
    rep['b7_strength'] = b7_strength

    # Attach cvd_z magnitude from df_b1
    df_b1 = compute_moneyflow_signal(df_15m)
    ts_idx3 = pd.DatetimeIndex(rep['ts'])
    ix4 = df_b1.index.searchsorted(ts_idx3, side='right') - 1
    rep['cvd_z_abs'] = [abs(float(df_b1['cvd_z'].iloc[i])) if 0 <= i < len(df_b1) else np.nan for i in ix4]

    # Session quality (from hour-of-day)
    rep['hour_utc'] = ts_idx3.hour
    rep['session_q'] = rep['hour_utc'].map(session_quality)

    # OKX magnitude
    rep['okx_delta_abs_z'] = rep['okx_delta_z'].abs()

    return rep


def feature_edge_table(rep: pd.DataFrame) -> pd.DataFrame:
    """For each candidate feature, split trades by quartile and report mean R."""
    features = ['okx_delta_abs_z', 'dist_to_va_R', 'b7_strength', 'cvd_z_abs',
                 'dist_resist_OB_R', 'session_q']
    rows = []
    for f in features:
        valid = rep[~rep[f].isna()].copy()
        if len(valid) < 40: continue
        q = pd.qcut(valid[f], q=4, labels=['Q1', 'Q2', 'Q3', 'Q4'], duplicates='drop')
        for label, sub in valid.groupby(q):
            if len(sub) < 5: continue
            rows.append({
                'feature': f, 'quartile': str(label), 'n': len(sub),
                'mean_R': round(float(sub['r_outcome'].mean()), 3),
                'wr': round(float((sub['r_outcome'] > 0).mean()), 3),
                'feature_med': round(float(sub[f].median()), 3),
            })
    return pd.DataFrame(rows)


def composite_score(rep: pd.DataFrame, *, weights: dict) -> pd.Series:
    """Build a composite confidence z-score from a weighted combination of
    normalized features."""
    df = rep.copy()
    components = []
    for f, sign in weights.items():
        col = df[f]
        # rank-normalize 0-1 (higher rank = larger feature value)
        ranks = col.rank(pct=True)
        # Apply sign: if sign == +1, higher feature is BETTER; if -1, lower is better
        if sign < 0:
            ranks = 1 - ranks
        components.append(ranks * abs(sign))
    weighted = pd.concat(components, axis=1).sum(axis=1)
    total_weight = sum(abs(s) for s in weights.values())
    return weighted / total_weight    # 0-1 confidence score


def bucket_into_quintiles(score: pd.Series) -> pd.Series:
    """Bucket score into quintiles: low / mid_low / mid / mid_high / high."""
    return pd.qcut(score, q=5,
                    labels=['low', 'mid_low', 'mid', 'mid_high', 'elite'],
                    duplicates='drop')


def summary(t: pd.DataFrame, label: str, risk_pct: float = 1.0) -> dict:
    """Summary with optional per-trade risk-% override (returns NAV-impact stats)."""
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True).copy()
    if 'risk_pct' not in t.columns:
        t['risk_pct'] = risk_pct
    # NAV impact per trade = r_outcome × risk_pct
    t['nav_impact'] = t['r_outcome'] * t['risk_pct']
    cum_nav = t['nav_impact'].cumsum().values
    peak = np.maximum.accumulate(cum_nav)
    dd_nav = cum_nav - peak
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_NAV_pct': round(float(cum_nav[-1]), 2),
        'max_dd_NAV_pct': round(float(dd_nav.min()), 2),
        'annual_NAV_pct': round(float(cum_nav[-1]) / max(span_y, 0.1), 1),
        'avg_risk_pct': round(float(t['risk_pct'].mean()), 3),
        'MAR_NAV': round((float(cum_nav[-1]) / max(span_y, 0.1)) / abs(float(dd_nav.min())), 2)
                     if dd_nav.min() != 0 else 0,
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<48s} empty'); return
    print(f'  {s["label"]:<48s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.2f}  WR={s["wr"]:.0%}  avg_risk={s["avg_risk_pct"]:.2f}%  '
           f'annual={s["annual_NAV_pct"]:>+7.1f}%  maxDD={s["max_dd_NAV_pct"]:>+5.1f}%  '
           f'MAR={s["MAR_NAV"]:>5.1f}')


def main():
    print('Building H_B trades with all candidate features...')
    rep = build_h_b_with_features()
    print(f'  H_B trades: {len(rep)}')

    # === Feature edge diagnostic ===
    print('\n=== Per-feature edge by quartile ===')
    tab = feature_edge_table(rep)
    print(tab.to_string(index=False))

    # === Build composite confidence score ===
    print('\n=== Composite confidence score (weights corrected from quartile data) ===')
    # CORRECTED weights based on actual per-quartile R data:
    # - dist_to_va_R: monotone decreasing (closer = better) → -1
    # - b7_strength: COUNTER-INTUITIVE monotone decreasing (lower = better) → -1
    # - dist_resist_OB_R: increasing (farther = better) → +1
    # - okx/cvd_z/session: flat or non-monotonic → 0 weight
    weights = {
        'dist_to_va_R': -1.0,           # closer to VA = better
        'b7_strength': -1.0,            # LOWER b7 = better (peak-extreme effect)
        'dist_resist_OB_R': +0.5,       # farther OB = better, weaker signal
    }
    rep['confidence_score'] = composite_score(rep, weights=weights)
    rep['bucket'] = bucket_into_quintiles(rep['confidence_score'])

    print(f'\n  Score range: [{rep["confidence_score"].min():.3f}, '
           f'{rep["confidence_score"].max():.3f}]')
    print(f'\n  Per-bucket mean R (uniform 1% risk):')
    bucket_stats = rep.groupby('bucket', observed=True).agg(
        n=('r_outcome', 'count'),
        mean_R=('r_outcome', 'mean'),
        wr=('r_outcome', lambda x: (x > 0).mean()),
    ).reset_index()
    bucket_stats['mean_R'] = bucket_stats['mean_R'].round(3)
    bucket_stats['wr'] = bucket_stats['wr'].round(2)
    print(bucket_stats.to_string(index=False))

    # === Test various risk-per-trade allocation schemes ===
    print('\n' + '=' * 80)
    print('=== Confidence-scaled leverage tests ===')
    print('=' * 80)

    schemes = {
        'uniform 1%': {'low': 1.0, 'mid_low': 1.0, 'mid': 1.0, 'mid_high': 1.0, 'elite': 1.0},
        'uniform 2%': {'low': 2.0, 'mid_low': 2.0, 'mid': 2.0, 'mid_high': 2.0, 'elite': 2.0},
        'uniform 3%': {'low': 3.0, 'mid_low': 3.0, 'mid': 3.0, 'mid_high': 3.0, 'elite': 3.0},
        'linear-up 0.5-2.5%': {'low': 0.5, 'mid_low': 1.0, 'mid': 1.5, 'mid_high': 2.0, 'elite': 2.5},
        'linear-up 1-3%': {'low': 1.0, 'mid_low': 1.5, 'mid': 2.0, 'mid_high': 2.5, 'elite': 3.0},
        # INVERTED: bigger on low-score (which actually has higher R per data)
        'linear-down 2.5-0.5%': {'low': 2.5, 'mid_low': 2.0, 'mid': 1.5, 'mid_high': 1.0, 'elite': 0.5},
        'linear-down 3-1%': {'low': 3.0, 'mid_low': 2.5, 'mid': 2.0, 'mid_high': 1.5, 'elite': 1.0},
        'top-heavy 0.5-4%': {'low': 0.5, 'mid_low': 0.75, 'mid': 1.0, 'mid_high': 2.0, 'elite': 4.0},
        # INVERTED top-heavy
        'low-heavy 4-0.5%': {'low': 4.0, 'mid_low': 2.0, 'mid': 1.0, 'mid_high': 0.75, 'elite': 0.5},
        'skip-elite 3/2.5/2/1/0%': {'low': 3.0, 'mid_low': 2.5, 'mid': 2.0, 'mid_high': 1.0, 'elite': 0.0},
        'concave 1/2/2.5/3/3.5%': {'low': 1.0, 'mid_low': 2.0, 'mid': 2.5, 'mid_high': 3.0, 'elite': 3.5},
    }
    print(f'{"scheme":<32s} {"n":>4s} {"meanR":>6s} {"avgRisk":>7s} {"annual%":>9s} {"maxDD%":>8s} {"MAR":>5s}')
    all_results = {}
    for name, scheme in schemes.items():
        risk_pct_series = rep['bucket'].map(scheme).astype(float)
        sub = rep.copy()
        sub['risk_pct'] = risk_pct_series.values
        sub_active = sub[sub['risk_pct'] > 0]
        if sub_active.empty:
            continue
        s = summary(sub_active, name)
        all_results[name] = s
        show(s)

    # === Ranking ===
    print('\n=== Ranking by annual NAV % (must keep maxDD ≤ -10%) ===')
    ranked = sorted([(k, v) for k, v in all_results.items() if v.get('max_dd_NAV_pct', 0) >= -10.0],
                      key=lambda kv: kv[1].get('annual_NAV_pct', 0), reverse=True)
    for k, s in ranked:
        show(s)
    print('\n=== Ranking by annual NAV % (no DD cap) ===')
    ranked_all = sorted(all_results.items(), key=lambda kv: kv[1].get('annual_NAV_pct', 0), reverse=True)
    for k, s in ranked_all:
        show(s)
    print('\n=== Ranking by MAR ===')
    ranked_mar = sorted(all_results.items(), key=lambda kv: kv[1].get('MAR_NAV', 0), reverse=True)
    for k, s in ranked_mar:
        show(s)

    # === $10k starting balance projection (NO compounding) ===
    print('\n' + '=' * 80)
    print(f'=== Year-1 projection on $10,000 starting balance (no compounding) ===')
    print('=' * 80)
    print(f'{"scheme":<32s} {"annual NAV%":>11s} {"$ profit (yr1)":>15s} {"max DD ($)":>12s}')
    for name, s in ranked_all:
        if s.get('n', 0) == 0: continue
        profit = 10000 * s['annual_NAV_pct'] / 100
        dd_dollars = 10000 * s['max_dd_NAV_pct'] / 100
        print(f'  {name:<32s} {s["annual_NAV_pct"]:>+9.1f}%  ${profit:>+12,.0f}   ${dd_dollars:>+11,.0f}')

    out_path = OUT_DIR / 'confidence_leverage_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Confidence-scaled leverage analysis on H_B trades. '
                      'Composite confidence score = rank-weighted sum of '
                      'okx_delta_abs_z, -dist_to_va_R, session_q, b7_strength, cvd_z_abs. '
                      'Tests different risk-%-per-bucket allocation schemes.'),
            'feature_quartile_edge': tab.to_dict(orient='records'),
            'bucket_stats': bucket_stats.to_dict(orient='records'),
            'scheme_results': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
