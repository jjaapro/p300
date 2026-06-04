"""validation_B13_hedge: hedge-mode range-profiting strategy.

Approach (incremental):

DIAGNOSTIC (step 1) — at each Triple trigger that fired inside a confirmed
range, simulate BOTH directions of the trade and see whether the opposite-
direction (hedge) leg has positive expectancy. If yes, hedge is real edge.

RANGE DETECTION:
  - Look back 48h (4*48 = 192 15m bars)
  - Range high = max(high), range low = min(low) in window
  - Range width = (range_high - range_low) / mid_price
  - Valid range if:
      * width >= 2 * ATR (wide enough to trade)
      * width <= 8 * ATR (not too wide, then it's not a "range" anymore)
      * at least 3 touches of high AND 3 touches of low
      * price not in extreme top/bottom 10% of range (room to fade)

HEDGE REPLAY (step 2):
  - At each Triple trigger inside a confirmed range, open BOTH long AND short
    at the trigger price, each at 50% size
  - Each leg has standard atr5 stop and atr5*6 target (= 6R)
  - Each leg can hit stop or target independently
  - TIF=72h per leg

COMPARISON:
  - Single-direction Triple (current strategy)
  - Hedge-in-range (pair-trade both legs)
  - Skip-when-in-range (just take normal trigger but only outside ranges)
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
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    compute_lsr_extremes, b5_triggers, load_lsr,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    features_at,
)
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (
    build_optimized, apply_filters, compute_volume_profile,
)

COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Range detection ========================================================

def detect_range(df_15m: pd.DataFrame, *,
                  window_bars: int = 4 * 48,    # 48h
                  min_atr_width: float = 2.0,
                  max_atr_width: float = 8.0,
                  min_touches: int = 3,
                  touch_atr_tol: float = 0.5,
                  ) -> pd.DataFrame:
    """At each bar, compute whether the trailing window_bars forms a valid
    range. Returns DataFrame with columns:
      range_high, range_low, range_width_atr, n_touch_high, n_touch_low,
      in_range (bool), range_pos (0 at low, 1 at high)
    """
    out = df_15m.copy()
    atr = compute_atr(out, period=14)
    out['atr_14'] = atr

    rh = out['high'].rolling(window_bars).max()
    rl = out['low'].rolling(window_bars).min()
    out['range_high'] = rh
    out['range_low'] = rl
    width = (rh - rl)
    out['range_width_atr'] = width / atr

    # Touches: bars where high was near rolling max (any time in window)
    # OR low was near rolling min. We use the bar's high/low directly.
    near_high = (out['high'] - rh.shift(1)).abs() / atr < touch_atr_tol
    near_low = (out['low'] - rl.shift(1)).abs() / atr < touch_atr_tol
    out['n_touch_high'] = near_high.rolling(window_bars).sum()
    out['n_touch_low'] = near_low.rolling(window_bars).sum()

    # Range valid
    out['in_range'] = (
        (out['range_width_atr'] >= min_atr_width) &
        (out['range_width_atr'] <= max_atr_width) &
        (out['n_touch_high'] >= min_touches) &
        (out['n_touch_low'] >= min_touches)
    )

    # Position in range
    out['range_pos'] = (out['close'] - rl) / width.replace(0, np.nan)
    # Skip extreme top/bottom 10% (already past range bound)
    out['range_pos_safe'] = out['range_pos'].between(0.10, 0.90)

    return out[['atr_14', 'range_high', 'range_low', 'range_width_atr',
                 'n_touch_high', 'n_touch_low', 'in_range', 'range_pos',
                 'range_pos_safe']]


# === Replay both directions independently ==================================

def replay_both_sides(trig, df_smc, df_atr, *,
                        atr_mult=5.0, target_r=6.0, tif_bars=4 * 72) -> dict | None:
    """At a single trigger, simulate BOTH long and short outcomes.
    Returns {long_r, short_r, long_exit_kind, short_exit_kind}."""
    ts = trig['ts']
    idx = df_smc.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df_smc) or df_smc.index[idx] != ts:
        idx = df_smc.index.searchsorted(ts, side='right') - 1
        if idx < 0: return None
    atr = float(df_atr['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0: return None
    entry = float(df_smc['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0: return None
    cost_R = (COST_BP / 10000.0) * (entry / risk)
    start = idx + 1
    end = min(start + tif_bars, len(df_smc))

    # Long side: stop at entry - risk, target at entry + 6*risk
    long_stop = entry - risk; long_target = entry + risk * target_r
    short_stop = entry + risk; short_target = entry - risk * target_r
    long_r = short_r = None
    long_exit_kind = short_exit_kind = None

    last_close = entry
    for j in range(start, end):
        bh = float(df_smc['high'].iloc[j])
        bl = float(df_smc['low'].iloc[j])
        bc = float(df_smc['close'].iloc[j])
        last_close = bc
        # Long
        if long_r is None:
            if bl <= long_stop:
                long_r = -1.0 - cost_R
                long_exit_kind = 'stop'
            elif bh >= long_target:
                long_r = (long_target - entry) / risk - cost_R
                long_exit_kind = 'target'
        # Short
        if short_r is None:
            if bh >= short_stop:
                short_r = -1.0 - cost_R
                short_exit_kind = 'stop'
            elif bl <= short_target:
                short_r = (entry - short_target) / risk - cost_R
                short_exit_kind = 'target'
        if long_r is not None and short_r is not None:
            break

    if long_r is None:
        long_r = (last_close - entry) / risk - cost_R
        long_exit_kind = 'tif'
    if short_r is None:
        short_r = (entry - last_close) / risk - cost_R
        short_exit_kind = 'tif'

    return {
        'ts': ts, 'entry': entry, 'risk': risk,
        'long_r': long_r, 'long_exit_kind': long_exit_kind,
        'short_r': short_r, 'short_exit_kind': short_exit_kind,
    }


def main():
    print('Building Triple triggers (5y BTC)...')
    df_15m = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  Triple triggers (5y): {len(triple):,}')

    print('\nComputing range states (48h window, 2-8 ATR width, ≥3 touches each side)...')
    range_df = detect_range(df_15m, window_bars=4 * 48,
                              min_atr_width=2.0, max_atr_width=8.0,
                              min_touches=3, touch_atr_tol=0.5)
    print(f'  In-range bars: {int(range_df["in_range"].sum()):,} of {len(range_df):,} '
           f'({range_df["in_range"].mean()*100:.1f}%)')

    print('\nAttaching range state to Triple triggers...')
    ts_idx = pd.DatetimeIndex(triple['ts'])
    ix = range_df.index.searchsorted(ts_idx, side='right') - 1
    triple = triple.copy()
    for col in ('in_range', 'range_pos', 'range_pos_safe',
                 'range_width_atr', 'range_high', 'range_low'):
        triple[col] = [float(range_df[col].iloc[i]) if 0 <= i < len(range_df) else np.nan
                        for i in ix]
    triple['in_range'] = triple['in_range'].astype(bool)

    n_in_range = int(triple['in_range'].sum())
    n_in_safe = int((triple['in_range'] & triple['range_pos_safe']).sum())
    print(f'  Triggers fired in-range: {n_in_range} of {len(triple)} '
           f'({n_in_range/len(triple)*100:.1f}%)')
    print(f'  Triggers in-range AND in safe zone (10-90% pos): {n_in_safe}')

    # === Replay both sides ===
    print('\nReplaying BOTH long and short for each trigger...')
    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)

    rows = []
    for _, t in triple.iterrows():
        r = replay_both_sides(t, df_smc, df_atr)
        if r is not None:
            r['direction'] = t['direction']
            r['in_range'] = bool(t['in_range'])
            r['range_pos_safe'] = bool(t.get('range_pos_safe', False))
            r['range_width_atr'] = float(t['range_width_atr'])
            r['range_pos'] = float(t.get('range_pos', 0.5))
            rows.append(r)
    rep = pd.DataFrame(rows)
    print(f'  Replayed: {len(rep)} trades')

    # === Diagnostic A: outcomes by direction, conditioned on in_range ===
    print('\n' + '=' * 80)
    print('=== DIAGNOSTIC A: Long vs short outcomes by range state ===')
    print('=' * 80)
    print(f'\n{"slice":<35s} {"n":>5s} {"long_R":>8s} {"short_R":>8s} {"hedge_R":>8s} {"long_wr":>8s} {"short_wr":>8s}')
    for label, mask in [
        ('all triggers (any state)', pd.Series([True] * len(rep), index=rep.index)),
        ('in-range trigger', rep['in_range']),
        ('in-range + safe zone (10-90% pos)', rep['in_range'] & rep['range_pos_safe']),
        ('NOT in-range', ~rep['in_range']),
    ]:
        sub = rep[mask]
        if sub.empty:
            print(f'  {label:<35s} empty'); continue
        long_r_mean = sub['long_r'].mean()
        short_r_mean = sub['short_r'].mean()
        hedge_r = (long_r_mean + short_r_mean) / 2     # 50/50 split
        long_wr = (sub['long_r'] > 0).mean()
        short_wr = (sub['short_r'] > 0).mean()
        print(f'  {label:<35s} {len(sub):>5d} {long_r_mean:>+8.3f} {short_r_mean:>+8.3f} '
               f'{hedge_r:>+8.3f} {long_wr:>8.0%} {short_wr:>8.0%}')

    # === Diagnostic B: outcomes by original direction in/out-of-range ===
    print('\n' + '=' * 80)
    print('=== DIAGNOSTIC B: Original direction performance vs in-range state ===')
    print('=' * 80)
    print(f'\n{"slice":<35s} {"n":>5s} {"orig_dir_R":>10s} {"opp_dir_R":>10s} {"hedge_R":>8s}')
    for label, mask in [
        ('all triggers', pd.Series([True] * len(rep), index=rep.index)),
        ('in-range', rep['in_range']),
        ('in-range + safe zone', rep['in_range'] & rep['range_pos_safe']),
        ('NOT in-range', ~rep['in_range']),
    ]:
        sub = rep[mask]
        if sub.empty: continue
        # For each trade, take the OUTCOME OF THE ORIGINAL TRIGGERED direction
        orig_r = np.where(sub['direction'] == 'long', sub['long_r'], sub['short_r'])
        opp_r = np.where(sub['direction'] == 'long', sub['short_r'], sub['long_r'])
        hedge_r = 0.5 * orig_r + 0.5 * opp_r
        print(f'  {label:<35s} {len(sub):>5d} {orig_r.mean():>+10.3f} '
               f'{opp_r.mean():>+10.3f} {hedge_r.mean():>+8.3f}')

    # === Hedge as additional sleeve: take BOTH directions on in-range triggers ===
    print('\n' + '=' * 80)
    print('=== STRATEGY VARIANTS ===')
    print('=' * 80)

    # Variant 1: Single-direction Triple (current — original direction only)
    # Variant 2: Hedge always (50/50 long+short on every trigger)
    # Variant 3: Hedge only in-range, single elsewhere
    # Variant 4: Skip in-range (only trade out-of-range)
    # Variant 5: Hedge only in-range AND safe zone

    def stats_from_outcomes(outcomes: np.ndarray, label: str) -> dict:
        if len(outcomes) == 0: return {'label': label, 'n': 0}
        cum = outcomes.cumsum()
        peak = np.maximum.accumulate(cum)
        dd = cum - peak
        n = len(outcomes)
        return {
            'label': label, 'n': int(n),
            'mean_R': round(float(np.mean(outcomes)), 3),
            'wr': round(float((outcomes > 0).mean()), 3),
            'cum_R': round(float(cum[-1]), 2),
            'max_dd_R': round(float(dd.min()), 2),
        }

    def show(s):
        if s.get('n', 0) == 0:
            print(f'  {s["label"]:<55s} empty'); return
        print(f'  {s["label"]:<55s} n={s["n"]:>4d}  R={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  maxDD={s["max_dd_R"]:+5.2f}')

    rep_sorted = rep.sort_values('ts').reset_index(drop=True)
    orig_outcomes = np.where(rep_sorted['direction'] == 'long',
                              rep_sorted['long_r'], rep_sorted['short_r'])
    opp_outcomes = np.where(rep_sorted['direction'] == 'long',
                              rep_sorted['short_r'], rep_sorted['long_r'])
    in_range_mask = rep_sorted['in_range'].values
    safe_mask = (rep_sorted['in_range'] & rep_sorted['range_pos_safe']).values

    print('\n--- Sleeve variants on full 5y Triple set ---')
    show(stats_from_outcomes(orig_outcomes, 'V1: single direction (current)'))
    hedge_all = 0.5 * orig_outcomes + 0.5 * opp_outcomes
    show(stats_from_outcomes(hedge_all, 'V2: hedge ALWAYS (50/50 on every trigger)'))
    v3 = np.where(in_range_mask, hedge_all, orig_outcomes)
    show(stats_from_outcomes(v3, 'V3: hedge in-range, single out-of-range'))
    v4 = orig_outcomes[~in_range_mask]
    show(stats_from_outcomes(v4, 'V4: SKIP in-range, single out-of-range'))
    v5 = np.where(safe_mask, hedge_all, orig_outcomes)
    show(stats_from_outcomes(v5, 'V5: hedge in-range+safe, single elsewhere'))
    # V6: hedge in-range, skip elsewhere (only trade ranges)
    v6 = hedge_all[in_range_mask]
    show(stats_from_outcomes(v6, 'V6: hedge ONLY in-range, skip elsewhere'))

    # === Save ===
    out_path = OUT_DIR / 'B13_hedge_diagnostic_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('B13 hedge-mode diagnostic on Triple triggers (full 5y). '
                      'Range detection: 48h window, 2-8 ATR width, 3+ touches each side. '
                      'Tests: per-direction outcomes by range state + 6 sleeve variants.'),
            'n_triple_triggers': len(triple),
            'n_in_range': n_in_range,
            'n_in_range_safe': n_in_safe,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
