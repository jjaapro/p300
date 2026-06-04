"""validation_group_A_redo: re-test A4 ladder, A7 partial+BE, A9 trailing
properly on the optimized stack (atr5_t6R + TIF=72h + no_tilt +
no_resist_OB + okx_aligned z>=0).

Previous tests had issues:
  - A4 fired at -1R but stop was at -1R (atr5 stop). Ladder could never trigger.
    Fix: ladder at -0.3R / -0.5R / -0.7R; widen combined stop to -2R after add.
  - A7 was tested as isolated partial trim. Chento's actual A7 is COMBINED
    with A3 (move SL to BE on remainder after partial). Fix: test combined.
  - A9 only tested tightening TP after MFE. Other forms not tested.
    Fix: also test trailing-stop activation (when MFE >= X, trail at MFE - Y).
"""
from __future__ import annotations

import json
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
    features_at,
)
from studies.notebooks.chento_journal.validation_multi_asset import (
    derive_binance_1h_close, compute_okx_delta_z, load_okx_close_asset,
)

COST_BP = 18.0
IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


# === Enhanced replay =======================================================

def replay_v2(trig, df_smc, df_atr, fvgs, obs, *,
               atr_mult=5.0, target_r=6.0,
               tif_bars=4 * 72,
               # A4 ladder
               ladder_at_adv_R=None,      # e.g., 0.5 (positive number)
               ladder_size_frac=0.5,
               post_ladder_stop_R=None,   # e.g., 2.0 (positive = -2R from orig entry)
               # A7 partial + A3 BE on remainder
               partial_at_R=None,         # e.g., 1.0 (positive = +1R fav)
               partial_frac=0.0,          # e.g., 0.33
               be_after_partial=False,    # move stop to entry on remainder
               # A9 trail stop after MFE
               trail_activate_at_R=None,  # e.g., 2.0 (positive = +2R fav)
               trail_step_R=None,         # e.g., 1.0 (trail at MFE - 1.0R)
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
    partial_taken = False
    partial_r = 0.0
    ladder_added = False
    ladder_size = 0.0
    ladder_entry_price = None
    max_fav_R = 0.0
    trail_active = False
    exit_kind = None
    outcome_main = None

    for j in range(start, end):
        bh = float(df_smc['high'].iloc[j])
        bl = float(df_smc['low'].iloc[j])
        bc = float(df_smc['close'].iloc[j])

        if direction == 'long':
            fav_R = (bh - entry) / risk
            adv_R = (entry - bl) / risk
        else:
            fav_R = (entry - bl) / risk
            adv_R = (bh - entry) / risk
        max_fav_R = max(max_fav_R, fav_R)

        # 1) Hit stop or target (priority: stop)
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

        # 2) A4 ladder add (fires when adverse excursion >= ladder_at_adv_R BUT BEFORE stop)
        if (ladder_at_adv_R is not None and not ladder_added
            and adv_R >= ladder_at_adv_R):
            ladder_added = True
            ladder_size = ladder_size_frac
            if direction == 'long':
                ladder_entry_price = entry - risk * ladder_at_adv_R
            else:
                ladder_entry_price = entry + risk * ladder_at_adv_R
            # Widen stop to combined level
            if post_ladder_stop_R is not None:
                if direction == 'long':
                    stop = entry - risk * post_ladder_stop_R
                else:
                    stop = entry + risk * post_ladder_stop_R

        # 3) A7 partial take + A3 BE
        if (partial_at_R is not None and not partial_taken
            and fav_R >= partial_at_R):
            partial_r = partial_at_R - cost_R    # partial leg pays cost
            partial_taken = True
            main_size -= partial_frac
            if be_after_partial:
                stop = entry    # move SL to BE on remainder
            if main_size <= 0:
                outcome_main = 0.0
                exit_kind = 'partial_close'; break

        # 4) A9 trail stop activation
        if (trail_activate_at_R is not None and not trail_active
            and max_fav_R >= trail_activate_at_R):
            trail_active = True
        if trail_active and trail_step_R is not None:
            # Trail stop at (max_fav_R - trail_step_R) from entry
            new_trail_R = max_fav_R - trail_step_R
            if direction == 'long':
                trail_stop_price = entry + risk * new_trail_R
                if trail_stop_price > stop:
                    stop = trail_stop_price
            else:
                trail_stop_price = entry - risk * new_trail_R
                if trail_stop_price < stop:
                    stop = trail_stop_price

    if outcome_main is None:
        last_close = float(df_smc['close'].iloc[end - 1])
        if direction == 'long':
            outcome_main = (last_close - entry) / risk - cost_R
        else:
            outcome_main = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'

    # Ladder leg outcome at same exit price as main
    if ladder_added:
        exit_price = stop if exit_kind == 'stop' else (target if exit_kind == 'target' else last_close)
        if direction == 'long':
            ladder_outcome = (exit_price - ladder_entry_price) / risk - cost_R
        else:
            ladder_outcome = (ladder_entry_price - exit_price) / risk - cost_R
    else:
        ladder_outcome = 0.0

    if partial_taken:
        total_r = partial_frac * partial_r + main_size * outcome_main + ladder_size * ladder_outcome
    else:
        total_r = main_size * outcome_main + ladder_size * ladder_outcome

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'risk': risk,
        'r_outcome': total_r,
        'exit_kind': exit_kind,
        'partial_taken': partial_taken,
        'ladder_added': ladder_added,
        'max_fav_R': round(max_fav_R, 3),
    }


# === Apply filters =========================================================

def apply_filters(rep, delta_df):
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


# === Build triggers ========================================================

def build_optimized_triggers():
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
    }


def show(s):
    if s.get('n', 0) == 0:
        print(f'  {s["label"]:<60s} empty')
        return
    print(f'  {s["label"]:<60s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
           f'R={s["mean_R"]:+.3f}  WR={s["wr"]:.0%}  cumR={s["cum_R"]:>+7.1f}  '
           f'maxDD={s["max_dd_R"]:+5.2f}  IS={s["IS_meanR"]:+.3f}({s["IS_n"]})  '
           f'OOS={s["OOS_meanR"]:+.3f}({s["OOS_n"]})')


def main():
    print('Building optimized triggers...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()
    print(f'  Triple triggers in OKX window: {len(triple_w):,}')

    def replay_set(label, **kw):
        rows = []
        for _, t in triple_w.iterrows():
            r = replay_v2(t, df_smc, df_atr, fvgs, obs, **kw)
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        if rep.empty:
            return summary(rep, label)
        dist_arr = []
        for _, row in rep.iterrows():
            idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
            if idx < 0 or idx >= len(df_smc):
                dist_arr.append(np.nan); continue
            f = features_at(idx, row['entry'], row['direction'], row['risk'],
                              df_smc, fvgs, obs)
            dist_arr.append(f.get('dist_resist_OB_R', np.nan))
        rep['dist_resist_OB_R'] = dist_arr
        opt = apply_filters(rep, delta_df)
        return summary(opt, label)

    all_results = {}

    # === Baseline (TIF=72h, no extras) ===
    print('\n=== Baseline atr5_t6R TIF=72h ===')
    s = replay_set('baseline_TIF72')
    show(s); all_results['baseline_TIF72'] = s

    # === A4 — ladder FIXED (trigger BEFORE atr5 stop hits) ===
    print('\n=== A4 ladder (fixed): trigger BEFORE stop with widened combined stop ===')
    for adv_R in (0.3, 0.5, 0.7, 1.0):
        # Note: ladder at 1.0 = at stop; meaningful test is < 1.0
        for size_frac in (0.5, 1.0):    # 0.5 = half-size add; 1.0 = full add
            for post_stop_R in (1.5, 2.0, 2.5):
                if adv_R >= post_stop_R:
                    continue
                lbl = f'A4_ladder_adv{adv_R}_size{int(size_frac*100)}_stop{post_stop_R}'
                s = replay_set(lbl,
                                ladder_at_adv_R=adv_R,
                                ladder_size_frac=size_frac,
                                post_ladder_stop_R=post_stop_R)
                all_results[lbl] = s
                show(s)

    # === A7 — partial + BE on rest (combined with A3) ===
    print('\n=== A7 partial trim + A3 BE on remainder (chento\'s actual rule) ===')
    for partial_R in (1.0, 1.5, 2.0, 3.0):
        for partial_frac in (0.25, 0.33, 0.50):
            lbl = f'A7_partial{int(partial_frac*100)}_at{partial_R}R_BErest'
            s = replay_set(lbl,
                            partial_at_R=partial_R,
                            partial_frac=partial_frac,
                            be_after_partial=True)
            all_results[lbl] = s
            show(s)

    # === A7 without BE for comparison (partial only) ===
    print('\n=== A7 partial trim ONLY (no BE) — for comparison ===')
    for partial_R in (1.0, 2.0):
        for partial_frac in (0.25, 0.33):
            lbl = f'A7_partial{int(partial_frac*100)}_at{partial_R}R_noBE'
            s = replay_set(lbl,
                            partial_at_R=partial_R,
                            partial_frac=partial_frac,
                            be_after_partial=False)
            all_results[lbl] = s
            show(s)

    # === A9 — trail stop activation (lock in profit after MFE >= X, trail at MFE - Y) ===
    print('\n=== A9 trail stop activation ===')
    for activate_at in (2.0, 3.0, 4.0):
        for step in (0.5, 1.0, 1.5, 2.0):
            if step >= activate_at:
                continue
            lbl = f'A9_trail_at{activate_at}R_step{step}R'
            s = replay_set(lbl,
                            trail_activate_at_R=activate_at,
                            trail_step_R=step)
            all_results[lbl] = s
            show(s)

    # === Best stacks ===
    print('\n=== Combined best A4+A7+A9 candidates ===')
    # Stack: best A4 + best A7 + best A9 (if any are positive vs baseline)
    candidates = [
        ('A4(0.5_50_2.0) + A7(33%_2R_BE)',
         {'ladder_at_adv_R': 0.5, 'ladder_size_frac': 0.5, 'post_ladder_stop_R': 2.0,
          'partial_at_R': 2.0, 'partial_frac': 0.33, 'be_after_partial': True}),
        ('A4(0.7_50_2.5) + A9(trail_at_3_step_1)',
         {'ladder_at_adv_R': 0.7, 'ladder_size_frac': 0.5, 'post_ladder_stop_R': 2.5,
          'trail_activate_at_R': 3.0, 'trail_step_R': 1.0}),
        ('A7(33%_2R_BE) + A9(trail_at_3_step_1)',
         {'partial_at_R': 2.0, 'partial_frac': 0.33, 'be_after_partial': True,
          'trail_activate_at_R': 3.0, 'trail_step_R': 1.0}),
    ]
    for lbl, kw in candidates:
        s = replay_set(lbl, **kw)
        all_results[lbl] = s
        show(s)

    # === Top by mean R, by cum R, by max-DD ===
    print('\n=== Top by mean R (n>=50) ===')
    ranked = sorted([(k, v) for k, v in all_results.items() if v.get('n', 0) >= 50],
                      key=lambda kv: kv[1].get('mean_R', 0), reverse=True)[:10]
    for k, v in ranked:
        print(f'  {k:<60s} meanR={v["mean_R"]:+.3f}  cumR={v["cum_R"]:+7.1f}  '
               f'maxDD={v["max_dd_R"]:+5.2f}  IS={v["IS_meanR"]:+.3f}  OOS={v["OOS_meanR"]:+.3f}')

    print('\n=== Top by max-DD (n>=50) ===')
    ranked_dd = sorted([(k, v) for k, v in all_results.items() if v.get('n', 0) >= 50],
                         key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)[:10]
    for k, v in ranked_dd:
        print(f'  {k:<60s} maxDD={v["max_dd_R"]:+5.2f}  meanR={v["mean_R"]:+.3f}  '
               f'cumR={v["cum_R"]:+7.1f}')

    out_path = OUT_DIR / 'group_A_redo_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Redo of A4/A7/A9 with proper semantics: A4 ladder triggers '
                      'BEFORE atr5 stop with widened combined stop; A7 partial + '
                      'A3 BE on remainder (combined); A9 trail stop activation '
                      '(not just TP tightening).'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
