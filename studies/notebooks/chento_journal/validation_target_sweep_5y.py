"""validation_target_sweep_5y: cross-validate fixed-R target choice on the
full 5y Triple-composite sample.

C1 (liq cluster) on the 2.4y TV-covered window showed fixed 4R/5R beats
fixed 3R. That could be a window artefact, or a real structural finding.
This script re-runs the atr-stop × target-multiple sweep on the FULL 5y
dataset (681 triggers) to confirm.
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
from studies.notebooks.chento_journal.validation_structural_stops import (
    replay_all, stop_atr, stats,
)

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def main():
    print('Building Triple composite (full 5y)...')
    df = load_btc_15m()
    df_b1 = compute_moneyflow_signal(df)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b5 = b5_triggers(df, compute_lsr_extremes(load_lsr('BTC')))
    b7 = b7_alignment_triggers(compute_multitf_cvd(df), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    df['atr'] = compute_atr(df, period=14)
    print(f'  Triple triggers: {len(triple):,}')

    # Sweep stop × target on the full 5y sample
    print(f'\n{"variant":<22s} {"n":>4s} {"meanR":>7s} {"WR":>4s} '
           f'{"tgt":>4s} {"stp":>4s} {"tif":>4s} {"holdH":>6s} '
           f'{"IS":>10s} {"OOS":>10s}  {"maxDD":>7s}')
    dump = {}
    for atr_mult in (2.0, 3.0, 4.0, 5.0, 6.0):
        for target_r in (2.0, 3.0, 4.0, 5.0, 6.0, 8.0):
            label = f'atr{atr_mult:.0f}_t{target_r:.0f}R'
            rep = replay_all(triple, df, stop_fn=stop_atr,
                              stop_kwargs={'atr_mult': atr_mult},
                              target_r=target_r)
            s = stats(rep, label)
            if not rep.empty:
                is_set = rep[rep['ts'] <= IS_END]
                oos_set = rep[rep['ts'] > IS_END]
                is_r = float(is_set['r_outcome'].mean()) if len(is_set) else 0
                oos_r = float(oos_set['r_outcome'].mean()) if len(oos_set) else 0
                rep_s = rep.sort_values('ts').reset_index(drop=True)
                cum = rep_s['r_outcome'].cumsum().values
                peak = np.maximum.accumulate(cum)
                max_dd = float((cum - peak).min())
                s['is_meanR'] = round(is_r, 3); s['is_n'] = len(is_set)
                s['oos_meanR'] = round(oos_r, 3); s['oos_n'] = len(oos_set)
                s['max_dd_R'] = round(max_dd, 2)
                s['cum_R'] = round(float(cum[-1]), 2)
            print(f'{label:<22s} {s["n"]:>4d} {s["mean_R"]:>+7.3f} '
                   f'{s["win_rate"]:>4.0%} {s["targets"]:>4d} {s["stops"]:>4d} '
                   f'{s["tif"]:>4d} {s["median_hold_h"]:>6.1f} '
                   f'{s["is_meanR"]:>+10.3f} {s["oos_meanR"]:>+10.3f}  '
                   f'{s["max_dd_R"]:>+7.2f}')
            dump[label] = s

    # Rank by OOS mean R (with min n filter and IS/OOS consistency check)
    print('\n=== Top by OOS mean R (n>=300, IS-OOS sign match) ===')
    cands = [(k, v) for k, v in dump.items()
             if v.get('n', 0) >= 300
             and v.get('is_meanR', 0) > 0 and v.get('oos_meanR', 0) > 0]
    cands.sort(key=lambda kv: kv[1].get('oos_meanR', -99), reverse=True)
    for k, v in cands[:8]:
        print(f'  {k:<22s}  meanR={v["mean_R"]:+.3f}  '
               f'IS={v["is_meanR"]:+.3f}  OOS={v["oos_meanR"]:+.3f}  '
               f'maxDD={v["max_dd_R"]:+.2f}  cumR={v["cum_R"]:+.1f}')

    print('\n=== Top by cum R (n>=300) ===')
    cands.sort(key=lambda kv: kv[1].get('cum_R', -99), reverse=True)
    for k, v in cands[:8]:
        print(f'  {k:<22s}  cumR={v["cum_R"]:+.1f}  meanR={v["mean_R"]:+.3f}  '
               f'maxDD={v["max_dd_R"]:+.2f}')

    out_path = OUT_DIR / 'target_sweep_5y_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('Full 5y atr_stop × target_R sweep on Triple composite. '
                      'Cross-checks the 2.4y TV-window finding that fixed 4R '
                      'beats fixed 3R.'),
            'variants': dump,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
