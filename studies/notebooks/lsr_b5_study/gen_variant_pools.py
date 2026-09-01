#!/usr/bin/env python3
"""Test 1 pools — the six pre-registered B5 variants of the backward-only
Triple composite (README.md). Read-only against prod.db; writes
results/pools/trades_{asset}_{variant}.csv and results/parity_{asset}.json.

Copies the studies/notebooks/overlay_study/gen_trades.py pipeline (its gen()
is monolithic) with two things made variable: the B5 window and the way B5
enters the intersection. Everything else — B1, B7, SMC replay (5×ATR stop,
6R fixed target, 72h TIF), the no_resist_OB filter, the OKX delta-z tag —
is the research code called with the production values. SHIFT_DAYS comes
from Test 0 (0 = PERIOD_START; the stamp-D LSR value is known at D 00:00).

Parity gate: V0 must reproduce overlay_study/results_backonly on
ts <= 2026-07-20; the study stops if it does not.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.chento_journal.validation_multi_asset import (  # noqa: E402
    ASSET_CONFIG, compute_okx_delta_z, derive_binance_1h_close,
    load_lsr_asset, load_okx_close_asset, load_perp_15m,
)
from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence import (  # noqa: E402
    b1_triggers, compute_atr, compute_moneyflow_signal,
)
from studies.notebooks.chento_journal.validation_B5_lsr_extremes import (  # noqa: E402
    b5_triggers, compute_lsr_extremes,
)
from studies.notebooks.chento_journal.validation_B7_multitf_cvd import (  # noqa: E402
    b7_alignment_triggers, compute_multitf_cvd,
)
from studies.notebooks.chento_journal.validation_group_A_tuning_backonly import (  # noqa: E402
    intersect_backward,
)
from studies.notebooks.chento_journal.validation_C5_smc_features import (  # noqa: E402
    compute_fvgs, compute_order_blocks, compute_pivots, compute_smc_state,
    replay_one,
)

OUT = HERE / 'results' / 'pools'
OUT.mkdir(parents=True, exist_ok=True)
PARITY_REF = HERE.parent / 'overlay_study' / 'results_backonly'
PARITY_END = pd.Timestamp('2026-07-20 23:59:59', tz='UTC')
KEEP = ['ts', 'direction', 'entry', 'stop', 'target', 'target_R', 'risk',
        'r_outcome', 'exit_kind', 'dist_resist_OB_R', 'okx_delta_z']

# name -> (B5 rolling window in daily rows, how B5 enters the intersection)
VARIANTS = {
    'V0_sym30': (30, 'sym'),
    'V1_long30': (30, 'long_only'),
    'V2_noB5': (None, 'none'),
    'V3_sym90': (90, 'sym'),
    'V4_sym365': (365, 'sym'),
    'V5_long365': (365, 'long_only'),
}


def shift_days() -> int:
    p = HERE / 'results' / 'test0_stamp_semantics.json'
    if not p.exists():
        raise SystemExit('run test0_stamp_semantics.py first (pre-registration order)')
    return int(json.loads(p.read_text(encoding='utf-8'))['decision']['shift_days_primary'])


def triple(b1: pd.DataFrame, b5: pd.DataFrame | None, b7: pd.DataFrame,
           mode: str) -> pd.DataFrame:
    """B1-anchored backward-only intersection ([-24h, 0], same direction)."""
    if mode == 'none':
        return intersect_backward(b1, b7)
    if mode == 'long_only':
        longs = intersect_backward(intersect_backward(b1[b1.direction == 'long'], b5), b7)
        shorts = intersect_backward(b1[b1.direction == 'short'], b7)
        return pd.concat([longs, shorts]).sort_values('ts')
    return intersect_backward(intersect_backward(b1, b5), b7)


def replay_pool(trig: pd.DataFrame, df_smc, df_atr, fvgs, obs, delta_df) -> pd.DataFrame:
    rows = []
    for _, t in trig.iterrows():
        r = replay_one(t, df_smc, df_atr, fvgs, obs,
                       atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    if not rows:
        return pd.DataFrame(columns=KEEP)
    rep = pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)
    # no_resist_OB filter only (tilt is applied at scoring, as in the overlay study)
    rep = rep[(rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()].copy()
    ix = delta_df.index.searchsorted(pd.DatetimeIndex(rep['ts']), side='right') - 1
    rep['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i]) if 0 <= i < len(delta_df)
                          else np.nan for i in ix]
    return rep[KEEP].reset_index(drop=True)


def parity(asset: str, v0: pd.DataFrame) -> dict:
    ref = pd.read_csv(PARITY_REF / f'trades_{asset}.csv', parse_dates=['ts'])
    ref = ref[ref.ts <= PARITY_END]
    new = v0[v0.ts <= PARITY_END]

    def keyed(d: pd.DataFrame) -> pd.DataFrame:
        k = d.ts.dt.strftime('%Y-%m-%dT%H:%M') + '|' + d.direction
        return d.assign(k=k.values).set_index('k')

    r, n = keyed(ref), keyed(new)
    common = r.index.intersection(n.index)
    diff = (r.loc[common, 'r_outcome'] - n.loc[common, 'r_outcome']).abs()
    return {
        'ref_n': int(len(r)), 'new_n': int(len(n)), 'common': int(len(common)),
        'only_ref': sorted(set(r.index) - set(n.index))[:10],
        'only_new': sorted(set(n.index) - set(r.index))[:10],
        'max_abs_r_diff': float(diff.max()) if len(common) else None,
        'ok': bool(len(r) == len(n) == len(common)
                   and (len(common) == 0 or float(diff.max()) < 1e-6)),
    }


def gen(asset: str, shift: int) -> bool:
    print(f'=== {asset} (SHIFT_DAYS={shift}) ===')
    df_15m = load_perp_15m(asset)
    print(f'  15m bars: {len(df_15m):,}')
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    lsr = load_lsr_asset(ASSET_CONFIG[asset]['lsr_asset'])
    if shift:
        lsr.index = lsr.index + pd.Timedelta(days=shift)
    print(f'  B1 {len(b1)} · B7 {len(b7)} · LSR rows {len(lsr)}')

    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)
    delta_df = compute_okx_delta_z(derive_binance_1h_close(df_15m),
                                   load_okx_close_asset(asset))

    b5_cache: dict[int, pd.DataFrame] = {}
    summary = {}
    pools = {}
    for name, (window, mode) in VARIANTS.items():
        b5 = None
        if mode != 'none':
            if window not in b5_cache:
                b5_cache[window] = b5_triggers(
                    df_15m, compute_lsr_extremes(lsr, rolling_days=window))
            b5 = b5_cache[window]
        trig = triple(b1, b5, b7, mode)
        pool = replay_pool(trig, df_smc, df_atr, fvgs, obs, delta_df)
        pool.to_csv(OUT / f'trades_{asset}_{name}.csv', index=False)
        pools[name] = pool
        summary[name] = {
            'b5_triggers': None if b5 is None else int(len(b5)),
            'triple_triggers': int(len(trig)), 'trades': int(len(pool)),
            'long': int((pool.direction == 'long').sum()),
            'short': int((pool.direction == 'short').sum()),
        }
        print(f'  {name:11s} B5 {summary[name]["b5_triggers"]!s:>5} · triple {len(trig):4d} '
              f'· trades {len(pool):4d} (L {summary[name]["long"]} / S {summary[name]["short"]})')

    par = parity(asset, pools['V0_sym30'])
    (HERE / 'results' / f'parity_{asset}.json').write_text(
        json.dumps({'shift_days': shift, 'summary': summary, 'parity': par}, indent=2),
        encoding='utf-8')
    print(f'  parity vs results_backonly (ts <= {PARITY_END.date()}): '
          f'{"OK" if par["ok"] else "FAILED"} — ref {par["ref_n"]} / new {par["new_n"]} / '
          f'common {par["common"]} / max |ΔR| {par["max_abs_r_diff"]}')
    return par['ok']


if __name__ == '__main__':
    shift = shift_days()
    ok = all([gen(a, shift) for a in ('BTC', 'ETH')])
    if not ok:
        print('PARITY FAILED — per pre-registration the study stops here until explained.')
        sys.exit(1)
    print('pools written; next: score_variants.py')
