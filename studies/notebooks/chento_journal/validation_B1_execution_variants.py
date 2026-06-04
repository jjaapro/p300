"""validation_B1_execution_variants: test whether better execution closes the
chento gap on the B1-aligned trigger subset.

The diagnostic showed:
  - B1 catches 100% of chento BTC timestamps
  - But aligned-subset R = -0.061 vs chento +0.34 mean on same trades
  - 63% of bot-LOSS + chento-WIN: we stop out before his move completes

This script reruns the B1 aligned triggers with progressively better exit
logic. If E4 closes the R gap on the aligned subset, execution was the issue.
If it doesn't, signal-confluence is also missing.

Variants:
  Baseline (B1 default) — 2*ATR stop, 2R target, 24h TIF
  E1  — 4*ATR stop, 2R target, 72h TIF
  E2  — STRUCTURAL stop (24h swing high/low + buffer), 2R target, 72h TIF
  E3  — E2 + bounded ladder add at -0.75R / -1.0R, hard stop -1.5R (A4)
  E4  — E3 + scaled trims (T1 25% @ +1R, T2 50% remaining @ +3R, runner trail 5%)

Each variant runs on:
  - ALIGNED subset (~451 triggers that align with chento)
  - NOISE subset (~7975 triggers) — control to detect overfitting
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

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'
CHENTO_TRADES = ROOT / 'studies' / 'material' / 'chento' / 'scan_aggregated' / 'trades.jsonl'

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, compute_atr, b1_triggers,
)

COST_RT = 0.0018  # 18 bps RT


# === Execution variants — different replay logic per variant ===============

def _structural_stop(df: pd.DataFrame, entry_idx: int, direction: str,
                      lookback_bars: int = 96, buffer_pct: float = 0.001):
    """Return stop price = swing low/high over last `lookback_bars` * (1 ± buffer)."""
    lo = entry_idx - lookback_bars
    if lo < 0:
        lo = 0
    win = df.iloc[lo:entry_idx]
    if win.empty:
        return None
    if direction == 'long':
        sl = float(win['low'].min())
        return sl * (1 - buffer_pct)
    else:
        sh = float(win['high'].max())
        return sh * (1 + buffer_pct)


def replay_variant(triggers: pd.DataFrame, df: pd.DataFrame, *,
                    variant: str,
                    atr_mult_stop: float = 2.0,
                    target_r: float = 2.0,
                    tif_bars: int = 4 * 24,
                    use_structural_stop: bool = False,
                    use_ladder: bool = False,
                    use_trims: bool = False,
                    rung1_r: float = -0.75, rung1_size: float = 0.5,
                    rung2_r: float = -1.0,  rung2_size: float = 0.5,
                    hard_stop_r: float = -1.5,
                    t1_r: float = 1.0, t1_close_pct: float = 0.25,
                    t2_r: float = 3.0, t2_close_pct: float = 0.5,
                    trail_pct: float = 0.05,
                    cost_rt: float = COST_RT,
                    ) -> pd.DataFrame:
    """Replay each trigger with the specified execution logic.

    Returns DataFrame with r_outcome, exit_kind, hold_hours.
    """
    if triggers.empty:
        return triggers
    out = triggers.copy()
    df_idx_map = {ts: i for i, ts in enumerate(df.index)}

    r_outcomes = []
    exit_kinds = []
    hold_hours = []
    rung1_fills = []
    rung2_fills = []

    for _, row in out.iterrows():
        ts = pd.Timestamp(row['ts'])
        entry = float(row['entry'])
        direction = row['direction']
        atr_val = float(row['atr'])
        entry_idx = df_idx_map.get(ts)
        if entry_idx is None:
            entry_idx = df.index.searchsorted(ts, side='left')
        # Determine initial stop
        if use_structural_stop:
            stop_initial = _structural_stop(df, entry_idx, direction)
            if stop_initial is None:
                r_outcomes.append(np.nan); exit_kinds.append('na')
                hold_hours.append(np.nan); rung1_fills.append(False); rung2_fills.append(False)
                continue
        else:
            stop_initial = entry + (atr_val * atr_mult_stop) * (1 if direction == 'short' else -1)
        risk = abs(entry - stop_initial)
        if risk <= 0:
            r_outcomes.append(np.nan); exit_kinds.append('na')
            hold_hours.append(np.nan); rung1_fills.append(False); rung2_fills.append(False)
            continue

        # Reference target (single R-multiple)
        if direction == 'long':
            target_simple = entry + target_r * risk
        else:
            target_simple = entry - target_r * risk

        # Ladder triggers + hard stop (only for E3/E4)
        rung1_filled = False; rung2_filled = False
        if direction == 'long':
            rung1_px = entry + rung1_r * risk  # below entry
            rung2_px = entry + rung2_r * risk
            hard_stop_px = entry + hard_stop_r * risk
            t1_px = entry + t1_r * risk
            t2_px = entry + t2_r * risk
        else:
            rung1_px = entry - rung1_r * risk
            rung2_px = entry - rung2_r * risk
            hard_stop_px = entry - hard_stop_r * risk
            t1_px = entry - t1_r * risk
            t2_px = entry - t2_r * risk

        # State
        fills = [(entry, 1.0)]
        active_stop = hard_stop_px if use_ladder else stop_initial
        realized_R = 0.0
        t1_done = False; t2_done = False; trail_armed = False
        high_water = entry; low_water = entry
        outcome_kind = 'tif'
        exit_ts = None; exit_px = None

        # Walk forward
        start_pos = entry_idx + 1
        end_pos = min(start_pos + tif_bars, len(df))
        for j in range(start_pos, end_pos):
            bar = df.iloc[j]
            bts = df.index[j]
            bar_h = float(bar['high']); bar_l = float(bar['low'])
            high_water = max(high_water, bar_h)
            low_water = min(low_water, bar_l)

            if use_ladder:
                if direction == 'long':
                    # Rung fills (adverse) — process before targets
                    if not rung1_filled and bar_l <= rung1_px:
                        fills.append((rung1_px, rung1_size)); rung1_filled = True
                    if not rung2_filled and bar_l <= rung2_px:
                        fills.append((rung2_px, rung2_size)); rung2_filled = True
                else:
                    if not rung1_filled and bar_h >= rung1_px:
                        fills.append((rung1_px, rung1_size)); rung1_filled = True
                    if not rung2_filled and bar_h >= rung2_px:
                        fills.append((rung2_px, rung2_size)); rung2_filled = True

            # Stop check (always — applies to combined ladder if active_stop = hard_stop_px)
            stop_hit = (bar_l <= active_stop) if direction == 'long' else (bar_h >= active_stop)
            if stop_hit:
                stop_px = active_stop
                if use_ladder:
                    ts_size = sum(s for _, s in fills)
                    ae = sum(p*s for p,s in fills) / max(ts_size, 1e-9)
                    r_per_unit = ((stop_px - ae) if direction=='long' else (ae - stop_px)) / risk
                    cost = cost_rt * (stop_px / risk) * ts_size
                    realized_R += r_per_unit * ts_size - cost
                else:
                    r_per_unit = ((stop_px - entry) if direction=='long' else (entry - stop_px)) / risk
                    cost = cost_rt * (stop_px / risk)
                    realized_R += r_per_unit - cost
                outcome_kind = 'trail_exit' if (trail_armed and active_stop != (hard_stop_px if use_ladder else stop_initial)) else 'stop'
                exit_ts = bts; exit_px = stop_px
                fills = []
                break

            # Targets (favourable)
            if use_trims:
                # T1
                if not t1_done:
                    t1_hit = (bar_h >= t1_px) if direction=='long' else (bar_l <= t1_px)
                    if t1_hit:
                        t1_done = True; trail_armed = True
                        cur_size = sum(s for _,s in fills)
                        close_size = cur_size * t1_close_pct
                        ae = sum(p*s for p,s in fills) / max(cur_size, 1e-9)
                        r_per_unit = ((t1_px - ae) if direction=='long' else (ae - t1_px)) / risk
                        cost = cost_rt * (t1_px / risk) * close_size
                        realized_R += r_per_unit * close_size - cost
                        fills = [(p, s * (1 - t1_close_pct)) for p, s in fills]
                if t1_done and not t2_done:
                    t2_hit = (bar_h >= t2_px) if direction=='long' else (bar_l <= t2_px)
                    if t2_hit:
                        t2_done = True
                        cur_size = sum(s for _,s in fills)
                        close_size = cur_size * t2_close_pct
                        ae = sum(p*s for p,s in fills) / max(cur_size, 1e-9)
                        r_per_unit = ((t2_px - ae) if direction=='long' else (ae - t2_px)) / risk
                        cost = cost_rt * (t2_px / risk) * close_size
                        realized_R += r_per_unit * close_size - cost
                        fills = [(p, s * (1 - t2_close_pct)) for p, s in fills]
                # Trail
                if trail_armed:
                    if direction == 'long':
                        new_trail = high_water * (1 - trail_pct)
                        if new_trail > active_stop:
                            active_stop = new_trail
                    else:
                        new_trail = low_water * (1 + trail_pct)
                        if new_trail < active_stop:
                            active_stop = new_trail
            else:
                # Simple target hit
                target_hit = (bar_h >= target_simple) if direction=='long' else (bar_l <= target_simple)
                if target_hit:
                    if use_ladder:
                        ts_size = sum(s for _,s in fills)
                        ae = sum(p*s for p,s in fills) / max(ts_size, 1e-9)
                        r_per_unit = ((target_simple - ae) if direction=='long' else (ae - target_simple)) / risk
                        cost = cost_rt * (target_simple / risk) * ts_size
                        realized_R += r_per_unit * ts_size - cost
                    else:
                        r_per_unit = ((target_simple - entry) if direction=='long' else (entry - target_simple)) / risk
                        cost = cost_rt * (target_simple / risk)
                        realized_R += r_per_unit - cost
                    outcome_kind = 'target'
                    exit_ts = bts; exit_px = target_simple
                    fills = []
                    break

            # Out of size
            cur_size = sum(s for _,s in fills) if fills else 0
            if cur_size <= 1e-9:
                break

        # TIF close
        if fills and sum(s for _,s in fills) > 1e-9:
            close_px = float(df.iloc[end_pos-1]['close']) if end_pos > 0 else entry
            cur_size = sum(s for _,s in fills)
            ae = sum(p*s for p,s in fills) / max(cur_size, 1e-9)
            r_per_unit = ((close_px - ae) if direction=='long' else (ae - close_px)) / risk
            cost = cost_rt * (close_px / risk) * cur_size
            realized_R += r_per_unit * cur_size - cost
            outcome_kind = 'tif'
            exit_ts = df.index[end_pos-1]
            exit_px = close_px

        r_outcomes.append(realized_R)
        exit_kinds.append(outcome_kind)
        hold_hours.append((exit_ts - ts).total_seconds() / 3600.0 if exit_ts else np.nan)
        rung1_fills.append(rung1_filled)
        rung2_fills.append(rung2_filled)

    out['r_outcome'] = r_outcomes
    out['exit_kind'] = exit_kinds
    out['hold_hours'] = hold_hours
    out['rung1_filled'] = rung1_fills
    out['rung2_filled'] = rung2_fills
    out['variant'] = variant
    return out


def stats(df: pd.DataFrame) -> dict:
    r = df['r_outcome'].dropna()
    if r.empty:
        return {'n': 0}
    return {
        'n': int(len(r)),
        'mean_R': round(float(r.mean()), 3),
        'median_R': round(float(r.median()), 3),
        'wr': round(float((r > 0).mean()), 3),
        'targets': int((df['exit_kind'] == 'target').sum()),
        'stops': int((df['exit_kind'] == 'stop').sum()),
        'trail_exits': int((df['exit_kind'] == 'trail_exit').sum()),
        'tif': int((df['exit_kind'] == 'tif').sum()),
        'rung1_fill_rate': round(float(df['rung1_filled'].mean()), 3),
        'rung2_fill_rate': round(float(df['rung2_filled'].mean()), 3),
        'median_hold_h': round(float(df['hold_hours'].median()), 1),
    }


def main():
    print('Loading BTC 15m + computing B1 signals + generating triggers...')
    df = load_btc_15m()
    df_enr = compute_moneyflow_signal(df)
    trigs = b1_triggers(df_enr, cvd_threshold=0.5, velocity_max=1.0)
    print(f'  {len(trigs)} loose B1 triggers')

    # Tag aligned vs noise
    rows = [json.loads(l) for l in CHENTO_TRADES.read_text(encoding='utf-8').splitlines() if l.strip()]
    chento = pd.DataFrame(rows)
    chento['ts'] = pd.to_datetime(chento['first_ts'], utc=True, errors='coerce')
    chento = chento.dropna(subset=['ts'])
    btc = chento[chento['asset'] == 'BTCUSDT'].copy()
    btc = btc[(btc['ts'] >= trigs['ts'].min()) & (btc['ts'] <= trigs['ts'].max())]

    aligned_mask = np.zeros(len(trigs), dtype=bool)
    for _, c in btc.iterrows():
        same_dir = trigs[trigs['direction'] == c['direction']]
        if same_dir.empty: continue
        delta_h = (same_dir['ts'] - c['ts']).dt.total_seconds() / 3600.0
        hits = same_dir[abs(delta_h) <= 72].index
        aligned_mask[trigs.index.isin(hits)] = True
    trigs['aligned'] = aligned_mask
    aligned = trigs[trigs['aligned']].copy()
    noise = trigs[~trigs['aligned']].copy()
    print(f'  Aligned with chento: {len(aligned)}  | Noise: {len(noise)}')

    # === Variants ========================================================
    variants = {
        'Baseline_2ATR_2R_24h': dict(
            atr_mult_stop=2.0, target_r=2.0, tif_bars=4*24,
            use_structural_stop=False, use_ladder=False, use_trims=False),
        'E1_4ATR_2R_72h': dict(
            atr_mult_stop=4.0, target_r=2.0, tif_bars=4*72,
            use_structural_stop=False, use_ladder=False, use_trims=False),
        'E2_structural_2R_72h': dict(
            atr_mult_stop=2.0, target_r=2.0, tif_bars=4*72,
            use_structural_stop=True, use_ladder=False, use_trims=False),
        'E3_structural_ladder_2R_72h': dict(
            atr_mult_stop=2.0, target_r=2.0, tif_bars=4*72,
            use_structural_stop=True, use_ladder=True, use_trims=False),
        'E4_structural_ladder_trims_72h': dict(
            atr_mult_stop=2.0, target_r=2.0, tif_bars=4*72,
            use_structural_stop=True, use_ladder=True, use_trims=True),
    }

    print('\n=== Variants × subsets ===')
    print(f'{"Variant":<35s} {"subset":<8s} {"n":>5s} {"meanR":>7s} {"medR":>7s} {"WR":>5s} '
           f'{"tgt":>4s} {"stop":>5s} {"trail":>5s} {"tif":>4s} {"r1%":>5s} {"r2%":>5s} {"holdH":>6s}')
    all_results = {}
    for label, params in variants.items():
        for subset_name, sub in (('ALIGNED', aligned), ('NOISE', noise)):
            if sub.empty: continue
            replayed = replay_variant(sub, df_enr, variant=label, **params)
            s = stats(replayed)
            print(f'{label:<35s} {subset_name:<8s} {s["n"]:>5d} {s["mean_R"]:>+7.3f} '
                   f'{s["median_R"]:>+7.3f} {s["wr"]:>5.0%} {s["targets"]:>4d} '
                   f'{s["stops"]:>5d} {s["trail_exits"]:>5d} {s["tif"]:>4d} '
                   f'{s["rung1_fill_rate"]:>5.0%} {s["rung2_fill_rate"]:>5.0%} '
                   f'{s["median_hold_h"]:>6.1f}')
            all_results[f'{label}_{subset_name}'] = s

    out_path = OUT_DIR / 'B1_execution_variants.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'study_question': (
                'Does better execution (wider stops, structural stops, '
                'bounded ladder, scaled trims) close the gap between bot '
                'and chento on the SAME entry timestamps?'
            ),
            'n_aligned_triggers': int(aligned_mask.sum()),
            'n_noise_triggers': int(len(noise)),
            'variants': all_results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
