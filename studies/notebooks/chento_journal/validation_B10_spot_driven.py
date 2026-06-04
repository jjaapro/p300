"""validation_B10: chento's spot-driven move detection.

Verbatim chento context (Dec 29 01:08): "Most mental spot only driven move
I've ever seen, free fucki[ng money]". Distinguishes price moves that are
*spot-driven* (real flow, continuation likely) from *futures-driven*
(leverage churn, reversal candidate).

Proxy:
  spot_drive[t] = |price_change_15m| / |oi_pct_change_same_window|
  - High ratio = price moved but OI barely budged = spot-driven (CONTINUATION)
  - Low ratio = price moved while OI also moved = leverage-driven

Two trigger modes:
  CONTINUATION: spot_drive_z > +THRESH AND |move_pct| above p70
                direction = sign(price move)
  REVERSAL:     spot_drive_z < -THRESH (leverage-driven)
                AND |move_pct| above p70
                direction = -sign(price move) (fade the leverage move)
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
    load_btc_15m, compute_atr, measure_r_outcomes, summarize_triggers,
    chento_coverage,
)
from studies.notebooks.chento_journal.validation_B9_oi_funding_standalone import (
    load_oi_hourly,
)


def compute_spot_drive(df_15m: pd.DataFrame, oi: pd.DataFrame, *,
                        window_bars: int = 4 * 2,  # 2h
                        zscore_window_bars: int = 4 * 24 * 30,
                        ) -> pd.DataFrame:
    """Compute spot-drive ratio over rolling 2h window, then z-score."""
    out = df_15m.copy()
    out['oi'] = oi['oi_close'].reindex(out.index, method='ffill', limit=8)
    move_pct = out['close'].pct_change(window_bars).abs()
    oi_change_pct = out['oi'].pct_change(window_bars).abs().clip(lower=1e-6)
    out['spot_drive'] = move_pct / oi_change_pct
    out['move_pct'] = out['close'].pct_change(window_bars)
    out['move_p70'] = move_pct.rolling(zscore_window_bars,
                                          min_periods=zscore_window_bars // 4).quantile(0.70)

    sd_mean = out['spot_drive'].rolling(zscore_window_bars,
                                          min_periods=zscore_window_bars // 4).mean()
    sd_std = out['spot_drive'].rolling(zscore_window_bars,
                                         min_periods=zscore_window_bars // 4).std()
    out['spot_drive_z'] = (out['spot_drive'] - sd_mean) / sd_std
    return out


def b10_triggers(df: pd.DataFrame, *,
                  mode: str = 'continuation',
                  spot_drive_z_thresh: float = 1.5,
                  cooldown_bars: int = 4 * 6,
                  atr_mult_stop: float = 2.0,
                  target_r: float = 2.0,
                  ) -> pd.DataFrame:
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)

    big_move = out['close'].pct_change(8).abs() >= out['move_p70']
    if mode == 'continuation':
        cond = (out['spot_drive_z'] > spot_drive_z_thresh) & big_move
    else:
        cond = (out['spot_drive_z'] < -spot_drive_z_thresh) & big_move

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(out)):
        if i - last_trigger_idx < cooldown_bars: continue
        if not bool(cond.iloc[i]): continue
        if pd.isna(out['atr'].iloc[i]) or pd.isna(out['move_pct'].iloc[i]):
            continue
        move = out['move_pct'].iloc[i]
        if move == 0: continue
        if mode == 'continuation':
            direction = 'long' if move > 0 else 'short'
        else:
            direction = 'short' if move > 0 else 'long'
        entry = float(out['close'].iloc[i])
        atr_val = float(out['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        if direction == 'long':
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'spot_drive_z': float(out['spot_drive_z'].iloc[i]),
                         'move_pct': float(move), 'atr': atr_val, 'mode': mode})
        else:
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'spot_drive_z': float(out['spot_drive_z'].iloc[i]),
                         'move_pct': float(move), 'atr': atr_val, 'mode': mode})
        last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print('Loading data + computing spot-drive signal...')
    df = load_btc_15m()
    oi = load_oi_hourly()
    df_enr = compute_spot_drive(df, oi)
    valid = df_enr['spot_drive_z'].dropna()
    print(f'  spot_drive_z range: [{valid.min():.1f}, {valid.max():.1f}]  std={valid.std():.2f}')

    print('\n=== B10 variants ===')
    results = {}
    for mode in ('continuation', 'reversal'):
        for z in (1.0, 1.5, 2.0):
            label = f'B10_{mode}_z{z}'
            trigs = b10_triggers(df_enr, mode=mode, spot_drive_z_thresh=z)
            if trigs.empty:
                print(f'  {label}: 0 triggers'); continue
            trigs_r = measure_r_outcomes(trigs, df)
            s = summarize_triggers(trigs_r, label=label)
            cov = chento_coverage(trigs_r, asset='BTCUSDT')
            print(f'  {label:<25s}: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f} '
                   f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%} '
                   f'ann={s["implied_annual_pct"]:+.1f}%  '
                   f'cov={cov.get("chento_to_trigger_loose_rate",0):.0%}')
            results[label] = {**s, 'coverage': cov}

    out_path = OUT_DIR / 'B10_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'variants': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
