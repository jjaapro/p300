"""validation_A4: BOUNDED 2-rung ladder-add experiment for chento sleeve.

EXPERIMENT — NOT FOR SHIP. Studies whether adding 0.5x size at -0.75R and
another 0.5x size at -1.0R (so total = 2.0x original notional) with a hard
combined-stop at -1.5R below the *original* entry produces better expectancy
than the v2 baseline (single 1.0x position, stop at -1.0R).

Risk arithmetic (important):
  - Original (v2): 1.0 unit, stop at -1.0R, max loss = 1.0R
  - A4 ladder:    up to 2.0 units (1.0 original + 0.5 @ -0.75R + 0.5 @ -1.0R)
                  avg_entry = entry - 0.4375R (when both rungs fill)
                  hard stop at entry - 1.5R
                  distance avg_entry -> stop = 1.0625R per unit
                  max loss when both rungs filled = 2 * 1.0625R = 2.125R
  - This is NOT same-risk — it accepts 2.1x max loss for chance of better
    avg-entry + higher hit rate on T1/T2. The experiment tests whether the
    WR + expectancy uplift justifies the loss-multiplier.

This is the BOUNDED version. We explicitly avoid the unbounded "double the
position to scratch the loss" pattern observed in chento's live trading on
2026-05-23 (see project_chento_size_doubling_observation memory).

Output: studies/material/chento/validation/A4_results.json
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
    ASSETS, RISK_PER_TRADE_NAV,
    build_btc_frame, build_eth_frame, build_op_frame,
    replay_exits, summarize, print_summary,
    _entry_cache_key, _load_cached_entries,
)
from strategies.sleeves.chento_limit_bid import config as cli_cfg
from strategies.sleeves.chento_limit_bid import math as cli_math


# === Bounded ladder replay (LONG side) =====================================

def replay_one_ladder_long(entry_row: dict, f: pd.DataFrame, *,
                            rung1_r: float = -0.75,
                            rung1_size: float = 0.5,
                            rung2_r: float = -1.0,
                            rung2_size: float = 0.5,
                            hard_stop_r: float = -1.5,
                            t1_r: float = 1.0,
                            t1_close_pct_of_total: float = 0.333,
                            t2_r: float = 3.0,
                            t2_close_pct_of_remaining: float = 0.5,
                            trail_pct: float = 0.05,
                            tif_days: float = 21,
                            cost_per_unit: float = 0.0018):
    """Replay long entry with bounded 2-rung ladder.

    State machine:
      - Always have unit 1 (1.0 size) at original entry
      - If price drops to entry + rung1_r * risk (e.g. -0.75R), add unit 2 (0.5 size)
      - If price drops to entry + rung2_r * risk (e.g. -1.0R), add unit 3 (0.5 size)
      - Hard stop on combined position at entry + hard_stop_r * risk
      - T1/T2 measured against ORIGINAL entry (not avg) so target prices stay constant
      - Trail watermark = best (highest) price seen since T1; closes if price drops trail_pct below

    R-accounting: 1 unit of size = 1R risk against ORIGINAL stop. PnL on partials uses
    the avg_entry at time of close, weighted by size closed.
    """
    now_ts = entry_row['now_ts']
    entry = float(entry_row['entry'])
    stop_initial = float(entry_row['stop'])
    risk = entry - stop_initial
    if risk <= 0:
        return None

    # Target prices (vs original entry)
    t1_price = entry + t1_r * risk
    t2_price = entry + t2_r * risk
    rung1_trigger = entry + rung1_r * risk
    rung2_trigger = entry + rung2_r * risk
    hard_stop_price = entry + hard_stop_r * risk

    # Position state
    fills = [(entry, 1.0)]  # list of (price, size) for active position
    rung1_filled = False
    rung2_filled = False

    tif_end = now_ts + pd.Timedelta(days=tif_days)
    forward = f.loc[now_ts:tif_end]

    realized_R = 0.0
    mfe_R = 0.0
    max_dd_R = 0.0
    t1_done = False
    t2_done = False
    trail_armed = False
    high_water = entry
    active_stop = hard_stop_price  # combined stop is the hard stop
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
        mfe_R = max(mfe_R, (bar_h - entry) / risk)
        max_dd_R = min(max_dd_R, (bar_l - entry) / risk)
        high_water = max(high_water, bar_h)

        # Process events in price-path order. For a long, the order within a bar
        # is: open -> low -> high -> close (typical). For safety we apply adverse
        # events (rung-fills + hard_stop) on low, then favourable (T1/T2) on high.

        # 1) Rung fills (adverse direction)
        if not rung1_filled and bar_l <= rung1_trigger:
            fills.append((rung1_trigger, rung1_size))
            rung1_filled = True
        if not rung2_filled and bar_l <= rung2_trigger:
            fills.append((rung2_trigger, rung2_size))
            rung2_filled = True

        # 2) Hard stop on combined position (adverse)
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

        # 3) T1 (favourable)
        if not t1_done and bar_h >= t1_price:
            t1_done = True
            trail_armed = True
            # Close t1_close_pct of TOTAL CURRENT size
            cur_size = total_size()
            close_size = cur_size * t1_close_pct_of_total
            ae = avg_entry()
            r_per_unit = (t1_price - ae) / risk
            cost = cost_per_unit * (t1_price / risk) * close_size
            realized_R += r_per_unit * close_size - cost
            # Reduce fills proportionally
            fills = [(p, s * (1 - t1_close_pct_of_total)) for p, s in fills]

        # 4) T2 (favourable)
        if t1_done and not t2_done and bar_h >= t2_price:
            t2_done = True
            cur_size = total_size()
            close_size = cur_size * t2_close_pct_of_remaining
            ae = avg_entry()
            r_per_unit = (t2_price - ae) / risk
            cost = cost_per_unit * (t2_price / risk) * close_size
            realized_R += r_per_unit * close_size - cost
            fills = [(p, s * (1 - t2_close_pct_of_remaining)) for p, s in fills]

        # 5) Trail stop arm after T1: active_stop = max(active_stop, high_water * (1-trail))
        if trail_armed:
            new_trail = high_water * (1 - trail_pct)
            if new_trail > active_stop:
                active_stop = new_trail

        if total_size() <= 1e-9:
            break

    # Force TIF close of remaining
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
    }


def replay_exits_ladder(entries: pd.DataFrame, f: pd.DataFrame, **kwargs):
    cost_per_unit = (cli_cfg.COST_BP_RT + cli_cfg.SLIPPAGE_BP_RT) / 10000.0
    rows = []
    for _, row in entries.iterrows():
        result = replay_one_ladder_long(row.to_dict(), f,
                                          cost_per_unit=cost_per_unit, **kwargs)
        if result is None:
            continue
        rows.append({**row.to_dict(), **result})
    return pd.DataFrame(rows)


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
            print(f'  {asset}: NO CACHE — run validation_A first to seed.')
            return
        entries_by_asset[asset] = cached
        print(f'  {asset}: {len(cached)} entries')

    # === Baseline (v2 long, no ladder) — for comparison ===================
    print('\n=== Baseline v2 long (no ladder) ===')
    base_rows = []
    for asset, e in entries_by_asset.items():
        base_rows.append(replay_exits(e, asset_frames[asset]))
    base_df = pd.concat(base_rows, ignore_index=True) if base_rows else pd.DataFrame()
    base_s = summarize(base_df, label='baseline_v2_long')
    print_summary(base_s)

    # === A4 variants =====================================================
    a4_variants = {
        'A4_rung1_-0.75R_rung2_-1.0R_stop_-1.5R': {
            'rung1_r': -0.75, 'rung2_r': -1.0, 'hard_stop_r': -1.5,
        },
        'A4_rung1_-0.5R_rung2_-0.75R_stop_-1.25R_tighter': {
            'rung1_r': -0.5, 'rung2_r': -0.75, 'hard_stop_r': -1.25,
        },
        'A4_rung1_-1.0R_rung2_-1.5R_stop_-2.0R_wider': {
            'rung1_r': -1.0, 'rung2_r': -1.5, 'hard_stop_r': -2.0,
        },
    }

    all_results = {'baseline_long': base_s, 'variants': {}}
    for label, params in a4_variants.items():
        print(f'\n=== {label} ===')
        rows = []
        for asset, e in entries_by_asset.items():
            rows.append(replay_exits_ladder(e, asset_frames[asset], **params))
        df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
        s = summarize(df, label=label)

        # Extra stats only meaningful for ladder variants
        if len(df) > 0:
            rung1_rate = float(df['rung1_filled'].mean())
            rung2_rate = float(df['rung2_filled'].mean())
            both_rate = float((df['rung1_filled'] & df['rung2_filled']).mean())
            stop_rate = float((df['outcome'] == 'hard_stop').mean())
            s['rung1_fill_rate'] = round(rung1_rate, 3)
            s['rung2_fill_rate'] = round(rung2_rate, 3)
            s['both_rungs_rate'] = round(both_rate, 3)
            s['hard_stop_rate'] = round(stop_rate, 3)

        print_summary(s)
        delta = s['mean_R'] - base_s['mean_R']
        print(f'  rung-fill rates: r1={s.get("rung1_fill_rate", 0):.0%}  '
               f'r2={s.get("rung2_fill_rate", 0):.0%}  '
               f'both={s.get("both_rungs_rate", 0):.0%}  '
               f'hard_stop={s.get("hard_stop_rate", 0):.0%}')
        print(f'  delta vs baseline: {delta:+.3f}R per trade  '
               f'({"BETTER" if delta > 0 else "WORSE"})')
        all_results['variants'][label] = {**s, 'params': params,
                                           'delta_R_vs_baseline': round(delta, 3)}

    out_path = OUT_DIR / 'A4_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_note': (
                'EXPERIMENT, not for ship. Bounded ladder-add tests whether '
                'adding 0.5x at one rung + 0.5x at a second rung with a hard '
                'combined-stop improves expectancy vs the single-position v2 '
                'baseline. Accepts up to ~2.1x max loss on full-ladder fills. '
                'Avoids the unbounded "double to scratch" pattern observed live '
                'on 2026-05-23 — see project_chento_size_doubling_observation '
                'memory.'
            ),
            **all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
