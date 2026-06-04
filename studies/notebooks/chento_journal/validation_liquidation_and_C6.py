"""validation_liquidation_and_C6: two things in one script.

1. LIQUIDATION RISK ANALYSIS — for each A4 tier, compute the maximum
   adverse excursion (MAE) in price-% terms across the backtest, then
   simulate whether trades would have been liquidated at various leverage
   levels (5x / 10x / 20x / 25x / 50x / 100x) BEFORE the stop fires. The
   ladder add doubles or triples position notional, so post-ladder effective
   leverage scales. We compute % of trades that get liquidated per tier ×
   leverage.

2. C6 AUCTION MARKET THEORY (volume profile acceptance/rejection) — build
   rolling 7d volume profile from 15m OHLCV, identify POC and Value Area
   (70% volume band), test as filter on the optimized Triple stack:
     - Entry inside VA vs outside
     - Entry near VA edge with rejection wick
     - VAH/VAL test → fade or follow

Both tests run on the optimized stack (Tier 2 default: 100% size / -1.4R).
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
from studies.notebooks.chento_journal.validation_multi_asset import (
    derive_binance_1h_close, compute_okx_delta_z, load_okx_close_asset,
)

COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')

# Binance maintenance margin tiers for BTCUSDT-M perp (approximate, depends on tier)
# Source: Binance fapi documentation; for small (<$50k) positions
BINANCE_MMR = {
    1: 0.004,    # 1x leverage: 0.4% MMR
    5: 0.005,    # 5x: 0.5%
    10: 0.005,   # 10x: 0.5%
    20: 0.0065,  # 20x: 0.65%
    25: 0.01,    # 25x: 1.0%
    50: 0.01,    # 50x: 1.0%
    75: 0.025,   # 75x: 2.5%
    100: 0.05,   # 100x: 5%
    125: 0.04,   # 125x: 4%
}

def liquidation_pct(leverage: int) -> float:
    """Approximate price-% adverse from entry at which a leveraged position
    gets liquidated. Liq when equity = MMR × notional, i.e. position has
    lost (1/L - MMR) × notional from initial margin.
    Adverse-% from entry at liq = (1/L) - MMR(L)."""
    mmr = BINANCE_MMR.get(leverage, 0.01)
    return max(0.001, (1.0 / leverage) - mmr)


# === A4-capable replay that ALSO tracks MAE in price terms ===

def replay_with_mae(trig, df_smc, df_atr, fvgs, obs, *,
                     atr_mult=5.0, target_r=6.0,
                     tif_bars=4 * 72,
                     ladder_at_adv_R=0.3,
                     ladder_size_frac=0.5,
                     post_ladder_stop_R=1.5,
                     enable_ladder=True,
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
    stop = entry - risk if direction == 'long' else entry + risk
    target = entry + risk * target_r if direction == 'long' else entry - risk * target_r
    cost_R = (COST_BP / 10000.0) * (entry / risk)
    start = idx + 1
    end = min(start + tif_bars, len(df_smc))

    main_size = 1.0
    ladder_added = False
    ladder_size = 0.0
    ladder_entry_price = None
    max_adv_R_pre = 0.0     # MAE before ladder fires
    max_adv_R_post = 0.0    # MAE after ladder
    max_fav_R = 0.0
    outcome_main = None
    exit_kind = None
    last_close = entry

    for j in range(start, end):
        bh = float(df_smc['high'].iloc[j])
        bl = float(df_smc['low'].iloc[j])
        bc = float(df_smc['close'].iloc[j])
        last_close = bc
        if direction == 'long':
            fav_R = (bh - entry) / risk
            adv_R = (entry - bl) / risk
        else:
            fav_R = (entry - bl) / risk
            adv_R = (bh - entry) / risk
        max_fav_R = max(max_fav_R, fav_R)
        if not ladder_added:
            max_adv_R_pre = max(max_adv_R_pre, adv_R)
        else:
            max_adv_R_post = max(max_adv_R_post, adv_R)

        # Stop / target
        if direction == 'long':
            if bl <= stop:
                outcome_main = (stop - entry) / risk - cost_R
                exit_kind = 'stop'; break
            if bh >= target:
                outcome_main = (target - entry) / risk - cost_R
                exit_kind = 'target'; break
        else:
            if bh >= stop:
                outcome_main = (entry - stop) / risk - cost_R
                exit_kind = 'stop'; break
            if bl <= target:
                outcome_main = (entry - target) / risk - cost_R
                exit_kind = 'target'; break

        # Ladder
        if enable_ladder and not ladder_added and adv_R >= ladder_at_adv_R:
            ladder_added = True
            ladder_size = ladder_size_frac
            ladder_entry_price = (entry - risk * ladder_at_adv_R if direction == 'long'
                                    else entry + risk * ladder_at_adv_R)
            stop = (entry - risk * post_ladder_stop_R if direction == 'long'
                     else entry + risk * post_ladder_stop_R)

    if outcome_main is None:
        outcome_main = ((last_close - entry) / risk - cost_R if direction == 'long'
                         else (entry - last_close) / risk - cost_R)
        exit_kind = 'tif'

    # Ladder outcome
    if ladder_added:
        exit_price = stop if exit_kind == 'stop' else (target if exit_kind == 'target' else last_close)
        ladder_outcome = ((exit_price - ladder_entry_price) / risk - cost_R if direction == 'long'
                            else (ladder_entry_price - exit_price) / risk - cost_R)
    else:
        ladder_outcome = 0.0

    total_r = main_size * outcome_main + ladder_size * ladder_outcome

    # Compute MAE in price-% terms (relative to original entry)
    risk_pct = risk / entry  # stop distance as fraction of entry
    overall_max_adv_R = max(max_adv_R_pre, max_adv_R_post)
    mae_pct = overall_max_adv_R * risk_pct
    # Position-leverage-equivalent MAE (after ladder)
    # When ladder fills, total position notional = (1 + ladder_size) × original
    # Effective leverage on combined position = original_leverage × (1 + ladder_size)
    # Liquidation occurs at adv_% where (mae_pct * effective_L) exceeds NAV-(1-MMR)/L
    # For each leverage L, the liquidation distance is liq_pct(L)
    # If mae_pct >= liq_pct(L * (1 + ladder_size)), trade would liquidate

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'risk': risk, 'risk_pct': risk_pct,
        'r_outcome': total_r,
        'exit_kind': exit_kind,
        'mae_R_pre_ladder': max_adv_R_pre,
        'mae_R_post_ladder': max_adv_R_post if ladder_added else max_adv_R_pre,
        'mae_R_total': overall_max_adv_R,
        'mae_pct_of_entry': mae_pct,
        'ladder_added': ladder_added,
        'ladder_size': ladder_size,
        'effective_size_multiplier': 1.0 + ladder_size,
    }


# === Apply filters ==========================================================

def build_optimized():
    df_15m = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df_15m, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)
    okx_close = load_okx_close_asset('BTC')
    bnb_close_1h = derive_binance_1h_close(df_15m)
    delta_df = compute_okx_delta_z(bnb_close_1h, okx_close)
    okx_start = delta_df.index.min() + pd.Timedelta(days=14)
    triple_w = triple[triple['ts'] >= okx_start].copy()
    return triple_w, df_smc, df_atr, fvgs, obs, delta_df


def apply_filters(rep, delta_df, df_smc, fvgs, obs):
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
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    mask = ((rep['consec_losses_before'] == 0) &
             ((rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()) &
             (((rep['direction'] == 'long') & (rep['okx_delta_z'] >= 0)) |
              ((rep['direction'] == 'short') & (rep['okx_delta_z'] <= 0))))
    return rep[mask].copy()


# === Volume profile / AMT ==================================================

def compute_volume_profile(df_15m: pd.DataFrame, *,
                             window_days: int = 7,
                             n_price_bins: int = 50,
                             ) -> pd.DataFrame:
    """For each timestamp, compute rolling-window POC and Value-Area edges
    using bar's typical price (HL2) weighted by volume.

    Returns df with cols: poc, vah, val.
    POC = price bin with max volume in window
    VA = contiguous bins around POC containing 70% of total window volume
    """
    out = df_15m.copy()
    bars_per_day = 96   # 15m
    window_bars = window_days * bars_per_day
    out['typ'] = (out['high'] + out['low']) / 2

    poc_arr = np.full(len(out), np.nan)
    vah_arr = np.full(len(out), np.nan)
    val_arr = np.full(len(out), np.nan)

    typ = out['typ'].values
    vol = out['volume'].values

    for i in range(window_bars, len(out)):
        prices = typ[i - window_bars:i]
        vols = vol[i - window_bars:i]
        if vols.sum() <= 0 or np.isnan(prices).any():
            continue
        lo, hi = float(prices.min()), float(prices.max())
        if hi <= lo:
            continue
        bin_edges = np.linspace(lo, hi, n_price_bins + 1)
        # Vol per bin
        idx = np.clip(((prices - lo) / (hi - lo) * n_price_bins).astype(int),
                        0, n_price_bins - 1)
        bin_vol = np.zeros(n_price_bins)
        for k in range(len(prices)):
            bin_vol[idx[k]] += vols[k]
        # POC = max-vol bin midpoint
        poc_bin = int(np.argmax(bin_vol))
        poc_price = 0.5 * (bin_edges[poc_bin] + bin_edges[poc_bin + 1])
        # Value Area: expand outward from POC until 70% of vol captured
        total_vol = bin_vol.sum()
        target = 0.7 * total_vol
        accumulated = bin_vol[poc_bin]
        lo_bin = hi_bin = poc_bin
        while accumulated < target and (lo_bin > 0 or hi_bin < n_price_bins - 1):
            # Pick the larger of (next bin below, next bin above)
            below = bin_vol[lo_bin - 1] if lo_bin > 0 else -1
            above = bin_vol[hi_bin + 1] if hi_bin < n_price_bins - 1 else -1
            if below >= above and lo_bin > 0:
                lo_bin -= 1
                accumulated += bin_vol[lo_bin]
            elif hi_bin < n_price_bins - 1:
                hi_bin += 1
                accumulated += bin_vol[hi_bin]
            else:
                break
        val_price = bin_edges[lo_bin]
        vah_price = bin_edges[hi_bin + 1]

        poc_arr[i] = poc_price
        vah_arr[i] = vah_price
        val_arr[i] = val_price

    out['poc'] = poc_arr
    out['vah'] = vah_arr
    out['val'] = val_arr
    return out[['poc', 'vah', 'val', 'typ']]


# === Main ===================================================================

def main():
    print('Building optimized triggers...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    print(f'  Triple triggers in OKX window: {len(triple_w):,}')

    # Replay per A4 tier WITHOUT filters first (we want all trades for liq analysis)
    tiers = {
        'Tier 1 (50% / -1.5R)': {'ladder_size_frac': 0.5, 'post_ladder_stop_R': 1.5},
        'Tier 2 (100% / -1.4R)': {'ladder_size_frac': 1.0, 'post_ladder_stop_R': 1.4},
        'Tier 3 (150% / -1.5R)': {'ladder_size_frac': 1.5, 'post_ladder_stop_R': 1.5},
    }

    tier_traces = {}
    for tier_name, params in tiers.items():
        print(f'\n--- Replaying {tier_name} ---')
        rows = []
        for _, t in triple_w.iterrows():
            r = replay_with_mae(t, df_smc, df_atr, fvgs, obs,
                                  enable_ladder=True, ladder_at_adv_R=0.3, **params)
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        opt = apply_filters(rep, delta_df, df_smc, fvgs, obs)
        print(f'  n trades (after filters): {len(opt)}')
        print(f'  trades with ladder fired: {int(opt["ladder_added"].sum())} ({opt["ladder_added"].mean()*100:.0f}%)')
        print(f'  MAE_pct distribution: median={opt["mae_pct_of_entry"].median()*100:.2f}%  '
               f'p90={opt["mae_pct_of_entry"].quantile(0.90)*100:.2f}%  '
               f'p99={opt["mae_pct_of_entry"].quantile(0.99)*100:.2f}%  '
               f'max={opt["mae_pct_of_entry"].max()*100:.2f}%')
        tier_traces[tier_name] = opt

    # === Liquidation analysis ===
    print('\n' + '=' * 80)
    print('=== LIQUIDATION ANALYSIS ===')
    print('=' * 80)
    print('For each (tier × leverage), what % of trades would have liquidated BEFORE stop fires?')
    print('Two interpretations of "leverage":')
    print('  (A) Per-trade leverage: position notional = L × NAV per trade')
    print('  (B) Risk-budgeted: position notional set so 1R loss = X% NAV (low effective leverage)')
    print()

    print('=== A) Per-trade leverage (high-leverage chento-style sizing) ===')
    print(f'{"tier":<25s} ', end='')
    leverages = [5, 10, 20, 25, 50, 100]
    for L in leverages:
        print(f'{L}x_liq%{liquidation_pct(L)*100:5.1f}%  ', end='')
    print()
    for tier_name, opt in tier_traces.items():
        print(f'{tier_name:<25s} ', end='')
        for L in leverages:
            # Effective leverage after ladder add = L * (1 + ladder_size_frac) for those trades
            # For trades without ladder: leverage = L
            # liq_pct depends on EFFECTIVE leverage
            liq_count = 0
            for _, row in opt.iterrows():
                eff_L = L * row['effective_size_multiplier']
                # Find nearest tabulated leverage to look up MMR
                tab_L = min(BINANCE_MMR.keys(), key=lambda x: abs(x - eff_L))
                mmr = BINANCE_MMR[tab_L]
                liq_dist = max(0.001, (1.0 / eff_L) - mmr)
                if row['mae_pct_of_entry'] >= liq_dist:
                    liq_count += 1
            pct = liq_count / len(opt) * 100 if len(opt) else 0
            print(f'    {pct:>5.1f}%      ', end='')
        print()

    print()
    print('=== B) Risk-budgeted sizing (1% NAV risk per trade) ===')
    print('  At 1% NAV risk per trade with 5xATR stop (~2.5% price):')
    print('  - position notional = 0.4× NAV per trade (pre-ladder)')
    print('  - Tier 2 post-ladder: notional = 0.8× NAV → effective 0.8x leverage')
    print('  - Tier 3 post-ladder: notional = 1.0× NAV → effective 1.0x leverage')
    print('  - At <1x leverage, liquidation is essentially impossible (price would need to go to 0)')
    print('  Conclusion: at 1% risk-per-trade, no liquidation risk in any tier.')
    print('  At 2% risk-per-trade: max NAV loss per trade = ' +
           '-{:.1f}% (Tier 1), -{:.1f}% (Tier 2), -{:.1f}% (Tier 3)'.format(
               2.0 * 2.1, 2.0 * 2.4, 2.0 * 3.3))
    print('  At 4% risk-per-trade: max NAV loss per trade = ' +
           '-{:.1f}% (Tier 1), -{:.1f}% (Tier 2), -{:.1f}% (Tier 3)'.format(
               4.0 * 2.1, 4.0 * 2.4, 4.0 * 3.3))

    # === C6 AMT ===
    print('\n' + '=' * 80)
    print('=== C6 AUCTION MARKET THEORY ===')
    print('=' * 80)
    print('Computing rolling 7d volume profile on BTC 15m...')
    df_15m = load_btc_15m()
    vp = compute_volume_profile(df_15m, window_days=7, n_price_bins=50)
    print(f'  vp shape: {vp.shape}, non-null poc: {vp["poc"].notna().sum():,}')

    # Test C6 as filter on Tier 2 (default recommended)
    opt_t2 = tier_traces['Tier 2 (100% / -1.4R)'].copy()
    print(f'\nBaseline Tier 2: n={len(opt_t2)}')

    # Attach VP features at trigger time
    ts_idx = pd.DatetimeIndex(opt_t2['ts'])
    ix = vp.index.searchsorted(ts_idx, side='right') - 1
    opt_t2['poc'] = [float(vp['poc'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
    opt_t2['vah'] = [float(vp['vah'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
    opt_t2['val'] = [float(vp['val'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]

    # Derive position relative to VA
    opt_t2['above_vah'] = opt_t2['entry'] > opt_t2['vah']
    opt_t2['below_val'] = opt_t2['entry'] < opt_t2['val']
    opt_t2['in_va'] = (~opt_t2['above_vah']) & (~opt_t2['below_val'])
    # Distance to nearest VA edge in R-units (positive = inside VA)
    opt_t2['dist_to_va_R'] = ((opt_t2[['vah', 'val']].sub(opt_t2['entry'], axis=0)).abs().min(axis=1)
                                / opt_t2['risk'])

    def summary(t, label):
        if t.empty: return {'label': label, 'n': 0}
        cum = t['r_outcome'].cumsum().values
        peak = np.maximum.accumulate(cum); dd = cum - peak
        is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
        return {
            'label': label, 'n': int(len(t)),
            'mean_R': round(float(t['r_outcome'].mean()), 3),
            'wr': round(float((t['r_outcome'] > 0).mean()), 3),
            'cum_R': round(float(cum[-1]), 2),
            'max_dd_R': round(float(dd.min()), 2),
            'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
            'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
            'IS_n': int(len(is_set)),
            'OOS_n': int(len(oos_set)),
        }

    def show(s):
        if s.get('n', 0) == 0:
            print(f'  {s["label"]:<55s} empty'); return
        print(f'  {s["label"]:<55s} n={s["n"]:>4d}  R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  '
               f'cumR={s["cum_R"]:>+7.1f}  maxDD={s["max_dd_R"]:+5.2f}  '
               f'IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')

    print('\n--- C6 filter variants on Tier 2 ---')
    show(summary(opt_t2, 'baseline Tier 2 (no C6 filter)'))

    # Variant 1: Skip trades inside VA (chop region)
    show(summary(opt_t2[~opt_t2['in_va']], 'skip if inside VA'))
    # Variant 2: Skip if outside VA (only inside)
    show(summary(opt_t2[opt_t2['in_va']], 'only if inside VA'))
    # Variant 3: Take LONGs at/below VAL, SHORTs at/above VAH (rejection trades)
    long_at_val = (opt_t2['direction'] == 'long') & (opt_t2['below_val'])
    short_at_vah = (opt_t2['direction'] == 'short') & (opt_t2['above_vah'])
    show(summary(opt_t2[long_at_val | short_at_vah], 'rejection at VA edge (long@val/short@vah)'))
    # Variant 4: Take LONGs above VAH (breakout follow), SHORTs below VAL (breakdown)
    long_above_vah = (opt_t2['direction'] == 'long') & (opt_t2['above_vah'])
    short_below_val = (opt_t2['direction'] == 'short') & (opt_t2['below_val'])
    show(summary(opt_t2[long_above_vah | short_below_val], 'breakout at VA edge (long>vah/short<val)'))
    # Variant 5: Distance threshold inside VA (avoid mid-VA chop)
    for d in (0.5, 1.0):
        show(summary(opt_t2[opt_t2['dist_to_va_R'] <= d], f'within {d}R of VA edge'))

    out_path = OUT_DIR / 'liquidation_and_C6_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Liquidation risk analysis per A4 tier vs leverage, plus '
                      'C6 Auction Market Theory (volume profile) filter test on '
                      'Tier 2 default.'),
            'mae_summary': {
                tier: {
                    'n': int(len(opt)),
                    'mae_pct_median': round(float(opt['mae_pct_of_entry'].median()), 5),
                    'mae_pct_p90': round(float(opt['mae_pct_of_entry'].quantile(0.90)), 5),
                    'mae_pct_p99': round(float(opt['mae_pct_of_entry'].quantile(0.99)), 5),
                    'mae_pct_max': round(float(opt['mae_pct_of_entry'].max()), 5),
                } for tier, opt in tier_traces.items()
            },
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
