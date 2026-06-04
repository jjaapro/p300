"""validation_B4_tv_8mo: re-run B4 squeeze direction on the 8-month
TradingView dataset.

Prior B4 used cd_liquidations (88 days, CoinDesk aggregation). The 88d sample
showed +0.87R per trade at 63% WR — promising but too small to trust.

This run uses tv_btc_perp_15m (236 days, Oct 2025 - May 2026, raw Binance
perp liquidations via TradingView). Spearman correlation with cd_liquidations
on overlap was 0.91, validating TV as a higher-fidelity source.

Methodology mirrors original B4:
  - Compute long_liq_z and short_liq_z over 30-day rolling window
  - Trigger SHORT when long_liq_z > threshold (longs being liquidated en masse)
  - Trigger LONG  when short_liq_z > threshold (shorts being liquidated)
  - Entry: next 15m close
  - Stop: 2 x ATR(14)
  - Target: 2R
  - TIF: 24h
  - Honest cost model: 18 bp RT scaled by stop_distance

Adds proper IS/OOS split: IS = Oct 2025 - Feb 2026 (153d), OOS = Mar - May 2026 (83d).
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    compute_atr, measure_r_outcomes, summarize_triggers, chento_coverage,
)


def load_tv_15m() -> pd.DataFrame:
    """Load TV BTC perp 15m frame with all chento-relevant fields."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, open, high, low, close, volume,
               long_liq, short_liq, funding_rate,
               oi_close, ls_ratio_accounts,
               top_long_positions_pct, top_short_positions_pct,
               top_ls_ratio_positions
        FROM tv_btc_perp_15m
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    # B1 measure_r_outcomes expects 'spot_h' and 'spot_l' columns — alias
    df['spot_h'] = df['high']
    df['spot_l'] = df['low']
    df['spot_c'] = df['close']
    return df


def compute_tv_squeeze_z(df: pd.DataFrame, *,
                          zscore_window_bars: int = 4 * 24 * 30,  # 30d at 15m
                          ) -> pd.DataFrame:
    """Compute long/short liquidation z-scores at 15m on TV data.

    TV gives raw long_liq / short_liq per 15m bar (USD-equivalent units).
    Z-score over 30-day rolling = ~2,880 bars.
    """
    out = df.copy()
    # Fill nulls (no liquidations in window) with 0
    long_q = out['long_liq'].fillna(0)
    short_q = out['short_liq'].abs().fillna(0)
    min_p = zscore_window_bars // 4
    out['long_liq_z'] = (
        (long_q - long_q.rolling(zscore_window_bars, min_periods=min_p).mean())
        / long_q.rolling(zscore_window_bars, min_periods=min_p).std()
    )
    out['short_liq_z'] = (
        (short_q - short_q.rolling(zscore_window_bars, min_periods=min_p).mean())
        / short_q.rolling(zscore_window_bars, min_periods=min_p).std()
    )
    return out


def b4_tv_triggers(df: pd.DataFrame, *,
                    z_threshold: float = 2.0,
                    cooldown_bars: int = 4 * 6,
                    atr_mult_stop: float = 2.0,
                    target_r: float = 2.0,
                    ) -> pd.DataFrame:
    """Generate B4 squeeze triggers from TV liquidation z-scores."""
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    short_mask = out['long_liq_z'] > z_threshold
    long_mask = out['short_liq_z'] > z_threshold

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(out)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(out['atr'].iloc[i]):
            continue
        long_z = out['long_liq_z'].iloc[i]
        short_z = out['short_liq_z'].iloc[i]
        if pd.isna(long_z) and pd.isna(short_z):
            continue
        entry = float(out['close'].iloc[i])
        atr_val = float(out['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0:
            continue
        ts = out.index[i]
        if pd.notna(long_z) and long_z > z_threshold:
            rows.append({
                'ts': ts, 'direction': 'short', 'entry': entry,
                'stop': entry + risk, 'target': entry - risk * target_r,
                'long_liq_z': float(long_z), 'atr': atr_val,
            })
            last_trigger_idx = i
        elif pd.notna(short_z) and short_z > z_threshold:
            rows.append({
                'ts': ts, 'direction': 'long', 'entry': entry,
                'stop': entry - risk, 'target': entry + risk * target_r,
                'short_liq_z': float(short_z), 'atr': atr_val,
            })
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print(f'DB: {DB}')
    print('Loading TV 15m frame...')
    df = load_tv_15m()
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')

    print('\nComputing squeeze z-scores (30d window at 15m = 2,880 bars)...')
    df_z = compute_tv_squeeze_z(df)
    valid = df_z['long_liq_z'].dropna()
    if len(valid) > 0:
        print(f'  long_liq_z range: [{valid.min():.2f}, {valid.max():.2f}]  '
               f'std={valid.std():.2f}')
    valid = df_z['short_liq_z'].dropna()
    if len(valid) > 0:
        print(f'  short_liq_z range: [{valid.min():.2f}, {valid.max():.2f}]  '
               f'std={valid.std():.2f}')

    print('\n=== Parameter sweep on z_threshold ===')
    for z in (1.5, 2.0, 2.5, 3.0, 3.5):
        trigs = b4_tv_triggers(df_z, z_threshold=z)
        if trigs.empty:
            print(f'  z={z}: 0 triggers')
            continue
        ds = trigs['direction'].value_counts()
        span_y = (trigs['ts'].max() - trigs['ts'].min()).total_seconds() / (365.25*86400)
        print(f'  z>{z}: {len(trigs):>4d} triggers  ({len(trigs)/max(span_y,0.01):>5.0f}/yr extrap.)  '
               f'short={ds.get("short",0)}  long={ds.get("long",0)}')

    # === Run main variants ===
    all_results = {}
    for label, params in (
        ('B4tv_z1.5', {'z_threshold': 1.5}),
        ('B4tv_z2.0', {'z_threshold': 2.0}),
        ('B4tv_z2.5', {'z_threshold': 2.5}),
        ('B4tv_z3.0', {'z_threshold': 3.0}),
    ):
        print(f'\n=== {label} ===')
        trigs = b4_tv_triggers(df_z, **params)
        if trigs.empty:
            print('  no triggers'); continue
        trigs_r = measure_r_outcomes(trigs, df_z)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  n={s["n"]}  /yr={s["trades_per_year"]}  '
               f'meanR={s["mean_R"]:+.3f}  WR={s["win_rate"]:.0%}  '
               f'ann={s["implied_annual_pct"]:+.1f}%')
        print(f'  exit kinds: {s["exit_kinds"]}')
        print(f'  direction split: {s["direction_split"]}')
        print(f'  chento (in window={cov.get("n_chento_in_trigger_window",0)}) -> '
               f'trigger same-dir loose: {cov.get("chento_to_trigger_loose",0)} '
               f'({cov.get("chento_to_trigger_loose_rate",0):.0%})')

        # IS/OOS split: IS through 2026-02-28, OOS from 2026-03-01
        is_end = pd.Timestamp('2026-02-28 23:59:59', tz='UTC')
        is_set = trigs_r[trigs_r['ts'] <= is_end]
        oos_set = trigs_r[trigs_r['ts'] > is_end]
        is_s = summarize_triggers(is_set, 'IS')
        oos_s = summarize_triggers(oos_set, 'OOS')
        print(f'  IS  (Oct 2025 - Feb 2026): n={is_s.get("n",0):>3d}  '
               f'meanR={is_s.get("mean_R",0):+.3f}  WR={is_s.get("win_rate",0):.0%}')
        print(f'  OOS (Mar - May 2026):       n={oos_s.get("n",0):>3d}  '
               f'meanR={oos_s.get("mean_R",0):+.3f}  WR={oos_s.get("win_rate",0):.0%}')
        if is_s.get('mean_R', 0) != 0 and oos_s.get('n', 0) > 0:
            decay = oos_s['mean_R'] - is_s['mean_R']
            pct_decay = decay / abs(is_s['mean_R']) * 100 if is_s['mean_R'] != 0 else 0
            print(f'  R decay (OOS - IS):       {decay:+.3f}R  ({pct_decay:+.0f}%)')

        all_results[label] = {**s, 'coverage': cov,
                              'is_meanR': is_s.get('mean_R', 0),
                              'is_n': is_s.get('n', 0),
                              'oos_meanR': oos_s.get('mean_R', 0),
                              'oos_n': oos_s.get('n', 0)}

    # === Compare to prior B4 finding ===
    print(f'\n=== Compared to prior 88-day cd_liquidations finding ===')
    print(f'  Prior B4 (88d, cd_liquidations):  +0.87R per trade, 63% WR (z>1.5)')
    print(f'  Source-comparison verdict: TV/CD Spearman r = 0.91 (validated)')

    out_path = OUT_DIR / 'B4_tv_8mo_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'data_source': 'tv_btc_perp_15m (TradingView Premium CSV exports)',
            'span_days': 236,
            'span_start': str(df.index.min()),
            'span_end': str(df.index.max()),
            'is_oos_split': '2026-02-28',
            'note': (
                "B4 squeeze direction re-run on 236d TV liquidations data. "
                "Prior 88d cd_liquidations sample gave +0.87R/63% WR; this run "
                "validates whether the signal generalizes over 3x larger window "
                "with proper IS/OOS split."
            ),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
