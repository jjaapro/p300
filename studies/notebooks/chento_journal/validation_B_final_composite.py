"""validation_B_final_composite: final composite stacking ALL winning filters
from B1-B12.

Base trigger:
  B1 (money-flow divergence) ∩ B5 (LSR extremes) ∩ B7-align (multi-TF CVD)

Filters layered on top (each only added if positive uplift):
  B3 range: drop (bottom-edge longs + midrange shorts)
  B8 timing: drop (Saturday + London-NY overlap 12-14 UTC)
  B11 POC: keep at_poc + short-above-POC + long-below-POC
  B12 DVOL: drop low_vol quartile

Build the composite step-by-step, showing R-uplift at each filter add.
Then split IS/OOS for the final.
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

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

OUT_DIR = ROOT / 'studies' / 'material' / 'chento' / 'validation'

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers, chento_coverage,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.notebooks.chento_journal.validation_B2_B3_range_filters import (
    annotate_triggers_with_range,
)
from studies.notebooks.chento_journal.validation_B11_B12_vp_dvol import (
    compute_rolling_poc, annotate_with_poc_distance,
    load_dvol, annotate_with_dvol_quartile,
)
from studies.lib.range_detector import classify_position


def show(label, df, baseline_R=None):
    if df.empty:
        print(f'  {label:<55s} n=0')
        return None
    s = summarize_triggers(df, label=label)
    is_set = df[df['ts'] <= pd.Timestamp('2024-12-31', tz='UTC')]
    oos_set = df[df['ts'] >= pd.Timestamp('2025-01-01', tz='UTC')]
    is_s = summarize_triggers(is_set, label='IS')
    oos_s = summarize_triggers(oos_set, label='OOS')
    delta = (s['mean_R'] - baseline_R) if baseline_R is not None else 0
    delta_str = f' Δ={delta:+.3f}' if baseline_R is not None else ''
    print(f'  {label:<55s} n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} '
           f'WR={s["win_rate"]:.0%} ann={s["implied_annual_pct"]:+5.1f}%  '
           f'IS={is_s.get("mean_R",0):+.3f}({is_s.get("n",0)}) '
           f'OOS={oos_s.get("mean_R",0):+.3f}({oos_s.get("n",0)}){delta_str}')
    return s


def main():
    print('Building all signals + base composite...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    triple_r = measure_r_outcomes(triple, df)
    print(f'  base triple composite: {len(triple_r)} triggers\n')

    base_s = show('STEP 0: triple composite (B1∩B5∩B7-align)', triple_r)
    base_R = base_s['mean_R']

    # === STEP 1: annotate with range context (B2/B3) ===================
    print('\nAnnotating with range context (B3)...')
    tr = annotate_triggers_with_range(triple_r, df)
    tr['zone'] = tr.apply(
        lambda r: classify_position(r['price_pos']) if r['in_range'] else 'no_range',
        axis=1)

    # === STEP 2: annotate with POC distance (B11) =======================
    print('Computing 7d rolling POC (B11)...')
    poc = compute_rolling_poc(df)
    tr = annotate_with_poc_distance(tr, poc)
    tr['poc_zone'] = tr['poc_dist_abs_pct'].apply(
        lambda d: 'at_poc' if pd.notna(d) and d < 0.005 else
                   'near_poc' if pd.notna(d) and d < 0.015 else
                   'mid' if pd.notna(d) and d < 0.030 else
                   'far_poc' if pd.notna(d) else 'unknown')

    # === STEP 3: annotate with DVOL quartile (B12) ======================
    print('Annotating with DVOL quartile (B12)...')
    tr = annotate_with_dvol_quartile(tr, load_dvol('BTC'))

    # === STEP 4: annotate with time/weekday (B8) ========================
    tr['hour_utc'] = pd.to_datetime(tr['ts']).dt.hour
    tr['weekday'] = pd.to_datetime(tr['ts']).dt.weekday

    # === Sequential filter additions =====================================
    print('\n=== Sequential filter additions (each is added on top of prior) ===')
    cur = tr.copy()

    # B3: drop bottom-edge longs + midrange shorts
    bad_b3 = ((cur['zone'] == 'bottom_edge') & (cur['direction'] == 'long')) | \
             ((cur['zone'] == 'midrange') & (cur['direction'] == 'short'))
    cur = cur[~bad_b3].copy()
    show('STEP 1: + B3 drop (bottom-edge longs + midrange shorts)', cur, base_R)

    # B8: drop Sat + London-NY overlap
    bad_b8 = (cur['weekday'] == 5) | ((cur['hour_utc'] >= 12) & (cur['hour_utc'] < 14))
    cur = cur[~bad_b8].copy()
    show('STEP 2: + B8 drop (Sat + London-NY overlap 12-14 UTC)', cur, base_R)

    # B11: prefer "good POC zones" — keep at_poc OR (short_above_POC) OR (long_below_POC) OR no_range/unknown POC
    good_poc = (cur['poc_zone'] == 'at_poc') | \
               ((cur['direction'] == 'short') & (cur['poc_dist_pct'] > 0)) | \
               ((cur['direction'] == 'long') & (cur['poc_dist_pct'] < 0)) | \
               (cur['poc_zone'] == 'unknown')
    cur = cur[good_poc].copy()
    show('STEP 3: + B11 POC alignment filter', cur, base_R)

    # B12: drop low_vol DVOL quartile (keep mid_low+mid_high+high+unknown)
    cur = cur[cur['dvol_quartile'] != 'low_vol'].copy()
    show('STEP 4: + B12 drop low_vol DVOL quartile', cur, base_R)

    # === Alternative: just B3 + B12 (the strongest two filters) ==========
    print('\n=== Alternative compositions ===')
    alt = tr.copy()
    alt = alt[~(((alt['zone'] == 'bottom_edge') & (alt['direction'] == 'long')) |
                ((alt['zone'] == 'midrange') & (alt['direction'] == 'short')))]
    alt = alt[alt['dvol_quartile'] != 'low_vol']
    show('ALT 1: base + B3 + B12 (no time/POC filters)', alt, base_R)

    alt2 = tr.copy()
    alt2 = alt2[(alt2['zone'] == 'no_range')]
    show('ALT 2: base + only no-range entries', alt2, base_R)

    # === Best of all (subjective pick) ==================================
    print('\n=== FINAL B-composite (subjective best of all winning filters) ===')
    final = cur.copy()  # STEP 4 already has the full stack
    s_final = show('FINAL: B1∩B5∩B7align + B3 + B8 + B11 + B12', final, base_R)

    # Save
    out_path = OUT_DIR / 'B_final_composite_results.json'
    out = {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'base_triple_R': base_R,
        'final_n': int(len(final)) if final is not None else 0,
        'final_mean_R': s_final['mean_R'] if s_final else None,
        'final_annual_pct': s_final['implied_annual_pct'] if s_final else None,
        'note': 'Stacked all winning filters from B2-B12 on triple composite.',
    }
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
