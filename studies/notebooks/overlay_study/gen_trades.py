#!/usr/bin/env python3
"""Regenerate the chento Triple-composite trade lists for BTC and ETH with
per-trade fields persisted (the 2026-05-26 multi-asset validation only saved
aggregates). Mirrors validation_multi_asset.run_asset with two deliberate
differences:

  - the no_tilt filter is NOT applied (tilt/throttle policies are tested as
    overlays downstream; the no_resist_OB filter IS applied)
  - the per-trade frame is saved: results/trades_{asset}.csv

Read-only against prod.db. OP is excluded: no LSR rows -> degraded composite,
not the same strategy.
"""
from __future__ import annotations

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
from studies.notebooks.chento_journal.validation_B_composite import intersect_triggers  # noqa: E402
from studies.notebooks.chento_journal.validation_C5_smc_features import (  # noqa: E402
    compute_fvgs, compute_order_blocks, compute_pivots, compute_smc_state,
    replay_one,
)

OUT = HERE / 'results'
OUT.mkdir(exist_ok=True)


def gen(asset: str) -> None:
    print(f'=== {asset} ===')
    df_15m = load_perp_15m(asset)
    print(f'  15m bars: {len(df_15m):,}')
    df_b1 = compute_moneyflow_signal(df_15m)
    df_b1['atr'] = compute_atr(df_b1, period=14)
    b1 = b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
    lsr_z = compute_lsr_extremes(load_lsr_asset(ASSET_CONFIG[asset]['lsr_asset']))
    b5 = b5_triggers(df_15m, lsr_z)
    b7 = b7_alignment_triggers(compute_multitf_cvd(df_15m), z_threshold=2.0)
    triple = intersect_triggers(intersect_triggers(b1, b5), b7)
    print(f'  triple triggers: {len(triple)}')

    df_p = compute_pivots(df_15m, n=5)
    df_smc = compute_smc_state(df_p, n=5)
    obs = compute_order_blocks(df_smc)
    fvgs = compute_fvgs(df_smc)
    df_atr = df_smc.copy()
    df_atr['atr'] = compute_atr(df_atr, period=14)

    rows = []
    for _, t in triple.iterrows():
        r = replay_one(t, df_smc, df_atr, fvgs, obs,
                       atr_mult=5.0, target_r=6.0, tp_mode='fixed')
        if r is not None:
            rows.append(r)
    rep = pd.DataFrame(rows).sort_values('ts').reset_index(drop=True)
    print(f'  replayed: {len(rep)}')

    # no_resist_OB filter only (no_tilt left for the overlay engine)
    rep = rep[(rep['dist_resist_OB_R'] > 2.0) | rep['dist_resist_OB_R'].isna()].copy()

    okx_close = load_okx_close_asset(asset)
    delta_df = compute_okx_delta_z(derive_binance_1h_close(df_15m), okx_close)
    ts_idx = pd.DatetimeIndex(rep['ts'])
    ix = delta_df.index.searchsorted(ts_idx, side='right') - 1
    rep['okx_delta_z'] = [float(delta_df['delta_z'].iloc[i]) if 0 <= i < len(delta_df)
                          else np.nan for i in ix]

    keep = ['ts', 'direction', 'entry', 'stop', 'target', 'target_R', 'risk',
            'r_outcome', 'exit_kind', 'dist_resist_OB_R', 'okx_delta_z']
    rep[keep].to_csv(OUT / f'trades_{asset}.csv', index=False)
    print(f'  wrote {len(rep)} trades (no_resist applied, no_tilt NOT applied)')


if __name__ == '__main__':
    for a in ('BTC', 'ETH'):
        gen(a)
