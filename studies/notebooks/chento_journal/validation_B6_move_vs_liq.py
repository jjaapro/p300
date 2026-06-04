"""validation_B6: chento's verbatim "TAME for 2.5% move... something is off" rule.

Chento context: he watches the ratio of (price move magnitude) / (liquidation
volume in same window). When a big move happens with LOW liquidations, it
suggests spot-driven flow (institutional / informed), not levered capitulation
— continuation likely. When liquidations are HIGH relative to move, the move
is leverage-driven and likely to reverse.

Proxy:
  move_size[t]    = |close[t] - close[t-N]| / close[t-N]   (return magnitude)
  liq_size[t]     = sum(long_q + short_q) over [t-N, t]    (total liq in window)
  spot_drive[t]   = move_size[t] / (liq_size_z[t] + 1)     (high = spot-driven)

Trigger:
  CONTINUATION when move_size > p80 AND spot_drive z > +THRESH:
    -> direction = sign(close[t] - close[t-N])
  REVERSAL when liq_size_z > +THRESH AND move_size > p80:
    -> direction = -sign(price move)

Test as STANDALONE on BTC 15m + cd_liquidations (3mo window limitation).
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
from studies.notebooks.chento_journal.validation_B4_squeeze_direction import (
    load_liquidations_hourly,
)


def compute_move_vs_liq(df_15m: pd.DataFrame, liq_h: pd.DataFrame, *,
                         move_window_bars: int = 4 * 2,    # 2h move
                         liq_window_hours: int = 6,         # 6h liq window
                         zscore_window_hours: int = 24 * 7, # 7d rolling
                         ) -> pd.DataFrame:
    """Compute move_size, liq_z, spot_drive z-scores."""
    out = df_15m.copy()
    out['move_pct'] = out['close'].pct_change(move_window_bars).abs()
    out['move_dir'] = np.sign(out['close'] - out['close'].shift(move_window_bars))

    # Total liquidation per hour, resample to 15m and roll
    liq_total = (liq_h['long_quote_quantity'].fillna(0)
                 + liq_h['short_quote_quantity'].fillna(0))
    liq_rolled = liq_total.rolling(liq_window_hours, min_periods=1).sum()
    # Reindex to 15m
    out['liq_window_sum'] = liq_rolled.reindex(out.index, method='ffill', limit=4)
    liq_mean = out['liq_window_sum'].rolling(zscore_window_hours * 4,
                                                min_periods=24 * 4).mean()
    liq_std = out['liq_window_sum'].rolling(zscore_window_hours * 4,
                                               min_periods=24 * 4).std()
    out['liq_z'] = (out['liq_window_sum'] - liq_mean) / liq_std

    # spot_drive: move per unit of liq. high = spot-driven, low = leverage-driven
    out['spot_drive'] = out['move_pct'] / (out['liq_window_sum'] / liq_mean).clip(lower=0.1)

    # Move percentile
    out['move_p80'] = out['move_pct'].rolling(zscore_window_hours * 4,
                                                  min_periods=24 * 4).quantile(0.80)
    return out


def b6_triggers(df_enriched: pd.DataFrame, *,
                 liq_z_thresh: float = 2.0,
                 cooldown_bars: int = 4 * 6,
                 atr_mult_stop: float = 2.0,
                 target_r: float = 2.0,
                 mode: str = 'reversal',  # 'reversal' or 'continuation'
                 ) -> pd.DataFrame:
    """Generate B6 triggers.

    REVERSAL mode (the chento read):
      liq_z > +liq_z_thresh AND move >= move_p80
      direction = -sign(move) (fade the leverage-driven move)

    CONTINUATION mode:
      liq_z < 0 AND move >= move_p80 (spot-driven big move)
      direction = sign(move)
    """
    df = df_enriched.copy()
    df['atr'] = compute_atr(df, period=14)

    if mode == 'reversal':
        cond = (df['liq_z'] > liq_z_thresh) & (df['move_pct'] >= df['move_p80'])
    else:  # continuation
        cond = (df['liq_z'] < 0) & (df['move_pct'] >= df['move_p80'])

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(df)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if not bool(cond.iloc[i]):
            continue
        if pd.isna(df['atr'].iloc[i]) or pd.isna(df['move_dir'].iloc[i]):
            continue
        move_dir = df['move_dir'].iloc[i]
        if move_dir == 0:
            continue
        if mode == 'reversal':
            direction = 'short' if move_dir > 0 else 'long'
        else:
            direction = 'long' if move_dir > 0 else 'short'

        entry = float(df['close'].iloc[i])
        atr_val = float(df['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = df.index[i]
        if direction == 'long':
            rows.append({
                'ts': ts, 'direction': 'long', 'entry': entry,
                'stop': entry - risk, 'target': entry + risk * target_r,
                'liq_z': float(df['liq_z'].iloc[i]),
                'move_pct': float(df['move_pct'].iloc[i]),
                'atr': atr_val, 'mode': mode,
            })
        else:
            rows.append({
                'ts': ts, 'direction': 'short', 'entry': entry,
                'stop': entry + risk, 'target': entry - risk * target_r,
                'liq_z': float(df['liq_z'].iloc[i]),
                'move_pct': float(df['move_pct'].iloc[i]),
                'atr': atr_val, 'mode': mode,
            })
        last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print('Loading BTC 15m + liquidations...')
    df = load_btc_15m()
    liq = load_liquidations_hourly()
    print(f'  BTC 15m: {len(df):,} bars')
    print(f'  Liq hourly: {len(liq):,} bars  {liq.index.min()} -> {liq.index.max()}')

    print('\nComputing move-vs-liq signal...')
    df_enr = compute_move_vs_liq(df, liq)

    all_results = {}
    for mode in ('reversal', 'continuation'):
        for z_thresh in (1.0, 1.5, 2.0):
            label = f'B6_{mode}_z{z_thresh}'
            print(f'\n=== {label} ===')
            trigs = b6_triggers(df_enr, liq_z_thresh=z_thresh, mode=mode)
            if trigs.empty:
                print('  no triggers'); continue
            trigs_r = measure_r_outcomes(trigs, df_enr)
            s = summarize_triggers(trigs_r, label=label)
            cov = chento_coverage(trigs_r, asset='BTCUSDT')
            print(f'  n={s["n"]}  /yr={s["trades_per_year"]}  '
                   f'meanR={s["mean_R"]:+.3f}  WR={s["win_rate"]:.0%}')
            print(f'  precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
                   f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}')
            all_results[label] = {**s, 'coverage': cov}

    out_path = OUT_DIR / 'B6_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "Chento's verbatim 'TAME for 2.5% move... something is off' "
                "rule as standalone trigger. REVERSAL mode fades big moves "
                "with high liquidations; CONTINUATION mode follows big moves "
                "with LOW liquidations (spot-driven). 3-mo sample limit."
            ),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
