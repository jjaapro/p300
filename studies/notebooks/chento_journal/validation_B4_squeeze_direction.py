"""validation_B4: trade-with-the-squeeze as a STANDALONE entry trigger.

Chento observation (3 documented CoinGlass screenshots, v24/v25/v28):
  - 16:1 long-liquidation ratio (Jan 21 "Damn") → he was long during long-squeeze
  - 10:1 short-liquidation ratio (Jan 14) → he was short during short-squeeze
  - "free short season" during 43:1 longs-liquidating cascade

Proxy:
  squeeze_dir[t] = sign of (long_quantity[t] - short_quantity[t]) z-scored
                   over rolling N hours on cd_liquidations (hourly data)

  Long-squeeze regime: longs being liquidated heavily → cascade DOWN → SHORT
  Short-squeeze regime: shorts being liquidated heavily → cascade UP → LONG

Trigger SHORT when: 24h long-liq z > +THRESH (longs capitulating)
Trigger LONG when: 24h short-liq z > +THRESH (shorts capitulating)

For each trigger:
  entry  = next 15m close
  stop   = entry ± ATR * 2  (15m ATR)
  target = entry ± ATR * 2 * 2  (2R)
  TIF    = 24h

Same chento-coverage methodology as B1.
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

CHENTO_TRADES = ROOT / 'studies' / 'material' / 'chento' / 'scan_aggregated' / 'trades.jsonl'

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_atr, measure_r_outcomes, summarize_triggers,
    chento_coverage,
)


def load_liquidations_hourly() -> pd.DataFrame:
    """Load BTC liquidations hourly (BTC-only by table convention)."""
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp, long_quantity, short_quantity,
               long_quote_quantity, short_quote_quantity,
               long_count, short_count,
               vwap_long_price, vwap_short_price
        FROM cd_liquidations
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    return df[~df.index.duplicated(keep='last')]


def compute_squeeze_signal(liq_h: pd.DataFrame, *,
                            zscore_window_hours: int = 24 * 30,  # 30d
                            ) -> pd.DataFrame:
    """Compute squeeze z-scores on hourly liquidation data."""
    out = liq_h.copy()
    # Long-squeeze: longs being liquidated
    long_q = out['long_quote_quantity'].fillna(0)
    short_q = out['short_quote_quantity'].fillna(0)
    out['long_liq_z'] = ((long_q - long_q.rolling(zscore_window_hours, min_periods=24*7).mean())
                          / long_q.rolling(zscore_window_hours, min_periods=24*7).std())
    out['short_liq_z'] = ((short_q - short_q.rolling(zscore_window_hours, min_periods=24*7).mean())
                           / short_q.rolling(zscore_window_hours, min_periods=24*7).std())
    # Ratio
    out['ls_ratio_log'] = np.log((long_q + 1) / (short_q + 1))
    return out


def b4_triggers(df_15m: pd.DataFrame, liq_signal: pd.DataFrame, *,
                 z_threshold: float = 2.0,
                 cooldown_bars: int = 4 * 6,  # 6h
                 atr_mult_stop: float = 2.0,
                 target_r: float = 2.0,
                 ) -> pd.DataFrame:
    """Generate B4 squeeze triggers on 15m bars by ffilling hourly liq signal."""
    df = df_15m.copy()
    df['atr'] = compute_atr(df, period=14)
    # Forward-fill liq signal to 15m grid (hourly -> 15m, with 1h freshness limit)
    df['long_liq_z'] = liq_signal['long_liq_z'].reindex(df.index, method='ffill', limit=4)
    df['short_liq_z'] = liq_signal['short_liq_z'].reindex(df.index, method='ffill', limit=4)

    # SHORT trigger: longs capitulating (long_liq_z >> 0)
    # LONG trigger: shorts capitulating (short_liq_z >> 0)
    short_mask = df['long_liq_z'] > z_threshold
    long_mask = df['short_liq_z'] > z_threshold

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(df)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(df['atr'].iloc[i]):
            continue
        entry = float(df['close'].iloc[i])
        atr_val = float(df['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0:
            continue
        ts = df.index[i]
        if short_mask.iloc[i]:
            rows.append({
                'ts': ts, 'direction': 'short',
                'entry': entry, 'stop': entry + risk,
                'target': entry - risk * target_r,
                'long_liq_z': float(df['long_liq_z'].iloc[i]),
                'short_liq_z': float(df['short_liq_z'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
        elif long_mask.iloc[i]:
            rows.append({
                'ts': ts, 'direction': 'long',
                'entry': entry, 'stop': entry - risk,
                'target': entry + risk * target_r,
                'long_liq_z': float(df['long_liq_z'].iloc[i]),
                'short_liq_z': float(df['short_liq_z'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print(f'DB: {DB}')
    print('Loading BTC 15m perp...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')

    print('\nLoading hourly liquidations...')
    liq = load_liquidations_hourly()
    print(f'  {len(liq):,} hourly bars  {liq.index.min()} -> {liq.index.max()}')

    print('\nComputing squeeze signal (long-liq-z, short-liq-z over 30d window)...')
    liq_sig = compute_squeeze_signal(liq)
    valid = liq_sig['long_liq_z'].dropna()
    print(f'  long_liq_z range: [{valid.min():.1f}, {valid.max():.1f}]  '
          f'std={valid.std():.2f}')

    # Param sweep
    print('\nParameter sweep (z_threshold -> trigger counts):')
    for z in (1.0, 1.5, 2.0, 2.5, 3.0):
        trigs = b4_triggers(df, liq_sig, z_threshold=z)
        if len(trigs) > 0:
            ds = trigs['direction'].value_counts()
            print(f'  z > {z}: {len(trigs)} triggers  '
                   f'({ds.get("short", 0)} short / {ds.get("long", 0)} long)')
        else:
            print(f'  z > {z}: 0 triggers')

    # Run variants
    all_results = {}
    for label, params in (
        ('B4_z1.5', {'z_threshold': 1.5}),
        ('B4_z2.0', {'z_threshold': 2.0}),
        ('B4_z2.5', {'z_threshold': 2.5}),
        ('B4_z3.0', {'z_threshold': 3.0}),
    ):
        print(f'\n=== {label} ===')
        trigs = b4_triggers(df, liq_sig, **params)
        if trigs.empty:
            print('  no triggers'); continue
        # ATR is in trigger row; need it for the measure_r_outcomes call from B1
        trigs_with_r = measure_r_outcomes(trigs, df)
        summary = summarize_triggers(trigs_with_r, label=label)
        print(f'  n={summary["n"]}  /yr={summary["trades_per_year"]}  '
               f'mean R={summary["mean_R"]:+.3f}  WR={summary["win_rate"]:.0%}  '
               f'annual={summary["implied_annual_pct"]:+.1f}%')
        print(f'  exit kinds: {summary["exit_kinds"]}')
        print(f'  direction split: {summary["direction_split"]}')

        cov = chento_coverage(trigs_with_r, asset='BTCUSDT')
        print(f'  chento coverage:')
        print(f'    trigger->chento same-dir loose (72h): '
               f'{cov.get("trigger_to_chento_loose", 0)}/{cov.get("n_triggers", 0)} = '
               f'{cov.get("trigger_to_chento_loose_rate", 0):.1%}')
        print(f'    chento (in window={cov.get("n_chento_in_trigger_window",0)}) -> trigger same-dir loose: '
               f'{cov.get("chento_to_trigger_loose", 0)} = '
               f'{cov.get("chento_to_trigger_loose_rate", 0):.1%}')
        all_results[label] = {**summary, 'coverage': cov, 'params': params}

    out_path = OUT_DIR / 'B4_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "Trade-with-the-squeeze: positive long_liq_z (longs being "
                "liquidated en masse) -> SHORT; positive short_liq_z -> LONG. "
                "Mirrors chento's 3 documented CoinGlass observations. "
                "Standalone trigger on BTC 15m + hourly cd_liquidations."
            ),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
