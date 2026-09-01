#!/usr/bin/env python3
"""Test 1 scoring — the production filter stack and the pre-registered
metrics per variant (README.md). Reads results/pools/*.csv; writes
results/variant_summary.csv and results/variant_summary.md.

Filter stack at scoring (as production applies it after the trigger):
  - OKX alignment: long needs okx_delta_z >= 0, short needs <= 0
    (overlay_study/run_overlays.py:164-166)
  - regime, asymmetric: skip SHORTS when BTC's trailing 30d return > +10 %
    (chento_triple_v3 filter 4; 30d = 30 × 96 fifteen-minute bars of the
    BTC perp table, the definition run_overlays uses for its H tag)
  - tilt: BTC skip-after-loss, ETH half-after-loss (production), plus a
    secondary tilt=none table (pool level, no sequence effect)
Metrics come from run_overlays.metrics (total R, maxDD, MAR-like, WR,
IS/OOS totals at IS_END = 2024-12-31) plus the long/short and IS/OOS mean-R
splits the pre-registration asks for.
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

from studies.notebooks.overlay_study.run_overlays import (  # noqa: E402
    IS_END, load_bars, metrics, tilt_sizes,
)

POOLS = HERE / 'results' / 'pools'
OUT = HERE / 'results'
VARIANTS = ['V0_sym30', 'V1_long30', 'V2_noB5', 'V3_sym90', 'V4_sym365', 'V5_long365']
PROD_TILT = {'BTC': 'skip_after_loss', 'ETH': 'half_after_loss'}
UP30_SKIP = 0.10
# pre-registered adoption thresholds
OOS_MEAN_R_LIFT = 0.10
MAR_MULT = 1.1
OOS_N_MIN = 20
IS_MEAN_R_TOL = 0.05
SHORT_OOS_N_MIN = 10


def apply_filters(t: pd.DataFrame, btc30: pd.Series) -> tuple[pd.DataFrame, dict]:
    aligned = (((t.direction == 'long') & (t.okx_delta_z >= 0))
               | ((t.direction == 'short') & (t.okx_delta_z <= 0)))
    t = t[aligned].reset_index(drop=True)
    i = btc30.index.searchsorted(pd.DatetimeIndex(t.ts)) - 1
    r30 = np.array([btc30.iloc[j] if j >= 0 else np.nan for j in i])
    skip = (t.direction.values == 'short') & (r30 > UP30_SKIP)
    return t[~skip].reset_index(drop=True), {'okx_dropped': int((~aligned).sum()),
                                             'up30_short_skipped': int(skip.sum())}


def score(t: pd.DataFrame, asset: str, name: str, tilt: str, tag: str) -> dict:
    rs = t.r_outcome.values.astype(float)
    keep = np.isfinite(rs)
    t, rs = t[keep].reset_index(drop=True), rs[keep]
    ts = pd.DatetimeIndex(t.ts)
    sizes = tilt_sizes(rs, ts, tilt)
    m = metrics(rs, sizes, ts, f'{asset}|{name}|tilt={tilt}')
    eff = rs * sizes
    # per-trade effective R for the figures / attribution step
    (OUT / 'scored').mkdir(exist_ok=True)
    pd.DataFrame({'ts': ts, 'direction': t.direction.values, 'entry': t.entry.values,
                  'stop': t.stop.values, 'r': rs, 'size': sizes, 'eff_r': eff}).to_csv(
        OUT / 'scored' / f'{asset}_{name}_{tag}.csv', index=False)
    traded = sizes > 0
    is_m = np.asarray(ts <= IS_END)
    d = t.direction.values
    out = {**m, 'asset': asset, 'variant': name, 'tilt': tag, 'tilt_policy': tilt}   # after **m: metrics() also emits 'variant'
    out['mar'] = None if not np.isfinite(m['mar_like']) else m['mar_like']
    for side in ('long', 'short'):
        mk = (d == side) & traded
        out[f'n_{side}'] = int(mk.sum())
        out[f'mean_r_{side}'] = float(eff[mk].mean()) if mk.any() else np.nan
        out[f'total_r_{side}'] = float(eff[mk].sum())
        oo = mk & ~is_m
        out[f'oos_n_{side}'] = int(oo.sum())
        out[f'oos_mean_r_{side}'] = float(eff[oo].mean()) if oo.any() else np.nan
        out[f'oos_total_r_{side}'] = float(eff[oo].sum())
        ii = mk & is_m
        out[f'is_n_{side}'] = int(ii.sum())
        out[f'is_total_r_{side}'] = float(eff[ii].sum())
    for tag, mask in (('is', is_m & traded), ('oos', ~is_m & traded)):
        out[f'{tag}_n'] = int(mask.sum())
        out[f'{tag}_mean_r'] = float(eff[mask].mean()) if mask.any() else np.nan
    return out


def adoption(rows: pd.DataFrame) -> list[dict]:
    """The pre-registered rule, per variant, on the production-tilt table."""
    prod = rows[rows.tilt == 'prod'].set_index(['asset', 'variant'])
    verdicts = []
    for v in VARIANTS[1:]:
        checks = {}
        for a in ('BTC', 'ETH'):
            v0, vx = prod.loc[(a, 'V0_sym30')], prod.loc[(a, v)]
            checks[a] = {
                'oos_mean_r': bool(vx.oos_mean_r >= v0.oos_mean_r + OOS_MEAN_R_LIFT),
                'mar': bool(v0.mar is not None and vx.mar is not None and vx.mar >= MAR_MULT * v0.mar),
                'oos_n': bool(vx.oos_n >= OOS_N_MIN),
                'is_mean_r': bool(vx.is_mean_r >= v0.is_mean_r - IS_MEAN_R_TOL),
            }
        ok = all(all(c.values()) for c in checks.values())
        verdicts.append({'variant': v, 'adopt': ok, 'checks': checks})
    return verdicts


def fmt(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return '—'
    return f'{x:+.{nd}f}' if isinstance(x, float) else str(x)


def main() -> int:
    btc30 = load_bars('BTC')['close'].pct_change(30 * 96)
    rows, filt = [], {}
    for asset in ('BTC', 'ETH'):
        for name in VARIANTS:
            t = pd.read_csv(POOLS / f'trades_{asset}_{name}.csv', parse_dates=['ts'])
            t, f = apply_filters(t, btc30)
            filt[f'{asset}/{name}'] = {**f, 'after_filters': int(len(t))}
            rows.append(score(t, asset, name, PROD_TILT[asset], 'prod'))
            rows.append(score(t, asset, name, 'none', 'none'))
    df = pd.DataFrame(rows)
    df.to_csv(OUT / 'variant_summary.csv', index=False)
    verdicts = adoption(df)

    md = ['# Test 1 — B5 variant ablation (backward-only Triple pool)', '',
          f'IS ≤ {IS_END.date()} · OOS after · production tilt = BTC skip-after-loss, '
          f'ETH half-after-loss · filters: OKX-aligned, shorts skipped when BTC 30d > +10 %', '']
    for tilt in ('prod', 'none'):
        md += [f'## tilt = {tilt}', '',
               '| asset | variant | n | L/S | mean R | total R | maxDD R | MAR | WR % | '
               'IS mean R (n) | OOS mean R (n) | short: OOS n / mean R / total R |',
               '|---|---|---|---|---|---|---|---|---|---|---|---|']
        for r in df[df.tilt == tilt].itertuples():
            md.append(f'| {r.asset} | {r.variant} | {r.traded_n} | {r.n_long}/{r.n_short} | '
                      f'{fmt(r.mean_r, 3)} | {fmt(r.total_r, 1)} | {fmt(r.max_dd_r, 1)} | '
                      f'{fmt(r.mar, 2)} | {r.wr_pct:.0f} | {fmt(r.is_mean_r, 3)} ({r.is_n}) | '
                      f'{fmt(r.oos_mean_r, 3)} ({r.oos_n}) | {r.oos_n_short} / '
                      f'{fmt(r.oos_mean_r_short, 3)} / {fmt(r.oos_total_r_short, 1)} |')
        md.append('')
    md += ['## Pre-registered adoption rule (production tilt, both assets)', '',
           f'OOS mean R ≥ V0 + {OOS_MEAN_R_LIFT}R AND MAR ≥ {MAR_MULT}×V0 AND OOS n ≥ {OOS_N_MIN} '
           f'AND IS mean R ≥ V0 − {IS_MEAN_R_TOL}R', '']
    for v in verdicts:
        fails = [f'{a}:{",".join(k for k, ok in c.items() if not ok)}'
                 for a, c in v['checks'].items() if not all(c.values())]
        md.append(f'- {v["variant"]}: **{"ADOPT" if v["adopt"] else "KILL"}**'
                  + (f' — fails {"; ".join(fails)}' if fails else ''))
    md += ['', '## Short-leg statement (V0, production tilt)', '']
    for r in df[(df.tilt == 'prod') & (df.variant == 'V0_sym30')].itertuples():
        verdict = ('insufficient evidence (OOS n < %d) — no production change may be '
                   'proposed from this study' % SHORT_OOS_N_MIN
                   if r.oos_n_short < SHORT_OOS_N_MIN else 'evaluable')
        md.append(f'- {r.asset}: IS n {r.is_n_short} total {fmt(r.is_total_r_short, 1)}R · '
                  f'OOS n {r.oos_n_short} mean {fmt(r.oos_mean_r_short, 3)}R total '
                  f'{fmt(r.oos_total_r_short, 1)}R → **{verdict}**')
    md += ['', '## Filter counts', '', '```', json.dumps(filt, indent=1), '```', '']
    (OUT / 'variant_summary.md').write_text('\n'.join(md), encoding='utf-8')
    print('\n'.join(md))
    return 0


if __name__ == '__main__':
    sys.exit(main())
