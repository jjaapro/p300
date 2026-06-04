"""validation_group_A_tuning: test the remaining untested Group A sleeve-
config knobs on top of the fully-optimized Triple stack (atr5_t6R +
no_tilt + no_resist_OB + okx_aligned z≥0).

Tests:
  A2 TIF tighten   — sweep TIF ∈ {6h, 12h, 24h, 48h, 72h, 168h}.
  A4 2-rung ladder-add at −1R   — re-enter same direction at price = entry
                                  ± 1R (50% size), hard stop at −1.5R below
                                  ORIGINAL entry. Bounded — no martingale.
  A7 25% partial trim at +1R    — exit 25% at price = entry ± 1R, remainder
                                  runs to 6R target.
  A9 dynamic TP adjust          — when MFE reaches 2R, tighten target from
                                  6R to 4R; when MFE reaches 4R, tighten to
                                  2R-trail.
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


# === Generalized replay ====================================================

def replay_with_extras(trig, df_smc, df_atr, fvgs, obs, *,
                        atr_mult=5.0, target_r=6.0,
                        tif_bars=4 * 24,
                        partial_at_R: float | None = None,
                        partial_frac: float = 0.0,
                        ladder_at_minus_R: float | None = None,
                        ladder_size_frac: float = 0.5,
                        ladder_stop_at_R: float = -1.5,
                        dynamic_tp_step1_at_R: float | None = None,
                        dynamic_tp_step1_to_R: float | None = None,
                        dynamic_tp_step2_at_R: float | None = None,
                        dynamic_tp_step2_to_R: float | None = None,
                        ) -> dict | None:
    """Replay with optional A2 (tif), A4 (ladder-add), A7 (partial), A9 (dynamic TP).

    Position sizing: total notional starts at 1 unit. Partial exits and
    ladder adds adjust that. Final R-outcome is the size-weighted sum of
    each leg's R outcome.
    """
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

    # State for legged management
    main_size = 1.0
    partial_taken = False
    partial_r = 0.0
    ladder_added = False
    ladder_size = 0.0
    ladder_entry = None
    ladder_r_unrealized = 0.0
    max_fav_R = 0.0
    target_active_R = target_r
    target_price = target
    exit_kind = None
    exit_ts = None
    outcome_main = None    # R-outcome on the size that remains until final exit

    for j in range(start, end):
        bo = float(df_smc['open'].iloc[j])
        bh = float(df_smc['high'].iloc[j])
        bl = float(df_smc['low'].iloc[j])
        bc = float(df_smc['close'].iloc[j])

        # Compute favorable / adverse excursion vs ORIGINAL entry
        if direction == 'long':
            fav_R_bar = (bh - entry) / risk
            adv_R_bar = (entry - bl) / risk
        else:
            fav_R_bar = (entry - bl) / risk
            adv_R_bar = (bh - entry) / risk
        max_fav_R = max(max_fav_R, fav_R_bar)

        # 1. Check stop-out on remaining main position
        if direction == 'long':
            if bl <= stop:
                outcome_main = (stop - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bh >= target_price:
                outcome_main = (target_price - entry) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break
        else:
            if bh >= stop:
                outcome_main = (entry - stop) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'stop'; break
            if bl <= target_price:
                outcome_main = (entry - target_price) / risk - cost_R
                exit_ts = df_smc.index[j]; exit_kind = 'target'; break

        # 2. Partial-take (A7) — when fav_R_bar reaches partial_at_R, exit partial_frac
        if (partial_at_R is not None and not partial_taken
            and fav_R_bar >= partial_at_R):
            # Pay cost on partial trade too
            partial_r = partial_at_R - cost_R
            partial_taken = True
            main_size -= partial_frac
            if main_size <= 0:
                outcome_main = 0.0
                exit_ts = df_smc.index[j]; exit_kind = 'partial_close'; break

        # 3. Ladder-add (A4) — when adv_R_bar reaches ladder_at_minus_R add ladder_size_frac
        if (ladder_at_minus_R is not None and not ladder_added
            and adv_R_bar >= ladder_at_minus_R):
            ladder_added = True
            ladder_size = ladder_size_frac
            # New entry is the price at this bar's low (long) or high (short)
            ladder_entry = entry - risk if direction == 'long' else entry + risk
            # Hard combined stop at ladder_stop_at_R (R-units relative to original entry)
            if direction == 'long':
                stop = entry + risk * ladder_stop_at_R    # negative R = below entry
            else:
                stop = entry - risk * ladder_stop_at_R

        # 4. Dynamic TP adjust (A9)
        if (dynamic_tp_step1_at_R is not None and dynamic_tp_step1_to_R is not None
            and max_fav_R >= dynamic_tp_step1_at_R
            and target_active_R != dynamic_tp_step1_to_R
            and target_active_R > dynamic_tp_step1_to_R):
            target_active_R = dynamic_tp_step1_to_R
            target_price = (entry + risk * target_active_R if direction == 'long'
                             else entry - risk * target_active_R)
        if (dynamic_tp_step2_at_R is not None and dynamic_tp_step2_to_R is not None
            and max_fav_R >= dynamic_tp_step2_at_R
            and target_active_R != dynamic_tp_step2_to_R
            and target_active_R > dynamic_tp_step2_to_R):
            target_active_R = dynamic_tp_step2_to_R
            target_price = (entry + risk * target_active_R if direction == 'long'
                             else entry - risk * target_active_R)

    if outcome_main is None:
        last_close = float(df_smc['close'].iloc[end - 1])
        if direction == 'long':
            outcome_main = (last_close - entry) / risk - cost_R
        else:
            outcome_main = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df_smc.index[end - 1]

    # If ladder added: compute its R outcome at the same exit price
    if ladder_added:
        # The ladder leg exited at the same time as the main leg
        # Its entry was 1R worse than original entry. Its outcome:
        if direction == 'long':
            ladder_outcome = (target_price - ladder_entry) / risk - cost_R \
                if exit_kind == 'target' else (
                (stop - ladder_entry) / risk - cost_R if exit_kind == 'stop'
                else (last_close - ladder_entry) / risk - cost_R)
        else:
            ladder_outcome = (ladder_entry - target_price) / risk - cost_R \
                if exit_kind == 'target' else (
                (ladder_entry - stop) / risk - cost_R if exit_kind == 'stop'
                else (ladder_entry - last_close) / risk - cost_R)
    else:
        ladder_outcome = 0.0

    # Composite R-outcome (size-weighted)
    if partial_taken:
        total_r = partial_frac * partial_r + main_size * outcome_main \
                  + ladder_size * ladder_outcome
    else:
        total_r = main_size * outcome_main + ladder_size * ladder_outcome

    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target_price,
        'risk': risk,
        'r_outcome': total_r,
        'exit_kind': exit_kind,
        'partial_taken': partial_taken,
        'ladder_added': ladder_added,
        'max_fav_R': round(max_fav_R, 3),
    }


# === Apply filters (Triple → optimized) ====================================

def build_optimized_triggers():
    """Build optimized Triple triggers (BTC, 5.4y window with OKX data)."""
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

    # OKX deltas
    okx_close = load_okx_close_asset('BTC')
    bnb_close_1h = derive_binance_1h_close(df_15m)
    delta_df = compute_okx_delta_z(bnb_close_1h, okx_close)

    # Restrict to OKX window
    okx_start = delta_df.index.min() + pd.Timedelta(days=14)
    triple_w = triple[triple['ts'] >= okx_start].copy()
    return triple_w, df_smc, df_atr, fvgs, obs, delta_df


def apply_filters(rep, delta_df):
    """Apply no_tilt + no_resist_OB + okx_aligned (z>=0)."""
    rep = rep.sort_values('ts').reset_index(drop=True)
    cur = 0; lb = []
    for r in rep['r_outcome'].shift(1).fillna(0):
        if r < 0: cur += 1
        else: cur = 0
        lb.append(cur)
    rep['consec_losses_before'] = lb

    # Attach okx_delta_z
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = delta_df.index.searchsorted(ts_idx, side='right') - 1
    rep['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i])
                            if 0 <= i < len(delta_df) else np.nan for i in ix]

    mask = ((rep['consec_losses_before'] == 0) &
             ((rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()) &
             (((rep['direction'] == 'long') & (rep['okx_delta_z'] >= 0)) |
              ((rep['direction'] == 'short') & (rep['okx_delta_z'] <= 0))))
    return rep[mask].copy()


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
        print(f'  {s["label"]:<55s} empty')
        return
    print(f'  {s["label"]:<55s} n={s["n"]:>4d} ({s.get("per_yr",0):>5.1f}/yr)  '
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
            r = replay_with_extras(t, df_smc, df_atr, fvgs, obs, **kw)
            if r is not None:
                # Attach dist_resist_OB_R from features_at for filter
                pass
            if r is not None:
                rows.append(r)
        rep = pd.DataFrame(rows)
        if rep.empty:
            return None, summary(rep, label)
        # Attach dist_resist_OB_R for the filter (recompute via features_at)
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
        return rep, summary(opt, label)

    all_results = {}

    # === BASELINE (no extras) ===
    print('\n=== Baseline (atr5_t6R, no extras) ===')
    rep_base, sb = replay_set('baseline')
    show(sb)
    all_results['baseline'] = sb

    # === A2 TIF sweep ===
    print('\n=== A2 TIF sweep ===')
    for tif_h in (6, 12, 24, 48, 72, 168):
        _, s = replay_set(f'A2_TIF_{tif_h}h', tif_bars=4 * tif_h)
        all_results[s['label']] = s
        show(s)

    # === A7 partial trim at +1R ===
    print('\n=== A7 partial trim at +1R ===')
    for frac in (0.25, 0.33, 0.50):
        _, s = replay_set(f'A7_partial_{int(frac*100)}pct_at_1R',
                            partial_at_R=1.0, partial_frac=frac)
        all_results[s['label']] = s
        show(s)
    # Also try +2R partial
    for frac in (0.25, 0.33, 0.50):
        _, s = replay_set(f'A7_partial_{int(frac*100)}pct_at_2R',
                            partial_at_R=2.0, partial_frac=frac)
        all_results[s['label']] = s
        show(s)

    # === A4 2-rung ladder-add at -1R ===
    print('\n=== A4 ladder-add at -1R (bounded, hard stop at -1.5R) ===')
    for size_frac in (0.50, 0.33):
        _, s = replay_set(f'A4_ladder_at_-1R_size{int(size_frac*100)}pct',
                            ladder_at_minus_R=1.0, ladder_size_frac=size_frac,
                            ladder_stop_at_R=-1.5)
        all_results[s['label']] = s
        show(s)

    # === A9 dynamic TP adjust ===
    print('\n=== A9 dynamic TP adjust ===')
    for s1_at, s1_to in [(2.0, 4.0), (3.0, 5.0)]:
        _, s = replay_set(f'A9_TP_adj_at{s1_at}R_to{s1_to}R',
                            dynamic_tp_step1_at_R=s1_at,
                            dynamic_tp_step1_to_R=s1_to)
        all_results[s['label']] = s
        show(s)

    # === Combine winners ===
    print('\n=== Combined: TIF + best partial + best dynamic TP ===')
    for tif_h, partial_at, partial_frac in [(24, 2.0, 0.25), (12, 2.0, 0.25)]:
        _, s = replay_set(f'combo_TIF{tif_h}h_partial{int(partial_frac*100)}pct_at_{partial_at}R',
                            tif_bars=4 * tif_h,
                            partial_at_R=partial_at, partial_frac=partial_frac)
        all_results[s['label']] = s
        show(s)

    # === Final ranking ===
    print('\n=== Top by mean R (n>=80) ===')
    ranked = sorted([(k, v) for k, v in all_results.items()
                       if v.get('n', 0) >= 80],
                      key=lambda kv: kv[1].get('mean_R', 0), reverse=True)[:8]
    for k, v in ranked:
        print(f'  {k:<48s} meanR={v["mean_R"]:+.3f}  cumR={v["cum_R"]:+7.1f}  '
               f'maxDD={v["max_dd_R"]:+5.2f}  IS={v["IS_meanR"]:+.3f}  '
               f'OOS={v["OOS_meanR"]:+.3f}')

    print('\n=== Top by max-DD (n>=80) ===')
    ranked_dd = sorted([(k, v) for k, v in all_results.items()
                          if v.get('n', 0) >= 80],
                         key=lambda kv: kv[1].get('max_dd_R', -99), reverse=True)[:8]
    for k, v in ranked_dd:
        print(f'  {k:<48s} maxDD={v["max_dd_R"]:+5.2f}  meanR={v["mean_R"]:+.3f}  '
               f'cumR={v["cum_R"]:+7.1f}')

    out_path = OUT_DIR / 'group_A_tuning_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Group A sleeve-tuning tested on top of fully optimized '
                      'Triple (atr5_t6R + no_tilt + no_resist_OB + okx_aligned z>=0). '
                      'Items: A2 TIF, A4 ladder, A7 partial, A9 dynamic TP.'),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
