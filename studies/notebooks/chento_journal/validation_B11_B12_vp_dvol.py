"""validation_B11_B12: volume-profile POC stickiness (B11) + DVOL regime (B12)
as FILTERS on the triple composite.

B11 chento context: heavy use of TradingView volume profile in v27 (purple/
green VP snapshots). Hypothesis: triggers fire when price is at the POC
(Point-of-Control = highest-volume price in a window) act differently than
triggers away from POC. Test by proximity to rolling 7d POC.

B12 chento context: implicit in his hedge-era trading style. Hypothesis:
high implied volatility (DVOL) periods favor different trades than low.
Bucket triggers by DVOL quartile.
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

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers


# === B11 volume profile POC =================================================

def compute_rolling_poc(df_15m: pd.DataFrame, *,
                          window_bars: int = 4 * 24 * 7,  # 7d
                          n_buckets: int = 50,
                          ) -> pd.Series:
    """Compute rolling POC (price-bucket with highest volume over the window).
    Approximation: at each bar t, look back `window_bars` bars, bin closes by
    volume-weighted price; POC = bucket with highest total volume.

    Returns Series of POC price per bar (NaN until warmup).
    """
    closes = df_15m['close'].values
    volumes = df_15m['volume'].values
    n = len(df_15m)
    poc = np.full(n, np.nan)
    for i in range(window_bars, n):
        win_close = closes[i - window_bars:i]
        win_vol = volumes[i - window_bars:i]
        lo, hi = win_close.min(), win_close.max()
        if hi <= lo: continue
        edges = np.linspace(lo, hi, n_buckets + 1)
        # Weighted histogram by volume
        hist, _ = np.histogram(win_close, bins=edges, weights=win_vol)
        if hist.sum() == 0: continue
        bucket = int(np.argmax(hist))
        poc[i] = (edges[bucket] + edges[bucket + 1]) / 2
    return pd.Series(poc, index=df_15m.index)


def annotate_with_poc_distance(triggers: pd.DataFrame,
                                 poc_series: pd.Series) -> pd.DataFrame:
    if triggers.empty:
        return triggers
    out = triggers.copy()
    ts_index = pd.DatetimeIndex(pd.to_datetime(out['ts'], utc=True))
    poc_at_ts = poc_series.reindex(ts_index, method='ffill')
    out['poc'] = poc_at_ts.values
    out['poc_dist_pct'] = (out['entry'] - out['poc']) / out['poc']  # signed
    out['poc_dist_abs_pct'] = out['poc_dist_pct'].abs()
    return out


# === B12 DVOL =================================================================

def load_dvol(asset: str = 'BTC') -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT timestamp, close FROM cd_dvol WHERE asset = ? "
                      "ORDER BY timestamp", con, params=(asset,))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df.set_index('ts').drop(columns='timestamp')


def annotate_with_dvol_quartile(triggers: pd.DataFrame,
                                  dvol: pd.DataFrame,
                                  *, lookback_days: int = 90) -> pd.DataFrame:
    if triggers.empty or dvol.empty:
        if not triggers.empty:
            triggers = triggers.copy()
            triggers['dvol'] = np.nan
            triggers['dvol_quartile'] = 'unknown'
        return triggers
    out = triggers.copy()
    ts_index = pd.DatetimeIndex(pd.to_datetime(out['ts'], utc=True))
    out['dvol'] = dvol['close'].reindex(ts_index, method='ffill').values
    # Quartile vs 90d rolling history
    dvol_rolling_p25 = dvol['close'].rolling(lookback_days, min_periods=20).quantile(0.25)
    dvol_rolling_p50 = dvol['close'].rolling(lookback_days, min_periods=20).quantile(0.50)
    dvol_rolling_p75 = dvol['close'].rolling(lookback_days, min_periods=20).quantile(0.75)
    p25 = dvol_rolling_p25.reindex(ts_index, method='ffill').values
    p50 = dvol_rolling_p50.reindex(ts_index, method='ffill').values
    p75 = dvol_rolling_p75.reindex(ts_index, method='ffill').values

    def qbucket(v, q25, q50, q75):
        if pd.isna(v) or pd.isna(q25): return 'unknown'
        if v < q25: return 'low_vol'
        if v < q50: return 'mid_low'
        if v < q75: return 'mid_high'
        return 'high_vol'
    out['dvol_quartile'] = [qbucket(v, q25, q50, q75)
                             for v, q25, q50, q75
                             in zip(out['dvol'], p25, p50, p75)]
    return out


def main():
    print('Building triple composite + measuring R...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    triple_r = measure_r_outcomes(triple, df)
    print(f'  triple: {len(triple_r)} triggers')

    base_s = summarize_triggers(triple_r, label='baseline_triple')
    print(f'  baseline: meanR={base_s["mean_R"]:+.3f} WR={base_s["win_rate"]:.0%}')

    # === B11 Volume profile POC =======================================
    print('\n=== B11 Volume profile POC stickiness ===')
    print('Computing 7d rolling POC...')
    poc = compute_rolling_poc(df)
    valid_poc = poc.dropna()
    print(f'  POC valid range: [{valid_poc.min():.0f}, {valid_poc.max():.0f}]  '
          f'({(~poc.isna()).sum()}/{len(poc)} bars)')

    tr_with_poc = annotate_with_poc_distance(triple_r, poc)
    # Bucket by abs distance from POC
    def poc_zone(d):
        if pd.isna(d): return 'unknown'
        if d < 0.005: return 'at_poc(<0.5%)'
        if d < 0.015: return 'near_poc(0.5-1.5%)'
        if d < 0.030: return 'mid(1.5-3%)'
        return 'far_poc(>3%)'
    tr_with_poc['poc_zone'] = tr_with_poc['poc_dist_abs_pct'].apply(poc_zone)
    for zone in ('at_poc(<0.5%)', 'near_poc(0.5-1.5%)', 'mid(1.5-3%)', 'far_poc(>3%)'):
        sub = tr_with_poc[tr_with_poc['poc_zone'] == zone]
        if sub.empty: continue
        s = summarize_triggers(sub, label=zone)
        print(f'  {zone:<22s}: n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}')
    # POC direction: entry above (long bias) or below (short bias) POC
    print('\n  By POC direction (entry vs POC):')
    for direction in ('long', 'short'):
        for sign_label, sign_mask in (('above_POC', tr_with_poc['poc_dist_pct'] > 0),
                                        ('below_POC', tr_with_poc['poc_dist_pct'] < 0)):
            sub = tr_with_poc[(tr_with_poc['direction'] == direction) & sign_mask]
            if sub.empty: continue
            s = summarize_triggers(sub, label=f'{direction}_{sign_label}')
            print(f'    {direction:<5s} {sign_label}: n={s["n"]:>4d} '
                   f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}')

    # === B12 DVOL =====================================================
    print('\n=== B12 DVOL regime quartile ===')
    dvol = load_dvol('BTC')
    print(f'  DVOL BTC: {len(dvol):,} rows  {dvol.index.min()} -> {dvol.index.max()}')
    tr_with_dvol = annotate_with_dvol_quartile(triple_r, dvol)
    for q in ('low_vol', 'mid_low', 'mid_high', 'high_vol', 'unknown'):
        sub = tr_with_dvol[tr_with_dvol['dvol_quartile'] == q]
        if sub.empty: continue
        s = summarize_triggers(sub, label=q)
        print(f'  {q:<10s}: n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} '
               f'WR={s["win_rate"]:.0%} ann={s["implied_annual_pct"]:+.1f}%')

    out_path = OUT_DIR / 'B11_B12_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': base_s,
            'note': 'B11 POC + B12 DVOL tested as filters on triple composite.',
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
