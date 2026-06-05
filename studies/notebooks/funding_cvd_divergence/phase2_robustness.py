"""Phase 2 robustness check for funding+CVD divergence:
  1. Decay analysis on the 21-trade ledger (is the recent 3-loss cluster
     genuine signal decay, or noise within sample variance?)
  2. Correlation with CHENTO_TRIPLE_V3 timing (is this a portfolio
     diversifier or a duplicate of an existing edge?)

Multi-asset (ETH/SOL/OP) validation deferred to Phase 3 — requires funding
rate ingestion per asset which the cd_funding_rate table doesn't cover.

Usage:
  python -m studies.notebooks.funding_cvd_divergence.phase2_robustness
"""
from __future__ import annotations

import json
import sqlite3
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
DB = ROOT / 'data' / 'databases' / 'prod.db'
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.funding_cvd_divergence.research import (
    load_btc_15m, load_funding, attach_features,
    generate_triggers, replay_trigger,
)


WINNING = dict(funding_z_threshold=2.0, cvd_z_threshold=0.5,
               cvd_sustain_bars=4, cooldown_bars=96,
               z_window_days=14)


def get_winning_ledger(start='2020-01-01', end='2026-04-13') -> pd.DataFrame:
    start_ts = int(pd.Timestamp(start, tz='UTC').timestamp())
    end_ts = int(pd.Timestamp(end, tz='UTC').timestamp())
    df_15m = load_btc_15m(start_ts, end_ts)
    f_h = load_funding(start_ts, end_ts)
    dff = attach_features(df_15m, f_h, WINNING['z_window_days'] * 96)
    idxs = generate_triggers(dff, direction='long',
                              funding_z_threshold=WINNING['funding_z_threshold'],
                              cvd_z_threshold=WINNING['cvd_z_threshold'],
                              cvd_sustain_bars=WINNING['cvd_sustain_bars'],
                              cooldown_bars=WINNING['cooldown_bars'])
    rows = []
    for i in idxs:
        r = replay_trigger(dff, i, direction='long',
                            atr_mult=5.0, target_R=6.0, tif_bars=72 * 4)
        if r:
            rows.append(r)
    return pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)


def decay_analysis(rep: pd.DataFrame) -> dict:
    print('\n' + '=' * 90)
    print('=== DECAY ANALYSIS ===')
    print('=' * 90)
    r = rep['r_outcome'].values
    n = len(r)
    print(f'  Total trades: {n}')

    # Split-half test
    mid = n // 2
    first_half = r[:mid]; second_half = r[mid:]
    print(f'\n  Split-half (first {len(first_half)} vs last {len(second_half)}):')
    print(f'    First half mean R: {first_half.mean():+.3f}')
    print(f'    Second half mean R: {second_half.mean():+.3f}')

    # Last-N analysis
    for last_n in (3, 5, 7, 10):
        if last_n > n:
            continue
        recent = r[-last_n:]
        print(f'    Last {last_n} mean R: {recent.mean():+.3f}  '
              f'(WR={(recent > 0).mean():.0%})')

    # Per-year split
    print(f'\n  Per-year breakdown:')
    rep_with_yr = rep.copy()
    rep_with_yr['year'] = rep['ts'].dt.year
    for yr, group in rep_with_yr.groupby('year'):
        print(f'    {yr}: n={len(group):>2d}  meanR={group["r_outcome"].mean():+.3f}  '
              f'WR={(group["r_outcome"] > 0).mean():.0%}')

    # Rolling 5-trade mean R
    print(f'\n  Rolling 5-trade mean R (smoothed):')
    rolling5 = rep['r_outcome'].rolling(5, min_periods=5).mean()
    for i in range(4, len(rep)):
        print(f'    after trade {i+1} ({rep["ts"].iloc[i].date()}): '
              f'rolling5 = {rolling5.iloc[i]:+.3f}')

    # Bayesian: posterior on mean given recent data
    # Prior: mean +1.09, sd from first 18 trades
    prior_n = max(1, n - 3)
    prior_mu = r[:prior_n].mean()
    prior_sd = r[:prior_n].std()
    recent3 = r[-3:].mean()
    pooled_sd = r.std()
    # Two-sample z-test: is recent significantly different from prior?
    se_recent = pooled_sd / np.sqrt(3)
    z = (recent3 - prior_mu) / se_recent
    print(f'\n  Bayesian recent-3 vs prior-{prior_n} test:')
    print(f'    Prior n={prior_n} mean: {prior_mu:+.3f}, sd: {prior_sd:.3f}')
    print(f'    Recent 3 mean: {recent3:+.3f}')
    print(f'    Z-score of recent: {z:+.3f}  (|z|>1.96 = significant 5%, |z|>2.58 = 1%)')

    # Verdict
    decay_flags = []
    if second_half.mean() < first_half.mean() * 0.5:
        decay_flags.append('second_half_meanR_below_half_of_first_half')
    if recent3 < 0 and prior_mu > 0:
        decay_flags.append('recent_3_negative_vs_positive_prior')
    if abs(z) > 1.96:
        decay_flags.append('recent_3_significantly_different_from_prior')
    print(f'\n  Decay flags raised: {decay_flags if decay_flags else "NONE"}')
    return {
        'first_half_meanR': float(first_half.mean()),
        'second_half_meanR': float(second_half.mean()),
        'recent_3_meanR': float(recent3),
        'prior_meanR': float(prior_mu),
        'recent_vs_prior_z': float(z),
        'decay_flags': decay_flags,
    }


def load_triple_v3_research_trades(start='2020-01-01', end='2026-04-13') -> pd.DataFrame | None:
    """Load TRIPLE_V3 backward-only validation trades from the most recent
    P4b run output if available."""
    p = OUT_DIR / 'p4b_regime_atr_extended_results.json'
    if not p.exists():
        return None
    # The P4b output stores summary stats per variant, not the per-trade
    # ledger. To get ledger we'd need to re-run validation_p4b on the same
    # window. For correlation analysis we can do something cheaper:
    # use the funding+CVD trade dates and ask if any chento research
    # validation ran on a window that includes that date.
    return None


def correlation_with_triple_v3(rep_fcd: pd.DataFrame) -> dict:
    """Quick correlation check: TRIPLE_V3 fires at chento mean-reversion
    confluences. Funding+CVD fires at funding-positioning extremes. To
    approximate correlation without re-running the full backtest, we
    examine the time-distribution of trades and check for clustering."""
    print('\n' + '=' * 90)
    print('=== CORRELATION / DIVERSIFICATION CHECK (timing distribution) ===')
    print('=' * 90)

    # Simple proxy: are funding+CVD trades clustered in specific market regimes
    # that TRIPLE_V3 might also fire in? Use BTC monthly return as a proxy.
    con = sqlite3.connect(str(DB))
    df_btc = pd.read_sql("""
        SELECT timestamp, close FROM cd_futures_15m
        ORDER BY timestamp
    """, con)
    con.close()
    df_btc['ts'] = pd.to_datetime(df_btc['timestamp'], unit='s', utc=True)
    df_btc = df_btc.set_index('ts')['close']

    # Per-trade context: BTC trend over prior 30d (proxy for regime)
    fcd_dates = pd.DatetimeIndex(rep_fcd['ts'])
    print(f'\n  Funding+CVD trade dates: {fcd_dates.min().date()} -> {fcd_dates.max().date()}')

    contexts = []
    for ts in fcd_dates:
        close_at = df_btc.asof(ts)
        close_30d_ago = df_btc.asof(ts - pd.Timedelta(days=30))
        if close_30d_ago and close_30d_ago > 0:
            ret_30d = (close_at / close_30d_ago) - 1
        else:
            ret_30d = np.nan
        contexts.append(ret_30d)
    rep_fcd = rep_fcd.copy()
    rep_fcd['btc_ret_30d_at_entry'] = contexts

    # Regime classification
    bear_30d = (rep_fcd['btc_ret_30d_at_entry'] < -0.1)
    flat_30d = ((rep_fcd['btc_ret_30d_at_entry'] >= -0.1)
                & (rep_fcd['btc_ret_30d_at_entry'] <= 0.1))
    bull_30d = (rep_fcd['btc_ret_30d_at_entry'] > 0.1)
    print(f'\n  Regime distribution of funding+CVD trades:')
    print(f'    bear_30d (ret_30d < -10%): n={bear_30d.sum():>2d}  '
          f'meanR={rep_fcd.loc[bear_30d, "r_outcome"].mean():+.3f}')
    print(f'    flat_30d (-10% < ret_30d < +10%): n={flat_30d.sum():>2d}  '
          f'meanR={rep_fcd.loc[flat_30d, "r_outcome"].mean():+.3f}')
    print(f'    bull_30d (ret_30d > +10%): n={bull_30d.sum():>2d}  '
          f'meanR={rep_fcd.loc[bull_30d, "r_outcome"].mean():+.3f}')

    # Compare to TRIPLE_V3 regime memory: bear/high-vol/trending wins,
    # up_30d (>+10%) loses. If funding+CVD wins in same regimes,
    # they're correlated; if different, diversifying.
    print(f'\n  Per [[chento-regime-filter]]: TRIPLE_V3 wins in bear (mean +4.18R) '
           f'and high-vol; LOSES in up_30d (mean +1.97R).')
    fcd_bear_mean = rep_fcd.loc[bear_30d, 'r_outcome'].mean() if bear_30d.sum() > 0 else 0
    fcd_bull_mean = rep_fcd.loc[bull_30d, 'r_outcome'].mean() if bull_30d.sum() > 0 else 0
    print(f'  Funding+CVD per regime: bear={fcd_bear_mean:+.3f}, '
          f'bull={fcd_bull_mean:+.3f}')

    # If funding+CVD ALSO wins more in bear vs bull, they're correlated.
    # If funding+CVD wins MORE in BULL (opposite of TRIPLE_V3), they're diversifying.
    if fcd_bear_mean > 0 and fcd_bull_mean > 0:
        delta = fcd_bear_mean - fcd_bull_mean
        if delta > 0.5:
            verdict = 'Funding+CVD also bear-favored — likely CORRELATED with TRIPLE_V3'
        elif delta < -0.5:
            verdict = 'Funding+CVD bull-favored — DIVERSIFYING vs TRIPLE_V3'
        else:
            verdict = 'Funding+CVD regime-neutral — partially diversifying'
    else:
        verdict = 'Insufficient regime overlap to classify'
    print(f'\n  Verdict: {verdict}')

    # Per-trade chronology — are funding+CVD entries near known TRIPLE_V3 windows?
    # The chento research's validation_adaptive_hybrid_backonly produced ~41 BTC
    # trades over 2021-2026. Spread evenly that's ~7/year. Funding+CVD has ~5/year.
    # If their dates are independent, the expected overlap (within 7 days) is small.
    return {
        'bear_30d_n': int(bear_30d.sum()),
        'bear_30d_meanR': float(fcd_bear_mean) if bear_30d.sum() > 0 else None,
        'bull_30d_n': int(bull_30d.sum()),
        'bull_30d_meanR': float(fcd_bull_mean) if bull_30d.sum() > 0 else None,
        'verdict': verdict,
    }


def main():
    print('Loading winning combo trade ledger...')
    rep = get_winning_ledger()
    print(f'  Confirmed n={len(rep)}, meanR={rep["r_outcome"].mean():+.3f}')

    decay = decay_analysis(rep)
    correlation = correlation_with_triple_v3(rep)

    out_path = OUT_DIR / 'funding_cvd_phase2_robustness.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'winning_combo': WINNING,
            'n_trades': len(rep),
            'mean_R': float(rep['r_outcome'].mean()),
            'decay': decay,
            'correlation_proxy': correlation,
            'ledger': [
                {'ts': str(r['ts']), 'r_outcome': float(r['r_outcome']),
                 'exit_kind': r['exit_kind'], 'funding_z': float(r['funding_z']),
                 'cvd_z': float(r['cvd_z'])}
                for _, r in rep.iterrows()
            ],
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
