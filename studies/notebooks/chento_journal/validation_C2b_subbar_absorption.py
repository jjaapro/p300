"""validation_C2b_subbar_absorption: test Rule 1 (whale CVD vs price velocity)
at SUB-bar resolutions (1m, 5m) to see if absorption shows up when aggregation
windows are shorter.

C2 at 15m showed standalone Rule 1 is NEGATIVE. The hypothesis is that at 15m
the absorption signal is averaged out — a 2-3 minute burst of whales hitting
the book without price moving is lost when summed over 15 minutes. At 1m or
5m, the burst remains visible.

Tests:
  1. Standalone Rule 1 at 1m, 5m, 15m using whale-CVD divergence
  2. Trade-cluster absorption proxy: 1m bar with >P95 taker volume on one
     side AND <P50 absolute price move = "eaten" = reversal expected
  3. Absorption-signal as gate on optimized Triple composite

For (1), the trade is opened on the next 15m bar (so it can use the
existing replay infrastructure), but the signal fires on the sub-bar.
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
    load_btc_15m, compute_moneyflow_signal, b1_triggers, compute_atr,
    measure_r_outcomes,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    replay_one,
)

IS_END = pd.Timestamp('2026-04-30 23:59:59', tz='UTC')


# === Loaders ===============================================================

def load_agg_at(bar_seconds: int) -> pd.DataFrame:
    """Load the binance_agg_trades_{label} table at the given resolution."""
    label = {60: '1m', 300: '5m', 900: '15m'}[bar_seconds]
    table = f'binance_agg_trades_{label}'
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f"""
        SELECT timestamp, whale_buy_usd, whale_sell_usd,
               mid_buy_usd, mid_sell_usd, n_trades
        FROM {table}
        WHERE asset='BTCUSDT'
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df['whale_cvd_usd'] = df['whale_buy_usd'] - df['whale_sell_usd']
    df['mid_cvd_usd'] = df['mid_buy_usd'] - df['mid_sell_usd']
    df['big_cvd_usd'] = df['whale_cvd_usd'] + df['mid_cvd_usd']    # whale+mid = "smart taker"
    return df


def attach_price(df_flow: pd.DataFrame, df_15m_close: pd.Series,
                  bar_seconds: int) -> pd.DataFrame:
    """For each flow bar, attach the 15m close that contains/just-after the bar.
    For sub-bar resolutions we resample 15m close to the bar's resolution by
    forward-fill (which is causal: at any given minute, the latest known 15m
    close is what we'd see if checking at that minute).
    """
    out = df_flow.copy()
    close_resampled = df_15m_close.reindex(out.index, method='ffill')
    out['close'] = close_resampled.values
    out['close_chg_pct'] = out['close'].pct_change()
    return out


def compute_zscores(out: pd.DataFrame, bar_seconds: int) -> pd.DataFrame:
    """Compute rolling z-scores for whale CVD + close-velocity at the
    given resolution. Window = ~14 days at the resolution."""
    bars_per_day = 86400 // bar_seconds
    win = 14 * bars_per_day
    min_p = max(200, win // 8)
    mu_w = out['whale_cvd_usd'].rolling(win, min_periods=min_p).mean()
    sd_w = out['whale_cvd_usd'].rolling(win, min_periods=min_p).std()
    out['whale_cvd_z'] = (out['whale_cvd_usd'] - mu_w) / sd_w
    mu_b = out['big_cvd_usd'].rolling(win, min_periods=min_p).mean()
    sd_b = out['big_cvd_usd'].rolling(win, min_periods=min_p).std()
    out['big_cvd_z'] = (out['big_cvd_usd'] - mu_b) / sd_b
    # Velocity = pct_change over a "1h equivalent" lookback at this resolution
    one_h_bars = max(1, 3600 // bar_seconds)
    vel = out['close'].pct_change(one_h_bars)
    mu_v = vel.rolling(win, min_periods=min_p).mean()
    sd_v = vel.rolling(win, min_periods=min_p).std()
    out['vel_z'] = (vel - mu_v) / sd_v
    return out


# === Trigger generation at sub-bar ==========================================

def signal_triggers_subbar(flow_z: pd.DataFrame, df_15m: pd.DataFrame, *,
                            whale_z_threshold: float = 1.5,
                            vel_z_max: float = 0.5,
                            cooldown_min: float = 60.0,
                            atr_mult_stop: float = 2.0,
                            target_r: float = 2.0,
                            ) -> pd.DataFrame:
    """Fire a trade signal whenever Rule 1 form fires at the sub-bar.

    SHORT: whale_cvd_z > +threshold AND vel_z < +max
    LONG:  whale_cvd_z < -threshold AND vel_z > -max

    Entry is the NEXT 15m close (snap-up to next 15m boundary) so the trade
    can be replayed on the 15m frame. Cooldown prevents back-to-back fills.
    """
    if 'atr' not in df_15m.columns:
        df_15m = df_15m.copy()
        df_15m['atr'] = compute_atr(df_15m, period=14)
    triggers = []
    last_signal_ts = pd.Timestamp('1970-01-01', tz='UTC')
    cooldown = pd.Timedelta(minutes=cooldown_min)

    for ts, row in flow_z.iterrows():
        wz = row['whale_cvd_z']; vz = row['vel_z']
        if pd.isna(wz) or pd.isna(vz):
            continue
        if ts - last_signal_ts < cooldown:
            continue
        direction = None
        if wz > whale_z_threshold and vz < vel_z_max:
            direction = 'short'
        elif wz < -whale_z_threshold and vz > -vel_z_max:
            direction = 'long'
        if direction is None:
            continue
        # Snap to next 15m boundary (entry)
        next_15m = ts.floor('15min')
        if next_15m <= ts:
            next_15m = next_15m + pd.Timedelta(minutes=15)
        # Find that bar in df_15m
        idx_match = df_15m.index.searchsorted(next_15m, side='left')
        if idx_match >= len(df_15m):
            continue
        actual_ts = df_15m.index[idx_match]
        entry = float(df_15m['close'].iloc[idx_match])
        atr = float(df_15m['atr'].iloc[idx_match])
        if pd.isna(atr) or atr <= 0:
            continue
        risk = atr * atr_mult_stop
        if direction == 'long':
            stop = entry - risk
            target = entry + risk * target_r
        else:
            stop = entry + risk
            target = entry - risk * target_r
        triggers.append({
            'ts': actual_ts, 'direction': direction,
            'entry': entry, 'stop': stop, 'target': target,
            'whale_cvd_z': float(wz), 'vel_z': float(vz),
            'signal_ts': ts,
        })
        last_signal_ts = ts
    return pd.DataFrame(triggers)


# === Trade-cluster absorption proxy ========================================

def absorption_triggers(flow_z: pd.DataFrame, df_15m: pd.DataFrame, *,
                          vol_pctile: float = 0.95,
                          move_pctile: float = 0.5,
                          cooldown_min: float = 60.0,
                          atr_mult_stop: float = 2.0,
                          target_r: float = 2.0,
                          ) -> pd.DataFrame:
    """Identify bars where taker volume on one side is in the top
    `vol_pctile` % of recent bars, BUT the price move (absolute) is below
    `move_pctile` median. That's the "ate the book" pattern.

    Direction of fade = opposite of which side was eaten:
      - if big_cvd_usd (taker buy) was top-pctile and price didn't rise => SHORT
      - if big_cvd_usd was bottom-pctile (heavy taker sell) and price didn't
        fall => LONG
    """
    out = flow_z.copy()
    win_pct = 14 * (86400 // (out.index[1] - out.index[0]).seconds)
    out['big_abs_usd'] = out['big_cvd_usd'].abs()
    out['move_abs'] = out['close_chg_pct'].abs()
    vol_thr = out['big_abs_usd'].rolling(int(win_pct), min_periods=200).quantile(vol_pctile)
    move_thr = out['move_abs'].rolling(int(win_pct), min_periods=200).quantile(move_pctile)
    out['absorbed'] = (out['big_abs_usd'] > vol_thr) & (out['move_abs'] < move_thr)

    if 'atr' not in df_15m.columns:
        df_15m = df_15m.copy()
        df_15m['atr'] = compute_atr(df_15m, period=14)
    triggers = []
    last_signal_ts = pd.Timestamp('1970-01-01', tz='UTC')
    cooldown = pd.Timedelta(minutes=cooldown_min)
    for ts, row in out[out['absorbed']].iterrows():
        if ts - last_signal_ts < cooldown:
            continue
        # Direction: fade the side that was eaten
        if row['big_cvd_usd'] > 0:
            direction = 'short'   # taker buys absorbed by sells
        else:
            direction = 'long'    # taker sells absorbed by bids
        next_15m = ts.floor('15min') + pd.Timedelta(minutes=15)
        idx_match = df_15m.index.searchsorted(next_15m, side='left')
        if idx_match >= len(df_15m):
            continue
        entry = float(df_15m['close'].iloc[idx_match])
        atr = float(df_15m['atr'].iloc[idx_match])
        if pd.isna(atr) or atr <= 0:
            continue
        risk = atr * atr_mult_stop
        if direction == 'long':
            stop = entry - risk
            target = entry + risk * target_r
        else:
            stop = entry + risk
            target = entry - risk * target_r
        triggers.append({
            'ts': df_15m.index[idx_match], 'direction': direction,
            'entry': entry, 'stop': stop, 'target': target,
            'big_cvd_usd': float(row['big_cvd_usd']),
            'move_abs_pct': float(row['move_abs']),
            'signal_ts': ts,
        })
        last_signal_ts = ts
    return pd.DataFrame(triggers)


# === Summary ================================================================

def summary(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    return {
        'label': label, 'n': int(len(t)),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<55s} empty')
        return
    print(f'  {s["label"]:<55s} n={s["n"]:>4d}  meanR={s["mean_R"]:+.3f}  '
           f'WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


# === Main ===================================================================

def main():
    print('Loading 15m BTC + flow data at multiple resolutions...')
    df_15m = load_btc_15m()
    df_15m['atr'] = compute_atr(df_15m, period=14)

    all_results = {}

    # === Standalone Rule 1 at each resolution ===
    print('\n=== Standalone Rule 1 at sub-bar resolutions ===')
    for bs in (60, 300, 900):
        label = {60: '1m', 300: '5m', 900: '15m'}[bs]
        try:
            flow = load_agg_at(bs)
        except Exception as e:
            print(f'\n--- {label}: skipped — {e}')
            continue
        if flow.empty:
            print(f'\n--- {label}: no data, skipping')
            continue
        print(f'\n--- {label} (n={len(flow):,} bars, '
               f'{flow.index.min()} -> {flow.index.max()}) ---')
        flow = attach_price(flow, df_15m['close'], bs)
        flow = compute_zscores(flow, bs)

        for wz_thr in (1.5, 2.0, 2.5):
            for vz_max in (0.5, 1.0):
                trigs = signal_triggers_subbar(
                    flow, df_15m,
                    whale_z_threshold=wz_thr, vel_z_max=vz_max,
                    cooldown_min=60.0)
                if trigs.empty:
                    continue
                trigs_r = measure_r_outcomes(trigs, df_15m)
                s = summary(trigs_r, f'{label} wz>{wz_thr}_vz<{vz_max}')
                all_results[s['label']] = s
                show(s)

    # === Trade-cluster absorption proxy ===
    print('\n=== Absorption-cluster proxy (top-vol with low-move) at each resolution ===')
    for bs in (60, 300, 900):
        label = {60: '1m', 300: '5m', 900: '15m'}[bs]
        try:
            flow = load_agg_at(bs)
        except Exception:
            continue
        if flow.empty:
            continue
        flow = attach_price(flow, df_15m['close'], bs)
        # No z-scores needed for absorption proxy; just use rolling pctiles
        for vp in (0.90, 0.95, 0.98):
            for mp in (0.30, 0.50):
                trigs = absorption_triggers(
                    flow, df_15m, vol_pctile=vp, move_pctile=mp,
                    cooldown_min=60.0)
                if trigs.empty:
                    continue
                trigs_r = measure_r_outcomes(trigs, df_15m)
                s = summary(trigs_r, f'{label} absorb_vp{int(vp*100)}_mp{int(mp*100)}')
                all_results[s['label']] = s
                show(s)

    # === Use 1m absorption as ADDITIONAL gate on optimized Triple ===
    print('\n=== 1m absorption signal as gate on optimized Triple composite ===')
    flow_1m = load_agg_at(60)
    flow_1m = attach_price(flow_1m, df_15m['close'], 60)
    flow_1m = compute_zscores(flow_1m, 60)

    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr_smc = df_smc.copy()
    df_atr_smc['atr'] = compute_atr(df_atr_smc, period=14)

    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)

    # Trim to whale window (with 14d burn-in)
    whale_start = flow_1m.index.min() + pd.Timedelta(days=15)
    triple_w = triple[triple['ts'] >= whale_start].copy()
    print(f'  Triple triggers in 1m-flow window: {len(triple_w)}')

    rows = []
    for _, t in triple_w.iterrows():
        r = replay_one(t, df_smc, df_atr_smc, fvgs, obs,
                        atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        print('  no optimized replays — abort')
    else:
        # Apply no_tilt + no_resist_OB
        rep = rep.sort_values('ts').reset_index(drop=True)
        cur = 0; lb = []
        for r in rep['r_outcome'].shift(1).fillna(0):
            if r < 0: cur += 1
            else: cur = 0
            lb.append(cur)
        rep['consec_losses_before'] = lb
        mask = (rep['consec_losses_before'] == 0) & (
            (rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna())
        opt = rep[mask].copy()
        show(summary(opt, 'optimized Triple (no flow filter)'))

        # Attach 1m whale_cvd_z at each entry's PRECEDING 1m bar
        ts_idx = pd.DatetimeIndex(opt['ts'])
        ix = flow_1m.index.searchsorted(ts_idx, side='right') - 1
        opt['whale_cvd_z_1m'] = [
            float(flow_1m['whale_cvd_z'].iloc[i]) if 0 <= i < len(flow_1m) else np.nan
            for i in ix]
        opt['vel_z_1m'] = [
            float(flow_1m['vel_z'].iloc[i]) if 0 <= i < len(flow_1m) else np.nan
            for i in ix]
        opt['absorbed_1m'] = [
            (flow_1m['whale_cvd_z'].iloc[i] > 1.5 and flow_1m['vel_z'].iloc[i] < 0.5)
            or (flow_1m['whale_cvd_z'].iloc[i] < -1.5 and flow_1m['vel_z'].iloc[i] > -0.5)
            if 0 <= i < len(flow_1m) else False for i in ix]

        # Filter: only take trades where the IMMEDIATELY PRIOR 1m bar shows
        # absorption signal in the SAME direction
        for thr in (1.0, 1.5, 2.0):
            aligned = (
                ((opt['direction'] == 'long') & (opt['whale_cvd_z_1m'] <= -thr) & (opt['vel_z_1m'] > -0.5)) |
                ((opt['direction'] == 'short') & (opt['whale_cvd_z_1m'] >= thr) & (opt['vel_z_1m'] < 0.5))
            )
            sub = opt[aligned]
            show(summary(sub, f'opt + 1m absorb confirm (|wz|>={thr})'))

    # Persist
    out_path = OUT_DIR / 'C2b_subbar_absorption_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Sub-bar Rule 1 + absorption proxy on aggTrades data. '
                      'Window: see binance_agg_trades_1m table. Tests whether '
                      'finer time resolution makes the whale-CVD-vs-price '
                      'divergence detectable.'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
