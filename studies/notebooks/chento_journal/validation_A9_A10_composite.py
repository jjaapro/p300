"""validation_A9_A10_composite: dynamic TP adjust, MTF cell filter, and
composite of the wins from A4/A7/A9.

EXPERIMENTS — NOT FOR SHIP.

A9 dynamic TP adjust:
  When T1 fires, if trail-watermark already > 1.5R, move T2 from 3R -> 2R
  (lock-in earlier in momentum decay).

A10 MTF cell filter (reframed from "leverage taxonomy"):
  v2 baseline accepts MTF_NET in (-3, 1). Test each sub-cell separately
  to see which net values actually carry the edge. Also test the
  capitulation sig '--+++' subset alone.

Composite C1 = A4 (rung -0.75R/-1.0R, hard stop -1.5R) + A7 (T1 close 25%)
Composite C2 = composite C1 + A9 (dynamic TP adjust)
Composite C3 = A4 + A7 + A9 + MTF cell filter (top cell from A10)
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

from studies.notebooks.chento_journal.validation_A_sleeve_tuning import (
    ASSETS,
    build_btc_frame, build_eth_frame, build_op_frame,
    replay_exits, replay_one, summarize, print_summary,
    _entry_cache_key, _load_cached_entries,
)
from studies.notebooks.chento_journal.validation_A4_bounded_ladder import (
    replay_one_ladder_long,
)
from strategies.sleeves.chento_limit_bid import config as cli_cfg


COST_PER_UNIT = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0


# === A9 dynamic-TP replay (no ladder) ======================================

def replay_one_dynamic_tp(entry_row: dict, f: pd.DataFrame, *,
                           t1_r: float = 1.0,
                           t1_close_pct: float = 0.333,
                           t2_r_default: float = 3.0,
                           t2_r_fast: float = 2.0,
                           fast_trigger_mfe_R: float = 1.5,
                           t2_close_pct: float = 0.5,
                           trail_pct: float = 0.05,
                           tif_days: float = 21,
                           cost_per_unit: float = COST_PER_UNIT):
    """Like replay_one but: at T1 fire, if MFE-so-far >= fast_trigger_mfe_R,
    use t2_r_fast (2R) instead of t2_r_default (3R).
    """
    now_ts = entry_row['now_ts']
    entry = float(entry_row['entry'])
    stop_initial = float(entry_row['stop'])
    risk = entry - stop_initial
    if risk <= 0:
        return None

    tif_end = now_ts + pd.Timedelta(days=tif_days)
    forward = f.loc[now_ts:tif_end]

    t1_price = entry + t1_r * risk
    t2_r_used = t2_r_default  # set when T1 fires based on MFE
    state = {
        't1_done': False, 't2_done': False, 'trail_armed': False,
        'high_water': entry, 'active_stop': stop_initial,
    }
    remaining = 1.0
    realized_R = 0.0
    mfe_R = 0.0
    max_dd_R = 0.0
    outcome_final = 'tif'
    exit_ts_final = forward.index[-1]
    exit_price_final = float(forward['spot_c'].iloc[-1])

    for ts_, bar in forward.iterrows():
        if ts_ == now_ts:
            continue
        bar_h = float(bar['spot_h']); bar_l = float(bar['spot_l'])
        cur_mfe_R = (bar_h - entry) / risk
        mfe_R = max(mfe_R, cur_mfe_R)
        max_dd_R = min(max_dd_R, (bar_l - entry) / risk)
        state['high_water'] = max(state['high_water'], bar_h)

        # Stop first
        if bar_l <= state['active_stop']:
            stop_px = state['active_stop']
            r = (stop_px - entry) / risk
            cost = cost_per_unit * (stop_px / risk) * remaining
            realized_R += remaining * r - cost
            outcome_final = ('trail_exit' if state['trail_armed'] and
                              state['active_stop'] > stop_initial else 'stop')
            exit_ts_final = ts_; exit_price_final = stop_px
            remaining = 0
            break

        # T1
        if not state['t1_done'] and bar_h >= t1_price:
            state['t1_done'] = True
            state['trail_armed'] = True
            sp = t1_close_pct
            r = (t1_price - entry) / risk
            cost = cost_per_unit * (t1_price / risk) * sp
            realized_R += sp * r - cost
            remaining -= sp
            # A9 dynamic: if MFE already past fast_trigger, use closer T2
            if cur_mfe_R >= fast_trigger_mfe_R:
                t2_r_used = t2_r_fast

        # T2 (uses t2_r_used, which may have been updated at T1)
        t2_price = entry + t2_r_used * risk
        if state['t1_done'] and not state['t2_done'] and bar_h >= t2_price:
            state['t2_done'] = True
            sp = t2_close_pct * remaining
            r = (t2_price - entry) / risk
            cost = cost_per_unit * (t2_price / risk) * sp
            realized_R += sp * r - cost
            remaining -= sp

        if state['trail_armed']:
            new_trail = state['high_water'] * (1 - trail_pct)
            if new_trail > state['active_stop']:
                state['active_stop'] = new_trail

        if remaining <= 1e-9:
            break

    if remaining > 0:
        r = (exit_price_final - entry) / risk
        cost = cost_per_unit * (exit_price_final / risk) * remaining
        realized_R += remaining * r - cost
        outcome_final = 'tif'

    hold_h = (exit_ts_final - now_ts).total_seconds() / 3600.0
    return {
        'r_net': realized_R,
        'hold_h': hold_h,
        'outcome': outcome_final,
        't1_done': state['t1_done'],
        't2_done': state['t2_done'],
        'mfe_R': mfe_R,
        'max_dd_R': max_dd_R,
        't2_r_used': t2_r_used,
    }


def run_replay(entries_by_asset: dict, asset_frames: dict, replay_fn,
                **kwargs) -> pd.DataFrame:
    rows = []
    for asset, entries in entries_by_asset.items():
        if len(entries) == 0:
            continue
        for _, row in entries.iterrows():
            r = replay_fn(row.to_dict(), asset_frames[asset], **kwargs)
            if r is None:
                continue
            rows.append({**row.to_dict(), **r})
    return pd.DataFrame(rows)


# === A10 MTF-cell filter ===================================================

def filter_by_mtf_cell(entries: pd.DataFrame, mtf_net_values: set | None = None,
                        require_capitulation: bool = False) -> pd.DataFrame:
    if entries.empty:
        return entries
    if require_capitulation:
        return entries[entries['mtf_sig'] == cli_cfg.MTF_CAPITULATION_SIG].copy()
    if mtf_net_values is None:
        return entries
    return entries[entries['mtf_net'].isin(mtf_net_values)].copy()


# === Composite replay (A4 ladder + A7 25% trim + A9 dynamic TP) ===========

def replay_one_composite(entry_row: dict, f: pd.DataFrame, *,
                          rung1_r: float = -0.75, rung1_size: float = 0.5,
                          rung2_r: float = -1.0, rung2_size: float = 0.5,
                          hard_stop_r: float = -1.5,
                          t1_r: float = 1.0,
                          t1_close_pct_of_total: float = 0.25,  # A7
                          t2_r_default: float = 3.0,
                          t2_r_fast: float = 2.0,                 # A9
                          fast_trigger_mfe_R: float = 1.5,        # A9
                          t2_close_pct_of_remaining: float = 0.5,
                          trail_pct: float = 0.05,
                          tif_days: float = 21,
                          cost_per_unit: float = COST_PER_UNIT):
    """Combines A4 bounded ladder + A7 25% trim at T1 + A9 dynamic TP."""
    now_ts = entry_row['now_ts']
    entry = float(entry_row['entry'])
    stop_initial = float(entry_row['stop'])
    risk = entry - stop_initial
    if risk <= 0:
        return None

    t1_price = entry + t1_r * risk
    rung1_trigger = entry + rung1_r * risk
    rung2_trigger = entry + rung2_r * risk
    hard_stop_price = entry + hard_stop_r * risk

    fills = [(entry, 1.0)]
    rung1_filled = False
    rung2_filled = False
    t1_done = False
    t2_done = False
    trail_armed = False
    high_water = entry
    active_stop = hard_stop_price
    t2_r_used = t2_r_default

    tif_end = now_ts + pd.Timedelta(days=tif_days)
    forward = f.loc[now_ts:tif_end]

    realized_R = 0.0
    mfe_R = 0.0
    max_dd_R = 0.0
    outcome_final = 'tif'
    exit_ts_final = forward.index[-1]
    exit_price_final = float(forward['spot_c'].iloc[-1])

    def total_size():
        return sum(s for _, s in fills)

    def avg_entry():
        ts = total_size()
        if ts <= 0:
            return entry
        return sum(p * s for p, s in fills) / ts

    for ts_, bar in forward.iterrows():
        if ts_ == now_ts:
            continue
        bar_h = float(bar['spot_h']); bar_l = float(bar['spot_l'])
        cur_mfe_R = (bar_h - entry) / risk
        mfe_R = max(mfe_R, cur_mfe_R)
        max_dd_R = min(max_dd_R, (bar_l - entry) / risk)
        high_water = max(high_water, bar_h)

        # Rung fills
        if not rung1_filled and bar_l <= rung1_trigger:
            fills.append((rung1_trigger, rung1_size))
            rung1_filled = True
        if not rung2_filled and bar_l <= rung2_trigger:
            fills.append((rung2_trigger, rung2_size))
            rung2_filled = True

        # Hard stop
        if bar_l <= active_stop:
            stop_px = active_stop
            ae = avg_entry(); ts_size = total_size()
            r_per_unit = (stop_px - ae) / risk
            cost = cost_per_unit * (stop_px / risk) * ts_size
            realized_R += r_per_unit * ts_size - cost
            outcome_final = ('trail_exit' if trail_armed and active_stop > hard_stop_price
                              else 'hard_stop')
            exit_ts_final = ts_; exit_price_final = stop_px
            fills = []
            break

        # T1 (with A7 25% close + A9 dynamic TP trigger)
        if not t1_done and bar_h >= t1_price:
            t1_done = True
            trail_armed = True
            cur_size = total_size()
            close_size = cur_size * t1_close_pct_of_total
            ae = avg_entry()
            r_per_unit = (t1_price - ae) / risk
            cost = cost_per_unit * (t1_price / risk) * close_size
            realized_R += r_per_unit * close_size - cost
            fills = [(p, s * (1 - t1_close_pct_of_total)) for p, s in fills]
            # A9 dynamic TP
            if cur_mfe_R >= fast_trigger_mfe_R:
                t2_r_used = t2_r_fast

        # T2 (price target uses t2_r_used)
        t2_price = entry + t2_r_used * risk
        if t1_done and not t2_done and bar_h >= t2_price:
            t2_done = True
            cur_size = total_size()
            close_size = cur_size * t2_close_pct_of_remaining
            ae = avg_entry()
            r_per_unit = (t2_price - ae) / risk
            cost = cost_per_unit * (t2_price / risk) * close_size
            realized_R += r_per_unit * close_size - cost
            fills = [(p, s * (1 - t2_close_pct_of_remaining)) for p, s in fills]

        # Trail
        if trail_armed:
            new_trail = high_water * (1 - trail_pct)
            if new_trail > active_stop:
                active_stop = new_trail

        if total_size() <= 1e-9:
            break

    if total_size() > 1e-9:
        ae = avg_entry(); cur_size = total_size()
        r_per_unit = (exit_price_final - ae) / risk
        cost = cost_per_unit * (exit_price_final / risk) * cur_size
        realized_R += r_per_unit * cur_size - cost
        outcome_final = 'tif'

    hold_h = (exit_ts_final - now_ts).total_seconds() / 3600.0
    return {
        'r_net': realized_R,
        'hold_h': hold_h,
        'outcome': outcome_final,
        't1_done': t1_done,
        't2_done': t2_done,
        'rung1_filled': rung1_filled,
        'rung2_filled': rung2_filled,
        'mfe_R': mfe_R,
        'max_dd_R': max_dd_R,
        't2_r_used': t2_r_used,
    }


# === Main ==================================================================

def main():
    print(f'DB: {DB}')
    cache_key = _entry_cache_key()
    print(f'Entry-cache key: {cache_key}')

    print('Loading per-asset 15m frames...')
    asset_frames = {
        'BTC': build_btc_frame(),
        'ETH': build_eth_frame(),
        'OP':  build_op_frame(),
    }
    for n, f in asset_frames.items():
        print(f'  {n}: {len(f):,} bars')

    print('\nLoading cached LONG entries...')
    entries_by_asset = {}
    for asset in ASSETS:
        cached = _load_cached_entries(asset, cache_key)
        if cached is None:
            print(f'  {asset}: NO CACHE'); return
        entries_by_asset[asset] = cached
        print(f'  {asset}: {len(cached)} entries')

    results = {}

    # Baseline
    print('\n=== Baseline v2 long ===')
    base_rows = []
    for asset, e in entries_by_asset.items():
        base_rows.append(replay_exits(e, asset_frames[asset]))
    base_df = pd.concat(base_rows, ignore_index=True)
    base_s = summarize(base_df, label='baseline_v2_long')
    print_summary(base_s)
    results['baseline'] = base_s

    # === A9 dynamic TP (no ladder) ========================================
    print('\n=== A9 dynamic TP (T2 -> 2R if MFE >= 1.5R at T1 fire) ===')
    a9_df = run_replay(entries_by_asset, asset_frames, replay_one_dynamic_tp)
    a9_s = summarize(a9_df, label='A9_dynamic_TP')
    if len(a9_df) > 0:
        a9_s['fast_t2_used_rate'] = round(float((a9_df['t2_r_used'] == 2.0).mean()), 3)
    print_summary(a9_s)
    print(f'  fast-T2 used on {a9_s.get("fast_t2_used_rate", 0):.0%} of trades')
    print(f'  delta vs baseline: {a9_s["mean_R"] - base_s["mean_R"]:+.3f}R')
    results['A9_dynamic_TP'] = a9_s

    # === A10 MTF cell filter ==============================================
    print('\n=== A10 MTF cell filter ===')
    a10_results = {}
    # Per-cell test on the v2 ACCEPT range (-3 to 1)
    for net in (-3, -2, -1, 0, 1):
        filtered = {a: filter_by_mtf_cell(e, mtf_net_values={net})
                     for a, e in entries_by_asset.items()}
        total = sum(len(e) for e in filtered.values())
        if total == 0:
            print(f'  mtf_net={net:+d}: 0 entries')
            continue
        rows = [replay_exits(e, asset_frames[a]) for a, e in filtered.items()]
        df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        s = summarize(df, label=f'A10_mtf_net_{net}')
        print_summary(s)
        a10_results[f'mtf_net_{net}'] = s

    # Capitulation subset
    capit = {a: filter_by_mtf_cell(e, require_capitulation=True)
              for a, e in entries_by_asset.items()}
    total = sum(len(e) for e in capit.values())
    if total > 0:
        rows = [replay_exits(e, asset_frames[a]) for a, e in capit.items()]
        df = pd.concat(rows, ignore_index=True)
        s = summarize(df, label='A10_capitulation_sig')
        print_summary(s)
        a10_results['capitulation'] = s
    else:
        print('  capitulation sig: 0 entries')
    results['A10_mtf_cell_filter'] = a10_results

    # === Composites ======================================================
    print('\n=== Composite C1 = A4 ladder + A7 25% trim ===')
    c1_df = run_replay(entries_by_asset, asset_frames, replay_one_composite,
                        fast_trigger_mfe_R=99,  # disable A9
                        t1_close_pct_of_total=0.25)
    c1_s = summarize(c1_df, label='C1_A4_A7')
    if len(c1_df) > 0:
        c1_s['hard_stop_rate'] = round(float((c1_df['outcome'] == 'hard_stop').mean()), 3)
        c1_s['rung1_fill_rate'] = round(float(c1_df['rung1_filled'].mean()), 3)
    print_summary(c1_s)
    print(f'  hard_stop={c1_s.get("hard_stop_rate", 0):.0%}  r1={c1_s.get("rung1_fill_rate", 0):.0%}')
    print(f'  delta vs baseline: {c1_s["mean_R"] - base_s["mean_R"]:+.3f}R')
    results['C1_A4_A7'] = c1_s

    print('\n=== Composite C2 = A4 + A7 + A9 ===')
    c2_df = run_replay(entries_by_asset, asset_frames, replay_one_composite,
                        t1_close_pct_of_total=0.25,
                        fast_trigger_mfe_R=1.5)
    c2_s = summarize(c2_df, label='C2_A4_A7_A9')
    if len(c2_df) > 0:
        c2_s['hard_stop_rate'] = round(float((c2_df['outcome'] == 'hard_stop').mean()), 3)
        c2_s['fast_t2_used_rate'] = round(float((c2_df['t2_r_used'] == 2.0).mean()), 3)
    print_summary(c2_s)
    print(f'  hard_stop={c2_s.get("hard_stop_rate", 0):.0%}  fast_T2={c2_s.get("fast_t2_used_rate", 0):.0%}')
    print(f'  delta vs baseline: {c2_s["mean_R"] - base_s["mean_R"]:+.3f}R')
    results['C2_A4_A7_A9'] = c2_s

    # C3: best A10 cell + A4 + A7 + A9
    if a10_results:
        best_cell = max(
            ((k, v) for k, v in a10_results.items() if v.get('n', 0) >= 10),
            key=lambda kv: kv[1]['mean_R'], default=(None, None))
        if best_cell[0] is not None:
            cell_label, _ = best_cell
            if cell_label == 'capitulation':
                filtered = {a: filter_by_mtf_cell(e, require_capitulation=True)
                             for a, e in entries_by_asset.items()}
            else:
                net = int(cell_label.replace('mtf_net_', ''))
                filtered = {a: filter_by_mtf_cell(e, mtf_net_values={net})
                             for a, e in entries_by_asset.items()}
            print(f'\n=== Composite C3 = best A10 cell ({cell_label}) + A4 + A7 + A9 ===')
            c3_df = run_replay(filtered, asset_frames, replay_one_composite,
                                t1_close_pct_of_total=0.25,
                                fast_trigger_mfe_R=1.5)
            c3_s = summarize(c3_df, label=f'C3_{cell_label}_A4_A7_A9')
            if len(c3_df) > 0:
                c3_s['hard_stop_rate'] = round(float((c3_df['outcome'] == 'hard_stop').mean()), 3)
            print_summary(c3_s)
            print(f'  delta vs baseline: {c3_s["mean_R"] - base_s["mean_R"]:+.3f}R')
            results['C3_best_cell_A4_A7_A9'] = c3_s
            results['C3_best_cell_label'] = cell_label

    # === Write output ====================================================
    out_path = OUT_DIR / 'A9_A10_composite_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                'EXPERIMENTS, not for ship. A9 tests dynamic TP-tightening '
                'when momentum is fast at T1. A10 tests which MTF-net cells '
                'in v2 ACCEPT range actually carry edge. Composites stack the '
                'wins from A4/A7/A9 (with optional A10 cell filter) to see if '
                'edges compound.'
            ),
            **results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
