"""validation_B9: OI-flush + funding-flip as STANDALONE entry triggers.

Structural question: the v2 chento_limit_bid sleeve uses OI flush + funding
flip as 2 of 4 confluence components. Do they actually carry edge alone, or
is the confluence score just adding noise on top of the swing-base detector?

Two sub-signals:
  B9a — OI flush + funding negative -> LONG (capitulation in futures, longs
        squeezed out, expect bounce)
  B9b — OI build + funding positive -> SHORT (over-leverage on long side,
        expect correction)

Plus the bilateral combos.

Data: cd_open_interest (hourly, multi-year) + cd_funding_rate (8h, multi-year).
Both BTC-only.
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


def load_oi_hourly() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT timestamp, oi_close, oi_value_close "
                      "FROM cd_open_interest ORDER BY timestamp", con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df.set_index('ts').drop(columns='timestamp')


def load_funding() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("SELECT timestamp, fr_close FROM cd_funding_rate "
                      "ORDER BY timestamp", con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    return df.set_index('ts').drop(columns='timestamp')


def compute_oi_funding_signals(df_15m: pd.DataFrame,
                                  oi: pd.DataFrame,
                                  funding: pd.DataFrame,
                                  *,
                                  oi_change_window_h: int = 6,
                                  ) -> pd.DataFrame:
    """Reindex OI + funding to 15m grid, compute oi change % over window."""
    df = df_15m.copy()
    df['oi'] = oi['oi_close'].reindex(df.index, method='ffill', limit=8)
    # Funding ffilled with longer limit (8h granularity)
    df['funding'] = funding['fr_close'].reindex(df.index, method='ffill', limit=32)
    # OI change over rolling N hours = N*4 bars
    df['oi_pct_change'] = df['oi'].pct_change(oi_change_window_h * 4)
    return df


def b9_triggers(df: pd.DataFrame, *,
                 mode: str = 'long_flush',  # long_flush | short_build | both
                 oi_flush_pct: float = -0.015,
                 oi_build_pct: float = 0.015,
                 funding_neg_thresh: float = 0.0,
                 funding_pos_thresh: float = 0.0,
                 cooldown_bars: int = 4 * 6,
                 atr_mult_stop: float = 2.0,
                 target_r: float = 2.0,
                 ) -> pd.DataFrame:
    """B9 trigger generator.

    long_flush:  oi_pct_change <= oi_flush_pct AND funding <= funding_neg_thresh
    short_build: oi_pct_change >= oi_build_pct AND funding >= funding_pos_thresh
    """
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    long_mask = pd.Series(False, index=out.index)
    short_mask = pd.Series(False, index=out.index)
    if mode in ('long_flush', 'both'):
        long_mask = (out['oi_pct_change'] <= oi_flush_pct) & (out['funding'] <= funding_neg_thresh)
    if mode in ('short_build', 'both'):
        short_mask = (out['oi_pct_change'] >= oi_build_pct) & (out['funding'] >= funding_pos_thresh)

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(out)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(out['atr'].iloc[i]) or pd.isna(out['oi_pct_change'].iloc[i]):
            continue
        if pd.isna(out['funding'].iloc[i]):
            continue
        entry = float(out['close'].iloc[i])
        atr_val = float(out['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        if bool(long_mask.iloc[i]):
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'oi_pct_change': float(out['oi_pct_change'].iloc[i]),
                         'funding': float(out['funding'].iloc[i]),
                         'atr': atr_val})
            last_trigger_idx = i
        elif bool(short_mask.iloc[i]):
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'oi_pct_change': float(out['oi_pct_change'].iloc[i]),
                         'funding': float(out['funding'].iloc[i]),
                         'atr': atr_val})
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print('Loading BTC 15m + OI + funding...')
    df = load_btc_15m()
    oi = load_oi_hourly()
    funding = load_funding()
    print(f'  BTC 15m: {len(df):,}  OI: {len(oi):,}  funding: {len(funding):,}')

    print('\nReindexing + computing OI change signal...')
    df_enr = compute_oi_funding_signals(df, oi, funding)

    print('\n=== B9 variants ===')
    results = {}
    for label, params in (
        ('B9a_long_flush_default', dict(mode='long_flush',
                                          oi_flush_pct=-0.015, funding_neg_thresh=0.0)),
        ('B9a_long_flush_strict', dict(mode='long_flush',
                                         oi_flush_pct=-0.025, funding_neg_thresh=-0.0001)),
        ('B9b_short_build_default', dict(mode='short_build',
                                           oi_build_pct=0.015, funding_pos_thresh=0.0)),
        ('B9b_short_build_strict', dict(mode='short_build',
                                          oi_build_pct=0.025, funding_pos_thresh=0.0001)),
        ('B9_both', dict(mode='both',
                          oi_flush_pct=-0.015, oi_build_pct=0.015,
                          funding_neg_thresh=0.0, funding_pos_thresh=0.0)),
    ):
        trigs = b9_triggers(df_enr, **params)
        if trigs.empty:
            print(f'  {label}: 0 triggers'); continue
        trigs_r = measure_r_outcomes(trigs, df)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label:<28s}: n={s["n"]:>4d} /yr={s["trades_per_year"]:>5.1f} '
               f'meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%} '
               f'ann={s["implied_annual_pct"]:+.1f}%  '
               f'cov={cov.get("chento_to_trigger_loose_rate",0):.0%}')
        # OOS split
        is_set = trigs_r[trigs_r['ts'] <= pd.Timestamp('2024-12-31', tz='UTC')]
        oos_set = trigs_r[trigs_r['ts'] >= pd.Timestamp('2025-01-01', tz='UTC')]
        is_s = summarize_triggers(is_set, label='IS')
        oos_s = summarize_triggers(oos_set, label='OOS')
        print(f'    IS: meanR={is_s.get("mean_R",0):+.3f}({is_s.get("n",0)})  '
               f'OOS: meanR={oos_s.get("mean_R",0):+.3f}({oos_s.get("n",0)})')
        results[label] = {**s, 'coverage': cov,
                          'is_meanR': is_s.get('mean_R', 0),
                          'oos_meanR': oos_s.get('mean_R', 0)}

    out_path = OUT_DIR / 'B9_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "B9 tests OI-flush + funding-sign as STANDALONE trigger. "
                "v2 sleeve uses these as 2 of 4 confluence components. "
                "Standalone result tells us if they carry edge alone."
            ),
            'variants': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
