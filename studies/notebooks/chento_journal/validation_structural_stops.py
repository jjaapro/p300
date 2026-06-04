"""validation_structural_stops: replace ATR-based stops with structural stops
on the triple composite and measure per-trade R uplift.

Current best composite (Triple = B1 ∩ B5 ∩ B7-align) on BTC:
  Stop = 2 * ATR(14, 15m)
  Target = 2R fixed
  +0.176R per trade (after cost), IS=+0.223 / OOS=+0.055

Hypothesis: ATR stops are too noisy at 15m for swing setups. Structural stops
(prior swing low/high + buffer) give the trade more room and align with what
chento documents in his journal ("invalidation = specific price, not %").

Variants tested (all on the SAME trigger set: Triple composite):
  S1 Baseline ATR2    — current stop (control)
  S2 ATR4             — 2x wider ATR (does extra room help?)
  S3 ATR6             — 3x wider
  S4 Swing-24h        — entry vs prior 24h swing low/high
  S5 Swing-12h        — prior 12h swing
  S6 Swing-6h         — prior 6h swing
  S7 Swing-3h         — prior 3h swing

For each: also vary target multiplier {2R, 3R, fixed-trail}.

Output: per-variant table with n, mean R, WR, IS/OOS, hold time, max risk.
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
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    compute_atr,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers


# --- Stop calculators ------------------------------------------------------

def stop_atr(df: pd.DataFrame, idx: int, direction: str, *,
              atr_mult: float = 2.0, atr_period: int = 14) -> tuple[float, float]:
    """Returns (stop_price, risk_distance)."""
    # df should have 'atr' column already computed
    atr = float(df['atr'].iloc[idx])
    entry = float(df['close'].iloc[idx])
    risk = atr * atr_mult
    if direction == 'long':
        return entry - risk, risk
    return entry + risk, risk


def stop_swing(df: pd.DataFrame, idx: int, direction: str, *,
                lookback_bars: int = 4 * 24,
                buffer_atr_mult: float = 0.5,
                buffer_pct: float | None = None,
                atr_period: int = 14) -> tuple[float, float]:
    """Stop placed just beyond the most-recent N-bar swing high/low.

    Buffer: small extra room so noise doesn't trigger stop. Either ATR-mult
    (default 0.5 ATR) or pct (e.g., 0.002 = 0.2%).
    """
    lo = max(0, idx - lookback_bars)
    win = df.iloc[lo:idx + 1]  # include current bar
    entry = float(df['close'].iloc[idx])
    atr = float(df['atr'].iloc[idx])
    buf = (atr * buffer_atr_mult) if buffer_pct is None else (entry * buffer_pct)
    if direction == 'long':
        sw_low = float(win['low'].min())
        stop = sw_low - buf
        risk = max(entry - stop, atr * 0.5)  # floor at 0.5 ATR to avoid 0-risk
        return stop, risk
    else:
        sw_high = float(win['high'].max())
        stop = sw_high + buf
        risk = max(stop - entry, atr * 0.5)
        return stop, risk


# --- Forward replay with variable stop -------------------------------------

COST_BP = 18.0  # round-trip


def replay_one(row: pd.Series, df: pd.DataFrame, *,
                stop_fn, stop_kwargs: dict,
                target_r: float = 2.0,
                tif_bars: int = 4 * 24,
                ) -> dict | None:
    """Replay a single trigger with a custom stop placement function."""
    ts = pd.Timestamp(row['ts'])
    direction = row['direction']
    entry = float(row['entry'])

    idx_arr = df.index.searchsorted(ts, side='left')
    if idx_arr == len(df) or df.index[idx_arr] != ts:
        # fallback: use the closest earlier bar
        idx_arr = df.index.searchsorted(ts, side='right') - 1
        if idx_arr < 0:
            return None
    # Compute structural stop AT TRIGGER (not look-ahead — uses prior bars)
    stop, risk = stop_fn(df, idx_arr, direction, **stop_kwargs)
    if risk <= 0:
        return None
    if direction == 'long':
        target = entry + risk * target_r
    else:
        target = entry - risk * target_r

    # Cost in R units: 18bp on notional / stop_distance_pct
    cost_R = (COST_BP / 10000.0) * (entry / risk)

    # Walk forward
    start = idx_arr + 1
    end = min(start + tif_bars, len(df))
    fwd = df.iloc[start:end]
    outcome = None
    exit_ts = None
    if direction == 'long':
        for j in range(start, end):
            bh = float(df['high'].iloc[j]); bl = float(df['low'].iloc[j])
            if bl <= stop:
                outcome = -1.0 - cost_R; exit_ts = df.index[j]; exit_kind = 'stop'; break
            if bh >= target:
                outcome = (target - entry) / risk - cost_R; exit_ts = df.index[j]; exit_kind = 'target'; break
    else:
        for j in range(start, end):
            bh = float(df['high'].iloc[j]); bl = float(df['low'].iloc[j])
            if bh >= stop:
                outcome = -1.0 - cost_R; exit_ts = df.index[j]; exit_kind = 'stop'; break
            if bl <= target:
                outcome = (entry - target) / risk - cost_R; exit_ts = df.index[j]; exit_kind = 'target'; break
    if outcome is None:
        # TIF close at last bar
        last_close = float(df['close'].iloc[end - 1]) if end > 0 else entry
        if direction == 'long':
            outcome = (last_close - entry) / risk - cost_R
        else:
            outcome = (entry - last_close) / risk - cost_R
        exit_kind = 'tif'
        exit_ts = df.index[end - 1] if end > 0 else ts

    hold_h = (exit_ts - ts).total_seconds() / 3600.0 if exit_ts else 0.0
    return {
        'ts': ts, 'direction': direction, 'entry': entry,
        'stop': stop, 'target': target, 'risk': risk,
        'r_outcome': outcome, 'exit_kind': exit_kind,
        'hold_hours': hold_h,
    }


def replay_all(triggers: pd.DataFrame, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
    rows = []
    for _, row in triggers.iterrows():
        r = replay_one(row, df, **kwargs)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


def stats(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return {'label': label, 'n': 0}
    r = df['r_outcome']
    span_y = (df['ts'].max() - df['ts'].min()).total_seconds() / (365.25 * 86400)
    return {
        'label': label,
        'n': int(len(df)),
        'span_years': round(span_y, 2),
        'trades_per_year': round(len(df) / max(span_y, 0.1), 1),
        'mean_R': round(float(r.mean()), 3),
        'median_R': round(float(r.median()), 3),
        'win_rate': round(float((r > 0).mean()), 3),
        'targets': int((df['exit_kind'] == 'target').sum()),
        'stops': int((df['exit_kind'] == 'stop').sum()),
        'tif': int((df['exit_kind'] == 'tif').sum()),
        'median_hold_h': round(float(df['hold_hours'].median()), 1),
        'mean_risk_dollars_per_btc': round(float(df['risk'].mean()), 1),
    }


# --- Main ------------------------------------------------------------------

def main():
    print('Loading BTC 15m + generating Triple composite triggers...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  Triple triggers: {len(triple)}')

    # Pre-compute ATR on df for both stop functions
    df = df.copy()
    df['atr'] = compute_atr(df, period=14)

    # Stop variants
    variants = [
        # (label, stop_fn, stop_kwargs, target_r)
        ('atr2_t2R',          stop_atr,   {'atr_mult': 2.0},                    2.0),
        ('atr2_t3R',          stop_atr,   {'atr_mult': 2.0},                    3.0),
        ('atr4_t2R',          stop_atr,   {'atr_mult': 4.0},                    2.0),
        ('atr4_t3R',          stop_atr,   {'atr_mult': 4.0},                    3.0),
        ('atr6_t2R',          stop_atr,   {'atr_mult': 6.0},                    2.0),
        ('swing3h_buf0.5atr_t2R',  stop_swing, {'lookback_bars': 4*3, 'buffer_atr_mult': 0.5}, 2.0),
        ('swing3h_buf0.5atr_t3R',  stop_swing, {'lookback_bars': 4*3, 'buffer_atr_mult': 0.5}, 3.0),
        ('swing6h_buf0.5atr_t2R',  stop_swing, {'lookback_bars': 4*6, 'buffer_atr_mult': 0.5}, 2.0),
        ('swing6h_buf0.5atr_t3R',  stop_swing, {'lookback_bars': 4*6, 'buffer_atr_mult': 0.5}, 3.0),
        ('swing12h_buf0.5atr_t2R', stop_swing, {'lookback_bars': 4*12,'buffer_atr_mult': 0.5}, 2.0),
        ('swing12h_buf0.5atr_t3R', stop_swing, {'lookback_bars': 4*12,'buffer_atr_mult': 0.5}, 3.0),
        ('swing24h_buf0.5atr_t2R', stop_swing, {'lookback_bars': 4*24,'buffer_atr_mult': 0.5}, 2.0),
        ('swing24h_buf0.5atr_t3R', stop_swing, {'lookback_bars': 4*24,'buffer_atr_mult': 0.5}, 3.0),
        ('swing24h_buf0.2pct_t2R', stop_swing, {'lookback_bars': 4*24,'buffer_pct': 0.002}, 2.0),
        ('swing24h_buf1atr_t2R',   stop_swing, {'lookback_bars': 4*24,'buffer_atr_mult': 1.0}, 2.0),
    ]

    dump = {}
    print(f'\n{"variant":<32s} {"n":>4s} {"meanR":>7s} {"medianR":>8s} {"WR":>4s} {"tgt":>4s} {"stop":>4s} {"tif":>4s} {"holdH":>6s} {"IS":>9s} {"OOS":>9s}')
    is_end = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')
    for label, stop_fn, stop_kwargs, target_r in variants:
        rep = replay_all(triple, df, stop_fn=stop_fn, stop_kwargs=stop_kwargs,
                          target_r=target_r)
        s = stats(rep, label)
        # IS/OOS
        if not rep.empty:
            is_set = rep[rep['ts'] <= is_end]
            oos_set = rep[rep['ts'] > is_end]
            is_r = float(is_set['r_outcome'].mean()) if len(is_set) else 0
            oos_r = float(oos_set['r_outcome'].mean()) if len(oos_set) else 0
            s['is_meanR'] = round(is_r, 3); s['is_n'] = len(is_set)
            s['oos_meanR'] = round(oos_r, 3); s['oos_n'] = len(oos_set)
            is_str = f'{is_r:+.3f}({len(is_set)})'
            oos_str = f'{oos_r:+.3f}({len(oos_set)})'
        else:
            is_str = '-'; oos_str = '-'
        print(f'{label:<32s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} {s["median_R"]:>+8.3f} '
               f'{s["win_rate"]:>4.0%} {s["targets"]:>4d} {s["stops"]:>4d} {s["tif"]:>4d} '
               f'{s["median_hold_h"]:>6.1f} {is_str:>10s} {oos_str:>10s}')
        dump[label] = s

    # Save
    out_path = OUT_DIR / 'structural_stops_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': 'Stop-placement sweep on Triple composite (B1∩B5∩B7-align) triggers',
            'baseline_atr2_t2R_R': dump.get('atr2_t2R', {}).get('mean_R'),
            'variants': dump,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')

    # Best
    candidates = [(k, v) for k, v in dump.items() if v.get('n', 0) >= 100]
    candidates.sort(key=lambda kv: kv[1].get('oos_meanR', -99), reverse=True)
    print('\n=== Top 5 by OOS meanR (n>=100) ===')
    for k, v in candidates[:5]:
        print(f'  {k:<32s}  meanR={v["mean_R"]:+.3f}  IS={v["is_meanR"]:+.3f}  OOS={v["oos_meanR"]:+.3f}')


if __name__ == '__main__':
    main()
