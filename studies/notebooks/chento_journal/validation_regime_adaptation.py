"""validation_regime_adaptation: classify market regime at each Triple
trigger and test whether expectancy varies meaningfully across regimes.
If so, test regime-gated or regime-sized variants of H_B.

Regime classifications attached at trigger time:

1. **Trend direction (HTF EMA)**:
   - bull  : price > 200d EMA AND 50d EMA slope up
   - bear  : price < 200d EMA AND 50d EMA slope down
   - chop  : otherwise

2. **ADX regime** (Wilder's ADX on 4h bars):
   - trending  : ADX > 25
   - transitioning: ADX 20–25
   - ranging   : ADX < 20

3. **Volatility regime** (14d realized vol percentile):
   - low_vol  : <p33
   - mid_vol  : p33–p66
   - high_vol : >p66

4. **30d return regime**:
   - up_30d, flat_30d, down_30d (based on percent return)

5. **DVOL (implied vol) regime** if available:
   - low_dvol, mid_dvol, high_dvol

Hypothesis: mean-reversion-into-extreme setups work better in chop/range
regimes (ADX < 20) and worse in strong trends. Expect moderate vol to be
better than extremes.
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
    load_btc_15m, compute_atr,
)
from studies.notebooks.chento_journal.validation_confidence_leverage import (
    build_h_b_with_features,
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def resample_to_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    out = df.resample(period).agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
        'volume': 'sum',
    }).dropna()
    return out


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX on the supplied OHLC."""
    h = df['high']; l = df['low']; c = df['close']
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    plus_dm = (h - h.shift(1)).where((h - h.shift(1)) > (l.shift(1) - l), 0).clip(lower=0)
    minus_dm = (l.shift(1) - l).where((l.shift(1) - l) > (h - h.shift(1)), 0).clip(lower=0)
    atr_w = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0, np.nan)
    mdi = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_w.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def compute_regimes(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Compute regime tags per 15m timestamp using HTF derivations."""
    out = df_15m.copy()
    # Daily resample for trend
    daily = resample_to_period(df_15m[['open', 'high', 'low', 'close', 'volume']], '1D')
    daily['ema50'] = daily['close'].ewm(span=50, adjust=False).mean()
    daily['ema200'] = daily['close'].ewm(span=200, adjust=False).mean()
    daily['ema50_slope'] = daily['ema50'].pct_change(20)
    daily['trend'] = 'chop'
    daily.loc[(daily['close'] > daily['ema200']) & (daily['ema50_slope'] > 0.005), 'trend'] = 'bull'
    daily.loc[(daily['close'] < daily['ema200']) & (daily['ema50_slope'] < -0.005), 'trend'] = 'bear'

    # Realized 14d vol (daily)
    daily['ret'] = daily['close'].pct_change()
    daily['rv14'] = daily['ret'].rolling(14).std() * np.sqrt(365)
    daily['rv14_p33'] = daily['rv14'].rolling(180, min_periods=30).quantile(0.33)
    daily['rv14_p66'] = daily['rv14'].rolling(180, min_periods=30).quantile(0.66)
    daily['vol_regime'] = 'mid_vol'
    daily.loc[daily['rv14'] < daily['rv14_p33'], 'vol_regime'] = 'low_vol'
    daily.loc[daily['rv14'] > daily['rv14_p66'], 'vol_regime'] = 'high_vol'

    # 30d return regime
    daily['ret30d'] = daily['close'].pct_change(30)
    daily['ret30d_regime'] = 'flat_30d'
    daily.loc[daily['ret30d'] > 0.10, 'ret30d_regime'] = 'up_30d'
    daily.loc[daily['ret30d'] < -0.10, 'ret30d_regime'] = 'down_30d'

    # 4h resample for ADX
    h4 = resample_to_period(df_15m[['open', 'high', 'low', 'close', 'volume']], '4h')
    h4['adx14'] = compute_adx(h4, period=14)
    h4['adx_regime'] = 'transitioning'
    h4.loc[h4['adx14'] < 20, 'adx_regime'] = 'ranging'
    h4.loc[h4['adx14'] > 25, 'adx_regime'] = 'trending'

    # Forward-fill onto 15m index
    out['trend'] = daily['trend'].reindex(out.index, method='ffill')
    out['vol_regime'] = daily['vol_regime'].reindex(out.index, method='ffill')
    out['ret30d_regime'] = daily['ret30d_regime'].reindex(out.index, method='ffill')
    out['ret30d'] = daily['ret30d'].reindex(out.index, method='ffill')
    out['rv14'] = daily['rv14'].reindex(out.index, method='ffill')
    out['adx14'] = h4['adx14'].reindex(out.index, method='ffill')
    out['adx_regime'] = h4['adx_regime'].reindex(out.index, method='ffill')

    return out[['trend', 'vol_regime', 'ret30d_regime', 'ret30d', 'rv14',
                  'adx14', 'adx_regime']]


def attach_regimes(rep: pd.DataFrame) -> pd.DataFrame:
    df_15m = load_btc_15m()
    regimes = compute_regimes(df_15m)
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = regimes.index.searchsorted(ts_idx, side='right') - 1
    out = rep.copy()
    for col in regimes.columns:
        out[col] = [regimes[col].iloc[i] if 0 <= i < len(regimes) else np.nan
                     for i in ix]
    return out


def regime_table(rep: pd.DataFrame, col: str) -> pd.DataFrame:
    g = rep.groupby(col, observed=True).agg(
        n=('r_outcome', 'count'),
        mean_R=('r_outcome', 'mean'),
        wr=('r_outcome', lambda x: (x > 0).mean()),
        std_R=('r_outcome', 'std'),
    ).reset_index()
    g['mean_R'] = g['mean_R'].round(3)
    g['wr'] = g['wr'].round(2)
    g['std_R'] = g['std_R'].round(3)
    return g.sort_values('mean_R')


def summary_simple(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum); dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'annual_R': round(float(cum[-1]) / max(span_y, 0.1), 1),
        'MAR': round((float(cum[-1]) / max(span_y, 0.1)) / abs(float(dd.min())), 2)
                 if dd.min() != 0 else 0,
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
    print('Building H_B trades with all features...')
    rep = build_h_b_with_features()
    print(f'  H_B trades: {len(rep)}')

    print('\nComputing regimes...')
    rep = attach_regimes(rep)

    # === Per-regime expectancy ===
    print('\n' + '=' * 80)
    print('=== Per-regime mean R / WR distribution ===')
    print('=' * 80)
    for regime_col in ('trend', 'vol_regime', 'ret30d_regime', 'adx_regime'):
        print(f'\n--- {regime_col} ---')
        print(regime_table(rep, regime_col).to_string(index=False))

    # === Combined (trend × adx_regime) ===
    print('\n--- Combined trend × adx_regime ---')
    rep['combined'] = rep['trend'].astype(str) + ' / ' + rep['adx_regime'].astype(str)
    g = rep.groupby('combined', observed=True).agg(
        n=('r_outcome', 'count'),
        mean_R=('r_outcome', 'mean'),
        wr=('r_outcome', lambda x: (x > 0).mean()),
    ).reset_index()
    g['mean_R'] = g['mean_R'].round(3); g['wr'] = g['wr'].round(2)
    print(g.sort_values('mean_R').to_string(index=False))

    # === Test regime-based filters ===
    print('\n' + '=' * 80)
    print('=== Regime-based filter tests (skip worst regimes) ===')
    print('=' * 80)
    show(summary_simple(rep, 'H_B baseline'))

    print('\n--- Skip individual regime cells (test each) ---')
    for col in ('trend', 'vol_regime', 'ret30d_regime', 'adx_regime'):
        for regime in rep[col].dropna().unique():
            kept = rep[rep[col] != regime]
            s = summary_simple(kept, f'skip {col}={regime}')
            show(s)

    # === Multi-skip configurations based on findings ===
    print('\n--- Skip combinations based on weakest regimes (top R per axis) ---')
    # Will be computed dynamically after looking at the per-axis worst-bucket
    worst_per_axis = {}
    for col in ('trend', 'vol_regime', 'ret30d_regime', 'adx_regime'):
        tab = regime_table(rep, col)
        if len(tab) > 0:
            worst_per_axis[col] = tab.iloc[0][col]   # smallest mean_R
    print(f'  Worst regime per axis: {worst_per_axis}')

    # Skip the worst in each axis combined
    mask = pd.Series([True] * len(rep), index=rep.index)
    for col, regime in worst_per_axis.items():
        mask &= rep[col] != regime
    show(summary_simple(rep[mask], f'skip ALL worst per axis ({worst_per_axis})'))

    # Skip just the worst-1 most impactful (we'll need to find that)
    impacts = {}
    for col in ('trend', 'vol_regime', 'ret30d_regime', 'adx_regime'):
        regime = worst_per_axis.get(col)
        if regime is None: continue
        kept = rep[rep[col] != regime]
        baseline = summary_simple(rep, 'baseline')
        filtered = summary_simple(kept, 'filtered')
        impacts[(col, regime)] = {
            'n_cut': len(rep) - len(kept),
            'annual_R_delta': filtered['annual_R'] - baseline['annual_R'],
            'MAR_delta': filtered['MAR'] - baseline['MAR'],
        }
    print(f'\n  Per-axis skip impact:')
    for (col, regime), d in impacts.items():
        print(f'    skip {col}={regime!s:<15s}: cut={d["n_cut"]}  '
               f'annual_delta={d["annual_R_delta"]:+.1f}R  MAR_delta={d["MAR_delta"]:+.2f}')

    # Save
    out_path = OUT_DIR / 'regime_adaptation_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Regime adaptation analysis: classify each Triple trigger '
                      'by trend (HTF EMA) / vol (14d RV) / 30d-return / ADX(4h), '
                      'then test if expectancy varies meaningfully across regimes.'),
            'regime_tables': {
                col: regime_table(rep, col).to_dict(orient='records')
                for col in ('trend', 'vol_regime', 'ret30d_regime', 'adx_regime')
            },
            'worst_per_axis': worst_per_axis,
            'per_axis_skip_impact': {f'{k[0]}={k[1]}': v for k, v in impacts.items()},
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
