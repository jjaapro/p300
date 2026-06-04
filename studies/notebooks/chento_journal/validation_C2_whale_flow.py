"""validation_C2_whale_flow: chento's Rule 1 STRICT form — segregate the
flow by trade-size cohort and test whether the WHALE CVD signal carries
more edge than the all-taker CVD signal that B1 uses.

Chento Rule 1 verbatim (2026-05-22 live):
  "While the money flow is positive, the price action is reacting weakly.
   It is going up very slowly and barely moving percentage-wise, so it
   will probably dump."

B1 tested this against ALL taker flow. C2 tests the strict form: the
divergence specifically between WHALE-tier flow and price velocity.

Data: binance_agg_trades_15m, $-bucketed by trade notional:
  whale  : single trades >= $100k
  mid    : $1k - $100k
  retail : < $1k

Tests:
  1. Standalone Rule 1 with whale-only CVD (vs the B1 baseline with all flow)
  2. Whale CVD as additional gate on the optimized Triple composite
  3. Whale-vs-retail divergence (smart-money proxy: whales selling, retail buying)
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
    measure_r_outcomes, summarize_triggers,
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

IS_END = pd.Timestamp('2026-04-30 23:59:59', tz='UTC')   # ~70% IS / 30% OOS within recent window


# === Data loading ==========================================================

def load_whale_flow() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql("""
        SELECT timestamp,
               whale_buy_qty, whale_sell_qty,
               mid_buy_qty, mid_sell_qty,
               retail_buy_qty, retail_sell_qty,
               whale_buy_usd, whale_sell_usd,
               mid_buy_usd, mid_sell_usd,
               retail_buy_usd, retail_sell_usd,
               n_trades
        FROM binance_agg_trades_15m
        WHERE asset='BTCUSDT'
        ORDER BY timestamp
    """, con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    return df


def compute_tier_cvd(whale_df: pd.DataFrame, *,
                      window_bars: int = 4 * 24 * 14    # 14-day rolling
                      ) -> pd.DataFrame:
    """Per-bar tier CVD (buy - sell) in USD notional + z-scores over rolling."""
    out = whale_df.copy()
    out['whale_cvd_usd'] = out['whale_buy_usd'] - out['whale_sell_usd']
    out['mid_cvd_usd'] = out['mid_buy_usd'] - out['mid_sell_usd']
    out['retail_cvd_usd'] = out['retail_buy_usd'] - out['retail_sell_usd']
    # z-scores
    for col in ('whale_cvd_usd', 'mid_cvd_usd', 'retail_cvd_usd'):
        z_col = col.replace('_usd', '_z')
        mu = out[col].rolling(window_bars, min_periods=window_bars // 4).mean()
        sd = out[col].rolling(window_bars, min_periods=window_bars // 4).std()
        out[z_col] = (out[col] - mu) / sd
    # whale-vs-retail divergence: smart money signal
    # +ve = whales buying while retail selling, -ve = whales selling while retail buying
    out['smart_money_div_z'] = out['whale_cvd_z'] - out['retail_cvd_z']
    return out


# === Standalone Rule 1 (whale form) ========================================

def rule1_whale_triggers(df_15m: pd.DataFrame, whale_z: pd.DataFrame, *,
                          whale_z_threshold: float = 1.0,
                          vel_z_max: float = 1.0,
                          cooldown_bars: int = 4,
                          atr_mult_stop: float = 2.0,
                          target_r: float = 2.0,
                          ) -> pd.DataFrame:
    """Generate triggers where whale CVD is extreme but price velocity is muted.
       SHORT when whale_cvd_z > +threshold AND vel_z < +max (whales buying but
                                                                price barely moves up)
       LONG  when whale_cvd_z < -threshold AND vel_z > -max (whales selling but
                                                                price barely moves down)

    Trigger row matches B1's schema: ts, direction, entry, stop, target,
    plus the diagnostic z-scores.
    """
    # Merge whale z onto 15m frame
    df = df_15m.copy()
    df = df.join(whale_z[['whale_cvd_z']], how='inner')
    if 'atr' not in df.columns:
        df['atr'] = compute_atr(df, period=14)
    # Compute velocity z (same formula as B1)
    vel = df['close'].pct_change(4)
    vel_mean = vel.rolling(4 * 24 * 14, min_periods=200).mean()
    vel_std = vel.rolling(4 * 24 * 14, min_periods=200).std()
    df['vel_z'] = (vel - vel_mean) / vel_std

    rows = []
    last_idx = -10**9
    for i in range(len(df)):
        if i - last_idx < cooldown_bars: continue
        wz = df['whale_cvd_z'].iloc[i]
        vz = df['vel_z'].iloc[i]
        atr = df['atr'].iloc[i]
        if pd.isna(wz) or pd.isna(vz) or pd.isna(atr) or atr <= 0:
            continue
        entry = float(df['close'].iloc[i])
        risk = float(atr) * atr_mult_stop
        if risk <= 0: continue
        if wz > whale_z_threshold and vz < vel_z_max:
            rows.append({
                'ts': df.index[i], 'direction': 'short',
                'entry': entry, 'stop': entry + risk,
                'target': entry - risk * target_r,
                'whale_cvd_z': float(wz), 'vel_z': float(vz),
            })
            last_idx = i
        elif wz < -whale_z_threshold and vz > -vel_z_max:
            rows.append({
                'ts': df.index[i], 'direction': 'long',
                'entry': entry, 'stop': entry - risk,
                'target': entry + risk * target_r,
                'whale_cvd_z': float(wz), 'vel_z': float(vz),
            })
            last_idx = i
    return pd.DataFrame(rows)


# === Summary ===============================================================

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


def main():
    print('Loading whale flow data...')
    whale_df = load_whale_flow()
    if whale_df.empty:
        print('ERROR: no whale flow data — run binance_agg_trades.py first')
        return
    print(f'  binance_agg_trades_15m: {len(whale_df):,} bins  '
           f'{whale_df.index.min()} -> {whale_df.index.max()}')

    print('  Computing tier CVDs + z-scores...')
    whale_z = compute_tier_cvd(whale_df)
    print(f'  whale_cvd_z range: '
           f'[{whale_z["whale_cvd_z"].min():.2f}, {whale_z["whale_cvd_z"].max():.2f}]')
    print(f'  USD share by tier (period totals):')
    total_w = float(whale_z['whale_buy_usd'].sum() + whale_z['whale_sell_usd'].sum())
    total_m = float(whale_z['mid_buy_usd'].sum() + whale_z['mid_sell_usd'].sum())
    total_r = float(whale_z['retail_buy_usd'].sum() + whale_z['retail_sell_usd'].sum())
    grand = total_w + total_m + total_r
    print(f'    whale:  ${total_w/1e9:>8.2f}B ({100*total_w/grand:.1f}%)')
    print(f'    mid:    ${total_m/1e9:>8.2f}B ({100*total_m/grand:.1f}%)')
    print(f'    retail: ${total_r/1e9:>8.2f}B ({100*total_r/grand:.1f}%)')

    # Load 15m for OHLCV
    print('\nLoading BTC 15m + building Triple composite (full data, will trim to whale window)...')
    df_15m = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    # Trim to whale data window
    whale_start = whale_z.index.min() + pd.Timedelta(days=15)   # need 15d for z to warm up
    triple_w = triple[triple['ts'] >= whale_start].copy()
    print(f'  Triple triggers in whale window (>= {whale_start.date()}): {len(triple_w):,}')

    if len(triple_w) < 20:
        print('  Too few triggers for analysis — need more whale flow history')

    # === Standalone Rule 1 (whale form) at multiple thresholds ===
    print('\n--- Standalone Rule 1 with WHALE-ONLY CVD ---')
    for wz_thr in (1.0, 1.5, 2.0):
        for vz_max in (0.5, 1.0):
            trigs = rule1_whale_triggers(df_b1, whale_z,
                                           whale_z_threshold=wz_thr,
                                           vel_z_max=vz_max)
            if trigs.empty:
                print(f'  whale_z>{wz_thr}, vel_z<{vz_max}: no triggers')
                continue
            # Trim to whale window (rule1_whale_triggers should be already)
            trigs = trigs[trigs['ts'] >= whale_start].copy()
            if trigs.empty:
                continue
            trigs_r = measure_r_outcomes(trigs, df_b1)
            s = summary(trigs_r, f'wz>{wz_thr}_vz<{vz_max}')
            show(s)

    # === Compare: B1 (all-taker CVD) on the same whale-window for fairness ===
    print('\n--- B1 baseline (all-taker CVD) on same window ---')
    b1_w = b1[b1['ts'] >= whale_start].copy()
    if not b1_w.empty:
        b1_r = measure_r_outcomes(b1_w, df_b1)
        show(summary(b1_r, 'B1_all_taker_cvd'))

    # === Attach whale features to optimized Triple config and test as gate ===
    if len(triple_w) >= 30:
        print('\n--- Whale CVD as gate on optimized Triple composite ---')
        df_p = compute_pivots(df_15m, n=5)
        df_smc = compute_smc_state(df_p, n=5)
        obs = compute_order_blocks(df_smc)
        fvgs = compute_fvgs(df_smc)
        df_atr = df_smc.copy()
        df_atr['atr'] = compute_atr(df_atr, period=14)

        rows = []
        for _, t in triple_w.iterrows():
            r = replay_one(t, df_smc, df_atr, fvgs, obs,
                            atr_mult=5.0, target_r=6.0, tp_mode='fixed')
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        # Apply no_tilt
        rep = rep.sort_values('ts').reset_index(drop=True)
        cur = 0; lb = []
        for r in rep['r_outcome'].shift(1).fillna(0):
            if r < 0: cur += 1
            else: cur = 0
            lb.append(cur)
        rep['consec_losses_before'] = lb
        mask = (rep['consec_losses_before'] == 0) & (
            (rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna())
        optimized = rep[mask].copy()
        show(summary(optimized, 'optimized (atr5_t6R+no_tilt+no_resist_OB) on whale window'))

        # Attach whale_cvd_z at trigger time
        ts_idx = pd.DatetimeIndex(optimized['ts'])
        ix = whale_z.index.searchsorted(ts_idx, side='right') - 1
        optimized['whale_cvd_z_at_entry'] = [
            float(whale_z['whale_cvd_z'].iloc[i]) if 0 <= i < len(whale_z) else np.nan
            for i in ix]
        optimized['smart_div_z_at_entry'] = [
            float(whale_z['smart_money_div_z'].iloc[i]) if 0 <= i < len(whale_z) else np.nan
            for i in ix]

        print('\n  Filter: whale-flow ALIGNED with trade direction')
        # For long trades, want whale_cvd > 0 (whales buying)
        # For short trades, want whale_cvd < 0 (whales selling)
        for thr in (0.0, 0.5, 1.0, 1.5):
            aligned = (
                ((optimized['direction'] == 'long') & (optimized['whale_cvd_z_at_entry'] >= thr)) |
                ((optimized['direction'] == 'short') & (optimized['whale_cvd_z_at_entry'] <= -thr))
            )
            sub = optimized[aligned]
            show(summary(sub, f'whale_aligned (|z|>={thr})'))

        print('\n  Filter: whale-flow CONTRARY to trade direction (chento Rule 1)')
        # For long trades, whales are SELLING (whale_cvd_z < -thr) AND we go long anyway
        # because B1 already triggered on weak velocity — i.e. shorts are squeezing
        for thr in (0.0, 0.5, 1.0, 1.5):
            contrary = (
                ((optimized['direction'] == 'long') & (optimized['whale_cvd_z_at_entry'] <= -thr)) |
                ((optimized['direction'] == 'short') & (optimized['whale_cvd_z_at_entry'] >= thr))
            )
            sub = optimized[contrary]
            show(summary(sub, f'whale_contrary (|z|>={thr})'))

        print('\n  Filter: smart-money divergence (whales vs retail)')
        # +ve smart_div = whales buying & retail selling. For LONG, that's bullish confluence.
        # For SHORT, want -ve smart_div (whales selling & retail buying).
        for thr in (0.0, 0.5, 1.0):
            smart_aligned = (
                ((optimized['direction'] == 'long') & (optimized['smart_div_z_at_entry'] >= thr)) |
                ((optimized['direction'] == 'short') & (optimized['smart_div_z_at_entry'] <= -thr))
            )
            sub = optimized[smart_aligned]
            show(summary(sub, f'smart_div_aligned (|z|>={thr})'))

    out_path = OUT_DIR / 'C2_whale_flow_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('C2 whale flow Rule 1 + gating tests on optimized Triple. '
                      'Backfill window: see binance_agg_trades_15m table.'),
            'whale_window_start': str(whale_z.index.min()),
            'whale_window_end': str(whale_z.index.max()),
            'usd_share': {
                'whale_pct': round(100*total_w/grand, 1),
                'mid_pct': round(100*total_m/grand, 1),
                'retail_pct': round(100*total_r/grand, 1),
            },
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
