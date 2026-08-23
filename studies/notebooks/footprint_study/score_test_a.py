#!/usr/bin/env python3
"""Test A scoring: does sweep-bar footprint imbalance separate winners?

Pre-registered flags (README.md — no additions after seeing data):
  C1  event-bar top-25%-of-range net delta < 0
  C2  event-bar top-zone sell share > 0.60
  C3  C1 on event bar AND prior bar combined

For each flag × target (1R / 1.5R): net expectancy of confirmed vs
unconfirmed, both halves of the window (split 2025-09-01 like the scanner
study). SUCCESS: confirmed net > 0 AND confirmed − unconfirmed ≥ +0.05R,
both halves. Anything else: kill.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')
SPLIT = pd.Timestamp('2025-09-01', tz='UTC')


def main() -> int:
    ev = pd.read_csv(os.path.join(OUT, 'events.csv'), parse_dates=['event_ts', 'time'])
    fp = pd.read_parquet(os.path.join(OUT, 'footprints.parquet'))
    fp['event_ts'] = pd.to_datetime(fp.event_ts, utc=True)
    df = ev.merge(fp, on=['symbol', 'event_ts'], how='inner')
    print(f'events with footprints: {df[df.target_r == 1.0].shape[0]} '
          f'of {ev[ev.target_r == 1.0].shape[0]}')

    df['C1'] = df.e_top_delta < 0
    df['C2'] = df.e_top_sell_share > 0.60
    df['C3'] = df.C1 & (df.get('p_top_delta', pd.Series(index=df.index)) < 0)

    rows = []
    for tgt in (1.0, 1.5):
        sub = df[df.target_r == tgt]
        for flag in ('C1', 'C2', 'C3'):
            for half, part in (('H1', sub[sub.time < SPLIT]),
                               ('H2', sub[sub.time >= SPLIT]),
                               ('ALL', sub)):
                c, u = part[part[flag]], part[~part[flag]]
                rows.append({
                    'target': tgt, 'flag': flag, 'half': half,
                    'n_conf': len(c), 'n_unconf': len(u),
                    'conf_net': c.net_r.mean() if len(c) else float('nan'),
                    'unconf_net': u.net_r.mean() if len(u) else float('nan'),
                    'conf_gross': c.gross_r.mean() if len(c) else float('nan'),
                    'edge': (c.net_r.mean() - u.net_r.mean())
                            if len(c) and len(u) else float('nan')})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, 'test_a_results.csv'), index=False)
    print(res.round(3).to_string(index=False))

    print('\nverdict per flag (success = conf_net>0 AND edge>=+0.05 in BOTH halves):')
    for tgt in (1.0, 1.5):
        for flag in ('C1', 'C2', 'C3'):
            h = res[(res.target == tgt) & (res.flag == flag) & (res.half != 'ALL')]
            ok = bool(((h.conf_net > 0) & (h.edge >= 0.05)).all()) and len(h) == 2
            print(f'  target {tgt} {flag}: {"SURVIVES" if ok else "kill"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
