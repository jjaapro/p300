"""validation_a4_eth_funding_a8: three remaining checks before the v3 sleeve PR:

1. A4 ladder on ETH — does the bounded ladder-add generalize from BTC to ETH?
2. Funding-cost model — apply real 8h Binance settlement funding to TIF=72h
   trades (long pays positive funding, short receives); compare with-funding
   vs without-funding mean R.
3. A8 trim→DCA→trim cycling — partial trim at +1R, re-add same size if price
   retraces to entry; test if exposure rotation extracts more R.
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
    compute_moneyflow_signal, b1_triggers, compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_C5_smc_features import (
    compute_pivots, compute_smc_state, compute_order_blocks, compute_fvgs,
    features_at,
)
from studies.notebooks.chento_journal.validation_multi_asset import (
    load_perp_15m, derive_binance_1h_close, compute_okx_delta_z,
    load_okx_close_asset, load_lsr_asset, ASSET_CONFIG,
)

COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')

FUNDING_TABLES = {'BTC': 'cd_funding_rate', 'ETH': 'cd_funding_rate_eth'}


# === Funding rate loader ===================================================

def load_funding(asset: str) -> pd.Series:
    """Load Binance perp funding rate at 8h settlement times only (UTC
    00:00 / 08:00 / 16:00). Pre-2026-04 data is hourly predictions; we
    filter to the settlement hours to get the actual paid rates."""
    table = FUNDING_TABLES.get(asset)
    if table is None:
        return pd.Series(dtype=float)
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(f"SELECT timestamp, fr_close FROM {table} ORDER BY timestamp", con)
    con.close()
    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.set_index('ts').drop(columns='timestamp')
    df = df[df.index.hour.isin([0, 8, 16])]
    return df['fr_close']


def funding_cost_R(entry_ts, exit_ts, entry_price, risk, direction,
                    funding_series: pd.Series) -> float:
    """Total funding cost in R units over the trade's holding period.
    Long pays positive funding, short receives positive funding."""
    if funding_series.empty or entry_ts >= exit_ts:
        return 0.0
    events = funding_series[(funding_series.index > entry_ts) &
                              (funding_series.index <= exit_ts)]
    if events.empty:
        return 0.0
    # funding_rate is a fraction (e.g. 0.0001 = 0.01%). It's the amount paid
    # on notional. For our 1R-risk-sized position, notional = entry / (risk/entry) = entry^2 / risk
    # but R-cost = funding * notional / risk-in-currency = funding * entry / (risk/entry) / risk = funding * entry / risk
    sign = 1.0 if direction == 'long' else -1.0
    funding_total = float(events.sum())
    return sign * funding_total * (entry_price / risk)


# === A4 / A8 capable replay ================================================

def replay_v3(trig, df_smc, df_atr, fvgs, obs, funding_series, *,
                atr_mult=5.0, target_r=6.0,
                tif_bars=4 * 72,
                # A4 ladder
                ladder_at_adv_R=0.3,
                ladder_size_frac=0.5,
                post_ladder_stop_R=1.5,
                enable_ladder=True,
                # A8 trim-DCA cycling
                enable_a8_cycle=False,
                a8_trim_at_R=1.0,
                a8_trim_frac=0.25,
                a8_redca_at_R=0.0,    # re-add when fav drops back to 0R (entry)
                # funding cost
                apply_funding=False,
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
        target = entry + risk * target_r
    else:
        stop = entry + risk
        target = entry - risk * target_r
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    start = idx + 1
    end = min(start + tif_bars, len(df_smc))
    if end <= start:
        return None

    # State
    main_size = 1.0
    ladder_added = False
    ladder_size = 0.0
    ladder_entry = None
    # A8 state
    trimmed = False
    trimmed_r = 0.0
    trimmed_size = 0.0
    redca_done = False
    max_fav_R = 0.0
    outcome_main = None
    exit_kind = None
    exit_ts = None

    last_close = None
    for j in range(start, end):
        bh = float(df_smc['high'].iloc[j])
        bl = float(df_smc['low'].iloc[j])
        bc = float(df_smc['close'].iloc[j])
        last_close = bc
        if direction == 'long':
            fav_R = (bh - entry) / risk
            adv_R = (entry - bl) / risk
            cur_R = (bc - entry) / risk
        else:
            fav_R = (entry - bl) / risk
            adv_R = (bh - entry) / risk
            cur_R = (entry - bc) / risk
        max_fav_R = max(max_fav_R, fav_R)

        # 1) Stop / target
        if direction == 'long':
            if bl <= stop:
                outcome_main = (stop - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bh >= target:
                outcome_main = (target - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break
        else:
            if bh >= stop:
                outcome_main = (entry - stop) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bl <= target:
                outcome_main = (entry - target) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break

        # 2) A4 ladder
        if (enable_ladder and not ladder_added and adv_R >= ladder_at_adv_R):
            ladder_added = True
            ladder_size = ladder_size_frac
            ladder_entry = (entry - risk * ladder_at_adv_R if direction == 'long'
                              else entry + risk * ladder_at_adv_R)
            if direction == 'long':
                stop = entry - risk * post_ladder_stop_R
            else:
                stop = entry + risk * post_ladder_stop_R

        # 3) A8 trim+DCA cycle (only one cycle)
        if (enable_a8_cycle and not trimmed and fav_R >= a8_trim_at_R):
            trimmed = True
            trimmed_size = a8_trim_frac
            trimmed_r = a8_trim_at_R - cost_R
            main_size -= a8_trim_frac
            if main_size <= 0:
                outcome_main = 0.0
                exit_ts = df_smc.index[j]; exit_kind = 'a8_all_trimmed'; break
        if (enable_a8_cycle and trimmed and not redca_done and cur_R <= a8_redca_at_R):
            redca_done = True
            main_size += trimmed_size
            # Note: re-add cost factored in implicitly through cost_R applied at exit

    if outcome_main is None:
        last_close_val = last_close if last_close is not None else float(df_smc['close'].iloc[end - 1])
        if direction == 'long':
            outcome_main = (last_close_val - entry) / risk - cost_R
        else:
            outcome_main = (entry - last_close_val) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df_smc.index[end - 1]

    # Ladder leg outcome at same exit price
    if ladder_added:
        if exit_kind == 'stop':
            exit_price = stop
        elif exit_kind == 'target':
            exit_price = target
        else:
            exit_price = last_close_val if outcome_main is not None else float(df_smc['close'].iloc[end - 1])
        if direction == 'long':
            ladder_outcome = (exit_price - ladder_entry) / risk - cost_R
        else:
            ladder_outcome = (ladder_entry - exit_price) / risk - cost_R
    else:
        ladder_outcome = 0.0

    # Combine size-weighted
    total_r = main_size * outcome_main + ladder_size * ladder_outcome
    if trimmed:
        total_r += trimmed_size * trimmed_r
        if redca_done:
            # The re-DCA'd portion's outcome is same as main_size's (we tracked main_size)
            # We already accounted for it by adding trimmed_size back to main_size above
            pass

    # Apply funding cost
    if apply_funding:
        fc_R = funding_cost_R(ts, exit_ts, entry, risk, direction, funding_series)
        total_r -= fc_R    # subtract funding paid (positive for longs in pos-funding regime)
    else:
        fc_R = 0.0

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'risk': risk,
        'r_outcome': total_r,
        'r_no_funding': total_r + fc_R if apply_funding else total_r,
        'funding_cost_R': fc_R,
        'exit_kind': exit_kind,
        'max_fav_R': round(max_fav_R, 3),
    }


# === Pipeline per asset ====================================================

def build_optimized(asset: str):
    df_15m = load_perp_15m(asset)
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    try:
        lsr_df = load_lsr_asset(ASSET_CONFIG[asset]['lsr_asset'])
        lsr_z = compute_lsr_extremes(lsr_df)
        b5 = b5_triggers(df_15m, lsr_z)
    except Exception as e:
        print(f'  ({asset} B5 unavailable: {e}; degraded composite)')
        b5 = pd.DataFrame()
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    if not b5.empty:
        triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    else:
        triple = intersect_triggers(b1, b7)
    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)
    okx_close = load_okx_close_asset(asset)
    bnb_close_1h = derive_binance_1h_close(df_15m)
    delta_df = compute_okx_delta_z(bnb_close_1h, okx_close)
    okx_start = delta_df.index.min() + pd.Timedelta(days=14)
    triple_w = triple[triple['ts'] >= okx_start].copy()
    return triple_w, df_smc, df_atr, fvgs, obs, delta_df


def apply_filters_with_delta(rep, delta_df):
    rep = rep.sort_values('ts').reset_index(drop=True)
    cur = 0; lb = []
    for r in rep['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = delta_df.index.searchsorted(ts_idx, side='right') - 1
    rep['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i])
                            if 0 <= i < len(delta_df) else np.nan for i in ix]
    mask = ((rep['consec_losses_before'] == 0) &
             ((rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()) &
             (((rep['direction'] == 'long') & (rep['okx_delta_z'] >= 0)) |
              ((rep['direction'] == 'short') & (rep['okx_delta_z'] <= 0))))
    return rep[mask].copy()


def replay_set(triple_w, df_smc, df_atr, fvgs, obs, delta_df, funding_series, **kw):
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_v3(t, df_smc, df_atr, fvgs, obs, funding_series, **kw)
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return rep
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    return apply_filters_with_delta(rep, delta_df)


def summary(t, label):
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
        'avg_funding_cost_R': (round(float(t['funding_cost_R'].mean()), 4)
                                if 'funding_cost_R' in t.columns else 0),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<58s} empty')
        return
    print(f'  {s["label"]:<58s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})  '
           f'avgFund={s["avg_funding_cost_R"]:+.4f}')


def main():
    print('Loading funding rates...')
    btc_funding = load_funding('BTC')
    eth_funding = load_funding('ETH')
    print(f'  BTC funding (8h settlements): {len(btc_funding):,} events  '
           f'{btc_funding.index.min()} -> {btc_funding.index.max()}')
    print(f'  Average BTC funding rate: {btc_funding.mean()*10000:+.2f} bps/8h')
    print(f'  ETH funding (8h settlements): {len(eth_funding):,} events  '
           f'{eth_funding.index.min()} -> {eth_funding.index.max()}')
    print(f'  Average ETH funding rate: {eth_funding.mean()*10000:+.2f} bps/8h')

    all_results = {}

    # === BTC: A4 + funding comparison ===
    print(f'\n{"="*80}\n=== BTC: A4 ladder + funding cost model ===\n{"="*80}')
    triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b = build_optimized('BTC')
    print(f'  Triple triggers: {len(triple_btc)}')

    print('\n--- WITHOUT A4 ladder (baseline TIF=72h) ---')
    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=False, apply_funding=False)
    s = summary(rep, 'BTC baseline (no ladder, no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=False, apply_funding=True)
    s = summary(rep, 'BTC baseline (no ladder, WITH funding)'); all_results[s['label']] = s; show(s)

    print('\n--- WITH A4 ladder (conservative 50%) ---')
    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=0.5,
                      post_ladder_stop_R=1.5, apply_funding=False)
    s = summary(rep, 'BTC A4 conservative (no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=0.5,
                      post_ladder_stop_R=1.5, apply_funding=True)
    s = summary(rep, 'BTC A4 conservative (WITH funding)'); all_results[s['label']] = s; show(s)

    print('\n--- WITH A4 ladder (aggressive 100%) ---')
    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=1.0,
                      post_ladder_stop_R=1.5, apply_funding=False)
    s = summary(rep, 'BTC A4 aggressive (no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=1.0,
                      post_ladder_stop_R=1.5, apply_funding=True)
    s = summary(rep, 'BTC A4 aggressive (WITH funding)'); all_results[s['label']] = s; show(s)

    # === ETH: A4 generalization ===
    print(f'\n{"="*80}\n=== ETH: A4 ladder cross-validation ===\n{"="*80}')
    triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e = build_optimized('ETH')
    print(f'  Triple triggers: {len(triple_eth)}')

    print('\n--- WITHOUT A4 ladder (baseline TIF=72h) ---')
    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=False, apply_funding=False)
    s = summary(rep, 'ETH baseline (no ladder, no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=False, apply_funding=True)
    s = summary(rep, 'ETH baseline (no ladder, WITH funding)'); all_results[s['label']] = s; show(s)

    print('\n--- WITH A4 ladder (conservative 50%) ---')
    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=0.5,
                      post_ladder_stop_R=1.5, apply_funding=False)
    s = summary(rep, 'ETH A4 conservative (no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=0.5,
                      post_ladder_stop_R=1.5, apply_funding=True)
    s = summary(rep, 'ETH A4 conservative (WITH funding)'); all_results[s['label']] = s; show(s)

    print('\n--- WITH A4 ladder (aggressive 100%) ---')
    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=1.0,
                      post_ladder_stop_R=1.5, apply_funding=False)
    s = summary(rep, 'ETH A4 aggressive (no funding)'); all_results[s['label']] = s; show(s)

    rep = replay_set(triple_eth, df_smc_e, df_atr_e, fvgs_e, obs_e, delta_e, eth_funding,
                      enable_ladder=True, ladder_at_adv_R=0.3, ladder_size_frac=1.0,
                      post_ladder_stop_R=1.5, apply_funding=True)
    s = summary(rep, 'ETH A4 aggressive (WITH funding)'); all_results[s['label']] = s; show(s)

    # === A8 trim-DCA cycling on BTC ===
    print(f'\n{"="*80}\n=== A8 trim → DCA-back → run cycling (BTC) ===\n{"="*80}')
    for trim_R, trim_frac, redca_R in [(1.0, 0.25, 0.0), (1.0, 0.25, 0.3),
                                          (1.5, 0.33, 0.0), (2.0, 0.25, 0.5)]:
        rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                          enable_ladder=False,
                          enable_a8_cycle=True,
                          a8_trim_at_R=trim_R, a8_trim_frac=trim_frac, a8_redca_at_R=redca_R,
                          apply_funding=False)
        s = summary(rep, f'A8 trim{int(trim_frac*100)}_at{trim_R}R_redca{redca_R}R')
        all_results[s['label']] = s
        show(s)
    # And A8 stacked WITH A4
    for trim_R, trim_frac in [(2.0, 0.25), (1.5, 0.33)]:
        rep = replay_set(triple_btc, df_smc_b, df_atr_b, fvgs_b, obs_b, delta_b, btc_funding,
                          enable_ladder=True, ladder_size_frac=0.5,
                          enable_a8_cycle=True,
                          a8_trim_at_R=trim_R, a8_trim_frac=trim_frac, a8_redca_at_R=0.0,
                          apply_funding=False)
        s = summary(rep, f'A8 trim{int(trim_frac*100)}_at{trim_R}R + A4 conservative')
        all_results[s['label']] = s
        show(s)

    # === Headline summary ===
    print(f'\n{"="*80}\n=== Summary table ===\n{"="*80}')
    for k, v in all_results.items():
        if v.get('n', 0) == 0: continue
        print(f'  {k:<56s} n={v["n"]:>4d}  R={v["mean_R"]:+.3f}  '
               f'maxDD={v["max_dd_R"]:+5.2f}  IS={v["IS_meanR"]:+.3f}  OOS={v["OOS_meanR"]:+.3f}')

    out_path = OUT_DIR / 'a4_eth_funding_a8_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('A4 cross-asset validation on ETH; funding-cost model applied '
                      'on TIF=72h trades using 8h Binance settlement rates; A8 '
                      'trim-DCA cycling test.'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
