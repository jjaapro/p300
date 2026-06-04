"""validation_B13: hedge-mode-as-range-profiting test.

Chento context (8+ documented instances): inside a confirmed range bracket,
he runs simultaneous long and short legs. Long target = range high, short
target = range low. Both legs scale. He profits from oscillation regardless
of direction.

Verbatim: hedge-mode-as-range-profiting is an articulated, named strategy
(msg 1503782683090878644).

Test architecture (simplified):
  - Detect active range (window 3d, max 6% range) at each 15m bar
  - When fresh range first appears: open hedge (1 unit long + 1 unit short)
  - Long target: range_high * (1 - 0.5*tp_pullback)  (chento takes early)
  - Short target: range_low * (1 + 0.5*tp_pullback)
  - Hard stop on each leg: range_high*(1+brk) for long, range_low*(1-brk) for short
    (i.e., stop only when range breaks)
  - TIF: 7 days max
  - Cost applied per leg

The hedge "wins" if BOTH legs hit their TPs at any point during the range
oscillation. The hedge "fails" if one leg's stop hits (range breaks).
Partial wins are common (one leg hits TP, the other stops or TIFs).

Output: per-hedge net R across both legs + win rate of double-TP scenario.
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

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import load_btc_15m
from studies.lib.range_detector import detect_active_range


COST_RT = 0.0018


def find_fresh_ranges(df: pd.DataFrame, *,
                       cooldown_bars: int = 4 * 24 * 2,  # 2d cooldown
                       range_window_bars: int = 4 * 24 * 3,
                       max_range_pct: float = 0.06,
                       ) -> list[dict]:
    """Walk forward; record each NEW range (not continuation of prior)."""
    hedges = []
    last_open_idx = -10**9
    last_range_low = None
    last_range_high = None
    for i in range(range_window_bars, len(df)):
        if i - last_open_idx < cooldown_bars:
            continue
        ts = df.index[i]
        r = detect_active_range(df, ts, window_bars=range_window_bars,
                                  max_range_pct=max_range_pct)
        if r is None:
            continue
        # Skip if it's basically the same range as last opened
        if last_range_low is not None:
            same_high = abs(r['range_high'] - last_range_high) / last_range_high < 0.01
            same_low = abs(r['range_low'] - last_range_low) / last_range_low < 0.01
            if same_high and same_low:
                continue
        hedges.append({
            'ts_open': ts,
            'range_low': r['range_low'],
            'range_high': r['range_high'],
            'range_pct': r['range_pct'],
            'duration_bars_at_open': r['duration_bars'],
            'open_idx': i,
        })
        last_open_idx = i
        last_range_low = r['range_low']
        last_range_high = r['range_high']
    return hedges


def replay_hedge(hedge: dict, df: pd.DataFrame, *,
                  tp_pullback: float = 0.10,  # take TP at 10% pull-in from edge
                  break_buffer: float = 0.005,  # 0.5% beyond range = stop
                  tif_bars: int = 4 * 24 * 7,
                  ) -> dict:
    """Replay a single hedge. Open at hedge['ts_open'] with both legs.

    Long leg:
      entry = mid (range midpoint) OR ts_open close
      tp = range_high * (1 - tp_pullback*range_pct)
      stop = range_high * (1 + break_buffer)  (long-stop ABOVE range)

    Wait — for a LONG, stop should be below entry. Let me reconsider.

    Hedge interpretation: long leg = bet that price stays in or goes up to range
    high. So entry is somewhere in the range, target is near range high, stop is
    below range low (range break to the downside).

    For symmetry:
      long  leg: entry mid, target range_high*(1-tp_pullback*rp), stop range_low*(1-bb)
      short leg: entry mid, target range_low*(1+tp_pullback*rp), stop range_high*(1+bb)

    Both legs have stops at the opposite range break (where the range itself fails).
    """
    open_idx = hedge['open_idx']
    rh = hedge['range_high']
    rl = hedge['range_low']
    rp = hedge['range_pct']
    entry_mid = (rh + rl) / 2

    long_target = rh * (1 - tp_pullback * rp)
    long_stop = rl * (1 - break_buffer)
    long_risk = entry_mid - long_stop
    long_r_target = (long_target - entry_mid) / long_risk if long_risk > 0 else 0

    short_target = rl * (1 + tp_pullback * rp)
    short_stop = rh * (1 + break_buffer)
    short_risk = short_stop - entry_mid
    short_r_target = (entry_mid - short_target) / short_risk if short_risk > 0 else 0

    end_idx = min(open_idx + 1 + tif_bars, len(df))

    long_state = {'open': True, 'exit_kind': None, 'r': None, 'exit_idx': None}
    short_state = {'open': True, 'exit_kind': None, 'r': None, 'exit_idx': None}

    cost_long = COST_RT * (entry_mid / long_risk) if long_risk > 0 else 0
    cost_short = COST_RT * (entry_mid / short_risk) if short_risk > 0 else 0

    for j in range(open_idx + 1, end_idx):
        bar = df.iloc[j]
        bh = float(bar['high']); bl = float(bar['low'])

        # Long leg
        if long_state['open']:
            if bl <= long_stop:
                long_state.update({'open': False, 'exit_kind': 'stop',
                                    'r': -1.0 - cost_long, 'exit_idx': j})
            elif bh >= long_target:
                long_state.update({'open': False, 'exit_kind': 'target',
                                    'r': long_r_target - cost_long, 'exit_idx': j})

        # Short leg
        if short_state['open']:
            if bh >= short_stop:
                short_state.update({'open': False, 'exit_kind': 'stop',
                                     'r': -1.0 - cost_short, 'exit_idx': j})
            elif bl <= short_target:
                short_state.update({'open': False, 'exit_kind': 'target',
                                     'r': short_r_target - cost_short, 'exit_idx': j})

        if not long_state['open'] and not short_state['open']:
            break

    # Force-close any open leg at end of TIF
    end_close = float(df['close'].iloc[end_idx - 1])
    if long_state['open']:
        r_unrealized = (end_close - entry_mid) / long_risk if long_risk > 0 else 0
        long_state.update({'open': False, 'exit_kind': 'tif',
                            'r': r_unrealized - cost_long, 'exit_idx': end_idx - 1})
    if short_state['open']:
        r_unrealized = (entry_mid - end_close) / short_risk if short_risk > 0 else 0
        short_state.update({'open': False, 'exit_kind': 'tif',
                             'r': r_unrealized - cost_short, 'exit_idx': end_idx - 1})

    total_r = long_state['r'] + short_state['r']
    return {
        'ts_open': hedge['ts_open'],
        'range_pct': rp,
        'duration_at_open': hedge['duration_bars_at_open'],
        'long_exit': long_state['exit_kind'],
        'long_r': long_state['r'],
        'short_exit': short_state['exit_kind'],
        'short_r': short_state['r'],
        'total_r': total_r,
        'both_targets': (long_state['exit_kind'] == 'target'
                          and short_state['exit_kind'] == 'target'),
    }


def main():
    print('Loading BTC 15m...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars')

    print('\nFinding fresh ranges (3d window, 6% max)...')
    hedges = find_fresh_ranges(df)
    print(f'  {len(hedges)} fresh ranges detected')

    print('\nReplaying hedges (10% TP pullback, 0.5% break buffer, 7d TIF)...')
    results = []
    for h in hedges:
        r = replay_hedge(h, df)
        results.append(r)
    rdf = pd.DataFrame(results)
    if rdf.empty:
        print('  no hedges to analyze')
        return

    n = len(rdf)
    span_y = (rdf['ts_open'].max() - rdf['ts_open'].min()).total_seconds() / (365.25*86400)
    per_y = n / max(span_y, 0.1)
    print(f'\n=== Hedge results (n={n}, {per_y:.1f}/yr, span {span_y:.1f}y) ===')
    print(f'  mean total R (both legs): {rdf["total_r"].mean():+.3f}')
    print(f'  median total R:           {rdf["total_r"].median():+.3f}')
    print(f'  both legs hit TP:         {rdf["both_targets"].mean():.0%}')

    print(f'\n  Long-leg exit kinds:')
    for k, v in rdf['long_exit'].value_counts().items():
        print(f'    {k}: {v}')
    print(f'  Short-leg exit kinds:')
    for k, v in rdf['short_exit'].value_counts().items():
        print(f'    {k}: {v}')

    print(f'\n  Long-leg mean R:  {rdf["long_r"].mean():+.3f}')
    print(f'  Short-leg mean R: {rdf["short_r"].mean():+.3f}')

    # OOS
    is_set = rdf[rdf['ts_open'] <= pd.Timestamp('2024-12-31', tz='UTC')]
    oos_set = rdf[rdf['ts_open'] >= pd.Timestamp('2025-01-01', tz='UTC')]
    print(f'\n  IS  total R: mean={is_set["total_r"].mean():+.3f} (n={len(is_set)})')
    print(f'  OOS total R: mean={oos_set["total_r"].mean():+.3f} (n={len(oos_set)})')

    out_path = OUT_DIR / 'B13_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'n_hedges': n, 'span_years': round(span_y, 2),
            'per_year': round(per_y, 1),
            'mean_total_r_both_legs': round(float(rdf['total_r'].mean()), 3),
            'both_targets_rate': round(float(rdf['both_targets'].mean()), 3),
            'long_leg_mean_r': round(float(rdf['long_r'].mean()), 3),
            'short_leg_mean_r': round(float(rdf['short_r'].mean()), 3),
            'long_exit_counts': rdf['long_exit'].value_counts().to_dict(),
            'short_exit_counts': rdf['short_exit'].value_counts().to_dict(),
            'is_mean_total_r': round(float(is_set['total_r'].mean() if not is_set.empty else 0), 3),
            'oos_mean_total_r': round(float(oos_set['total_r'].mean() if not oos_set.empty else 0), 3),
            'note': 'Hedge profits from oscillation: both long+short legs targeting opposite range edges',
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
