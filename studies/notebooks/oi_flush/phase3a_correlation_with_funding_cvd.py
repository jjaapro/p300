"""Phase 3a of the OI flush study: correlation with funding+CVD divergence.

Both sleeves are bull-favored (crypto squeeze-rebound asymmetry):
  - funding+CVD: positioning-extreme squeeze (deep negative funding +
    spot buying agreement)
  - OI flush: capitulation bounce (rapid OI drop + price drop in bull regime)

Question: do they fire on the SAME days (redundant) or DIFFERENT days
(complementary)?

Method:
  1. Generate OI flush ledger (Phase 2 best combo: s2.0_t3.0_tif48h, full
     pooled — regime-gating analysis stays separate).
  2. Generate funding+CVD ledger (Phase 1 winning combo from existing helper).
  3. Restrict both to overlap window.
  4. Measure timing overlap + monthly P&L Pearson correlation.
  5. Combined portfolio metrics: do they sum cleanly, or correlate?

Decision: if monthly Pearson r < 0.3, ship both as portfolio diversifiers.
If r > 0.5, ship only the higher-MAR one (OI flush at 1.51 vs funding+CVD
at 1.34).
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


# ─── OI flush ledger generator ─────────────────────────────────────────────


def get_oi_flush_ledger() -> pd.DataFrame:
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
    # Phase 2 best combo: stop=2%, target=3%, TIF=48h
    for i in event_idxs:
        r = replay(highs, lows, closes,
                    entry_idx=i, stop_pct=0.02, target_pct=0.03, tif_bars=48)
        if r is None:
            continue
        regime = ('bear_30d' if ret30[i] < -0.10
                    else 'bull_30d' if ret30[i] > 0.10
                    else 'flat_30d')
        rows.append({
            'ts': ts_arr[i], 'direction': 'long',
            'r_outcome': r['r_outcome'],
            'regime': regime, 'sleeve': 'OI_FLUSH',
        })
    return pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)


# ─── funding+CVD ledger ─────────────────────────────────────────────────────


def get_funding_cvd_ledger() -> pd.DataFrame:
    from studies.notebooks.funding_cvd_divergence.phase2_robustness import (
        get_winning_ledger,
    )
    rep = get_winning_ledger().copy()
    rep['sleeve'] = 'FUNDING_CVD'
    return rep[['ts', 'direction', 'r_outcome', 'sleeve']].sort_values('ts').reset_index(drop=True)


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
        'sumR': round(float(rep['r_outcome'].sum()), 2),
        'WR': round(float((rep['r_outcome'] > 0).mean()), 3),
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


def main():
    print('Generating OI flush ledger (Phase 2 best combo: s2.0/t3.0/tif48h)...')
    rep_oi = get_oi_flush_ledger()
    print(f'  OI flush trades: {len(rep_oi)}')

    print('\nGenerating funding+CVD ledger (winning combo)...')
    rep_fcd = get_funding_cvd_ledger()
    print(f'  funding+CVD trades: {len(rep_fcd)}')

    # Restrict both to overlap window
    window_start = max(rep_oi['ts'].min(), rep_fcd['ts'].min())
    window_end = min(rep_oi['ts'].max(), rep_fcd['ts'].max())
    print(f'\nOverlap window: {window_start} -> {window_end}')
    rep_oi_w = rep_oi[(rep_oi['ts'] >= window_start) & (rep_oi['ts'] <= window_end)].copy()
    rep_fcd_w = rep_fcd[(rep_fcd['ts'] >= window_start) & (rep_fcd['ts'] <= window_end)].copy()

    print('\n' + '=' * 95)
    print('=== STANDALONE METRICS (overlap window) ===')
    print('=' * 95)
    show(compute_metrics(rep_oi_w, 'OI flush alone'))
    show(compute_metrics(rep_fcd_w, 'funding+CVD alone'))

    # Regime split for OI flush (bull-gated is the actual edge)
    print('\n=== OI flush regime breakdown ===')
    for regime in ('bear_30d', 'flat_30d', 'bull_30d'):
        sub = rep_oi_w[rep_oi_w['regime'] == regime]
        show(compute_metrics(sub, f'OI flush ({regime})'))

    # Bull-gated combined
    rep_oi_bull = rep_oi_w[rep_oi_w['regime'] == 'bull_30d'].copy()
    print('\n=== Bull-gated OI flush + funding+CVD combined ===')
    combined = pd.concat([rep_oi_bull, rep_fcd_w], ignore_index=True)
    combined = combined.sort_values('ts').reset_index(drop=True)
    show(compute_metrics(combined, 'combined (bull-gated OI + fcd)'))

    # ─── Timing overlap ────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== TIMING OVERLAP ===')
    print('=' * 95)
    oi_dates = set(rep_oi_w['ts'].dt.date)
    fcd_dates = set(rep_fcd_w['ts'].dt.date)
    common = oi_dates & fcd_dates
    print(f'\n  OI flush trade-days: {len(oi_dates)}')
    print(f'  funding+CVD trade-days: {len(fcd_dates)}')
    print(f'  Same-day overlap: {len(common)} days')
    n_overlap = rep_fcd_w['ts'].dt.date.isin(common).sum()
    print(f'  funding+CVD trades on an OI flush day: {n_overlap} / {len(rep_fcd_w)}')

    # Within-window overlap
    for hours in (24, 72, 168):
        n_within = 0
        for ts in rep_fcd_w['ts']:
            deltas = (rep_oi_w['ts'] - ts).dt.total_seconds().abs() / 3600.0
            if (deltas <= hours).any():
                n_within += 1
        print(f'  funding+CVD trades within {hours}h of any OI flush entry: '
               f'{n_within} / {len(rep_fcd_w)}')

    # ─── Monthly correlation ───────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== MONTHLY P&L CORRELATION ===')
    print('=' * 95)
    oi_m = rep_oi_w.set_index('ts')['r_outcome'].resample('1ME').sum()
    fcd_m = rep_fcd_w.set_index('ts')['r_outcome'].resample('1ME').sum()
    aligned = pd.concat([oi_m.rename('oi'), fcd_m.rename('fcd')],
                          axis=1).fillna(0)
    active = aligned[(aligned['oi'] != 0) | (aligned['fcd'] != 0)]
    print(f'\n  Active months (>=1 sleeve fired): {len(active)}')
    if len(active) >= 5:
        corr = active['oi'].corr(active['fcd'])
        print(f'  Pearson r (OI monthly R vs funding+CVD monthly R): {corr:+.3f}')
        if corr < 0.3 and corr > -0.3:
            print(f'  -> NEAR-ZERO correlation: complementary diversifiers')
        elif corr < -0.3:
            print(f'  -> NEGATIVE correlation: strong diversification')
        elif corr > 0.5:
            print(f'  -> HIGH POSITIVE correlation: redundant')
        else:
            print(f'  -> Mild positive correlation: partial overlap')

    # Bull-only monthly correlation
    oi_bull_m = rep_oi_bull.set_index('ts')['r_outcome'].resample('1ME').sum()
    aligned_bull = pd.concat([oi_bull_m.rename('oi_bull'), fcd_m.rename('fcd')],
                                axis=1).fillna(0)
    active_bull = aligned_bull[(aligned_bull['oi_bull'] != 0)
                                  | (aligned_bull['fcd'] != 0)]
    if len(active_bull) >= 5:
        corr_bull = active_bull['oi_bull'].corr(active_bull['fcd'])
        print(f'\n  Same correlation but ONLY bull-gated OI flush: '
               f'{corr_bull:+.3f}')

    # ─── Verdict ──────────────────────────────────────────────────────
    print('\n' + '=' * 95)
    print('=== VERDICT ===')
    print('=' * 95)
    m_oi_bull = compute_metrics(rep_oi_bull, 'oi_bull')
    m_fcd = compute_metrics(rep_fcd_w, 'fcd')
    m_combined = compute_metrics(combined, 'combined')
    sum_annual = m_oi_bull['annual_R'] + m_fcd['annual_R']
    print(f'\n  Bull-gated OI flush annual: {m_oi_bull["annual_R"]:+.2f}R/yr')
    print(f'  funding+CVD annual: {m_fcd["annual_R"]:+.2f}R/yr')
    print(f'  Theoretical sum (if independent): {sum_annual:+.2f}R/yr')
    print(f'  Realized combined annual: {m_combined["annual_R"]:+.2f}R/yr')
    print(f'  Combined maxDD: {m_combined["maxDD"]:+.2f}R '
           f'(individual worst: {min(m_oi_bull["maxDD"], m_fcd["maxDD"]):+.2f}, '
           f'individual sum: {m_oi_bull["maxDD"] + m_fcd["maxDD"]:+.2f})')
    print(f'  Combined MAR: {m_combined["MAR"]:.2f}')


if __name__ == '__main__':
    main()
