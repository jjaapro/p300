"""validation_four_refinements: four follow-up tests on the H_B + skip_up_30d
optimized config:

1. D2 — DCA-fill triggers immediate trim. After A4 ladder fills at -0.3R,
   when price recovers to ENTRY (fav_R returns to 0), trim the ladder leg
   only (not the original position). Compare against H_B baseline.

2. VA window comparison — test 3-day / 7-day / 14-day rolling windows for
   the C6 AMT volume profile classifier. Which window optimizes the
   inside-VA vs outside-VA classification quality?

3. Per-direction regime asymmetry — does skip_up_30d hurt long trades only,
   short trades only, or both equally? If asymmetric we should skip only
   the bad direction.

4. B4 squeeze direction re-test — B4 was +0.87R on 88d cd_liquidations
   sample but -0.x on 236d TV sample. Re-run on full 5y cd_liquidations
   data to confirm the drop verdict.
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
    measure_r_outcomes, summarize_triggers,
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
from studies.notebooks.chento_journal.validation_regime_adaptation import (
    attach_regimes, compute_regimes,
)

COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === D2: ladder-only trim replay ===========================================

def replay_with_d2(trig, df_smc, df_atr, fvgs, obs, *,
                    atr_mult=5.0, target_r=6.0, tif_bars=4 * 72,
                    ladder_size_frac=1.5, post_ladder_stop_R=1.5,
                    d2_trim_at_fav_R: float | None = 0.0,
                    d2_trim_frac: float = 1.0,    # 1.0 = remove all of ladder leg
                    ) -> dict | None:
    """Replay with A4 ladder + optional D2 trim of ladder leg on price
    recovery. d2_trim_at_fav_R=0.0 means trim when fav_R returns to 0
    (price back at entry); d2_trim_frac=1.0 means trim 100% of ladder."""
    direction = trig['direction']
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
    stop = entry - risk if direction == 'long' else entry + risk
    target = entry + risk * target_r if direction == 'long' else entry - risk * target_r
    cost_R = (COST_BP / 10000.0) * (entry / risk)
    start = idx + 1
    end = min(start + tif_bars, len(df_smc))

    main_size = 1.0
    ladder_added = False
    ladder_size = 0.0
    ladder_entry_price = None
    ladder_trimmed = False
    ladder_trim_r = 0.0
    ladder_trim_size = 0.0
    max_fav_R_post_ladder = 0.0
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
            cur_R = (bc - entry) / risk
        else:
            fav_R = (entry - bl) / risk
            adv_R = (bh - entry) / risk
            cur_R = (entry - bc) / risk

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

        # A4 ladder fires at -0.3R adv
        if not ladder_added and adv_R >= 0.3:
            ladder_added = True
            ladder_size = ladder_size_frac
            ladder_entry_price = entry - risk * 0.3 if direction == 'long' else entry + risk * 0.3
            stop = entry - risk * post_ladder_stop_R if direction == 'long' else entry + risk * post_ladder_stop_R

        # Track max fav after ladder added
        if ladder_added:
            max_fav_R_post_ladder = max(max_fav_R_post_ladder, fav_R)

        # D2 trim: after ladder, when fav_R reaches d2_trim_at_fav_R, trim
        # (note: this fires when price recovers from -0.3R back to entry or
        # higher, which is fav_R going from negative back to >= 0)
        if (ladder_added and not ladder_trimmed and d2_trim_at_fav_R is not None
            and cur_R >= d2_trim_at_fav_R):
            # The ladder leg's R outcome on this trim: exit at current close
            if direction == 'long':
                ladder_trim_r = (bc - ladder_entry_price) / risk - cost_R
            else:
                ladder_trim_r = (ladder_entry_price - bc) / risk - cost_R
            ladder_trim_size = ladder_size * d2_trim_frac
            ladder_size -= ladder_trim_size
            ladder_trimmed = True

    if outcome_main is None:
        outcome_main = ((last_close - entry) / risk - cost_R if direction == 'long'
                          else (entry - last_close) / risk - cost_R)
        exit_kind = 'tif'

    # Ladder leg final outcome (whatever ladder_size remains)
    if ladder_added and ladder_size > 0:
        exit_price = (stop if exit_kind == 'stop'
                       else (target if exit_kind == 'target' else last_close))
        if direction == 'long':
            ladder_outcome = (exit_price - ladder_entry_price) / risk - cost_R
        else:
            ladder_outcome = (ladder_entry_price - exit_price) / risk - cost_R
    else:
        ladder_outcome = 0.0

    total_r = (main_size * outcome_main +
                ladder_size * ladder_outcome +
                ladder_trim_size * ladder_trim_r)

    return {
        'ts': ts, 'direction': direction, 'entry': entry, 'risk': risk,
        'r_outcome': total_r, 'exit_kind': exit_kind,
        'ladder_added': ladder_added, 'ladder_trimmed': ladder_trimmed,
        'mae_R_total': 0.0,    # placeholder for compatibility
        'mae_pct_of_entry': 0.0,
        'effective_size_multiplier': 1.0 + ladder_size + ladder_trim_size,
    }


def replay_set_d2(triple_w, df_smc, df_atr, fvgs, obs, delta_df, **kw):
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_d2(t, df_smc, df_atr, fvgs, obs, **kw)
        if r is not None: rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty: return rep
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    return apply_filters(rep, delta_df, df_smc, fvgs, obs)


def summary(t, label):
    if t.empty: return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum); dd = cum - peak
    is_set = t[t['ts'] <= IS_END]; oos_set = t[t['ts'] > IS_END]
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label, 'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'annual_R': round(float(cum[-1]) / max(span_y, 0.1), 1),
        'MAR': round((float(cum[-1]) / max(span_y, 0.1)) / abs(float(dd.min())), 2)
                 if dd.min() != 0 else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': int(len(is_set)),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': int(len(oos_set)),
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<58s} empty'); return
    print(f'  {s["label"]:<58s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  maxDD={s["max_dd_R"]:+5.2f}  '
           f'annual={s["annual_R"]:+.1f}R  MAR={s["MAR"]:>5.2f}  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


# === Main ===================================================================

def main():
    print('Building baseline H_B trade set...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized()
    df_15m = load_btc_15m()

    print(f'  Triple triggers in OKX window: {len(triple_w):,}')

    # ============================================================================
    # TEST 1: D2 — ladder-only trim
    # ============================================================================
    print('\n' + '=' * 80)
    print('=== TEST 1: D2 ladder-only trim on price recovery ===')
    print('=' * 80)

    # First reconstruct the H_B baseline (T3 inside-VA / T1 outside-VA)
    vp_7d = compute_volume_profile(df_15m, window_days=7, n_price_bins=50)

    def attach_va(df, vp):
        ts_idx = pd.DatetimeIndex(df['ts'])
        ix = vp.index.searchsorted(ts_idx, side='right') - 1
        df['vah'] = [float(vp['vah'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
        df['val'] = [float(vp['val'].iloc[i]) if 0 <= i < len(vp) else np.nan for i in ix]
        df['in_va'] = ((df['entry'] <= df['vah']) & (df['entry'] >= df['val']))
        df['dist_to_va_R'] = (df[['vah','val']].sub(df['entry'], axis=0)).abs().min(axis=1) / df['risk']
        return df

    # H_B baseline: T3 on inside-VA, T1 on outside-VA
    # We need to know in_va BEFORE replay to pick tier. Run T1 first, attach VA,
    # then replay with conditional sizing.
    print('\n--- H_B baseline (no D2) ---')
    rep_t1 = replay_set_d2(triple_w, df_smc, df_atr, fvgs, obs, delta_df,
                            ladder_size_frac=0.5, post_ladder_stop_R=1.5,
                            d2_trim_at_fav_R=None)
    rep_t1 = attach_va(rep_t1, vp_7d)
    show(summary(rep_t1, 'T1 baseline (50% ladder)'))

    # T3 replay
    rep_t3 = replay_set_d2(triple_w, df_smc, df_atr, fvgs, obs, delta_df,
                            ladder_size_frac=1.5, post_ladder_stop_R=1.5,
                            d2_trim_at_fav_R=None)
    rep_t3 = attach_va(rep_t3, vp_7d)
    show(summary(rep_t3, 'T3 baseline (150% ladder)'))

    # Hybrid H_B
    rep_hb = pd.concat([
        rep_t3[rep_t3['in_va']],
        rep_t1[~rep_t1['in_va']],
    ], ignore_index=True).sort_values('ts').reset_index(drop=True)
    show(summary(rep_hb, 'H_B baseline (T3 in-VA / T1 out)'))

    # Add regime filter
    rep_hb_regime = attach_regimes(rep_hb)
    skip_up_30d_mask = rep_hb_regime['ret30d_regime'] != 'up_30d'
    rep_hb_regime_filtered = rep_hb_regime[skip_up_30d_mask].copy()
    show(summary(rep_hb_regime_filtered, 'H_B + skip_up_30d (CURRENT DEFAULT)'))

    # Now D2 variants — re-run with d2_trim active
    print('\n--- D2 variants: trim ladder leg on price recovery ---')
    for trim_at_R, trim_frac, label_suffix in [
        (0.0, 1.0, 'trim 100% of ladder at fav=0R (entry)'),
        (0.0, 0.5, 'trim 50% of ladder at fav=0R'),
        (-0.1, 1.0, 'trim 100% of ladder at fav=-0.1R (early)'),
        (0.3, 1.0, 'trim 100% of ladder at fav=+0.3R (late)'),
        (0.5, 1.0, 'trim 100% of ladder at fav=+0.5R'),
    ]:
        # Build T1 + T3 with D2 active, then form H_B
        rep_t1_d2 = replay_set_d2(triple_w, df_smc, df_atr, fvgs, obs, delta_df,
                                    ladder_size_frac=0.5, post_ladder_stop_R=1.5,
                                    d2_trim_at_fav_R=trim_at_R, d2_trim_frac=trim_frac)
        rep_t1_d2 = attach_va(rep_t1_d2, vp_7d)
        rep_t3_d2 = replay_set_d2(triple_w, df_smc, df_atr, fvgs, obs, delta_df,
                                    ladder_size_frac=1.5, post_ladder_stop_R=1.5,
                                    d2_trim_at_fav_R=trim_at_R, d2_trim_frac=trim_frac)
        rep_t3_d2 = attach_va(rep_t3_d2, vp_7d)
        rep_hb_d2 = pd.concat([
            rep_t3_d2[rep_t3_d2['in_va']],
            rep_t1_d2[~rep_t1_d2['in_va']],
        ], ignore_index=True).sort_values('ts').reset_index(drop=True)
        rep_hb_d2_regime = attach_regimes(rep_hb_d2)
        filtered = rep_hb_d2_regime[rep_hb_d2_regime['ret30d_regime'] != 'up_30d']
        show(summary(filtered, f'D2 {label_suffix}'))

    # ============================================================================
    # TEST 2: VA window comparison (3d / 7d / 14d)
    # ============================================================================
    print('\n' + '=' * 80)
    print('=== TEST 2: VA window comparison ===')
    print('=' * 80)

    for window_days in (3, 7, 14):
        vp_w = compute_volume_profile(df_15m, window_days=window_days, n_price_bins=50)
        rep_t1_w = rep_t1.drop(columns=['in_va', 'dist_to_va_R', 'vah', 'val'], errors='ignore').copy()
        rep_t3_w = rep_t3.drop(columns=['in_va', 'dist_to_va_R', 'vah', 'val'], errors='ignore').copy()
        rep_t1_w = attach_va(rep_t1_w, vp_w)
        rep_t3_w = attach_va(rep_t3_w, vp_w)
        rep_hb_w = pd.concat([
            rep_t3_w[rep_t3_w['in_va']],
            rep_t1_w[~rep_t1_w['in_va']],
        ], ignore_index=True).sort_values('ts').reset_index(drop=True)
        rep_hb_w_regime = attach_regimes(rep_hb_w)
        filtered_w = rep_hb_w_regime[rep_hb_w_regime['ret30d_regime'] != 'up_30d']
        show(summary(filtered_w, f'H_B+skip_up_30d (VA window = {window_days}d)'))
        inside_count = int(rep_t1_w['in_va'].sum())
        print(f'    inside-VA classification: {inside_count}/{len(rep_t1_w)} '
               f'({inside_count/len(rep_t1_w)*100:.0f}%)')

    # ============================================================================
    # TEST 3: Per-direction regime asymmetry
    # ============================================================================
    print('\n' + '=' * 80)
    print('=== TEST 3: Per-direction regime asymmetry ===')
    print('=' * 80)

    print('\n--- H_B baseline split by direction ---')
    show(summary(rep_hb_regime[rep_hb_regime['direction'] == 'long'], 'all longs (no regime filter)'))
    show(summary(rep_hb_regime[rep_hb_regime['direction'] == 'short'], 'all shorts (no regime filter)'))

    print('\n--- Per-direction × ret30d_regime ---')
    print('regime\tdir\tn\tmean_R\tWR\tannual_R\tmaxDD')
    for regime in ('up_30d', 'flat_30d', 'down_30d'):
        for dir_ in ('long', 'short'):
            sub = rep_hb_regime[(rep_hb_regime['ret30d_regime'] == regime) &
                                  (rep_hb_regime['direction'] == dir_)]
            if len(sub) < 5:
                print(f'  {regime}\t{dir_}\tn={len(sub)} (too small)')
                continue
            cum = sub.sort_values('ts')['r_outcome'].cumsum().values
            peak = np.maximum.accumulate(cum); dd = (cum - peak).min()
            span_y = (sub['ts'].max() - sub['ts'].min()).total_seconds() / (365.25 * 86400)
            print(f'  {regime}\t{dir_}\tn={len(sub):>3d}\t'
                   f'mean_R={sub["r_outcome"].mean():+.3f}\t'
                   f'WR={(sub["r_outcome"] > 0).mean():.0%}\t'
                   f'annual={sub["r_outcome"].sum()/max(span_y,0.1):+.1f}\t'
                   f'maxDD={dd:+.2f}')

    # Asymmetric skip variants
    print('\n--- Asymmetric skip variants ---')
    show(summary(rep_hb_regime[~((rep_hb_regime['ret30d_regime'] == 'up_30d') &
                                    (rep_hb_regime['direction'] == 'long'))],
                   'skip ONLY longs in up_30d'))
    show(summary(rep_hb_regime[~((rep_hb_regime['ret30d_regime'] == 'up_30d') &
                                    (rep_hb_regime['direction'] == 'short'))],
                   'skip ONLY shorts in up_30d'))
    show(summary(rep_hb_regime[rep_hb_regime['ret30d_regime'] != 'up_30d'],
                   'skip ALL up_30d (current default)'))

    # ============================================================================
    # TEST 4: B4 squeeze direction re-test on full 5y
    # ============================================================================
    print('\n' + '=' * 80)
    print('=== TEST 4: B4 squeeze direction re-test on full 5y cd_liquidations ===')
    print('=' * 80)

    con = sqlite3.connect(str(DB))
    liq = pd.read_sql("""
        SELECT timestamp, long_quantity, short_quantity
        FROM cd_liquidations
        WHERE asset='BTC'
        ORDER BY timestamp
    """, con)
    con.close()
    if liq.empty:
        print('  No cd_liquidations BTC data — cannot re-test')
    else:
        liq['ts'] = pd.to_datetime(liq['timestamp'], unit='s', utc=True)
        liq = liq.set_index('ts').drop(columns='timestamp')
        liq['long_quantity'] = liq['long_quantity'].fillna(0)
        liq['short_quantity'] = liq['short_quantity'].fillna(0)
        print(f'  cd_liquidations span: {liq.index.min()} to {liq.index.max()}, {len(liq):,} rows')

        # Compute z-scores over 30-day rolling
        window_hours = 30 * 24
        for col in ('long_quantity', 'short_quantity'):
            mu = liq[col].rolling(window_hours, min_periods=window_hours // 4).mean()
            sd = liq[col].rolling(window_hours, min_periods=window_hours // 4).std()
            liq[f'{col[0]}_z'] = (liq[col] - mu) / sd

        df_b1 = compute_moneyflow_signal(df_15m)
        df_b1['atr'] = compute_atr(df_b1, period=14)
        # Reindex liq onto 15m frame
        df_b1['l_z'] = liq['l_z'].reindex(df_b1.index, method='ffill')
        df_b1['s_z'] = liq['s_z'].reindex(df_b1.index, method='ffill')

        # Generate triggers per chento B4 rules:
        # SHORT when long_z > threshold (longs being liquidated en masse)
        # LONG when short_z > threshold (shorts being liquidated en masse)
        print('\n  B4 squeeze direction at z>1.5 threshold (full 5y window)...')
        rows = []
        cooldown_bars = 4
        last_idx = -10**9
        for i in range(len(df_b1)):
            if i - last_idx < cooldown_bars: continue
            l_z = df_b1['l_z'].iloc[i]; s_z = df_b1['s_z'].iloc[i]
            atr = df_b1['atr'].iloc[i]
            if pd.isna(l_z) or pd.isna(s_z) or pd.isna(atr) or atr <= 0: continue
            entry = float(df_b1['close'].iloc[i])
            risk = float(atr) * 2.0
            direction = None
            if l_z > 1.5 and (pd.isna(s_z) or s_z <= 1.5):
                direction = 'short'
            elif s_z > 1.5 and (pd.isna(l_z) or l_z <= 1.5):
                direction = 'long'
            if direction is None: continue
            if direction == 'long':
                stop = entry - risk; target = entry + risk * 2.0
            else:
                stop = entry + risk; target = entry - risk * 2.0
            rows.append({'ts': df_b1.index[i], 'direction': direction,
                          'entry': entry, 'stop': stop, 'target': target,
                          'long_z': float(l_z), 'short_z': float(s_z)})
            last_idx = i
        trigs = pd.DataFrame(rows)
        print(f'  Triggers generated: {len(trigs)}')
        if not trigs.empty:
            trigs_r = measure_r_outcomes(trigs, df_b1)
            s = summarize_triggers(trigs_r, label='B4 full 5y')
            print(f'  n={s["n"]}  meanR={s["mean_R"]:+.3f}  WR={s["win_rate"]:.0%}  '
                   f'targets={s["targets"]}  stops={s["stops"]}  tifs={s["tifs"]}')
            is_set = trigs_r[trigs_r['ts'] <= IS_END]
            oos_set = trigs_r[trigs_r['ts'] > IS_END]
            print(f'  IS:  n={len(is_set):>4d}  meanR={is_set["r_outcome"].mean():+.3f}')
            print(f'  OOS: n={len(oos_set):>4d}  meanR={oos_set["r_outcome"].mean():+.3f}')

    out_path = OUT_DIR / 'four_refinements_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Four refinement tests: D2 ladder-trim, VA window comparison, '
                      'per-direction regime asymmetry, B4 5y re-test.'),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
