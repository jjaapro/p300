"""validation_B7: multi-timeframe CVD alignment as a standalone trigger.

Proxy for chento's flowx.trade Multi-TF Money Flow Grid which shows
buy/sell pressure on 1m/5m/15m/30m/1h/2h/4h/Daily.

We compute CVD over rolling horizons on 15m bars:
  cvd_1h  = sum(buy_q - sell_q) over last 4 bars
  cvd_4h  = sum over last 16 bars
  cvd_1d  = sum over last 96 bars
  cvd_3d  = sum over last 288 bars

Each z-scored over trailing 30 days.

Two trigger modes:
  ALIGNMENT — all 4 TF z-scores same sign AND |median z| > THRESH
              direction = sign
              (whales agree across all horizons → continuation)
  DIVERGENCE — short-horizon (1h) z-score opposite of long-horizon (3d) z-score
               AND both magnitudes > THRESH
               direction = sign(long-horizon)
               (short-term flow disagrees with regime → fade short-term)
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
    load_btc_15m, compute_atr, measure_r_outcomes, summarize_triggers,
    chento_coverage,
)


def compute_multitf_cvd(df: pd.DataFrame, *,
                         zscore_window_bars: int = 4 * 24 * 30,  # 30d
                         horizons: dict = None,
                         ) -> pd.DataFrame:
    """Compute CVD z-scores across multiple horizons."""
    if horizons is None:
        horizons = {'1h': 4, '4h': 16, '1d': 96, '3d': 288}
    out = df.copy()
    bar_mf = out['quote_volume_buy'] - out['quote_volume_sell']
    for label, bars in horizons.items():
        cvd = bar_mf.rolling(bars, min_periods=bars // 2).sum()
        mean = cvd.rolling(zscore_window_bars, min_periods=zscore_window_bars // 4).mean()
        std = cvd.rolling(zscore_window_bars, min_periods=zscore_window_bars // 4).std()
        out[f'cvd_{label}_z'] = (cvd - mean) / std
    return out


def b7_alignment_triggers(df: pd.DataFrame, *,
                            z_threshold: float = 1.0,
                            cooldown_bars: int = 4 * 6,
                            atr_mult_stop: float = 2.0,
                            target_r: float = 2.0,
                            ) -> pd.DataFrame:
    """ALIGNMENT trigger: all 4 TF z-scores agree on sign + |median z| > threshold."""
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    z_cols = ['cvd_1h_z', 'cvd_4h_z', 'cvd_1d_z', 'cvd_3d_z']
    z_mat = out[z_cols].copy()
    pos_count = (z_mat > 0).sum(axis=1)
    neg_count = (z_mat < 0).sum(axis=1)
    med_z = z_mat.median(axis=1)
    long_mask = (pos_count == 4) & (med_z > z_threshold)
    short_mask = (neg_count == 4) & (med_z < -z_threshold)

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(out)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(out['atr'].iloc[i]) or pd.isna(med_z.iloc[i]):
            continue
        entry = float(out['close'].iloc[i])
        atr_val = float(out['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        if bool(long_mask.iloc[i]):
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'med_z': float(med_z.iloc[i]), 'atr': atr_val})
            last_trigger_idx = i
        elif bool(short_mask.iloc[i]):
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'med_z': float(med_z.iloc[i]), 'atr': atr_val})
            last_trigger_idx = i
    return pd.DataFrame(rows)


def b7_divergence_triggers(df: pd.DataFrame, *,
                              short_z_threshold: float = 1.5,
                              long_z_threshold: float = 1.0,
                              cooldown_bars: int = 4 * 6,
                              atr_mult_stop: float = 2.0,
                              target_r: float = 2.0,
                              ) -> pd.DataFrame:
    """DIVERGENCE trigger: 1h z opposite of 3d z, both above thresholds.
    Fade the short-term move in favor of long-term direction."""
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    short_z = out['cvd_1h_z']
    long_z = out['cvd_3d_z']
    # Short-term up, long-term down -> SHORT
    short_div = (short_z > short_z_threshold) & (long_z < -long_z_threshold)
    # Short-term down, long-term up -> LONG
    long_div = (short_z < -short_z_threshold) & (long_z > long_z_threshold)

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(out)):
        if i - last_trigger_idx < cooldown_bars: continue
        if pd.isna(out['atr'].iloc[i]) or pd.isna(short_z.iloc[i]) or pd.isna(long_z.iloc[i]):
            continue
        entry = float(out['close'].iloc[i])
        atr_val = float(out['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        if bool(short_div.iloc[i]):
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'short_z': float(short_z.iloc[i]),
                         'long_z': float(long_z.iloc[i]), 'atr': atr_val})
            last_trigger_idx = i
        elif bool(long_div.iloc[i]):
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'short_z': float(short_z.iloc[i]),
                         'long_z': float(long_z.iloc[i]), 'atr': atr_val})
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print('Loading BTC 15m + computing multi-TF CVD signals...')
    df = load_btc_15m()
    df_enr = compute_multitf_cvd(df)
    print(f'  bars: {len(df_enr):,}')

    print('\nz-score ranges:')
    for col in ('cvd_1h_z', 'cvd_4h_z', 'cvd_1d_z', 'cvd_3d_z'):
        v = df_enr[col].dropna()
        if len(v) > 0:
            print(f'  {col}: [{v.min():.1f}, {v.max():.1f}]  std={v.std():.2f}')

    all_results = {}
    print('\n=== B7 ALIGNMENT (all 4 TFs same sign + |median z|>threshold) ===')
    for z in (0.5, 1.0, 1.5, 2.0):
        label = f'B7_align_z{z}'
        trigs = b7_alignment_triggers(df_enr, z_threshold=z)
        if trigs.empty:
            print(f'  {label}: 0 triggers'); continue
        trigs_r = measure_r_outcomes(trigs, df_enr)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label}: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f}  '
               f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}')
        all_results[label] = {**s, 'coverage': cov}

    print('\n=== B7 DIVERGENCE (1h opposite 3d) ===')
    for s_z, l_z in ((1.0, 0.5), (1.5, 1.0), (2.0, 1.0), (2.5, 1.5)):
        label = f'B7_div_s{s_z}_l{l_z}'
        trigs = b7_divergence_triggers(df_enr, short_z_threshold=s_z,
                                          long_z_threshold=l_z)
        if trigs.empty:
            print(f'  {label}: 0 triggers'); continue
        trigs_r = measure_r_outcomes(trigs, df_enr)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label}: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f}  '
               f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}')
        all_results[label] = {**s, 'coverage': cov}

    out_path = OUT_DIR / 'B7_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "Multi-TF CVD as proxy for chento's flowx multi-TF money-flow "
                "grid. Two modes: ALIGNMENT (all TFs agree -> continuation), "
                "DIVERGENCE (short TF opposite long TF -> fade). 5y history."
            ),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
