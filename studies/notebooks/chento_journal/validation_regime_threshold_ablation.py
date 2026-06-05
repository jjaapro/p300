"""Regime threshold ablation: is the +10% fixed cutoff overfitting?

The production filter `FILTER_SKIP_UP_30D_SHORTS` skips SHORT trades when
`BTC.ret_30d > +0.10`. That +10% is the only hardcoded absolute threshold
left in the stack (everything else is ATR-scaled, R-scaled, or z-scored).

This study tests whether dynamic/normalized variants:
  - generalize better across regimes,
  - improve OOS lift,
  - or simply confirm the +10% was lucky calibration.

Pool: backward-only triple triggers, atr_mult=5, target_r=6, TIF=72h
(production config). Filters 1-3 applied. Filter 4 (regime short skip)
is the variable under test.

Variants tested:
  - none                — no regime filter (NULL baseline)
  - fixed_05 ... fix_20 — fixed % cutoffs: 5/8/10/12/15/20%
  - pct_<lb>_<thresh>   — rolling-percentile cutoff: ret_30d > percentile
                            of trailing <lb> years × thresh in {.80,.85,.90,.95}
  - z_<lb>_<thresh>     — rolling-z cutoff: (ret_30d - mu) / sigma > thresh
                            with <lb>-year rolling mu, sigma × thresh in {1.0, 1.5, 2.0}

All variants apply ONLY to shorts (matches mechanism).

Decision rule: a variant is a credible replacement for the +10% fixed
threshold if BOTH:
  (a) full-sample MAR >= 90% of fixed_10's MAR
  (b) OOS_meanR >= fixed_10's OOS_meanR (i.e. doesn't degrade OOS)

A variant that lifts OOS while staying within tolerance on MAR is a real
improvement worth shipping.
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import studies.notebooks.chento_journal.validation_B_composite as src_comp
import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3
from studies.notebooks.chento_journal.validation_group_A_tuning import (
    build_optimized_triggers, apply_filters, replay_with_extras, summary,
)
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m,
)
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    features_at,
)
from studies.notebooks.chento_journal.validation_p4b_regime_atr_extended import (
    intersect_backward, compute_btc_ret_30d, bootstrap_maxdd_ci,
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
ATR_MULT = 5.0
TARGET_R = 6.0
TIF_HOURS = 72
BOOTSTRAP_N = 1000


# ─── Regime stats: percentile rank + z-score on rolling lookback ────────────


def compute_regime_stats(df_15m: pd.DataFrame, ret_30d: pd.Series,
                          lookback_years: float) -> pd.DataFrame:
    """Compute rolling percentile-rank and z-score of ret_30d.

    Rolling window: <lookback_years> calendar years of DAILY observations.
    Resampled to daily so we don't double-count intraday flat-fill noise.
    Percentile rank: fraction of trailing-window observations <= current.
    Z-score: (current - mu) / sigma over trailing window.

    Reindexed back onto 15m frame via ffill (regime stats are slow-moving).
    """
    # Step down to daily before computing rolling stats — ret_30d is itself
    # a daily-cadence signal, so 15m ffill values are redundant.
    daily_ret_30d = ret_30d.resample('1D').last().dropna()
    lookback_days = int(round(lookback_years * 365.25))

    # Min observations: at least lookback_days/2 valid (need enough history
    # before rolling stats are meaningful). For 1y: ~180 days. For 3y: ~547.
    min_periods = max(60, lookback_days // 2)

    mu = daily_ret_30d.rolling(lookback_days, min_periods=min_periods).mean()
    sigma = daily_ret_30d.rolling(lookback_days, min_periods=min_periods).std()
    z = (daily_ret_30d - mu) / sigma

    # Percentile rank: rolling apply (slower but correct)
    def _pct_rank(x):
        if len(x) < min_periods:
            return np.nan
        return float((x <= x.iloc[-1]).sum()) / len(x)

    pct = daily_ret_30d.rolling(lookback_days,
                                 min_periods=min_periods).apply(_pct_rank,
                                                                  raw=False)

    out = pd.DataFrame({
        'ret_30d': daily_ret_30d, 'mu': mu, 'sigma': sigma,
        'z': z, 'pct': pct,
    })
    # Reindex back to 15m grid
    return out.reindex(df_15m.index, method='ffill')


# ─── Regime filter variants ─────────────────────────────────────────────────


def apply_regime_variant(rep: pd.DataFrame, stats: pd.DataFrame, *,
                          variant: str, **kwargs) -> pd.DataFrame:
    """Drop SHORT trades where the variant trigger fires.

    variant ∈ {'none', 'fixed', 'pct', 'z'}.
    kwargs:
      - fixed: threshold (e.g. 0.10)
      - pct:   threshold (e.g. 0.90 means skip if ret_30d in top 10%)
      - z:     threshold (e.g. 1.5 means skip if ret_30d > 1.5 sigma above mu)
    """
    if rep.empty:
        return rep
    if variant == 'none':
        return rep.copy()
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = stats.index.searchsorted(ts_idx, side='right') - 1
    rep = rep.copy()
    if variant == 'fixed':
        rep['ret_30d_at_entry'] = [float(stats['ret_30d'].iloc[i])
                                     if 0 <= i < len(stats) else np.nan
                                     for i in ix]
        drop_mask = ((rep['direction'] == 'short') &
                     (rep['ret_30d_at_entry'] > kwargs['threshold']))
    elif variant == 'pct':
        rep['pct_at_entry'] = [float(stats['pct'].iloc[i])
                                 if 0 <= i < len(stats) else np.nan
                                 for i in ix]
        drop_mask = ((rep['direction'] == 'short') &
                     (rep['pct_at_entry'] > kwargs['threshold']))
    elif variant == 'z':
        rep['z_at_entry'] = [float(stats['z'].iloc[i])
                                if 0 <= i < len(stats) else np.nan
                                for i in ix]
        drop_mask = ((rep['direction'] == 'short') &
                     (rep['z_at_entry'] > kwargs['threshold']))
    else:
        raise ValueError(f'Unknown variant: {variant}')

    # NaN entries (insufficient history): pass through (don't gate when we
    # don't have stats). This matches production behavior at start of series.
    drop_mask = drop_mask.fillna(False)
    return rep[~drop_mask].copy()


# ─── Replay-once, filter-many (efficiency) ──────────────────────────────────


def build_unfiltered_ledger(triple_w, df_smc, df_atr, fvgs, obs, delta_df):
    """Run replay + filter steps 1-3 once. Returns the pre-regime-filter
    ledger that ALL regime variants will subsample from."""
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_extras(t, df_smc, df_atr, fvgs, obs,
                                atr_mult=ATR_MULT, target_r=TARGET_R,
                                tif_bars=4 * TIF_HOURS,
                                partial_at_R=None, partial_frac=0.0,
                                ladder_at_minus_R=None, ladder_size_frac=0.0)
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return rep
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    return apply_filters(rep, delta_df)


def full_summary(rep: pd.DataFrame, label: str) -> dict:
    s = summary(rep, label)
    if s.get('n', 0) == 0:
        s.update({'annual_R': 0, 'MAR': 0,
                   'dd_p05': 0, 'dd_p50': 0, 'dd_p95': 0, 'dd_point': 0,
                   'n_long': 0, 'n_short': 0,
                   'meanR_long': 0, 'meanR_short': 0})
        return s
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                / (365.25 * 86400))
    annual_R = s['mean_R'] * s['n'] / max(span_y, 0.1)
    s['annual_R'] = round(annual_R, 1)
    s['MAR'] = round(annual_R / abs(s['max_dd_R']) if s['max_dd_R'] != 0 else 0, 2)
    boot = bootstrap_maxdd_ci(rep.sort_values('ts')['r_outcome'].values,
                                n_boot=BOOTSTRAP_N)
    s['dd_point'] = boot['point']
    s['dd_p05'] = boot['p05']
    s['dd_p50'] = boot['p50']
    s['dd_p95'] = boot['p95']

    # Per-direction breakdown
    longs = rep[rep['direction'] == 'long']
    shorts = rep[rep['direction'] == 'short']
    s['n_long'] = int(len(longs))
    s['n_short'] = int(len(shorts))
    s['meanR_long'] = round(float(longs['r_outcome'].mean()) if len(longs) else 0, 3)
    s['meanR_short'] = round(float(shorts['r_outcome'].mean()) if len(shorts) else 0, 3)
    return s


def main():
    src_comp.intersect_triggers = intersect_backward
    src_p3.intersect_triggers = intersect_backward
    print('[ablation] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    print('Building optimized triggers (backward-only)...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()
    print(f'  Triple bars: {len(triple_w)}')

    print('Computing BTC 30d return + regime stats (1y, 2y, 3y lookbacks)...')
    df_15m_full = load_btc_15m()
    ret_30d = compute_btc_ret_30d(df_15m_full)
    stats_by_lb = {
        lb: compute_regime_stats(df_15m_full, ret_30d, lookback_years=lb)
        for lb in (1.0, 2.0, 3.0)
    }

    print('\nReplaying + applying filters 1-3 (production-equivalent)...')
    rep_base = build_unfiltered_ledger(triple_w, df_smc, df_atr,
                                          fvgs, obs, delta_df)
    print(f'  Pre-regime-filter trades: {len(rep_base)} '
           f'(longs={int((rep_base["direction"]=="long").sum())}, '
           f'shorts={int((rep_base["direction"]=="short").sum())})')

    # ─── Build variant list ────────────────────────────────────────────────
    variants = []
    # Baseline: no regime filter
    variants.append(('none', 'none', {}))
    # Fixed % thresholds
    for thresh in (0.05, 0.08, 0.10, 0.12, 0.15, 0.20):
        variants.append((f'fixed_{int(thresh*100):02d}', 'fixed',
                          {'threshold': thresh}))
    # Percentile thresholds × lookbacks
    for lb in (1.0, 2.0, 3.0):
        for thresh in (0.80, 0.85, 0.90, 0.95):
            variants.append((f'pct_{int(lb)}y_{int(thresh*100):02d}', 'pct',
                              {'threshold': thresh, 'lookback': lb}))
    # Z-score thresholds × lookbacks
    for lb in (1.0, 2.0, 3.0):
        for thresh in (1.0, 1.5, 2.0):
            variants.append((f'z_{int(lb)}y_{int(thresh*10):02d}', 'z',
                              {'threshold': thresh, 'lookback': lb}))

    print(f'\nEvaluating {len(variants)} regime-filter variants...')
    print(f'\n{"label":<16s} {"n":>3s} {"L/S":>7s} {"meanR":>7s} {"meanR_S":>8s} '
           f'{"WR":>4s} {"maxDD":>7s} {"p95":>7s} '
           f'{"annual":>7s} {"MAR":>5s} {"OOS(n)":>14s}')

    results = {}
    for label, variant, kwargs in variants:
        if variant in ('pct', 'z'):
            stats = stats_by_lb[kwargs['lookback']]
            filtered = apply_regime_variant(rep_base, stats, variant=variant,
                                             threshold=kwargs['threshold'])
        elif variant == 'fixed':
            # Use the 2y stats df just for ret_30d column (any lb works,
            # ret_30d itself is the same)
            stats = stats_by_lb[2.0]
            filtered = apply_regime_variant(rep_base, stats, variant=variant,
                                             threshold=kwargs['threshold'])
        else:  # none
            filtered = rep_base.copy()
        s = full_summary(filtered, label)
        results[label] = s
        ls_str = f'{s["n_long"]}/{s["n_short"]}'
        print(f'{label:<16s} {s["n"]:>3d} {ls_str:>7s} '
               f'{s["mean_R"]:>+7.3f} {s["meanR_short"]:>+8.3f} '
               f'{s["wr"]:>4.0%} {s["max_dd_R"]:>+7.2f} '
               f'{s["dd_p95"]:>+7.2f} {s["annual_R"]:>+7.1f} '
               f'{s["MAR"]:>5.2f} '
               f'{s["OOS_meanR"]:>+8.3f}({s["OOS_n"]:>2d})')

    # ─── Decision analysis ─────────────────────────────────────────────────
    print('\n' + '=' * 110)
    print('=== DECISION ANALYSIS: vs fixed_10 (production baseline) ===')
    print('=' * 110)
    base = results.get('fixed_10', {})
    if base.get('n', 0) == 0:
        print('  ERROR: fixed_10 baseline empty; cannot compare')
        return

    print(f'\n  Production baseline (fixed_10):')
    print(f'    n={base["n"]} (L={base["n_long"]}/S={base["n_short"]})  '
           f'meanR={base["mean_R"]:+.3f}  meanR_short={base["meanR_short"]:+.3f}')
    print(f'    annual_R={base["annual_R"]:+.1f}  MAR={base["MAR"]:.2f}  '
           f'maxDD={base["max_dd_R"]:+.2f}')
    print(f'    OOS_meanR={base["OOS_meanR"]:+.3f}(n={base["OOS_n"]})')

    # Find Pareto-better variants
    print('\n  Variants with MAR >= 90% of baseline AND OOS_meanR >= baseline OOS:')
    pareto_ok = []
    for k, v in results.items():
        if k == 'fixed_10' or v.get('n', 0) == 0:
            continue
        mar_ratio = v['MAR'] / base['MAR'] if base['MAR'] > 0 else 0
        oos_ok = v['OOS_meanR'] >= base['OOS_meanR']
        if mar_ratio >= 0.90 and oos_ok:
            pareto_ok.append((k, v, mar_ratio))
    pareto_ok.sort(key=lambda x: (x[1]['MAR'], x[1]['OOS_meanR']), reverse=True)

    if not pareto_ok:
        print('  NONE — baseline is on the Pareto frontier.')
    else:
        for k, v, ratio in pareto_ok[:10]:
            mar_lift = (v['MAR'] - base['MAR']) / base['MAR'] * 100
            oos_lift = v['OOS_meanR'] - base['OOS_meanR']
            print(f'    {k:<16s} MAR={v["MAR"]:.2f} ({mar_lift:+.0f}%)  '
                   f'OOS={v["OOS_meanR"]:+.3f} ({oos_lift:+.3f})  '
                   f'n={v["n"]} (L={v["n_long"]}/S={v["n_short"]})')

    # Stability check: does the BEST variant for each family beat baseline?
    print('\n  Best per family (full-sample MAR):')
    for prefix in ('fixed', 'pct', 'z'):
        family = [(k, v) for k, v in results.items() if k.startswith(prefix)]
        if not family:
            continue
        family.sort(key=lambda x: x[1].get('MAR', 0), reverse=True)
        best_k, best_v = family[0]
        print(f'    {prefix}: best={best_k}  MAR={best_v["MAR"]:.2f}  '
               f'annual={best_v["annual_R"]:+.1f}R  '
               f'maxDD={best_v["max_dd_R"]:+.2f}  '
               f'OOS={best_v["OOS_meanR"]:+.3f}(n={best_v["OOS_n"]})')

    # ─── Robustness: does a single threshold/lookback dominate? ────────────
    print('\n  Threshold stability within percentile family (1y/2y/3y at same %):')
    for thresh_pct in (80, 85, 90, 95):
        line = [f'    pct_{thresh_pct}: ']
        for lb in (1, 2, 3):
            k = f'pct_{lb}y_{thresh_pct:02d}'
            v = results.get(k, {})
            if v.get('n', 0) > 0:
                line.append(f'  {lb}y MAR={v["MAR"]:.2f} OOS={v["OOS_meanR"]:+.2f}')
        print(''.join(line))

    print('\n  Threshold stability within z-score family (1y/2y/3y at same σ):')
    for thresh_z in (10, 15, 20):
        line = [f'    z_{thresh_z/10:.1f}σ: ']
        for lb in (1, 2, 3):
            k = f'z_{lb}y_{thresh_z:02d}'
            v = results.get(k, {})
            if v.get('n', 0) > 0:
                line.append(f'  {lb}y MAR={v["MAR"]:.2f} OOS={v["OOS_meanR"]:+.2f}')
        print(''.join(line))

    # ─── Write ─────────────────────────────────────────────────────────────
    out_path = OUT_DIR / 'regime_threshold_ablation.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Threshold ablation on TRIPLE_V3 regime filter: tests '
                     'whether dynamic normalization (rolling percentile, '
                     'rolling z-score) outperforms the +10% fixed threshold. '
                     'All variants apply to SHORTS only. atr_mult=5, target_R=6, '
                     'TIF=72h. Backward-only triggers, production filters 1-3 '
                     'applied. IS_END=2024-12-31.'),
            'config': {
                'atr_mult': ATR_MULT, 'target_r': TARGET_R,
                'tif_hours': TIF_HOURS, 'bootstrap_n': BOOTSTRAP_N,
                'IS_END': str(IS_END),
            },
            'baseline_label': 'fixed_10',
            'results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
