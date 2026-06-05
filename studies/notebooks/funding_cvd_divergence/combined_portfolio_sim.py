"""Combined-portfolio simulation: CHENTO_TRIPLE_V3 + funding+CVD divergence.

Answers the question: does adding funding+CVD to a portfolio that already
runs TRIPLE_V3 increase total profit, given that the two strategies have
opposite regime sensitivities (bear-favored vs bull-favored)?

Method:
  1. Generate TRIPLE_V3 trade ledger using validation_p4b's
     production-equivalent stack (atr5_t6R, no ladder, no_tilt +
     no_resist_OB + okx_aligned + skip_up_30d_shorts regime filter,
     TIF=72h, cost 18bp, backward-only intersect_triggers).
  2. Generate funding+CVD trade ledger using the Phase 1 winning combo
     (long-only, funding_z<-2.0, cvd_z>+0.5, sustain 4 bars, cd 24h).
  3. Concatenate, sort by ts, compute combined cumulative R / max DD /
     MAR. Also report:
       - per-month overlap (months where both sleeves trade)
       - regime correlation (concurrent open positions)
       - what max DD would be if perfectly correlated (sum) vs
         perfectly diversified (max of individuals)

Same window: 2021-01-29 (TRIPLE_V3 start) -> 2026-04-13 (funding+CVD
pre-cutover end). Trades outside the overlap window are dropped per
sleeve for apples-to-apples.
"""
from __future__ import annotations

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

# ─── TRIPLE_V3 ledger ──────────────────────────────────────────────────────


def get_triple_v3_ledger() -> pd.DataFrame:
    """Replicate P4b's atr5_t6R post-filter (with regime) production-equivalent
    trade ledger using backward-only intersect_triggers."""
    # Patch intersect_triggers to backward-only (same as P4b)
    import studies.notebooks.chento_journal.validation_B_composite as src_comp
    import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3

    def intersect_backward(a, b, *, window_hours=24.0):
        if a.empty or b.empty:
            return pd.DataFrame(columns=a.columns)
        keep = []
        for i, ra in a.iterrows():
            bs = b[b['direction'] == ra['direction']]
            if bs.empty:
                continue
            delta_h = (bs['ts'] - ra['ts']).dt.total_seconds() / 3600.0
            if ((delta_h >= -window_hours) & (delta_h <= 0)).any():
                keep.append(i)
        return a.loc[keep].copy()

    src_comp.intersect_triggers = intersect_backward
    src_p3.intersect_triggers = intersect_backward

    from studies.notebooks.chento_journal.validation_group_A_tuning import (
        build_optimized_triggers, apply_filters, replay_with_extras,
    )
    from studies.notebooks.chento_journal.validation_C5_smc_features import features_at
    from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import load_btc_15m

    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()

    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_extras(t, df_smc, df_atr, fvgs, obs,
                                atr_mult=5.0, target_r=6.0,
                                tif_bars=72 * 4,
                                partial_at_R=None, partial_frac=0.0,
                                ladder_at_minus_R=None, ladder_size_frac=0.0)
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return rep

    # Attach dist_resist_OB_R for filter
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    rep = apply_filters(rep, delta_df)

    # Apply regime filter: skip shorts in up_30d (ret_30d > +10%)
    df_15m_full = load_btc_15m()
    daily_close = df_15m_full['close'].resample('1D').last()
    ret_30d_daily = daily_close.pct_change(30)
    ret_30d_15m = ret_30d_daily.reindex(df_15m_full.index, method='ffill')
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = ret_30d_15m.index.searchsorted(ts_idx, side='right') - 1
    rep = rep.copy()
    rep['ret_30d'] = [float(ret_30d_15m.iloc[i]) if 0 <= i < len(ret_30d_15m)
                       else np.nan for i in ix]
    drop_mask = ((rep['direction'] == 'short') & (rep['ret_30d'] > 0.10))
    rep = rep[~drop_mask].copy()
    rep['sleeve'] = 'TRIPLE_V3'
    return rep[['ts', 'direction', 'r_outcome', 'sleeve']].sort_values('ts').reset_index(drop=True)


# ─── funding+CVD ledger ────────────────────────────────────────────────────


def get_funding_cvd_ledger() -> pd.DataFrame:
    from studies.notebooks.funding_cvd_divergence.phase2_robustness import (
        get_winning_ledger,
    )
    rep = get_winning_ledger()
    rep = rep.copy()
    rep['sleeve'] = 'FUNDING_CVD'
    return rep[['ts', 'direction', 'r_outcome', 'sleeve']].sort_values('ts').reset_index(drop=True)


# ─── Stats ─────────────────────────────────────────────────────────────────


def compute_metrics(rep: pd.DataFrame, label: str) -> dict:
    if rep.empty:
        return {'label': label, 'n': 0}
    cum = rep['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                / (365.25 * 86400))
    annual_R = float(rep['r_outcome'].mean()) * len(rep) / max(span_y, 0.05)
    return {
        'label': label, 'n': int(len(rep)),
        'mean_R': round(float(rep['r_outcome'].mean()), 3),
        'sum_R': round(float(rep['r_outcome'].sum()), 2),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 2),
        'MAR': round(annual_R / abs(float(dd.min())), 2) if dd.min() < 0 else 0,
        'span_y': round(span_y, 2),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<32s} empty'); return
    print(f'  {s["label"]:<32s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
          f'sumR={s["sum_R"]:+7.2f}  WR={s["WR"]:.0%}  '
          f'maxDD={s["maxDD"]:+6.2f}  annual={s["annual_R"]:+6.2f}R  '
          f'MAR={s["MAR"]:>5.2f}')


def overlap_analysis(rep_tv3: pd.DataFrame, rep_fcd: pd.DataFrame, *,
                      open_window_hours: int = 72):
    """For each funding+CVD trade, check if a TRIPLE_V3 trade was OPEN
    during the same period (entry within open_window_hours of either side)."""
    print('\n=== TIMING OVERLAP CHECK ===')
    # Same-day overlap
    tv3_dates = set(rep_tv3['ts'].dt.date)
    fcd_dates = set(rep_fcd['ts'].dt.date)
    common_days = tv3_dates & fcd_dates
    print(f'  TRIPLE_V3 trade-days: {len(tv3_dates)}, '
           f'funding+CVD trade-days: {len(fcd_dates)}')
    print(f'  Same-day overlap: {len(common_days)} days')
    print(f'  funding+CVD trades on a TRIPLE_V3-day: '
           f'{rep_fcd["ts"].dt.date.isin(common_days).sum()} / {len(rep_fcd)}')

    # Concurrent-open window: funding+CVD entry within ±72h of TRIPLE_V3 entry
    n_concurrent = 0
    closest_gaps_h = []
    for ts in rep_fcd['ts']:
        deltas_h = (rep_tv3['ts'] - ts).dt.total_seconds().abs() / 3600.0
        min_gap = deltas_h.min() if len(deltas_h) > 0 else np.inf
        closest_gaps_h.append(min_gap)
        if min_gap <= open_window_hours:
            n_concurrent += 1
    print(f'  funding+CVD trades within {open_window_hours}h of any TRIPLE_V3 entry: '
           f'{n_concurrent} / {len(rep_fcd)}')
    print(f'  Closest-gap distribution (hours):')
    gaps = np.array(closest_gaps_h)
    print(f'    median: {np.median(gaps):.0f}h  '
           f'p25: {np.percentile(gaps, 25):.0f}h  '
           f'p75: {np.percentile(gaps, 75):.0f}h  '
           f'max: {gaps.max():.0f}h')


def correlation_sleeves(rep_tv3, rep_fcd):
    """Per-month R-aggregation correlation."""
    print('\n=== PER-MONTH R CORRELATION ===')
    tv3_m = rep_tv3.set_index('ts')['r_outcome'].resample('1ME').sum()
    fcd_m = rep_fcd.set_index('ts')['r_outcome'].resample('1ME').sum()
    aligned = pd.concat([tv3_m.rename('tv3'), fcd_m.rename('fcd')], axis=1).fillna(0)
    # Filter to months where at least one fired
    active = aligned[(aligned['tv3'] != 0) | (aligned['fcd'] != 0)]
    print(f'  Active months (>=1 sleeve fired): {len(active)}')
    if len(active) >= 3:
        corr = active['tv3'].corr(active['fcd'])
        print(f'  Pearson r (TRIPLE_V3 monthly R vs funding+CVD monthly R): {corr:+.3f}')
        if corr < -0.3:
            print(f'  -> STRONG NEGATIVE correlation: diversifying pair (as predicted)')
        elif corr < 0:
            print(f'  -> Mild negative correlation: weakly diversifying')
        elif corr < 0.3:
            print(f'  -> Near-zero correlation: orthogonal')
        else:
            print(f'  -> Positive correlation: redundant')


def main():
    print('Generating TRIPLE_V3 ledger (post-filter atr5_t6R no-ladder)...')
    rep_tv3 = get_triple_v3_ledger()
    print(f'  TRIPLE_V3 trades: {len(rep_tv3)}')
    print(f'  span: {rep_tv3["ts"].min()} -> {rep_tv3["ts"].max()}')

    print('\nGenerating funding+CVD ledger (winning combo)...')
    rep_fcd = get_funding_cvd_ledger()
    print(f'  funding+CVD trades: {len(rep_fcd)}')
    print(f'  span: {rep_fcd["ts"].min()} -> {rep_fcd["ts"].max()}')

    # Restrict both to the overlap window for apples-to-apples
    window_start = max(rep_tv3['ts'].min(), rep_fcd['ts'].min())
    window_end = min(rep_tv3['ts'].max(), rep_fcd['ts'].max())
    print(f'\nOverlap window: {window_start} -> {window_end}')
    rep_tv3_w = rep_tv3[(rep_tv3['ts'] >= window_start) & (rep_tv3['ts'] <= window_end)].copy()
    rep_fcd_w = rep_fcd[(rep_fcd['ts'] >= window_start) & (rep_fcd['ts'] <= window_end)].copy()

    print('\n' + '=' * 95)
    print('=== STANDALONE METRICS (overlap window) ===')
    print('=' * 95)
    m_tv3 = compute_metrics(rep_tv3_w, 'TRIPLE_V3 alone')
    m_fcd = compute_metrics(rep_fcd_w, 'funding+CVD alone')
    show(m_tv3); show(m_fcd)

    # Combined: chronologically sort all trades
    combined = pd.concat([rep_tv3_w, rep_fcd_w], ignore_index=True)
    combined = combined.sort_values('ts').reset_index(drop=True)
    m_combined = compute_metrics(combined, 'COMBINED portfolio')

    print('\n' + '=' * 95)
    print('=== COMBINED PORTFOLIO (chronologically interleaved trades) ===')
    print('=' * 95)
    show(m_combined)

    # Theoretical bounds
    sum_annual_R = m_tv3['annual_R'] + m_fcd['annual_R']
    sum_maxDD = m_tv3['maxDD'] + m_fcd['maxDD']
    max_individual_DD = min(m_tv3['maxDD'], m_fcd['maxDD'])  # most-negative
    print(f'\n  Theoretical bounds:')
    print(f'    annual_R sum (independent): {sum_annual_R:+.2f}R   '
           f'realized combined: {m_combined["annual_R"]:+.2f}R')
    print(f'    maxDD if perfectly CORRELATED (sum): {sum_maxDD:+.2f}R')
    print(f'    maxDD if perfectly DIVERSIFIED (max of individuals): {max_individual_DD:+.2f}R')
    print(f'    REALIZED combined maxDD: {m_combined["maxDD"]:+.2f}R')

    overlap_analysis(rep_tv3_w, rep_fcd_w)
    correlation_sleeves(rep_tv3_w, rep_fcd_w)

    # Verdict
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    mar_delta = m_combined['MAR'] - m_tv3['MAR']
    ann_delta = m_combined['annual_R'] - m_tv3['annual_R']
    print(f'\n  vs TRIPLE_V3 standalone:')
    print(f'    annual R: {m_tv3["annual_R"]:+.2f}R -> {m_combined["annual_R"]:+.2f}R  '
           f'(delta {ann_delta:+.2f}R/yr)')
    print(f'    maxDD: {m_tv3["maxDD"]:+.2f}R -> {m_combined["maxDD"]:+.2f}R')
    print(f'    MAR: {m_tv3["MAR"]:.2f} -> {m_combined["MAR"]:.2f}  (delta {mar_delta:+.2f})')
    if ann_delta > 0 and m_combined['MAR'] >= m_tv3['MAR']:
        print(f'\n  CONCLUSION: ADDING funding+CVD strictly Pareto-DOMINATES TRIPLE_V3 alone.')
    elif ann_delta > 0 and m_combined['MAR'] < m_tv3['MAR']:
        print(f'\n  CONCLUSION: ADDING funding+CVD INCREASES absolute return but '
              f'DECREASES MAR. Trade-off: more raw $ at worse risk-adjusted ratio.')
    elif ann_delta <= 0:
        print(f'\n  CONCLUSION: ADDING funding+CVD does NOT increase return — '
              f'either the trades overlap negatively or the size hurts.')


if __name__ == '__main__':
    main()
