#!/usr/bin/env python3
"""Test 2 — attribution of the short leg (GATED, see README.md).

Gate: run only if |total R(V0) − total R(V1)| on the short side ≥ 5R on
either asset (production-tilt table). The script checks the gate itself
and exits without running when it is not met.

Pools: V0 shorts, V1 shorts, V0 longs per asset — the production filter
stack (OKX-aligned, up30 short skip, tilt size > 0) as scored by
score_variants.py, re-walked with attribution._walk (stop-first, fixed
target, 72h, gross) exactly as attribution.chento_backonly() does, then
attribution.attribute(): actual = regime (random-time holds) + timing
(same-time hold − regime) + exit (actual − same-time hold).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

from studies.notebooks.attribution.attribution import _walk, attribute  # noqa: E402

OUT = HERE / 'results'
GATE_R = 5.0
SYM = {'BTC': 'BTCUSDT', 'ETH': 'ETHUSDT'}


def gate() -> tuple[bool, dict]:
    df = pd.read_csv(OUT / 'variant_summary.csv')
    prod = df[df.tilt == 'prod'].set_index(['asset', 'variant'])
    deltas = {a: abs(float(prod.loc[(a, 'V0_sym30'), 'total_r_short'])
                     - float(prod.loc[(a, 'V1_long30'), 'total_r_short']))
              for a in ('BTC', 'ETH')}
    return any(d >= GATE_R for d in deltas.values()), deltas


def pool(asset: str, variant: str, direction: str) -> pd.DataFrame:
    scored = pd.read_csv(OUT / 'scored' / f'{asset}_{variant}_prod.csv', parse_dates=['ts'])
    raw = pd.read_csv(OUT / 'pools' / f'trades_{asset}_{variant}.csv', parse_dates=['ts'])
    t = scored[(scored.direction == direction) & (scored['size'] > 0)]
    t = t.merge(raw[['ts', 'direction', 'target']], on=['ts', 'direction'], how='left')
    recs = []
    for r in t.itertuples():
        sign = 1 if r.direction == 'long' else -1
        res = _walk(SYM[asset], r.ts, r.entry, r.stop, r.target, sign)
        if res is None:
            continue
        actual, ts_exit = res
        recs.append({'symbol': SYM[asset], 'side_sign': sign, 'entry': r.entry,
                     'stop': r.stop, 'ts_entry': r.ts, 'ts_exit': ts_exit,
                     'actual_r': actual})
    return pd.DataFrame(recs)


def main() -> int:
    ok, deltas = gate()
    print(f'short-leg |ΔtotalR| V0 vs V1: {deltas} (gate ≥ {GATE_R}R) → '
          f'{"RUN" if ok else "NOT GATED IN — nothing run"}')
    if not ok:
        (OUT / 'attribution.md').write_text(
            f'# Test 2 — attribution\n\nNot gated in: short-leg |total R(V0) − total R(V1)| = '
            f'{deltas} < {GATE_R}R on both assets.\n', encoding='utf-8')
        return 0
    rows = []
    for asset in ('BTC', 'ETH'):
        for variant, direction in (('V0_sym30', 'short'), ('V1_long30', 'short'),
                                   ('V0_sym30', 'long')):
            t = pool(asset, variant, direction)
            label = f'{asset} {variant} {direction}s (n={len(t)})'
            if not len(t):
                print(f'{label}: empty')
                continue
            res = attribute(t, label)
            rows.append({'asset': asset, 'variant': variant, 'direction': direction,
                         'n': len(t), **(res if isinstance(res, dict) else {})})
    pd.DataFrame(rows).to_csv(OUT / 'attribution.csv', index=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
