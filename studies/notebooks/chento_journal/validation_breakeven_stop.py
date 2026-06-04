"""validation_breakeven_stop: how often do losing trades briefly go into
profit before stopping out, and what happens if we move the stop to entry
(BE) when the NEXT bar after entry closes favorably?

This codifies chento's Rule A3 verbatim ("Taking 50% rest is for 90% or BE").
We're testing a strict mechanical form: on the bar immediately following
entry, if close is on the right side of entry, move SL to entry. From then
on, the trade either reaches target, stops at BE (scratch, ~zero R minus
fees), or TIFs out.

Outputs:
  1. Loser MFE distribution: how far in our favor did losers go before
     stopping?
  2. Fraction of losers that touched: BE / +0.25R / +0.5R / +1R / +1.5R
     before stop-out (these are the trades a BE-stop could rescue).
  3. Backtest comparison: baseline atr4_t3R vs BE-rule atr4_t3R, on the
     same Triple composite trigger set.
  4. Variations: BE-on-next-close vs BE-on-N-bar-close vs BE-after-+0.5R-MFE.
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
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers

COST_BP = 18.0      # 18 bp round-trip (same as structural-stop study)
TIF_BARS = 4 * 24   # 24h TIF at 15m


# === Replay with optional BE rule ==========================================

def replay_one(trig, df: pd.DataFrame, *,
                atr_mult: float = 4.0, target_r: float = 3.0,
                tif_bars: int = TIF_BARS,
                be_rule: str = 'none',
                be_param: float = 0.0,
                ) -> dict | None:
    """Replay a single trigger with optional BE-stop rule.

    be_rule:
      'none'           - baseline (no BE)
      'next_close'     - if bar at idx+1 closes favorably, move SL to entry
      'n_bar_close'    - if bar at idx+N (N=be_param) closes favorably, move SL to entry
      'mfe_R'          - if MFE reaches +be_param * R, move SL to entry
    """
    direction = trig['direction']
    ts = trig['ts']
    idx = df.index.searchsorted(ts, side='right') - 1
    if idx < 0 or idx >= len(df) or df.index[idx] != ts:
        idx = df.index.searchsorted(ts, side='right') - 1
        if idx < 0:
            return None
    atr = float(df['atr'].iloc[idx])
    if pd.isna(atr) or atr <= 0:
        return None
    entry = float(df['close'].iloc[idx])
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
    end = min(start + tif_bars, len(df))
    if end <= start:
        return None

    # Track MFE / MAE in R units, and whether BE has been armed
    max_fav_R = 0.0   # max R-distance in our favor
    max_adv_R = 0.0   # max R-distance against (positive = adverse)
    be_armed = False
    exit_ts = None
    outcome = None
    exit_kind = None

    n_bar_close_idx = be_param if be_rule == 'n_bar_close' else 1

    for j in range(start, end):
        bo = float(df['open'].iloc[j])
        bh = float(df['high'].iloc[j])
        bl = float(df['low'].iloc[j])
        bc = float(df['close'].iloc[j])
        bars_since_entry = j - idx   # 1 for first bar after entry

        # Compute intrabar excursion in R
        if direction == 'long':
            fav_r = (bh - entry) / risk
            adv_r = (entry - bl) / risk
        else:
            fav_r = (entry - bl) / risk
            adv_r = (bh - entry) / risk
        max_fav_R = max(max_fav_R, fav_r)
        max_adv_R = max(max_adv_R, adv_r)

        # Step 1: check whether stop/target is hit on this bar
        # Order convention: if stop is between open and the extreme, stop hits.
        # Conservative assumption: when both stop and target could hit, stop hits first.
        if direction == 'long':
            if bl <= stop:
                # Stop hit. If BE-armed, exit at entry (~0R - cost).
                stop_R = (stop - entry) / risk
                outcome = stop_R - cost_R
                exit_ts = df.index[j]
                exit_kind = 'stop_be' if be_armed and abs(stop - entry) < 1e-6 else 'stop'
                break
            if bh >= target:
                outcome = (target - entry) / risk - cost_R
                exit_ts = df.index[j]
                exit_kind = 'target'
                break
        else:
            if bh >= stop:
                stop_R = (entry - stop) / risk
                outcome = stop_R - cost_R
                exit_ts = df.index[j]
                exit_kind = 'stop_be' if be_armed and abs(stop - entry) < 1e-6 else 'stop'
                break
            if bl <= target:
                outcome = (entry - target) / risk - cost_R
                exit_ts = df.index[j]
                exit_kind = 'target'
                break

        # Step 2: apply BE rule at END of this bar (close-based)
        if not be_armed and be_rule != 'none':
            arm = False
            if be_rule == 'next_close' and bars_since_entry >= 1:
                arm = (bc > entry) if direction == 'long' else (bc < entry)
            elif be_rule == 'n_bar_close' and bars_since_entry >= n_bar_close_idx:
                arm = (bc > entry) if direction == 'long' else (bc < entry)
            elif be_rule == 'mfe_R':
                arm = max_fav_R >= be_param
            if arm:
                stop = entry   # move to BE
                be_armed = True

    if outcome is None:
        last_close = float(df['close'].iloc[end - 1])
        if direction == 'long':
            outcome = (last_close - entry) / risk - cost_R
        else:
            outcome = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df.index[end - 1]

    hold_h = (exit_ts - ts).total_seconds() / 3600.0 if exit_ts else 0.0
    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target, 'risk': risk,
        'r_outcome': outcome, 'exit_kind': exit_kind,
        'hold_hours': hold_h,
        'max_fav_R': round(max_fav_R, 3),
        'max_adv_R': round(max_adv_R, 3),
        'be_armed': be_armed,
    }


def replay_all(triggers, df, **kw) -> pd.DataFrame:
    rows = []
    for _, row in triggers.iterrows():
        r = replay_one(row, df, **kw)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


# === Summarize =============================================================

def summarize(t: pd.DataFrame, label: str) -> dict:
    if t.empty:
        return {'label': label, 'n': 0}
    t = t.sort_values('ts').reset_index(drop=True)
    cum = t['r_outcome'].cumsum().values
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    span_y = (t['ts'].max() - t['ts'].min()).total_seconds() / (365.25 * 86400)
    is_set = t[t['ts'] <= pd.Timestamp('2024-12-31 23:59:59', tz='UTC')]
    oos_set = t[t['ts'] > pd.Timestamp('2024-12-31 23:59:59', tz='UTC')]
    return {
        'label': label,
        'n': int(len(t)),
        'per_yr': round(len(t) / max(span_y, 0.1), 1),
        'mean_R': round(float(t['r_outcome'].mean()), 3),
        'median_R': round(float(t['r_outcome'].median()), 3),
        'wr': round(float((t['r_outcome'] > 0).mean()), 3),
        'cum_R': round(float(cum[-1]), 2),
        'max_dd_R': round(float(dd.min()), 2),
        'targets': int((t['exit_kind'] == 'target').sum()),
        'stops': int((t['exit_kind'] == 'stop').sum()),
        'stop_BEs': int((t['exit_kind'] == 'stop_be').sum()),
        'tifs': int((t['exit_kind'] == 'tif').sum()),
        'be_armed_pct': round(float(t['be_armed'].mean()) * 100, 1) if 'be_armed' in t.columns else 0,
        'IS_meanR': round(float(is_set['r_outcome'].mean()) if len(is_set) else 0, 3),
        'IS_n': len(is_set),
        'OOS_meanR': round(float(oos_set['r_outcome'].mean()) if len(oos_set) else 0, 3),
        'OOS_n': len(oos_set),
    }


def main():
    print('Building Triple composite...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    print(f'  Triple triggers: {len(triple)}')

    # === Baseline ===
    print('\n--- Baseline (atr4_t3R, no BE) ---')
    base = replay_all(triple, df_b1, be_rule='none')
    sb = summarize(base, 'baseline')
    print(f'  n={sb["n"]}  meanR={sb["mean_R"]}  WR={sb["wr"]}  '
           f'cumR={sb["cum_R"]}  maxDD={sb["max_dd_R"]}')
    print(f'  targets={sb["targets"]}  stops={sb["stops"]}  tifs={sb["tifs"]}')

    # === MFE distribution for losing trades ===
    losers = base[base['r_outcome'] < 0]
    winners = base[base['r_outcome'] > 0]
    tifs = base[base['exit_kind'] == 'tif']
    print(f'\n--- MFE distribution by outcome ---')
    print(f'  losers (n={len(losers)}):')
    print(f'    median MFE = {losers["max_fav_R"].median():.3f}R')
    print(f'    mean MFE   = {losers["max_fav_R"].mean():.3f}R')
    print(f'    p25 / p75  = {losers["max_fav_R"].quantile(.25):.3f}R / {losers["max_fav_R"].quantile(.75):.3f}R')
    print(f'    fraction that touched +0   = {(losers["max_fav_R"] > 0).mean():.1%}')
    print(f'    fraction that touched +0.1R = {(losers["max_fav_R"] >= 0.1).mean():.1%}')
    print(f'    fraction that touched +0.25R = {(losers["max_fav_R"] >= 0.25).mean():.1%}')
    print(f'    fraction that touched +0.5R = {(losers["max_fav_R"] >= 0.5).mean():.1%}')
    print(f'    fraction that touched +1R   = {(losers["max_fav_R"] >= 1.0).mean():.1%}')
    print(f'    fraction that touched +1.5R = {(losers["max_fav_R"] >= 1.5).mean():.1%}')

    print(f'\n  winners (n={len(winners)}):')
    print(f'    median MFE = {winners["max_fav_R"].median():.3f}R')
    print(f'    mean MFE   = {winners["max_fav_R"].mean():.3f}R')
    print(f'    fraction touched +1R = {(winners["max_fav_R"] >= 1.0).mean():.1%}')

    # === Test BE rules ===
    print('\n--- BE-rule variants on Triple + atr4_t3R ---')
    print(f'{"label":<45s} {"n":>4s} {"meanR":>7s} {"WR":>5s} {"cumR":>7s} {"maxDD":>7s} {"tgt":>4s} {"stp":>4s} {"BE":>4s} {"tif":>4s} {"BE%":>5s} {"IS":>9s} {"OOS":>9s}')
    print(f'{"baseline":<45s} {sb["n"]:>4d} {sb["mean_R"]:>+7.3f} '
           f'{sb["wr"]:>5.2%} {sb["cum_R"]:>+7.1f} {sb["max_dd_R"]:>+7.1f} '
           f'{sb["targets"]:>4d} {sb["stops"]:>4d} {sb.get("stop_BEs",0):>4d} '
           f'{sb["tifs"]:>4d} {sb.get("be_armed_pct",0):>5.1f} '
           f'{sb["IS_meanR"]:>+9.3f} {sb["OOS_meanR"]:>+9.3f}')

    variants = []
    # next-bar close BE
    variants.append(('BE_next_close', {'be_rule': 'next_close'}))
    # N-bar close BE
    for n in (2, 3, 4, 6, 8, 12):
        variants.append((f'BE_after_{n}_closes', {'be_rule': 'n_bar_close', 'be_param': n}))
    # MFE-based BE
    for m in (0.25, 0.5, 0.75, 1.0):
        variants.append((f'BE_after_MFE_{m}R', {'be_rule': 'mfe_R', 'be_param': m}))

    all_results = {'baseline': sb}
    for label, kw in variants:
        r = replay_all(triple, df_b1, **kw)
        s = summarize(r, label)
        all_results[label] = s
        print(f'{label:<45s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} '
               f'{s["wr"]:>5.2%} {s["cum_R"]:>+7.1f} {s["max_dd_R"]:>+7.1f} '
               f'{s["targets"]:>4d} {s["stops"]:>4d} {s.get("stop_BEs",0):>4d} '
               f'{s["tifs"]:>4d} {s.get("be_armed_pct",0):>5.1f} '
               f'{s["IS_meanR"]:>+9.3f} {s["OOS_meanR"]:>+9.3f}')

    # === Stacked: BE rule + no-tilt filter ===
    print('\n--- BE rule combined with no-tilt filter (consec_losses_before == 0) ---')
    # Apply no-tilt filter to baseline trades, then to BE variant trades
    for label, kw in [
        ('baseline_NO_TILT', {'be_rule': 'none'}),
        ('BE_next_close_NO_TILT', {'be_rule': 'next_close'}),
        ('BE_after_MFE_0.5R_NO_TILT', {'be_rule': 'mfe_R', 'be_param': 0.5}),
        ('BE_after_2_closes_NO_TILT', {'be_rule': 'n_bar_close', 'be_param': 2}),
    ]:
        r = replay_all(triple, df_b1, **kw)
        # Apply no-tilt filter
        r = r.sort_values('ts').reset_index(drop=True)
        losses_before = []
        cur = 0
        for ro in r['r_outcome'].shift(1).fillna(0):
            if ro < 0:
                cur += 1
            else:
                cur = 0
            losses_before.append(cur)
        r['consec_losses_before'] = losses_before
        kept = r[r['consec_losses_before'] == 0].copy()
        s = summarize(kept, label)
        all_results[label] = s
        print(f'{label:<45s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} '
               f'{s["wr"]:>5.2%} {s["cum_R"]:>+7.1f} {s["max_dd_R"]:>+7.1f} '
               f'{s["targets"]:>4d} {s["stops"]:>4d} {s.get("stop_BEs",0):>4d} '
               f'{s["tifs"]:>4d} {s.get("be_armed_pct",0):>5.1f} '
               f'{s["IS_meanR"]:>+9.3f} {s["OOS_meanR"]:>+9.3f}')

    out_path = OUT_DIR / 'breakeven_stop_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Tests BE-on-favorable-next-close rule on Triple + atr4_t3R, '
                      'plus MFE distribution diagnostic and combinations with '
                      'no-tilt filter.'),
            'baseline_loser_MFE_summary': {
                'n': int(len(losers)),
                'median_MFE_R': round(float(losers['max_fav_R'].median()), 3),
                'mean_MFE_R': round(float(losers['max_fav_R'].mean()), 3),
                'pct_touched_BE': round(float((losers['max_fav_R'] > 0).mean()) * 100, 1),
                'pct_touched_0.25R': round(float((losers['max_fav_R'] >= 0.25).mean()) * 100, 1),
                'pct_touched_0.5R': round(float((losers['max_fav_R'] >= 0.5).mean()) * 100, 1),
                'pct_touched_1.0R': round(float((losers['max_fav_R'] >= 1.0).mean()) * 100, 1),
                'pct_touched_1.5R': round(float((losers['max_fav_R'] >= 1.5).mean()) * 100, 1),
            },
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
