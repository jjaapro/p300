"""validation_B_composite: combine signal-stream triggers via INTERSECTION
within a time window to test the multi-signal-confluence hypothesis.

Composite logic:
  fire only if B_X trigger AND B_Y trigger within `window_hours` AND same direction

Tests:
  C_B1_B5    = B1 (MF divergence) ∩ B5 (LSR extremes)  -- 5y history
  C_B1_B4    = B1 ∩ B4 (squeeze direction)             -- 3mo history (B4 limited)
  C_B5_B4    = B5 ∩ B4                                  -- 3mo history
  C_all3     = B1 ∩ B5 ∩ B4                             -- 3mo history

Hypothesis: precision should compound while coverage stays decent.
If chento really uses ≥2 signal streams aligned, the intersection of any 2
of our signals should match his trades much more reliably than either alone.
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
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers, chento_coverage,
)
from studies.notebooks.chento_journal.validation_B4_squeeze_direction import (
    load_liquidations_hourly, compute_squeeze_signal, b4_triggers,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)


WINDOW_HOURS = 24  # require both signals to fire within ±24h


def intersect_triggers(a: pd.DataFrame, b: pd.DataFrame, *,
                       window_hours: float = WINDOW_HOURS) -> pd.DataFrame:
    """Keep rows from `a` that have a same-direction `b` trigger within window."""
    if a.empty or b.empty:
        return pd.DataFrame(columns=a.columns)
    keep = []
    for i, ra in a.iterrows():
        bs = b[b['direction'] == ra['direction']]
        if bs.empty:
            continue
        delta_h = (bs['ts'] - ra['ts']).dt.total_seconds() / 3600.0
        if (abs(delta_h) <= window_hours).any():
            keep.append(i)
    return a.loc[keep].copy()


def main():
    print(f'OUT_DIR: {OUT_DIR}')
    print('Loading BTC 15m + signals...')
    df = load_btc_15m()
    print(f'  {len(df):,} bars')

    print('\nGenerating B1 triggers (cvd>±0.5, vel<1.0)...')
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    print(f'  B1: {len(b1)} triggers ({(b1["direction"]=="short").sum()} short / '
           f'{(b1["direction"]=="long").sum()} long)')

    print('\nGenerating B4 triggers (z>2.0)...')
    liq = load_liquidations_hourly()
    liq_sig = compute_squeeze_signal(liq)
    b4 = b4_triggers(df, liq_sig, z_threshold=2.0)
    print(f'  B4: {len(b4)} triggers ({(b4["direction"]=="short").sum()} short / '
           f'{(b4["direction"]=="long").sum()} long)')

    print('\nGenerating B5 triggers (long_pct extremes vs 30d p10/p90)...')
    lsr = load_lsr('BTC')
    lsr_sig = compute_lsr_extremes(lsr)
    b5 = b5_triggers(df, lsr_sig)
    print(f'  B5: {len(b5)} triggers ({(b5["direction"]=="short").sum()} short / '
           f'{(b5["direction"]=="long").sum()} long)')

    # Compute R outcomes once for each signal
    for label, trigs in (('B1', b1), ('B4', b4), ('B5', b5)):
        if 'r_outcome' not in trigs.columns:
            print(f'  Measuring R outcomes for {label}...')

    results = {}

    print('\n=== Single-signal baselines (recap) ===')
    for label, trigs in (('B1', b1), ('B4', b4), ('B5', b5)):
        if trigs.empty: continue
        trigs_r = measure_r_outcomes(trigs, df)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label}: n={s["n"]:>5d}  mean R={s["mean_R"]:+.3f}  WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}')
        results[f'{label}_standalone'] = {**s, 'coverage': cov}

    # === Composites =====================================================
    composites = {
        'C_B1_B5':  intersect_triggers(b1, b5),
        'C_B1_B4':  intersect_triggers(b1, b4),
        'C_B5_B4':  intersect_triggers(b5, b4),
    }
    # Triple intersection: start from B1∩B4, then intersect with B5
    if not composites['C_B1_B4'].empty:
        composites['C_B1_B4_B5'] = intersect_triggers(composites['C_B1_B4'], b5)
    else:
        composites['C_B1_B4_B5'] = pd.DataFrame()

    print('\n=== Composite (intersection, same direction within ±24h) ===')
    for label, trigs in composites.items():
        if trigs.empty:
            print(f'  {label}: 0 triggers')
            continue
        trigs_r = measure_r_outcomes(trigs, df)
        s = summarize_triggers(trigs_r, label=label)
        cov = chento_coverage(trigs_r, asset='BTCUSDT')
        print(f'  {label}: n={s["n"]:>5d}  mean R={s["mean_R"]:+.3f}  WR={s["win_rate"]:.0%}  '
               f'precision={cov.get("trigger_to_chento_loose_rate",0):.1%}  '
               f'coverage={cov.get("chento_to_trigger_loose_rate",0):.1%}  '
               f'(chento in window={cov.get("n_chento_in_trigger_window",0)})')
        results[label] = {**s, 'coverage': cov}

    out_path = OUT_DIR / 'B_composite_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'window_hours_intersection': WINDOW_HOURS,
            'study_note': (
                'Multi-signal composites via intersection. Test whether '
                'requiring B_X AND B_Y AND B_Z to all fire same-direction '
                'within ±24h compounds precision while keeping reasonable '
                'coverage on chento BTC trades.'
            ),
            'results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
