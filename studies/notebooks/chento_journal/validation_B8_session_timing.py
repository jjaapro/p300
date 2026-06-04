"""validation_B8: chento's session-timing + Mon/Tue/Wed 1h-before-NY pattern.

Two distinct sub-rules from chento corpus:

  B8a — Pre-NY profit-take: "Take the 10 pre ny" — close winners before
        14:30 UTC (NY open). Tested as a TIF-tightening filter: if hold
        spans across NY-open, prefer to close earlier than full TIF.

  B8b — Mon/Tue/Wed 1h-before-NY entry pattern (~13:30 UTC):
        msg 1453381078608908412 documents 3 consecutive days (Dec 22-24
        2025) with identical intraday setups at this exact time.
        Tested as an entry-time filter: keep only triggers within a
        ±N-minute window around 13:30 UTC on Mon/Tue/Wed.

Both are FILTERS layered on the triple composite.
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

from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m, compute_moneyflow_signal, b1_triggers,
    measure_r_outcomes, summarize_triggers,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers


def main():
    print('Building triple composite + measuring R outcomes...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    triple_r = measure_r_outcomes(triple, df)
    print(f'  triple: {len(triple_r)} triggers')

    # Annotate temporal context
    triple_r['hour_utc'] = pd.to_datetime(triple_r['ts']).dt.hour
    triple_r['minute_utc'] = pd.to_datetime(triple_r['ts']).dt.minute
    triple_r['weekday'] = pd.to_datetime(triple_r['ts']).dt.weekday  # 0=Mon
    triple_r['hhmm'] = triple_r['hour_utc'] * 100 + triple_r['minute_utc']

    base_s = summarize_triggers(triple_r, label='baseline_triple')
    print(f'  baseline: n={base_s["n"]} meanR={base_s["mean_R"]:+.3f} '
           f'WR={base_s["win_rate"]:.0%}')

    # === B8b: Mon/Tue/Wed at 13:30 UTC (1h before NY) =====================
    print('\n=== B8b: Mon/Tue/Wed near 13:30 UTC ===')
    for window_min in (30, 60, 120, 180):
        # Time window: 13:30 ± window_min
        mid_min = 13 * 60 + 30
        tm = triple_r['hour_utc'] * 60 + triple_r['minute_utc']
        in_window = abs(tm - mid_min) <= window_min
        mtw = triple_r['weekday'].isin([0, 1, 2])
        keep = in_window & mtw
        sub = triple_r[keep]
        s = summarize_triggers(sub, label=f'B8b_MTW_pm{window_min}min')
        print(f'  MTW 13:30 ±{window_min:>3d}min: n={s.get("n",0):>4d} '
               f'meanR={s.get("mean_R",0):+.3f} WR={s.get("win_rate",0):.0%} '
               f'ann={s.get("implied_annual_pct",0):+.1f}%')

    # === Per-weekday breakdown ============================================
    print('\n=== Per-weekday (full triggers) ===')
    for wd in range(7):
        sub = triple_r[triple_r['weekday'] == wd]
        if sub.empty: continue
        wd_name = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][wd]
        s = summarize_triggers(sub, label=f'{wd_name}')
        print(f'  {wd_name}: n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} '
               f'WR={s["win_rate"]:.0%}')

    # === Per-session breakdown (Asia/London/NY) ===========================
    print('\n=== Per-session (UTC bucket) ===')
    def session(h):
        # Rough buckets, all UTC
        if 0 <= h < 6: return 'asia_late'
        if 6 <= h < 12: return 'london_morning'
        if 12 <= h < 14: return 'london_ny_overlap'
        if 14 <= h < 21: return 'ny_session'
        return 'asia_early'
    triple_r['session'] = triple_r['hour_utc'].apply(session)
    for sess in ('asia_late','london_morning','london_ny_overlap','ny_session','asia_early'):
        sub = triple_r[triple_r['session'] == sess]
        if sub.empty: continue
        s = summarize_triggers(sub, label=sess)
        print(f'  {sess:<22s}: n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} WR={s["win_rate"]:.0%}')

    # === Pre-NY profit-take heuristic =====================================
    # Crude proxy: for trades opened before 14:30 UTC, did they reach +1R BEFORE
    # 14:30 UTC and would close-then have been better than holding?
    # We'd need MFE-by-time which our current ledger doesn't carry.
    # Instead: filter triggers that fire AFTER NY open (14:30 UTC) and see if
    # their R differs from before-NY triggers.
    print('\n=== Pre-NY vs Post-NY trigger time (entry hour) ===')
    pre_ny = triple_r[triple_r['hour_utc'] < 14]
    post_ny = triple_r[triple_r['hour_utc'] >= 14]
    for label, sub in (('pre_NY (<14:00 UTC)', pre_ny), ('post_NY (>=14:00 UTC)', post_ny)):
        if sub.empty: continue
        s = summarize_triggers(sub, label=label)
        print(f'  {label:<22s}: n={s["n"]:>4d} meanR={s["mean_R"]:+.3f} '
               f'WR={s["win_rate"]:.0%} ann={s["implied_annual_pct"]:+.1f}%')

    out_path = OUT_DIR / 'B8_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': base_s,
            'note': 'B8 tested as time-of-day + day-of-week filter on triple composite.',
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
