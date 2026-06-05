"""P4 of the 2026-06-05 lookahead audit: post-filter ATR_STOP_MULT x TARGET_R
joint sweep under BACKWARD-ONLY intersect_triggers AND production filter stack
(no_tilt + no_resist_OB + okx_aligned), with NO LADDER (T0 baseline, matching
the LADDER_ENABLED=False decision from P1).

P2 hinted at target_R widening (6R -> 8R) on the pre-filter pool. P3 confirmed
TIF_HOURS=72 is optimal. This script resolves whether the target widening
survives once the production filters compress the pool to ~35 trades.

Decision rule: ship `TARGET_R = 8` (or other winner) only if it (a) beats the
current 6R baseline on both annual_R AND MAR with adequate OOS sample, AND
(b) the lift is > 10% MAR (otherwise curve-fitting risk on small-n).
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

import studies.notebooks.chento_journal.validation_B_composite as src_comp
import studies.notebooks.chento_journal.validation_group_A_tuning as src_p3
from studies.notebooks.chento_journal.validation_group_A_tuning import (
    build_optimized_triggers, apply_filters, replay_with_extras, summary, show,
)
from studies.notebooks.chento_journal.validation_C5_smc_features import features_at

IS_END = pd.Timestamp('2024-12-31 23:59:59', tz='UTC')


def intersect_backward(a: pd.DataFrame, b: pd.DataFrame, *,
                       window_hours: float = 24.0) -> pd.DataFrame:
    if a.empty or b.empty:
        return pd.DataFrame(columns=a.columns)
    keep = []
    for i, ra in a.iterrows():
        bs = b[b['direction'] == ra['direction']]
        if bs.empty:
            continue
        delta_h = (bs['ts'] - ra['ts']).dt.total_seconds() / 3600.0
        if ((delta_h >= -window_hours) & (delta_h <= 0)).any():
            keep.append(i)
    return a.loc[keep].copy()


def replay_then_filter(triple_w, df_smc, df_atr, fvgs, obs, delta_df, *,
                       atr_mult: float, target_r: float, tif_hours: int = 72):
    """T0 (no ladder) replay + production filter stack."""
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_extras(t, df_smc, df_atr, fvgs, obs,
                                atr_mult=atr_mult, target_r=target_r,
                                tif_bars=4 * tif_hours,
                                partial_at_R=None, partial_frac=0.0,
                                ladder_at_minus_R=None, ladder_size_frac=0.0)
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows)
    if rep.empty:
        return rep
    # attach dist_resist_OB_R for the filter
    dist_arr = []
    for _, row in rep.iterrows():
        idx = df_smc.index.searchsorted(row['ts'], side='right') - 1
        if idx < 0 or idx >= len(df_smc):
            dist_arr.append(np.nan); continue
        f = features_at(idx, row['entry'], row['direction'], row['risk'],
                          df_smc, fvgs, obs)
        dist_arr.append(f.get('dist_resist_OB_R', np.nan))
    rep['dist_resist_OB_R'] = dist_arr
    return apply_filters(rep, delta_df)


def main():
    src_comp.intersect_triggers = intersect_backward
    src_p3.intersect_triggers = intersect_backward
    print('[P4-backonly] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    print('Building optimized triggers (backward-only)...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = build_optimized_triggers()
    print(f'  Triple bars (backward-only): {len(triple_w)}')

    # Grid: atr_mult x target_r, all NO LADDER, TIF=72h
    ATR_MULTS = (4.0, 5.0, 6.0)
    TARGETS = (3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0)

    print('\n' + '=' * 100)
    print('=== Post-filter T0 (no ladder) ATR x Target sweep — BACKWARD-ONLY ===')
    print('=' * 100)
    print(f'\n{"variant":<22s} {"n":>3s} {"meanR":>7s} {"WR":>4s} '
           f'{"maxDD":>7s} {"annual":>8s} {"MAR":>5s} '
           f'{"IS":>9s} {"OOS":>9s}')

    results = {}
    for atr_mult in ATR_MULTS:
        for target_r in TARGETS:
            label = f'atr{atr_mult:.0f}_t{target_r:.0f}R'
            rep = replay_then_filter(triple_w, df_smc, df_atr, fvgs, obs,
                                       delta_df, atr_mult=atr_mult,
                                       target_r=target_r, tif_hours=72)
            s = summary(rep, label)
            # add MAR
            if s.get('n', 0) > 0 and s.get('max_dd_R', 0) != 0:
                # annual_R: rep span years
                span_y = ((rep['ts'].max() - rep['ts'].min()).total_seconds()
                            / (365.25 * 86400))
                annual_R = s['mean_R'] * s['n'] / max(span_y, 0.1)
                s['annual_R'] = round(annual_R, 1)
                s['MAR'] = round(annual_R / abs(s['max_dd_R']), 2)
            else:
                s['annual_R'] = 0; s['MAR'] = 0
            results[label] = s
            print(f'{label:<22s} {s["n"]:>3d} {s["mean_R"]:>+7.3f} '
                   f'{s["wr"]:>4.0%} {s["max_dd_R"]:>+7.2f} '
                   f'{s["annual_R"]:>+8.1f} {s["MAR"]:>5.2f} '
                   f'{s["IS_meanR"]:>+9.3f}({s["IS_n"]}) '
                   f'{s["OOS_meanR"]:>+9.3f}({s["OOS_n"]})')

    # Decision: rank by MAR with adequate n (≥20)
    print('\n' + '=' * 100)
    print('=== Ranked by MAR (n≥20, IS+OOS both positive) ===')
    print('=' * 100)
    cands = [(k, v) for k, v in results.items()
              if v.get('n', 0) >= 20
              and v.get('IS_meanR', 0) > 0
              and v.get('OOS_meanR', 0) > 0]
    cands.sort(key=lambda kv: kv[1]['MAR'], reverse=True)
    for k, v in cands[:10]:
        print(f'  {k:<22s} MAR={v["MAR"]:>5.2f}  annual={v["annual_R"]:>+7.1f}R  '
               f'maxDD={v["max_dd_R"]:+5.2f}R  meanR={v["mean_R"]:+.3f}  '
               f'OOS={v["OOS_meanR"]:+.3f}(n={v["OOS_n"]})')

    # Headline comparison
    baseline = results.get('atr5_t6R', {})
    print('\n' + '=' * 100)
    print('=== HEADLINE: baseline (atr5_t6R) vs candidate winners ===')
    print('=' * 100)
    if baseline.get('n', 0) > 0:
        bm = baseline['MAR']; ba = baseline['annual_R']; bdd = baseline['max_dd_R']
        print(f'  baseline atr5_t6R: annual={ba:+.1f}R  maxDD={bdd:+.2f}R  MAR={bm:.2f}')
        for k in ('atr5_t8R', 'atr5_t10R', 'atr5_t7R', 'atr5_t5R',
                   'atr6_t8R', 'atr6_t6R', 'atr4_t8R'):
            v = results.get(k)
            if not v or v.get('n', 0) == 0:
                continue
            d_mar = (v['MAR'] - bm) / bm * 100 if bm > 0 else 0
            d_ann = v['annual_R'] - ba
            print(f'  {k:<14s}      annual={v["annual_R"]:+.1f}R  '
                   f'maxDD={v["max_dd_R"]:+.2f}R  MAR={v["MAR"]:.2f}   '
                   f'(deltaMAR={d_mar:+.1f}%, deltaAnn={d_ann:+.1f}R)')

    out_path = OUT_DIR / 'target_sweep_postfilter_backonly_results.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('P4 of lookahead audit: ATR_STOP_MULT x TARGET_R sweep '
                      'with backward-only triggers + production filter stack '
                      '(no_tilt + no_resist_OB + okx_aligned) + NO LADDER (T0). '
                      'TIF=72h fixed (confirmed optimum in P3). Resolves whether '
                      'target widening (P2 hint) survives post-filter.'),
            'baseline': 'atr5_t6R',
            'results': results,
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
