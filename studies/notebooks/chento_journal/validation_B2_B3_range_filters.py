"""validation_B2_B3: chento's range-based filter rules tested on triggers.

Chento Rule 2 (verbatim): "The more time spent in a specific range, the
stronger the resistance becomes." → time_in_range as a conviction scalar.
Stronger resistance means shorts entered near range high have higher edge
when the range has held for longer.

Chento Rule 3 (verbatim): "Don't trade in midrange." → only fire entries
when price is in outer 25% of the active range (top edge for shorts,
bottom edge for longs).

Both are FILTERS layered on top of an existing trigger set (here we use
the B1∩B5∩B7-align triple composite from earlier work).

For B2 we test multiple time-in-range thresholds.
For B3 we test multiple edge-band widths (10/25/33%).
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
    measure_r_outcomes, summarize_triggers, chento_coverage,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (
    load_lsr, compute_lsr_extremes, b5_triggers,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (
    compute_multitf_cvd, b7_alignment_triggers,
)
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers
from studies.lib.range_detector import (
    detect_active_range, price_position_in_range, classify_position,
)


def annotate_triggers_with_range(triggers: pd.DataFrame,
                                    df_15m: pd.DataFrame,
                                    *, range_max_pct: float = 0.06,
                                    range_window_bars: int = 4 * 24 * 3,
                                    ) -> pd.DataFrame:
    """For each trigger, compute the active range bracket + price position +
    duration. Adds cols: in_range, price_pos, range_pct, duration_bars."""
    if triggers.empty:
        return triggers
    out = triggers.copy()
    out['in_range'] = False
    out['price_pos'] = np.nan
    out['range_pct'] = np.nan
    out['duration_bars'] = 0
    rows = []
    # Use df_15m's index for ts lookup; only need close at trigger time
    for idx, row in out.iterrows():
        ts = pd.Timestamp(row['ts'])
        r = detect_active_range(df_15m, ts,
                                  window_bars=range_window_bars,
                                  max_range_pct=range_max_pct)
        if r:
            entry = float(row['entry'])
            pos = price_position_in_range(entry, r)
            out.loc[idx, 'in_range'] = True
            out.loc[idx, 'price_pos'] = pos
            out.loc[idx, 'range_pct'] = r['range_pct']
            out.loc[idx, 'duration_bars'] = r['duration_bars']
    return out


def main():
    print('Loading data + generating triple composite triggers...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    lsr = load_lsr('BTC')
    lsr_sig = compute_lsr_extremes(lsr)
    b5 = b5_triggers(df, lsr_sig)
    df_b7 = compute_multitf_cvd(df)
    b7_align = b7_alignment_triggers(df_b7, z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7_align)
    print(f'Triple composite: {len(triple)} triggers')

    print('\nMeasuring baseline R outcomes (no filter)...')
    triple_r = measure_r_outcomes(triple, df)
    base_s = summarize_triggers(triple_r, label='Triple_baseline')
    print(f'  baseline: n={base_s["n"]} meanR={base_s["mean_R"]:+.3f} '
           f'WR={base_s["win_rate"]:.0%} annual={base_s["implied_annual_pct"]:+.1f}%')

    print('\nAnnotating triggers with range context...')
    triple_with_range = annotate_triggers_with_range(triple_r, df)
    in_range_pct = float(triple_with_range['in_range'].mean())
    print(f'  triggers inside an active range: {in_range_pct:.1%}')

    # === B3: midrange avoidance ============================================
    # Only fire if price is in outer-X% of range
    print('\n=== B3 midrange-avoidance (only outer-X% of range) ===')
    b3_results = {}
    for edge_pct in (0.10, 0.20, 0.25, 0.33):
        # For longs: keep if price_pos <= edge_pct OR not in range
        # For shorts: keep if price_pos >= 1-edge_pct OR not in range
        def keep(row):
            if not row['in_range']:
                return True  # always allow if no range — chento Rule 3 is about ranges
            pos = row['price_pos']
            if np.isnan(pos):
                return True
            if row['direction'] == 'long':
                return pos <= edge_pct
            else:
                return pos >= 1 - edge_pct
        keep_mask = triple_with_range.apply(keep, axis=1)
        # Variant A: enforce edge-rule (in-range entries restricted)
        kept = triple_with_range[keep_mask].copy()
        # Variant B: ONLY entries within ranges (drop out-of-range triggers)
        kept_strict = triple_with_range[(triple_with_range['in_range']) & keep_mask].copy()
        s = summarize_triggers(kept, label=f'B3_edge{int(edge_pct*100)}')
        s_strict = summarize_triggers(kept_strict, label=f'B3_edge{int(edge_pct*100)}_inrange_only')
        print(f'  edge={int(edge_pct*100)}%: lenient n={s["n"]} meanR={s["mean_R"]:+.3f} '
               f'WR={s["win_rate"]:.0%} ann={s["implied_annual_pct"]:+.1f}%  '
               f'| strict n={s_strict.get("n",0)} meanR={s_strict.get("mean_R",0):+.3f} '
               f'WR={s_strict.get("win_rate",0):.0%} ann={s_strict.get("implied_annual_pct",0):+.1f}%')
        b3_results[f'edge_{int(edge_pct*100)}'] = {
            'lenient': s, 'strict': s_strict,
        }

    # === B2: time-in-range strengthens resistance ==========================
    # For trades INSIDE a range with price at edges, weight by time-in-range.
    # Test: only fire if duration_bars >= threshold (range has held N bars).
    print('\n=== B2 time-in-range minimum (range has held N bars) ===')
    b2_results = {}
    # Use B3 strict-25 as the base set (in-range entries at edges only)
    in_range_edge = triple_with_range[triple_with_range['in_range']].copy()
    # Apply edge filter at 25%
    def edge_25(row):
        pos = row['price_pos']
        if np.isnan(pos): return False
        if row['direction'] == 'long':
            return pos <= 0.25
        else:
            return pos >= 0.75
    edge_set = in_range_edge[in_range_edge.apply(edge_25, axis=1)].copy()
    print(f'  base set (in-range + edge): {len(edge_set)} triggers')
    for min_bars in (24, 48, 96, 192, 288):  # 6h, 12h, 1d, 2d, 3d at 15m
        sub = edge_set[edge_set['duration_bars'] >= min_bars]
        s = summarize_triggers(sub, label=f'B2_min{min_bars}bars')
        print(f'  duration >= {min_bars} bars ({min_bars/4:.0f}h): '
               f'n={s.get("n",0)} meanR={s.get("mean_R",0):+.3f} '
               f'WR={s.get("win_rate",0):.0%} ann={s.get("implied_annual_pct",0):+.1f}%')
        b2_results[f'min_{min_bars}_bars'] = s

    # === Per-zone breakdown for reference =====================================
    print('\n=== Per-zone (where in range did the trigger fire?) ===')
    triple_with_range['zone'] = triple_with_range.apply(
        lambda r: classify_position(r['price_pos']) if r['in_range'] else 'no_range',
        axis=1)
    for zone in ('bottom_edge', 'midrange', 'top_edge', 'no_range'):
        sub = triple_with_range[triple_with_range['zone'] == zone]
        if sub.empty: continue
        s = summarize_triggers(sub, label=f'zone_{zone}')
        # also direction breakdown
        for d in ('long', 'short'):
            ds = sub[sub['direction'] == d]
            if ds.empty: continue
            r = ds['r_outcome'].dropna()
            print(f'  {zone:<14s} {d:<5s}: n={len(ds):>4d} meanR={r.mean():+.3f} WR={(r>0).mean():.0%}')

    out_path = OUT_DIR / 'B2_B3_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': base_s,
            'B3_midrange_avoidance': b3_results,
            'B2_time_in_range': b2_results,
            'study_note': (
                "B2 + B3 tested as filters on top of B1∩B5∩B7-align composite. "
                "B3 lenient = restrict in-range entries to outer-X%, allow out-of-range. "
                "B3 strict = only fire in-range edge entries. B2 = require minimum "
                "time-in-range duration."
            ),
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
