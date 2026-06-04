"""validation_C5_smc_features: codify Smart Money Concepts structure
features (Order Block, FVG, BOS, CHoCH) and test them as entry filters
and exit-placement rules on Triple composite + atr4 stop.

SMC primitives implemented (pure compute on 15m OHLC, no new data):

  Swing pivots:
    A bar is a swing-high if its high > N preceding AND >= N following highs.
    swing-low mirror. Default N = 5.

  Break of Structure (BOS):
    The latest close exceeds the most recent swing-high while the trend
    state is already up (continuation), or breaks the most recent swing-low
    while trend is down. Updates trend_state.

  Change of Character (CHoCH):
    First counter-trend BOS — flip in trend state. From "down" to "up" is
    CHoCH_up (bullish flip), and vice versa.

  Order Block (OB):
    On BOS_up firing at bar i, the bullish OB is the last down-close bar
    (close<open) in the impulse leg leading up to i. The OB zone is
    [low, high] of that bar. Bearish OB mirror.

  Fair Value Gap (FVG):
    At bar i, bullish FVG if df.high[i-2] < df.low[i] (3-bar gap up).
    Bearish FVG if df.low[i-2] > df.high[i].
    Zone = (high[i-2], low[i]) bullish or (high[i], low[i-2]) bearish.

  Fill rule:
    A bullish OB/FVG is "filled" once any subsequent bar's low <= its
    upper edge (price retraced into it). Bearish mirror with bar high.

Tests for each Triple trigger:
  - SMC-aligned filter: take long only if structure trend == 'up' at entry
    (BOS_up most recent); analogous for shorts.
  - Nearest-OB filter: take long if a fresh bullish OB exists within X*R
    below entry (support); skip if a fresh bearish OB sits within X*R above
    (resistance from opposite OB).
  - Nearest-FVG conflict filter: skip if an unfilled bullish FVG sits
    below entry for a long (the FVG would magnet price down first).
  - SMC-aware exit: TP at nearest opposite-side OB/FVG instead of fixed R.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve()
while not (ROOT / 'data' / 'databases' / 'prod.db').exists():
    if ROOT == ROOT.parent:
        raise RuntimeError('locate prod.db')
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'
OUT_DIR.mkdir(parents=True, exist_ok=True)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers

COST_BP = 18.0
TIF_BARS = 4 * 24
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === SMC primitives ========================================================

def compute_pivots(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Mark pivot highs / lows with N bars on each side. Uses confirmed
    pivots only — a pivot at index i is known at index i+n (not before)."""
    h = df['high'].values
    l = df['low'].values
    is_ph = np.zeros(len(df), dtype=bool)
    is_pl = np.zeros(len(df), dtype=bool)
    for i in range(n, len(df) - n):
        # Strictly higher than preceding n; >= following n
        if h[i] > h[i-n:i].max() and h[i] >= h[i+1:i+n+1].max():
            is_ph[i] = True
        if l[i] < l[i-n:i].min() and l[i] <= l[i+1:i+n+1].min():
            is_pl[i] = True
    out = df.copy()
    out['piv_high'] = is_ph
    out['piv_low'] = is_pl
    return out


def compute_smc_state(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Walk the dataframe to maintain trend state, BOS/CHoCH events, and
    track current 'last confirmed' swing-high / swing-low. Confirms pivots
    only at +n bars (so it's strictly causal for backtesting)."""
    out = df.copy()
    n_bars = len(out)

    # State
    trend = 'undef'      # 'up' | 'down' | 'undef'
    last_sh = np.nan     # last confirmed swing-high price
    last_sh_idx = -1
    last_sl = np.nan
    last_sl_idx = -1
    # Per-bar outputs
    trend_arr = np.full(n_bars, 'undef', dtype=object)
    last_sh_arr = np.full(n_bars, np.nan)
    last_sl_arr = np.full(n_bars, np.nan)
    last_sh_idx_arr = np.full(n_bars, -1, dtype=int)
    last_sl_idx_arr = np.full(n_bars, -1, dtype=int)
    bos_event = np.full(n_bars, '', dtype=object)
    choch_event = np.full(n_bars, '', dtype=object)

    h_arr = out['high'].values
    l_arr = out['low'].values
    c_arr = out['close'].values
    ph_arr = out['piv_high'].values
    pl_arr = out['piv_low'].values

    for i in range(n_bars):
        # Pivot at index (i - n) was confirmed at bar i
        confirm_idx = i - n
        if confirm_idx >= 0:
            if ph_arr[confirm_idx]:
                last_sh = h_arr[confirm_idx]
                last_sh_idx = confirm_idx
            if pl_arr[confirm_idx]:
                last_sl = l_arr[confirm_idx]
                last_sl_idx = confirm_idx

        # Check BOS/CHoCH on current close
        if not np.isnan(last_sh) and c_arr[i] > last_sh:
            if trend == 'up':
                bos_event[i] = 'BOS_up'
            else:
                choch_event[i] = 'CHoCH_up'
                bos_event[i] = 'BOS_up'      # CHoCH is also a BOS
            trend = 'up'
            last_sh = np.nan       # consumed
        if not np.isnan(last_sl) and c_arr[i] < last_sl:
            if trend == 'down':
                bos_event[i] = 'BOS_down'
            else:
                choch_event[i] = 'CHoCH_down'
                bos_event[i] = 'BOS_down'
            trend = 'down'
            last_sl = np.nan

        trend_arr[i] = trend
        last_sh_arr[i] = last_sh
        last_sl_arr[i] = last_sl
        last_sh_idx_arr[i] = last_sh_idx
        last_sl_idx_arr[i] = last_sl_idx

    out['trend'] = trend_arr
    out['last_swing_high'] = last_sh_arr
    out['last_swing_low'] = last_sl_arr
    out['last_sh_idx'] = last_sh_idx_arr
    out['last_sl_idx'] = last_sl_idx_arr
    out['bos_event'] = bos_event
    out['choch_event'] = choch_event
    return out


def compute_fvgs(df: pd.DataFrame) -> list[dict]:
    """Identify FVGs in causal order. Each FVG = dict with keys:
      idx_created (bar index where it became visible — that's i+1 because
                    we need bar i+1 confirmed),
      direction ('bull' | 'bear'),
      zone_low, zone_high,
      idx_filled (None until a subsequent bar fills into the zone).

    A bullish FVG at bar i is created when df.high[i-2] < df.low[i] —
    the 3-bar pattern is complete at bar i. Fill: any subsequent bar
    whose low <= zone_high (touched into the gap).
    """
    h = df['high'].values; l = df['low'].values
    fvgs = []
    n = len(df)
    for i in range(2, n):
        # bullish
        if h[i-2] < l[i]:
            fvgs.append({
                'idx_created': i,
                'direction': 'bull',
                'zone_low': float(h[i-2]),
                'zone_high': float(l[i]),
                'idx_filled': None,
            })
        # bearish
        if l[i-2] > h[i]:
            fvgs.append({
                'idx_created': i,
                'direction': 'bear',
                'zone_low': float(h[i]),
                'zone_high': float(l[i-2]),
                'idx_filled': None,
            })

    # Mark fills (causal)
    # Sort by idx_created for the sweep
    fvgs.sort(key=lambda x: x['idx_created'])
    for fvg in fvgs:
        zl, zh = fvg['zone_low'], fvg['zone_high']
        d = fvg['direction']
        for j in range(fvg['idx_created'], n):
            if d == 'bull' and l[j] <= zh:
                fvg['idx_filled'] = j
                break
            if d == 'bear' and h[j] >= zl:
                fvg['idx_filled'] = j
                break
    return fvgs


def compute_order_blocks(df_smc: pd.DataFrame) -> list[dict]:
    """Identify Order Blocks from BOS events. For each BOS_up, scan back
    from the BOS bar to the most recent close<open bar (bullish OB) inside
    the impulse leg starting at last_sl_idx. Mirror for bear.
    """
    h = df_smc['high'].values
    l = df_smc['low'].values
    o = df_smc['open'].values
    c = df_smc['close'].values
    bos = df_smc['bos_event'].values
    sl_idx = df_smc['last_sl_idx'].values
    sh_idx = df_smc['last_sh_idx'].values

    obs = []
    n = len(df_smc)
    for i in range(n):
        ev = bos[i]
        if ev == 'BOS_up':
            # Impulse leg starts at the swing low that preceded this BOS
            leg_start = max(int(sl_idx[i]), 0)
            # Find most recent close<open bar in [leg_start, i)
            best = -1
            for j in range(i - 1, leg_start - 1, -1):
                if c[j] < o[j]:
                    best = j
                    break
            if best >= 0:
                obs.append({
                    'idx_created': i,    # OB visible at BOS confirmation
                    'idx_origin': best,
                    'direction': 'bull',
                    'zone_low': float(l[best]),
                    'zone_high': float(h[best]),
                    'idx_filled': None,
                })
        elif ev == 'BOS_down':
            leg_start = max(int(sh_idx[i]), 0)
            best = -1
            for j in range(i - 1, leg_start - 1, -1):
                if c[j] > o[j]:
                    best = j
                    break
            if best >= 0:
                obs.append({
                    'idx_created': i,
                    'idx_origin': best,
                    'direction': 'bear',
                    'zone_low': float(l[best]),
                    'zone_high': float(h[best]),
                    'idx_filled': None,
                })

    # Mark fills (causal): a bull OB is filled when subsequent low <= zone_high
    for ob in obs:
        for j in range(ob['idx_created'], n):
            if ob['direction'] == 'bull' and l[j] <= ob['zone_high']:
                ob['idx_filled'] = j
                break
            if ob['direction'] == 'bear' and h[j] >= ob['zone_low']:
                ob['idx_filled'] = j
                break
    return obs


# === Feature lookup at trigger time ========================================

def features_at(idx: int, entry: float, direction: str, risk: float,
                 df_smc: pd.DataFrame,
                 fvgs: list[dict], obs: list[dict]) -> dict:
    """For a trigger at bar idx with given entry/direction/risk, compute:
      - trend at entry
      - nearest unfilled OB in trade direction below (long) / above (short)
      - nearest unfilled opposite OB above (long) / below (short)
      - nearest unfilled bullish FVG below entry; bearish FVG above
    All distances in R units.
    """
    feats = {
        'trend_at_entry': df_smc['trend'].iloc[idx],
        'dist_support_OB_R': np.nan,        # bull OB below (long) / bear OB above (short)
        'dist_resist_OB_R': np.nan,         # opposite-direction OB in the way
        'dist_below_bull_FVG_R': np.nan,    # for longs: unfilled bull FVG below entry
        'dist_above_bear_FVG_R': np.nan,    # for longs: unfilled bear FVG above (potential TP)
        'has_recent_choch': False,
        'choch_dir': '',
        'bars_since_choch': -1,
    }

    # OBs: filter to unfilled-at-idx
    unfilled_obs = [ob for ob in obs
                    if ob['idx_created'] <= idx
                    and (ob['idx_filled'] is None or ob['idx_filled'] > idx)]
    unfilled_fvgs = [fv for fv in fvgs
                      if fv['idx_created'] <= idx
                      and (fv['idx_filled'] is None or fv['idx_filled'] > idx)]

    # For LONG: support = nearest bull OB BELOW entry; resistance = nearest bear OB ABOVE
    if direction == 'long':
        below_bull = [ob for ob in unfilled_obs
                       if ob['direction'] == 'bull' and ob['zone_high'] < entry]
        if below_bull:
            best = max(below_bull, key=lambda x: x['zone_high'])
            feats['dist_support_OB_R'] = (entry - best['zone_high']) / risk
        above_bear = [ob for ob in unfilled_obs
                       if ob['direction'] == 'bear' and ob['zone_low'] > entry]
        if above_bear:
            best = min(above_bear, key=lambda x: x['zone_low'])
            feats['dist_resist_OB_R'] = (best['zone_low'] - entry) / risk
        # bullish FVG below — a magnet that could pull price down first
        below_bull_fvg = [fv for fv in unfilled_fvgs
                           if fv['direction'] == 'bull' and fv['zone_high'] < entry]
        if below_bull_fvg:
            best = max(below_bull_fvg, key=lambda x: x['zone_high'])
            feats['dist_below_bull_FVG_R'] = (entry - best['zone_high']) / risk
        # bearish FVG above — potential TP target
        above_bear_fvg = [fv for fv in unfilled_fvgs
                           if fv['direction'] == 'bear' and fv['zone_low'] > entry]
        if above_bear_fvg:
            best = min(above_bear_fvg, key=lambda x: x['zone_low'])
            feats['dist_above_bear_FVG_R'] = (best['zone_low'] - entry) / risk
    else:    # short
        above_bear = [ob for ob in unfilled_obs
                       if ob['direction'] == 'bear' and ob['zone_low'] > entry]
        if above_bear:
            best = min(above_bear, key=lambda x: x['zone_low'])
            feats['dist_support_OB_R'] = (best['zone_low'] - entry) / risk
        below_bull = [ob for ob in unfilled_obs
                       if ob['direction'] == 'bull' and ob['zone_high'] < entry]
        if below_bull:
            best = max(below_bull, key=lambda x: x['zone_high'])
            feats['dist_resist_OB_R'] = (entry - best['zone_high']) / risk
        # bearish FVG above — magnet
        above_bear_fvg = [fv for fv in unfilled_fvgs
                           if fv['direction'] == 'bear' and fv['zone_low'] > entry]
        if above_bear_fvg:
            best = min(above_bear_fvg, key=lambda x: x['zone_low'])
            feats['dist_below_bull_FVG_R'] = (best['zone_low'] - entry) / risk
        # bullish FVG below — potential TP
        below_bull_fvg = [fv for fv in unfilled_fvgs
                           if fv['direction'] == 'bull' and fv['zone_high'] < entry]
        if below_bull_fvg:
            best = max(below_bull_fvg, key=lambda x: x['zone_high'])
            feats['dist_above_bear_FVG_R'] = (entry - best['zone_high']) / risk

    # CHoCH lookback (last 100 bars)
    lookback_start = max(0, idx - 100)
    choch_col = df_smc['choch_event'].iloc[lookback_start:idx + 1].values
    for k in range(len(choch_col) - 1, -1, -1):
        ev = choch_col[k]
        if ev:
            feats['has_recent_choch'] = True
            feats['choch_dir'] = ev
            feats['bars_since_choch'] = (len(choch_col) - 1 - k)
            break

    return feats


# === Replay ================================================================

def replay_one(trig, df_smc: pd.DataFrame, df_atr: pd.DataFrame,
                fvgs, obs, *,
                atr_mult: float = 4.0, target_r: float = 3.0,
                tif_bars: int = TIF_BARS,
                tp_mode: str = 'fixed',          # 'fixed' | 'ob' | 'fvg'
                tp_cap_R: float = 5.0,
                ) -> dict | None:
    direction = trig['direction']
    ts = trig['ts']
    idx = df_smc.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df_smc) or df_smc.index[idx] != ts:
        idx = df_smc.index.searchsorted(ts, side='right') - 1
        if idx < 0:
            return None
    atr = float(df_atr['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    entry = float(df_smc['close'].iloc[idx])
    risk = atr * atr_mult
    if risk <= 0:
        return None
    if direction == 'long':
        stop = entry - risk
    else:
        stop = entry + risk

    # Attach features at trigger time
    feats = features_at(idx, entry, direction, risk, df_smc, fvgs, obs)

    # TP selection
    tp_source = 'fixed'
    if tp_mode == 'fixed':
        target = entry + risk * target_r if direction == 'long' else entry - risk * target_r
    elif tp_mode == 'ob':
        # TP at nearest opposite-side OB above (long) / below (short)
        dist_R = feats['dist_resist_OB_R']
        if not np.isnan(dist_R) and 0.5 <= dist_R <= tp_cap_R:
            target = entry + risk * dist_R if direction == 'long' else entry - risk * dist_R
            tp_source = 'ob'
        else:
            target = entry + risk * target_r if direction == 'long' else entry - risk * target_r
    elif tp_mode == 'fvg':
        dist_R = feats['dist_above_bear_FVG_R']
        if not np.isnan(dist_R) and 0.5 <= dist_R <= tp_cap_R:
            target = entry + risk * dist_R if direction == 'long' else entry - risk * dist_R
            tp_source = 'fvg'
        else:
            target = entry + risk * target_r if direction == 'long' else entry - risk * target_r

    cost_R = (COST_BP / 10000.0) * (entry / risk)
    start = idx + 1
    end = min(start + tif_bars, len(df_smc))
    outcome = None; exit_kind = None; exit_ts = None
    if direction == 'long':
        for j in range(start, end):
            bh = float(df_smc['high'].iloc[j]); bl = float(df_smc['low'].iloc[j])
            if bl <= stop:
                outcome = (stop - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bh >= target:
                outcome = (target - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break
    else:
        for j in range(start, end):
            bh = float(df_smc['high'].iloc[j]); bl = float(df_smc['low'].iloc[j])
            if bh >= stop:
                outcome = (entry - stop) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bl <= target:
                outcome = (entry - target) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break
    if outcome is None:
        last_close = float(df_smc['close'].iloc[end - 1])
        if direction == 'long':
            outcome = (last_close - entry) / risk - cost_R
        else:
            outcome = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'; exit_ts = df_smc.index[end - 1]

    rec = {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target,
        'target_R': round((target - entry) / risk if direction == 'long'
                           else (entry - target) / risk, 3),
        'risk': risk,
        'r_outcome': outcome, 'exit_kind': exit_kind,
        'tp_source': tp_source,
    }
    rec.update(feats)
    return rec


def replay_all(triggers, df_smc, df_atr, fvgs, obs, **kw):
    rows = []
    for _, row in triggers.iterrows():
        r = replay_one(row, df_smc, df_atr, fvgs, obs, **kw)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


def summarize(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]
    oos_set = t[t['ts'] > IS_END]
    return {
        'label': label,
        'n': int(len(t)),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'targets': int((t['exit_kind'] == 'target').sum()),
        'stops': int((t['exit_kind'] == 'stop').sum()),
        'tifs': int((t['exit_kind'] == 'tif').sum()),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def main():
    print('Loading 15m BTC + computing SMC structure...')
    df = load_btc_15m()
    print(f'  bars: {len(df):,}  {df.index.min()} -> {df.index.max()}')
    print('  Pivots (n=5)...')
    df_p = compute_pivots(df, n=5)
    print('  SMC state (trend / BOS / CHoCH)...')
    df_smc = compute_smc_state(df_p, n=5)
    print('  Order Blocks...')
    obs = compute_order_blocks(df_smc)
    print(f'    {len(obs):,} OBs ({sum(o["direction"]=="bull" for o in obs)} bull, '
           f'{sum(o["direction"]=="bear" for o in obs)} bear)')
    print('  FVGs...')
    fvgs = compute_fvgs(df_smc)
    print(f'    {len(fvgs):,} FVGs ({sum(f["direction"]=="bull" for f in fvgs)} bull, '
           f'{sum(f["direction"]=="bear" for f in fvgs)} bear)')

    print('  ATR + Triple composite...')
    df_atr = df_smc.copy()
    df_b1 = compute_moneyflow_signal(df)
    df_atr['atr'] = compute_atr(df_atr, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  Triple triggers: {len(triple):,}')

    # === Baseline: fixed atr4_t3R ===
    print('\n--- Baseline atr4_t3R with SMC features attached ---')
    base = replay_all(triple, df_smc, df_atr, fvgs, obs,
                       atr_mult=4.0, target_r=3.0, tp_mode='fixed')
    sb = summarize(base, 'baseline_atr4_t3R')
    print(f'  n={sb["n"]}  meanR={sb["mean_R"]}  WR={sb["wr"]}  '
           f'cumR={sb["cum_R"]}  maxDD={sb["max_dd_R"]}')
    all_results = {'baseline_atr4_t3R': sb}

    # === Diagnostic: by trend at entry ===
    print('\n--- R by trend at entry ---')
    for trend, sub in base.groupby('trend_at_entry'):
        if len(sub) < 5: continue
        # Aligned subset
        aligned_long = sub[(sub['direction'] == 'long')]
        aligned_short = sub[(sub['direction'] == 'short')]
        s = summarize(sub, f'trend={trend}')
        print(f'  trend_at_entry={trend!s:<10s}  n={s["n"]:>3d}  '
               f'meanR={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}')

    print('\n--- Long-trend alignment ---')
    aligned = base[((base['direction'] == 'long') & (base['trend_at_entry'] == 'up')) |
                    ((base['direction'] == 'short') & (base['trend_at_entry'] == 'down'))]
    counter = base[((base['direction'] == 'long') & (base['trend_at_entry'] == 'down')) |
                    ((base['direction'] == 'short') & (base['trend_at_entry'] == 'up'))]
    sa = summarize(aligned, 'aligned')
    sc = summarize(counter, 'counter')
    print(f'  Aligned (with-trend):    n={sa["n"]:>3d}  meanR={sa["mean_R"]:+.3f}  '
           f'WR={sa["wr"]:.0%}  cumR={sa["cum_R"]:+.1f}  maxDD={sa["max_dd_R"]:+.1f}')
    print(f'  Counter-trend:           n={sc["n"]:>3d}  meanR={sc["mean_R"]:+.3f}  '
           f'WR={sc["wr"]:.0%}  cumR={sc["cum_R"]:+.1f}  maxDD={sc["max_dd_R"]:+.1f}')
    all_results['aligned'] = sa
    all_results['counter'] = sc

    # Note: Triple composite is INTENTIONALLY mean-reversion, so counter-trend is the
    # expected mode. With-trend should under-perform.

    # === Filter: OB-support — only take longs with bull OB within X*R below ===
    print('\n--- Entry filter: support OB within X*R ---')
    for x in (1.0, 2.0, 3.0, 5.0):
        ok = base[(base['dist_support_OB_R'] <= x) & base['dist_support_OB_R'].notna()]
        s = summarize(ok, f'support_OB_within_{x}R')
        all_results[s['label']] = s
        print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Filter: skip if resistance OB within X*R ahead ===
    print('\n--- Entry filter: NO resistance OB within X*R ---')
    for x in (1.0, 2.0, 3.0):
        ok = base[(base['dist_resist_OB_R'] > x) | base['dist_resist_OB_R'].isna()]
        s = summarize(ok, f'no_resist_OB_within_{x}R')
        all_results[s['label']] = s
        print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Filter: skip if conflict FVG (FVG below for long / FVG above for short) ===
    print('\n--- Entry filter: NO conflict FVG within X*R ---')
    for x in (1.0, 2.0):
        ok = base[(base['dist_below_bull_FVG_R'] > x) | base['dist_below_bull_FVG_R'].isna()]
        s = summarize(ok, f'no_conflict_FVG_within_{x}R')
        all_results[s['label']] = s
        print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Filter: recent CHoCH in trade direction ===
    print('\n--- Entry filter: recent CHoCH in trade direction (bars since <=N) ---')
    for n in (20, 50, 100):
        mask = (base['has_recent_choch']) & (base['bars_since_choch'] <= n) & (
            ((base['direction'] == 'long') & (base['choch_dir'] == 'CHoCH_up')) |
            ((base['direction'] == 'short') & (base['choch_dir'] == 'CHoCH_down'))
        )
        ok = base[mask]
        s = summarize(ok, f'recent_choch_aligned_{n}bars')
        all_results[s['label']] = s
        print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Exit placement: OB or FVG TP instead of fixed 3R ===
    print('\n--- Exit placement variants ---')
    for tp_mode in ('ob', 'fvg'):
        rep = replay_all(triple, df_smc, df_atr, fvgs, obs,
                          atr_mult=4.0, target_r=3.0, tp_mode=tp_mode,
                          tp_cap_R=5.0)
        s = summarize(rep, f'tp_{tp_mode}')
        cluster_pct = float((rep['tp_source'] == tp_mode).mean()) * 100
        mean_tgt_R = float(rep['target_R'].mean())
        all_results[s['label']] = s
        print(f'  {s["label"]:<35s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'src%={cluster_pct:.0f}  mTgtR={mean_tgt_R:.2f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Stacked: SMC filter + no-tilt ===
    print('\n--- Stacking SMC filters + no-tilt ---')
    base_s = base.sort_values('ts').reset_index(drop=True)
    losses_before = []
    cur = 0
    for r in base_s['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        losses_before.append(cur)
    base_s['consec_losses_before'] = losses_before
    no_tilt = base_s['consec_losses_before'] == 0

    candidates = [
        ('no_tilt_only', no_tilt),
        ('no_tilt + support_OB_within_3R',
         no_tilt & (base_s['dist_support_OB_R'] <= 3.0) & base_s['dist_support_OB_R'].notna()),
        ('no_tilt + no_resist_OB_within_2R',
         no_tilt & ((base_s['dist_resist_OB_R'] > 2.0) | base_s['dist_resist_OB_R'].isna())),
        ('no_tilt + no_conflict_FVG_within_2R',
         no_tilt & ((base_s['dist_below_bull_FVG_R'] > 2.0) | base_s['dist_below_bull_FVG_R'].isna())),
    ]
    for label, mask in candidates:
        s = summarize(base_s[mask], label)
        all_results[label] = s
        print(f'  {label:<45s} n={s["n"]:>3d}  meanR={s["mean_R"]:+.3f}  '
               f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:+.1f}  maxDD={s["max_dd_R"]:+.1f}  '
               f'IS={s["IS_meanR"]:+.3f}  OOS={s["OOS_meanR"]:+.3f}')

    # === Ranking ===
    print('\n=== Top by mean R (n>=100) ===')
    ranked = sorted([(k, v) for k, v in all_results.items()
                       if v.get('n', 0) >= 100 and k != 'baseline_atr4_t3R'],
                      key=lambda kv: kv[1].get('mean_R', 0), reverse=True)[:8]
    for k, v in ranked:
        print(f'  {k:<48s}  meanR={v["mean_R"]:+.3f}  cumR={v["cum_R"]:+7.1f}  '
               f'maxDD={v["max_dd_R"]:+5.1f}  IS={v["IS_meanR"]:+.3f}  OOS={v["OOS_meanR"]:+.3f}')

    print('\n=== Top by max-DD (n>=100) ===')
    ranked_dd = sorted([(k, v) for k, v in all_results.items()
                          if v.get('n', 0) >= 100],
                         key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)[:8]
    for k, v in ranked_dd:
        print(f'  {k:<48s}  maxDD={v["max_dd_R"]:+5.1f}  meanR={v["mean_R"]:+.3f}  '
               f'cumR={v["cum_R"]:+7.1f}')

    out_path = OUT_DIR / 'C5_smc_features_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('C5 SMC features: OB/FVG/BOS/CHoCH causally computed '
                      'from 15m OHLC; tested as filters and exit placement on '
                      'Triple + atr4 stop.'),
            'n_pivots_high': int(df_smc['piv_high'].sum()),
            'n_pivots_low': int(df_smc['piv_low'].sum()),
            'n_obs_total': len(obs),
            'n_fvgs_total': len(fvgs),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
