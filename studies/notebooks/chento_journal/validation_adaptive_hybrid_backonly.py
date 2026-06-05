"""validation_adaptive_hybrid_backonly: P1 of the 2026-06-05 lookahead audit.

Re-runs the H_B / T1 / T3 / hybrid frontier from validation_adaptive_hybrid.py
but with intersect_triggers patched to BACKWARD-ONLY semantics — anchored on
B1's timestamp, accepting B5/B7 fires only in [-24h, 0] relative to the B1 bar.

This mirrors production CHENTO_TRIPLE_V3's compute_triple_windowed exactly:
  - B1 fires fresh at this bar (B1 cooldown 6h, applied inside b1_triggers)
  - B5 same-dir fired in trailing 24h (B5 cooldown 24h, inside b5_triggers)
  - B7 same-dir fired in trailing 24h (B7 cooldown 6h, inside b7_triggers)

Research's bidirectional `intersect_triggers(a, b, ±24h)` becomes backward-only
`a-rows where any b-row of same direction sits in [ra.ts - 24h, ra.ts]`.

Goal of the test:
  Under backward-only, does H_B (T3 inside-VA + T1 outside-VA) still
  Pareto-dominate T1-uniform? Decision rule per audit:
    if MAR(H_B) <= MAR(T1), production should ship LADDER_ENABLED=False.
"""
from __future__ import annotations

import json
import sys
import sqlite3
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

# Import the original module so we can monkeypatch its captured binding.
import studies.notebooks.chento_journal.validation_liquidation_and_C6 as src_liq
import studies.notebooks.chento_journal.validation_B_composite as src_comp
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (
    load_btc_15m,
)
from studies.notebooks.chento_journal.validation_liquidation_and_C6 import (
    replay_with_mae, apply_filters, compute_volume_profile,
)
from studies.notebooks.chento_journal.validation_adaptive_hybrid import (
    TIER_PARAMS, replay_all_tiers, attach_va, hybrid, summary, show, IS_END,
)


def replay_no_ladder(triple_w, df_smc, df_atr, fvgs, obs):
    """T0 — main position only, no ladder add at adverse excursion. Same
    risk units, target, TIF as T1/T2/T3 but with enable_ladder=False."""
    rows = []
    for _, t in triple_w.iterrows():
        r = replay_with_mae(t, df_smc, df_atr, fvgs, obs,
                              enable_ladder=False, ladder_at_adv_R=0.3,
                              ladder_size_frac=0.0, post_ladder_stop_R=1.0)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


# ─── Backward-only intersect ───────────────────────────────────────────────

def intersect_backward(a: pd.DataFrame, b: pd.DataFrame, *,
                       window_hours: float = 24.0) -> pd.DataFrame:
    """Keep rows from `a` that have a same-direction `b` trigger within
    the BACKWARD-ONLY window [ra.ts - window_hours, ra.ts]."""
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


def main():
    # Patch BOTH the source module's attribute (so future imports see backward)
    # AND the captured binding in validation_liquidation_and_C6 (so its
    # already-imported `intersect_triggers` resolves to the patched version).
    src_comp.intersect_triggers = intersect_backward
    src_liq.intersect_triggers = intersect_backward
    print('[P1-backonly] intersect_triggers patched to BACKWARD-ONLY [-24h, 0]')

    print('Building optimized triggers (backward-only) and replays at all three tiers...')
    triple_w, df_smc, df_atr, fvgs, obs, delta_df = src_liq.build_optimized()
    print(f'  Triple bars (backward-only): {len(triple_w)}')
    print(f'    long: {(triple_w["direction"]=="long").sum()}, short: {(triple_w["direction"]=="short").sum()}')
    print(f'    span: {triple_w["ts"].min()} -> {triple_w["ts"].max()}')

    replays_raw = replay_all_tiers(triple_w, df_smc, df_atr, fvgs, obs)
    # Also replay T0 (no ladder) for honest comparison vs production
    # post-ladder-disable behavior.
    replays_raw['T0'] = replay_no_ladder(triple_w, df_smc, df_atr, fvgs, obs)
    print('Applying filters per tier...')
    filtered = {tier: apply_filters(rep, delta_df, df_smc, fvgs, obs)
                for tier, rep in replays_raw.items()}

    # ensure same trigger universe in each tier
    ts_intersect = set(filtered['T1']['ts'])
    for tier in ('T0', 'T2', 'T3'):
        ts_intersect &= set(filtered[tier]['ts'])
    print(f'  Intersecting ts: {len(ts_intersect)} '
          f'(T0={len(filtered["T0"])}, T1={len(filtered["T1"])}, '
          f'T2={len(filtered["T2"])}, T3={len(filtered["T3"])})')

    for tier in list(TIER_PARAMS) + ['T0']:
        filtered[tier] = filtered[tier][filtered[tier]['ts'].isin(ts_intersect)].copy()
        filtered[tier] = filtered[tier].sort_values('ts').reset_index(drop=True)

    print('Computing volume profile (7d window)...')
    vp = compute_volume_profile(load_btc_15m(), window_days=7, n_price_bins=50)

    for tier in TIER_PARAMS:
        attach_va(filtered[tier], vp)

    # sanity
    assert (filtered['T1']['in_va'].values == filtered['T2']['in_va'].values).all()
    assert (filtered['T1']['in_va'].values == filtered['T3']['in_va'].values).all()

    inside = filtered['T1']['in_va']
    within1R = filtered['T1']['dist_to_va_R'] <= 1.0
    print(f'\n  Inside-VA: {int(inside.sum())} of {len(inside)} '
          f'({inside.mean()*100:.0f}%)')
    print(f'  Within 1R of VA edge: {int(within1R.sum())} of {len(within1R)} '
          f'({within1R.mean()*100:.0f}%)')

    print('\n' + '=' * 80)
    print('=== Reference points (single-tier) — BACKWARD-ONLY ===')
    print('=' * 80)
    for tier in ['T0'] + list(TIER_PARAMS):
        show(summary(filtered[tier], f'{tier} alone (all trades)'))

    print()
    show(summary(filtered['T3'][inside], 'T3 + inside-VA only (no skip otherwise)'))
    show(summary(filtered['T3'][within1R], 'T3 + within-1R-VA only'))
    show(summary(filtered['T2'][inside], 'T2 + inside-VA only'))

    print('\n' + '=' * 80)
    print('=== Adaptive hybrids — BACKWARD-ONLY ===')
    print('=' * 80)

    print('\n--- H_A: T3 inside-VA + T2 outside-VA ---')
    h_a = hybrid(filtered['T3'], filtered['T2'], inside)
    show(summary(h_a, 'H_A: T3 inside + T2 outside'))

    print('\n--- H_B: T3 inside-VA + T1 outside-VA ---')
    h_b = hybrid(filtered['T3'], filtered['T1'], inside)
    show(summary(h_b, 'H_B: T3 inside + T1 outside'))

    print('\n--- H_D: T2 inside-VA + T1 outside-VA ---')
    h_d = hybrid(filtered['T2'], filtered['T1'], inside)
    show(summary(h_d, 'H_D: T2 inside + T1 outside'))

    print('\n--- H_E: T3 within-1R-VA + T1 outside-1R-VA ---')
    h_e = hybrid(filtered['T3'], filtered['T1'], within1R)
    show(summary(h_e, 'H_E: T3 within-1R + T1 outside'))

    print('\n--- H_F: T3 within-1R-VA + skip outside ---')
    h_f = hybrid(filtered['T3'], None, within1R)
    show(summary(h_f, 'H_F: T3 within-1R only (skip outside)'))

    print('\n--- H_C: T3 inside-VA + skip outside ---')
    h_c = hybrid(filtered['T3'], None, inside)
    show(summary(h_c, 'H_C: T3 inside only (skip outside)'))

    print('\n' + '=' * 80)
    print('=== Pareto frontier analysis — BACKWARD-ONLY ===')
    print('=' * 80)
    candidates = [
        ('T0 alone (no ladder)', summary(filtered['T0'], 'T0 alone (no ladder)')),
        ('T1 alone', summary(filtered['T1'], 'T1 alone')),
        ('T2 alone', summary(filtered['T2'], 'T2 alone')),
        ('T3 alone', summary(filtered['T3'], 'T3 alone')),
        ('T3 + inside-VA', summary(filtered['T3'][inside], 'T3 + inside-VA')),
        ('T3 + within-1R-VA', summary(filtered['T3'][within1R], 'T3 + within-1R-VA')),
        ('H_A: T3in+T2out', summary(h_a, 'H_A')),
        ('H_B: T3in+T1out', summary(h_b, 'H_B')),
        ('H_D: T2in+T1out', summary(h_d, 'H_D')),
        ('H_E: T3w1R+T1out', summary(h_e, 'H_E')),
    ]
    print(f'\n{"variant":<28s} {"n/yr":>5s} {"meanR":>7s} {"maxDD":>7s} '
          f'{"annual":>8s} {"MAR":>5s} {"OOS":>7s}')
    for name, s in candidates:
        if s.get('n', 0) == 0:
            continue
        print(f'  {name:<28s} {s["per_yr"]:>5.1f} {s["mean_R"]:>+7.3f} '
              f'{s["max_dd_R"]:>+7.2f} {s["annual_R"]:>+8.1f} '
              f'{s["MAR"]:>5.2f} {s["OOS_meanR"]:>+7.3f}')

    # Decision read-out
    print('\n' + '=' * 80)
    print('=== P1 DECISION ===')
    print('=' * 80)
    s_t0 = summary(filtered['T0'], 'T0')
    s_t1 = summary(filtered['T1'], 'T1')
    s_hb = summary(h_b, 'H_B')
    mar_t0 = s_t0['MAR']
    mar_t1 = s_t1['MAR']
    mar_hb = s_hb['MAR']
    edge_pct = (mar_hb - mar_t1) / mar_t1 * 100 if mar_t1 > 0 else 0
    print(f'  T0 no-ladder: annual={s_t0["annual_R"]:+.1f}R  maxDD={s_t0["max_dd_R"]:+.2f}R  MAR={mar_t0:.2f}  meanR={s_t0["mean_R"]:+.3f}')
    print(f'  T1 alone:     annual={s_t1["annual_R"]:+.1f}R  maxDD={s_t1["max_dd_R"]:+.2f}R  MAR={mar_t1:.2f}  meanR={s_t1["mean_R"]:+.3f}')
    print(f'  H_B hybrid:   annual={s_hb["annual_R"]:+.1f}R  maxDD={s_hb["max_dd_R"]:+.2f}R  MAR={mar_hb:.2f}  meanR={s_hb["mean_R"]:+.3f}')
    print(f'  H_B edge over T1: {edge_pct:+.1f}% MAR')
    print(f'  T0 vs T1 MAR delta: {((mar_t0 - mar_t1) / mar_t1 * 100):+.1f}%')
    print(f'  T0 vs H_B MAR delta: {((mar_t0 - mar_hb) / mar_hb * 100):+.1f}%')
    if mar_hb > mar_t1:
        print(f'  VERDICT: H_B still Pareto-dominates T1 — KEEP LADDER_ENABLED=True')
    else:
        print(f'  VERDICT: H_B FAILS to Pareto-dominate T1 — SHIP LADDER_ENABLED=False')
    if mar_t0 > mar_t1:
        print(f'  ADDITIONAL: T0 (no ladder) beats T1 by {((mar_t0 - mar_t1) / mar_t1 * 100):+.1f}% MAR — confirms ladder net-NEGATIVE')
    else:
        print(f'  ADDITIONAL: T0 (no ladder) LOSES to T1 by {((mar_t0 - mar_t1) / mar_t1 * 100):+.1f}% MAR — keep ladder at T1 sizing')

    out_path = OUT_DIR / 'adaptive_hybrid_results_backonly.json'
    with out_path.open('w', encoding='utf-8') as fh:
        json.dump({
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'note': ('P1 of lookahead audit: same pipeline as adaptive_hybrid_results.json '
                     'but with intersect_triggers patched to backward-only [-24h, 0]. '
                     'Mirrors production CHENTO_TRIPLE_V3 compute_triple_windowed semantics.'),
            'verdict': ('keep_ladder' if mar_hb > mar_t1 else 'disable_ladder'),
            'mar_t1': mar_t1,
            'mar_hb': mar_hb,
            'edge_pct': edge_pct,
            'results': {name: s for name, s in candidates},
        }, fh, indent=2, default=str)
    print(f'\nWrote {out_path}')


if __name__ == '__main__':
    main()
