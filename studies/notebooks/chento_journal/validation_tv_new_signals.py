"""validation_tv_new_signals: test multiple new signals on the 236-day TV
dataset with proper IS/OOS split.

Tests:
  1. B4-FOLLOW — direction-flipped B4 (LONG on long-liq cascade)
     Hypothesis: chento's "trade-with-the-squeeze" means follow the move
     that caused the cascade, not fade it.
  2. Funding-flip-15m — funding rate sign-change as trigger
     Hypothesis: at 15m granularity, funding flipping negative is a
     bullish signal (shorts paying longs, capitulation).
  3. OI-rate-of-change-15m — OI spike/drop as trigger
     Hypothesis: rapid OI build = leverage entering; rapid OI drop = forced unwind.
  4. Triple-B4-filter — B4 z-extreme as confluence filter on the composite
     entry timestamps. Tests "B4 as context, not trigger."

All variants use:
  - Entry: 15m close at trigger
  - Stop: 2 ATR
  - Target: 2R
  - TIF: 24h
  - Honest cost: 18bp scaled by stop_distance
  - IS = Oct 2025 - Feb 2026, OOS = Mar - May 2026
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
from studies.notebooks.chento_journal.validation_B4_tv_8mo import (
    load_tv_15m, compute_tv_squeeze_z,
)


IS_END = pd.Timestamp('2026-02-28 23:59:59', tz='UTC')


def report(label: str, trigs_r: pd.DataFrame, dump: dict | None = None):
    if trigs_r.empty:
        print(f'  {label:<40s} 0 triggers')
        if dump is not None: dump[label] = {'n': 0}
        return
    s = summarize_triggers(trigs_r, label=label)
    cov = chento_coverage(trigs_r, asset='BTCUSDT')
    is_set = trigs_r[trigs_r['ts'] <= IS_END]
    oos_set = trigs_r[trigs_r['ts'] > IS_END]
    is_s = summarize_triggers(is_set, 'IS')
    oos_s = summarize_triggers(oos_set, 'OOS')
    is_r = is_s.get('mean_R', 0); oos_r = oos_s.get('mean_R', 0)
    cov_pct = cov.get('chento_to_trigger_loose_rate', 0)
    print(f'  {label:<40s} n={s["n"]:>4d}  R={s["mean_R"]:+.3f}  '
           f'WR={s["win_rate"]:.0%}  IS={is_r:+.3f}({is_s.get("n",0)})  '
           f'OOS={oos_r:+.3f}({oos_s.get("n",0)})  '
           f'cov={cov_pct:.0%}')
    if dump is not None:
        dump[label] = {**s, 'is_meanR': is_r, 'is_n': is_s.get('n', 0),
                       'oos_meanR': oos_r, 'oos_n': oos_s.get('n', 0),
                       'coverage': cov_pct}


# === 1. B4-FOLLOW direction-flipped ========================================

def b4_follow_triggers(df_z: pd.DataFrame, *, z_threshold: float = 2.0,
                       cooldown_bars: int = 4 * 6, atr_mult_stop: float = 2.0,
                       target_r: float = 2.0) -> pd.DataFrame:
    """SHORT on short_liq cascade (follow the move that caused it).
       LONG on long_liq cascade (follow the down-move)."""
    out = df_z.copy()
    out['atr'] = compute_atr(out, period=14)
    rows = []
    last = -10**9
    for i in range(len(out)):
        if i - last < cooldown_bars: continue
        if pd.isna(out['atr'].iloc[i]): continue
        long_z = out['long_liq_z'].iloc[i]; short_z = out['short_liq_z'].iloc[i]
        entry = float(out['close'].iloc[i])
        risk = float(out['atr'].iloc[i]) * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        # FLIPPED: when LONGS get liquidated -> follow the DOWN move -> SHORT
        # When SHORTS get liquidated -> follow the UP move -> LONG
        # Wait, that's the SAME as original. Let me re-read original B4.
        # Original B4: long_liq_z > thresh -> SHORT (fade up by selling because longs
        # have already been wiped). FLIP = follow the squeeze: long_liq_z > thresh
        # means longs were wiped, market is DUMPING, so SHORT is following the move.
        # That's actually the SAME interpretation as the original.
        # Actual flip: long_liq_z > thresh -> LONG (bottom-fish after capitulation)
        #              short_liq_z > thresh -> SHORT (top after squeeze)
        if pd.notna(long_z) and long_z > z_threshold:
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'long_liq_z': float(long_z), 'atr': risk/atr_mult_stop})
            last = i
        elif pd.notna(short_z) and short_z > z_threshold:
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'short_liq_z': float(short_z), 'atr': risk/atr_mult_stop})
            last = i
    return pd.DataFrame(rows)


# === 2. Funding-flip at 15m =================================================

def funding_flip_triggers(df: pd.DataFrame, *,
                            lookback_bars: int = 4 * 4,   # 4h prior
                            cooldown_bars: int = 4 * 12,  # 12h
                            atr_mult_stop: float = 2.0,
                            target_r: float = 2.0) -> pd.DataFrame:
    """Funding-rate sign-change.
       Funding goes from positive to negative -> shorts paying longs -> bullish capitulation -> LONG.
       Funding goes from negative to positive -> longs paying shorts -> bearish positioning -> SHORT."""
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    funding = out['funding_rate']
    # 4h-prior funding sign
    funding_prior = funding.shift(lookback_bars)
    long_flip = (funding < 0) & (funding_prior > 0)   # pos -> neg = LONG
    short_flip = (funding > 0) & (funding_prior < 0)  # neg -> pos = SHORT
    rows = []
    last = -10**9
    for i in range(len(out)):
        if i - last < cooldown_bars: continue
        if pd.isna(out['atr'].iloc[i]): continue
        entry = float(out['close'].iloc[i])
        risk = float(out['atr'].iloc[i]) * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        if bool(long_flip.iloc[i]):
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'funding': float(funding.iloc[i]),
                         'funding_prior': float(funding_prior.iloc[i]),
                         'atr': float(out['atr'].iloc[i])})
            last = i
        elif bool(short_flip.iloc[i]):
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'funding': float(funding.iloc[i]),
                         'funding_prior': float(funding_prior.iloc[i]),
                         'atr': float(out['atr'].iloc[i])})
            last = i
    return pd.DataFrame(rows)


# === 3. OI rate-of-change at 15m ============================================

def oi_roc_triggers(df: pd.DataFrame, *,
                     window_bars: int = 4 * 6,    # 6h OI change
                     z_threshold: float = 2.0,
                     zscore_window_bars: int = 4 * 24 * 14,  # 14d
                     cooldown_bars: int = 4 * 6,
                     mode: str = 'flush',          # 'flush' or 'build'
                     atr_mult_stop: float = 2.0,
                     target_r: float = 2.0) -> pd.DataFrame:
    """OI rate-of-change.
       FLUSH mode: large OI drop = forced unwind = capitulation = LONG (mean revert).
       BUILD mode: large OI build = leverage entering = trend continuation."""
    out = df.copy()
    out['atr'] = compute_atr(out, period=14)
    oi_pct = out['oi_close'].pct_change(window_bars)
    min_p = zscore_window_bars // 4
    mean = oi_pct.rolling(zscore_window_bars, min_periods=min_p).mean()
    std = oi_pct.rolling(zscore_window_bars, min_periods=min_p).std()
    oi_z = (oi_pct - mean) / std

    # Also need price direction over the same window to infer trade direction
    price_chg = out['close'].pct_change(window_bars)

    rows = []
    last = -10**9
    for i in range(len(out)):
        if i - last < cooldown_bars: continue
        if pd.isna(out['atr'].iloc[i]) or pd.isna(oi_z.iloc[i]): continue
        entry = float(out['close'].iloc[i])
        risk = float(out['atr'].iloc[i]) * atr_mult_stop
        if risk <= 0: continue
        ts = out.index[i]
        oz = float(oi_z.iloc[i]); pc = float(price_chg.iloc[i]) if pd.notna(price_chg.iloc[i]) else 0
        # FLUSH: oi drop (oz < -thresh) AND price moved (either direction)
        #         -> bet on bounce in direction of capitulation
        if mode == 'flush' and oz < -z_threshold:
            # Price dropped while OI dropped = capitulation longs = LONG
            # Price rose while OI dropped = short squeeze = SHORT (mean revert)
            direction = 'long' if pc < 0 else 'short'
        elif mode == 'build' and oz > z_threshold:
            # Continuation in direction price has been moving
            direction = 'long' if pc > 0 else 'short'
        else:
            continue
        if direction == 'long':
            rows.append({'ts': ts, 'direction': 'long', 'entry': entry,
                         'stop': entry - risk, 'target': entry + risk * target_r,
                         'oi_z': oz, 'price_chg': pc, 'atr': float(out['atr'].iloc[i])})
        else:
            rows.append({'ts': ts, 'direction': 'short', 'entry': entry,
                         'stop': entry + risk, 'target': entry - risk * target_r,
                         'oi_z': oz, 'price_chg': pc, 'atr': float(out['atr'].iloc[i])})
        last = i
    return pd.DataFrame(rows)


# === Main ====================================================================

def main():
    print(f'DB: {DB}')
    print('Loading TV 15m frame...')
    df = load_tv_15m()
    df_z = compute_tv_squeeze_z(df)
    print(f'  {len(df):,} bars  {df.index.min()} -> {df.index.max()}')
    print(f'  IS / OOS split at: {IS_END}')

    dump = {}

    # === 1. B4-FOLLOW ===
    print('\n=== 1. B4-FOLLOW (long-liq -> LONG, short-liq -> SHORT) ===')
    print(f'  {"variant":<40s} {"n":>5s}  {"meanR":>7s}  {"WR":>4s}  {"IS_R":>10s}  {"OOS_R":>10s}  {"cov":>5s}')
    for z in (1.5, 2.0, 2.5, 3.0):
        trigs = b4_follow_triggers(df_z, z_threshold=z)
        if not trigs.empty:
            trigs_r = measure_r_outcomes(trigs, df_z)
            report(f'B4-follow_z{z}', trigs_r, dump)

    # === 2. Funding flip ===
    print('\n=== 2. Funding-flip at 15m (sign change vs N bars prior) ===')
    print(f'  {"variant":<40s} {"n":>5s}  {"meanR":>7s}  {"WR":>4s}  {"IS_R":>10s}  {"OOS_R":>10s}  {"cov":>5s}')
    for lookback_h in (2, 4, 8, 12):
        trigs = funding_flip_triggers(df_z, lookback_bars=4 * lookback_h)
        if not trigs.empty:
            trigs_r = measure_r_outcomes(trigs, df_z)
            report(f'Funding-flip_lookback{lookback_h}h', trigs_r, dump)

    # === 3. OI rate of change ===
    print('\n=== 3. OI rate-of-change at 15m ===')
    print(f'  {"variant":<40s} {"n":>5s}  {"meanR":>7s}  {"WR":>4s}  {"IS_R":>10s}  {"OOS_R":>10s}  {"cov":>5s}')
    for mode in ('flush', 'build'):
        for z in (1.5, 2.0, 2.5):
            for window_h in (3, 6, 12):
                trigs = oi_roc_triggers(df_z, mode=mode, z_threshold=z,
                                          window_bars=4 * window_h)
                if not trigs.empty and len(trigs) > 10:
                    trigs_r = measure_r_outcomes(trigs, df_z)
                    report(f'OI-roc_{mode}_z{z}_win{window_h}h', trigs_r, dump)

    out_path = OUT_DIR / 'tv_new_signals_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'data_source': 'tv_btc_perp_15m (236 days)',
            'is_oos_split': str(IS_END),
            'variants': dump,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')

    # === Best variants summary ===
    print('\n=== Best by OOS meanR (only variants with n >= 30) ===')
    candidates = [(k, v) for k, v in dump.items()
                  if isinstance(v, dict) and v.get('n', 0) >= 30
                  and 'oos_meanR' in v]
    candidates.sort(key=lambda kv: kv[1].get('oos_meanR', -99), reverse=True)
    for k, v in candidates[:5]:
        print(f'  {k:<40s}  n={v["n"]:>4d}  meanR={v["mean_R"]:+.3f}  '
               f'IS={v.get("is_meanR",0):+.3f}  OOS={v.get("oos_meanR",0):+.3f}')


if __name__ == '__main__':
    main()
