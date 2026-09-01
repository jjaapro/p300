"""validation_B5: Binance long/short account ratio extremes as STANDALONE
entry trigger.

When the crowd is heavily one-sided, the contrarian read is to fade them:
  long_pct > p90 of trailing 30d  -> over-long crowd -> SHORT
  long_pct < p10 of trailing 30d  -> over-short crowd -> LONG

Data source: ca_long_short_ratio (asset, timestamp, ratio, long_pct, short_pct).
Resolution: DAILY rows (Binance globalLongShortAccountRatio period=1d refresh
+ Coinalyze daily backfill) — one sample per day, so a 30d window is 30 rows.
(Docstring corrected 2026-09-01; it previously claimed 30-min snapshots.)

Entry: next 15m close after the trigger timestamp.
Stop: 2*ATR(14) on 15m bars.
Target: 2R.
TIF: 24h.
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


def load_lsr(asset: str = 'BTC') -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(
        "SELECT timestamp, ratio, long_pct, short_pct FROM ca_long_short_ratio "
        "WHERE asset = ? ORDER BY timestamp", con, params=(asset,))
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    return df[~df.index.duplicated(keep='last')]


def compute_lsr_extremes(lsr: pd.DataFrame, *,
                          rolling_days: int = 30,
                          ) -> pd.DataFrame:
    """Add p10 and p90 of long_pct over trailing `rolling_days`."""
    out = lsr.copy()
    # Estimate samples-per-day from the index
    if len(out) < 100:
        return out
    samples_per_day = max(1, int(round(len(out) / max((out.index.max() - out.index.min()).days, 1))))
    window = rolling_days * samples_per_day
    out['lp_p10'] = out['long_pct'].rolling(window, min_periods=window // 4).quantile(0.10)
    out['lp_p90'] = out['long_pct'].rolling(window, min_periods=window // 4).quantile(0.90)
    out['lp_p50'] = out['long_pct'].rolling(window, min_periods=window // 4).quantile(0.50)
    return out


def b5_triggers(df_15m: pd.DataFrame, lsr_signal: pd.DataFrame, *,
                 cooldown_bars: int = 4 * 24,  # 24h - less noisy than B1/B4
                 atr_mult_stop: float = 2.0,
                 target_r: float = 2.0,
                 ) -> pd.DataFrame:
    """Generate B5 triggers."""
    df = df_15m.copy()
    df['atr'] = compute_atr(df, period=14)
    # Forward-fill LSR onto 15m grid; LSR is 30-min so 4 bars freshness
    df['long_pct'] = lsr_signal['long_pct'].reindex(df.index, method='ffill', limit=4)
    df['lp_p10'] = lsr_signal['lp_p10'].reindex(df.index, method='ffill', limit=4)
    df['lp_p90'] = lsr_signal['lp_p90'].reindex(df.index, method='ffill', limit=4)

    short_mask = df['long_pct'] > df['lp_p90']  # over-long crowd -> contrarian short
    long_mask = df['long_pct'] < df['lp_p10']   # over-short crowd -> contrarian long

    rows = []
    last_trigger_idx = -10**9
    for i in range(len(df)):
        if i - last_trigger_idx < cooldown_bars:
            continue
        if pd.isna(df['atr'].iloc[i]) or pd.isna(df['long_pct'].iloc[i]):
            continue
        if pd.isna(df['lp_p10'].iloc[i]) or pd.isna(df['lp_p90'].iloc[i]):
            continue
        entry = float(df['close'].iloc[i])
        atr_val = float(df['atr'].iloc[i])
        risk = atr_val * atr_mult_stop
        if risk <= 0:
            continue
        ts = df.index[i]
        if bool(short_mask.iloc[i]):
            rows.append({
                'ts': ts, 'direction': 'short',
                'entry': entry, 'stop': entry + risk,
                'target': entry - risk * target_r,
                'long_pct': float(df['long_pct'].iloc[i]),
                'lp_p90': float(df['lp_p90'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
        elif bool(long_mask.iloc[i]):
            rows.append({
                'ts': ts, 'direction': 'long',
                'entry': entry, 'stop': entry - risk,
                'target': entry + risk * target_r,
                'long_pct': float(df['long_pct'].iloc[i]),
                'lp_p10': float(df['lp_p10'].iloc[i]),
                'atr': atr_val,
            })
            last_trigger_idx = i
    return pd.DataFrame(rows)


def main():
    print(f'DB: {DB}')
    print('Loading BTC 15m perp...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')

    print('\nLoading Binance long/short account ratio...')
    lsr = load_lsr('BTC')
    print(f'  {len(lsr):,} LSR samples  {lsr.index.min()} -> {lsr.index.max()}')
    print(f'  long_pct: median={lsr["long_pct"].median():.1%}  '
          f'p10={lsr["long_pct"].quantile(0.1):.1%}  '
          f'p90={lsr["long_pct"].quantile(0.9):.1%}')

    print('\nComputing rolling p10/p90 (30d window)...')
    lsr_sig = compute_lsr_extremes(lsr)

    print('\nRunning B5 (long_pct > rolling p90 -> SHORT; < p10 -> LONG)...')
    trigs = b5_triggers(df, lsr_sig)
    if trigs.empty:
        print('  no triggers')
        return
    print(f'  {len(trigs)} triggers  '
           f'({(trigs["direction"]=="short").sum()} short / '
           f'{(trigs["direction"]=="long").sum()} long)')

    trigs_with_r = measure_r_outcomes(trigs, df)
    summary = summarize_triggers(trigs_with_r, label='B5_p10_p90')
    print(f'\n=== B5 results ===')
    print(f'  n={summary["n"]}  /yr={summary["trades_per_year"]}  '
           f'mean R={summary["mean_R"]:+.3f}  WR={summary["win_rate"]:.0%}  '
           f'annual={summary["implied_annual_pct"]:+.1f}%')
    print(f'  exit kinds: {summary["exit_kinds"]}')
    print(f'  direction split: {summary["direction_split"]}')

    cov = chento_coverage(trigs_with_r, asset='BTCUSDT')
    print(f'\n  chento coverage:')
    print(f'    trigger->chento same-dir tight (24h): '
           f'{cov.get("trigger_to_chento_tight", 0)}/{cov.get("n_triggers", 0)} = '
           f'{cov.get("trigger_to_chento_tight_rate", 0):.1%}')
    print(f'    trigger->chento same-dir loose (72h): '
           f'{cov.get("trigger_to_chento_loose", 0)}/{cov.get("n_triggers", 0)} = '
           f'{cov.get("trigger_to_chento_loose_rate", 0):.1%}')
    print(f'    chento (in window={cov.get("n_chento_in_trigger_window",0)}) -> trigger same-dir loose: '
           f'{cov.get("chento_to_trigger_loose", 0)} = '
           f'{cov.get("chento_to_trigger_loose_rate", 0):.1%}')

    out_path = OUT_DIR / 'B5_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                "Binance long/short account ratio extremes. SHORT when "
                "long_pct > 30d-rolling p90 (over-long crowd); LONG when "
                "long_pct < p10. Contrarian setup. Standalone trigger on BTC."
            ),
            'summary': summary, 'coverage': cov,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
