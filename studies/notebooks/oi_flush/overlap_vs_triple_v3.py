"""Timing overlap: TRIPLE_V3 (shipped) vs SQUEEZE (OI flush + funding+CVD).

Both sleeves are long-side bias in bull regimes. This script measures:
  1. How often they fire on the same day / within 24h / 72h / 168h
  2. Direction alignment (when both fire, same or opposite direction?)
  3. Monthly P&L correlation
  4. Combined portfolio metrics with all three sleeves

Decision impact: high overlap = need concurrent-position risk management
in orchestrator. Low overlap = sleeves can run independently.

Uses production-equivalent backward-only TRIPLE_V3 pipeline (atr5/t6R,
all filters incl. up_30d short skip). OI flush at the new -2% threshold
(bull-gated). Funding+CVD at the validated winning combo.
"""
from __future__ import annotations

import sys
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


# ─── Ledger generators ──────────────────────────────────────────────────────


def get_triple_v3_ledger() -> pd.DataFrame:
    """Production-equivalent TRIPLE_V3 ledger: backward-only triggers,
    atr_mult=5, target_r=6, TIF=72h, all filters (incl up_30d short skip)."""
    import studies.notebooks.chento_journal.validation_B_composite as src_comp
    import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3
    from studies.notebooks.chento_journal.validation_p4b_regime_atr_extended import (
        intersect_backward, compute_btc_ret_30d,
        replay_then_filter_with_regime,
    )
    from studies.notebooks.chento_journal.validation_group_A_tuning import (
        build_optimized_triggers,
    )
    from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
        load_btc_15m,
    )

    src_comp.intersect_triggers = intersect_backward
    src_p3.intersect_triggers = intersect_backward

    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()
    df_15m_full = load_btc_15m()
    ret_30d = compute_btc_ret_30d(df_15m_full)

    rep = replay_then_filter_with_regime(
        triple_w, df_smc, df_atr, fvgs, obs, delta_df, ret_30d,
        atr_mult=5.0, target_r=6.0, tif_hours=72,
    )
    rep['sleeve'] = 'TRIPLE_V3'
    return rep[['ts', 'direction', 'r_outcome', 'sleeve']].sort_values('ts').reset_index(drop=True)


def get_oi_flush_bull_ledger() -> pd.DataFrame:
    """OI flush bull-gated ledger (NEW -2% threshold)."""
    from studies.notebooks.oi_flush.phase2_backtest import (
        load_data, identify_long_flush_events, replay,
    )
    df = load_data()
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    ret30 = df['ret_30d'].values
    ts_arr = df.index

    event_idxs = identify_long_flush_events(df)
    rows = []
    for i in event_idxs:
        r = replay(highs, lows, closes,
                    entry_idx=i, stop_pct=0.02, target_pct=0.03, tif_bars=48)
        if r is None:
            continue
        if ret30[i] > 0.10:   # bull-gated only
            rows.append({
                'ts': ts_arr[i], 'direction': 'long',
                'r_outcome': r['r_outcome'], 'sleeve': 'OI_FLUSH_BULL',
            })
    return pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)


def get_funding_cvd_ledger() -> pd.DataFrame:
    from studies.notebooks.funding_cvd_divergence.phase2_robustness import (
        get_winning_ledger,
    )
    rep = get_winning_ledger().copy()
    rep['sleeve'] = 'FUNDING_CVD'
    return rep[['ts', 'direction', 'r_outcome', 'sleeve']].sort_values('ts').reset_index(drop=True)


# ─── Metrics ────────────────────────────────────────────────────────────────


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
        'meanR': round(float(rep['r_outcome'].mean()), 3),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
        'sumR': round(float(rep['r_outcome'].sum()), 2),
        'maxDD': round(float(dd.min()), 2),
        'annual_R': round(annual_R, 2),
        'MAR': round(annual_R / abs(float(dd.min())), 2) if dd.min() < 0 else 0,
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<35s} empty'); return
    print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["meanR"]:+.3f}  '
          f'sumR={s["sumR"]:>+7.2f}  WR={s["WR"]:.0%}  '
          f'maxDD={s["maxDD"]:>+6.2f}  annR={s["annual_R"]:>+6.2f}  '
          f'MAR={s["MAR"]:>5.2f}')


def overlap_analysis(rep_a: pd.DataFrame, rep_b: pd.DataFrame,
                       label_a: str, label_b: str):
    """Same-day + within-N-hours overlap between two sleeves."""
    a_dates = set(rep_a['ts'].dt.date)
    b_dates = set(rep_b['ts'].dt.date)
    common = a_dates & b_dates
    n_common_b = rep_b['ts'].dt.date.isin(common).sum()
    print(f'\n  {label_a} trade-days: {len(a_dates)}')
    print(f'  {label_b} trade-days: {len(b_dates)}')
    print(f'  Same-day overlap: {len(common)} days')
    print(f'  {label_b} trades on a {label_a} day: '
          f'{n_common_b} / {len(rep_b)}')

    for hours in (24, 72, 168):
        n_within = 0
        for ts in rep_b['ts']:
            deltas = (rep_a['ts'] - ts).dt.total_seconds().abs() / 3600.0
            if len(deltas) > 0 and (deltas <= hours).any():
                n_within += 1
        print(f'  {label_b} trades within {hours}h of any {label_a} entry: '
              f'{n_within} / {len(rep_b)}')


def direction_alignment(rep_a: pd.DataFrame, rep_b: pd.DataFrame,
                         hours_window: int, label_a: str, label_b: str):
    """When sleeves fire within `hours_window`, count same-direction vs
    opposite-direction occurrences."""
    same_dir = 0
    opp_dir = 0
    for _, b_row in rep_b.iterrows():
        deltas = (rep_a['ts'] - b_row['ts']).dt.total_seconds().abs() / 3600.0
        within_mask = deltas <= hours_window
        if not within_mask.any():
            continue
        a_dirs = rep_a.loc[within_mask, 'direction'].values
        for d in a_dirs:
            if d == b_row['direction']:
                same_dir += 1
            else:
                opp_dir += 1
    print(f'\n  Within {hours_window}h of {label_a} entry, {label_b} fires:')
    print(f'    same direction: {same_dir}')
    print(f'    opposite direction: {opp_dir}')


# ─── Main ──────────────────────────────────────────────────────────────────


def main():
    print('Generating TRIPLE_V3 production ledger...')
    rep_tv3 = get_triple_v3_ledger()
    print(f'  TRIPLE_V3 trades: {len(rep_tv3)}  '
           f'(longs={(rep_tv3["direction"]=="long").sum()}, '
           f'shorts={(rep_tv3["direction"]=="short").sum()})')

    print('\nGenerating OI flush bull-gated ledger (NEW -2% threshold)...')
    rep_oi = get_oi_flush_bull_ledger()
    print(f'  OI flush bull trades: {len(rep_oi)} (all longs)')

    print('\nGenerating funding+CVD ledger...')
    rep_fcd = get_funding_cvd_ledger()
    print(f'  funding+CVD trades: {len(rep_fcd)} (all longs)')

    # Restrict to overlap window: max of mins to min of maxes
    starts = [rep_tv3['ts'].min(), rep_oi['ts'].min(), rep_fcd['ts'].min()]
    ends = [rep_tv3['ts'].max(), rep_oi['ts'].max(), rep_fcd['ts'].max()]
    window_start = max(starts)
    window_end = min(ends)
    print(f'\n3-way overlap window: {window_start} -> {window_end}')

    def restrict(rep):
        return rep[(rep['ts'] >= window_start) & (rep['ts'] <= window_end)].copy()

    rep_tv3_w = restrict(rep_tv3)
    rep_oi_w = restrict(rep_oi)
    rep_fcd_w = restrict(rep_fcd)
    rep_squeeze_w = pd.concat([rep_oi_w, rep_fcd_w],
                                ignore_index=True).sort_values('ts').reset_index(drop=True)

    print('\n' + '=' * 95)
    print('=== STANDALONE METRICS (3-way overlap window) ===')
    print('=' * 95)
    show(compute_metrics(rep_tv3_w, 'TRIPLE_V3'))
    show(compute_metrics(rep_oi_w, 'OI flush bull-gated'))
    show(compute_metrics(rep_fcd_w, 'funding+CVD'))
    show(compute_metrics(rep_squeeze_w, 'SQUEEZE (OI bull + fCVD)'))

    # Combined: all three
    print('\n=== COMBINED: TRIPLE_V3 + SQUEEZE ===')
    rep_all = pd.concat([rep_tv3_w, rep_squeeze_w],
                          ignore_index=True).sort_values('ts').reset_index(drop=True)
    show(compute_metrics(rep_all, 'ALL THREE pooled'))

    # ─── Timing overlap ───────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== TIMING OVERLAP: TRIPLE_V3 vs SQUEEZE composite ===')
    print('=' * 95)
    overlap_analysis(rep_tv3_w, rep_squeeze_w, 'TRIPLE_V3', 'SQUEEZE')

    # Break down OI flush vs funding+CVD separately
    print('\n--- vs OI flush bull-gated alone ---')
    overlap_analysis(rep_tv3_w, rep_oi_w, 'TRIPLE_V3', 'OI_FLUSH_BULL')
    print('\n--- vs funding+CVD alone ---')
    overlap_analysis(rep_tv3_w, rep_fcd_w, 'TRIPLE_V3', 'FUNDING_CVD')

    # ─── Direction alignment ──────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== DIRECTION ALIGNMENT (overlapping trades) ===')
    print('=' * 95)
    for h in (24, 72):
        direction_alignment(rep_tv3_w, rep_squeeze_w, h,
                              'TRIPLE_V3', 'SQUEEZE')

    # ─── Monthly correlation ──────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== MONTHLY P&L CORRELATION ===')
    print('=' * 95)
    tv3_m = rep_tv3_w.set_index('ts')['r_outcome'].resample('1ME').sum()
    sq_m = rep_squeeze_w.set_index('ts')['r_outcome'].resample('1ME').sum()
    oi_m = rep_oi_w.set_index('ts')['r_outcome'].resample('1ME').sum()
    fcd_m = rep_fcd_w.set_index('ts')['r_outcome'].resample('1ME').sum()

    aligned = pd.concat([tv3_m.rename('tv3'), sq_m.rename('sq'),
                          oi_m.rename('oi'), fcd_m.rename('fcd')],
                          axis=1).fillna(0)
    active = aligned[(aligned['tv3'] != 0) | (aligned['sq'] != 0)]
    print(f'\n  Active months: {len(active)}')
    if len(active) >= 5:
        print(f'  Pearson r:')
        print(f'    TRIPLE_V3 vs SQUEEZE:        {active["tv3"].corr(active["sq"]):+.3f}')
        print(f'    TRIPLE_V3 vs OI bull alone:  {active["tv3"].corr(active["oi"]):+.3f}')
        print(f'    TRIPLE_V3 vs funding+CVD:    {active["tv3"].corr(active["fcd"]):+.3f}')
        print(f'    OI bull vs funding+CVD:      {active["oi"].corr(active["fcd"]):+.3f}')

    # ─── Verdict ──────────────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    m_tv3 = compute_metrics(rep_tv3_w, 'tv3')
    m_sq = compute_metrics(rep_squeeze_w, 'sq')
    m_all = compute_metrics(rep_all, 'all')
    sum_annual = m_tv3['annual_R'] + m_sq['annual_R']
    print(f'\n  TRIPLE_V3 annual: {m_tv3["annual_R"]:+.2f}R/yr (MAR {m_tv3["MAR"]:.2f})')
    print(f'  SQUEEZE annual:   {m_sq["annual_R"]:+.2f}R/yr (MAR {m_sq["MAR"]:.2f})')
    print(f'  Theoretical sum (if uncorrelated): {sum_annual:+.2f}R/yr')
    print(f'  Realized combined annual: {m_all["annual_R"]:+.2f}R/yr')
    print(f'  Combined maxDD: {m_all["maxDD"]:+.2f}R '
           f'(individual worst: {min(m_tv3["maxDD"], m_sq["maxDD"]):+.2f}, '
           f'individual sum: {m_tv3["maxDD"] + m_sq["maxDD"]:+.2f})')
    print(f'  Combined MAR: {m_all["MAR"]:.2f}')


if __name__ == '__main__':
    main()
