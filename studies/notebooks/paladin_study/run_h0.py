#!/usr/bin/env python3
"""H0 — the decider: trade his published plan mechanically and compare it to
what he actually did. Uses the pack's own replay_plan/excursions so the
mechanics match the spec exactly; only load_ohlcv is ours.

Variants: time limits 24h/72h/168h/700h, each with and without move-stop-to-BE
once +1R trades (his stated rule). 15m bars, conservative ambiguity (stop wins
when a bar spans both).

Writes results/h0_replay_<variant>.csv and results/h0_summary.csv.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.join(HERE, '..', '..', 'material', 'paladin', 'analysis')
sys.path.insert(0, PACK)
from load import excursions, replay_plan  # noqa: E402  (the pack's reference impl)

from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')


def run(pos: pd.DataFrame, hours: int, be_at_r: float | None,
        fill: str = 'plan') -> pd.DataFrame:
    """fill='plan' enters at his stated price (his accounting); fill='market'
    enters at the first bar open after the signal (what a follower gets —
    his stated entry price is often minutes-to-hours stale, see venue_offset)."""
    rows = []
    for _, r in pos.iterrows():
        start = r['signal_time_utc']
        try:
            bars = load_ohlcv(r['symbol'], start, start + pd.Timedelta(hours=hours), '15m')
        except Exception as exc:  # noqa: BLE001
            rows.append({'position_id': r['position_id'], 'error': str(exc)})
            continue
        if bars is None or len(bars) < 2:
            rows.append({'position_id': r['position_id'], 'error': 'no bars'})
            continue
        entry = r['entry_ref'] if fill == 'plan' else float(bars.iloc[0]['open'])
        if fill == 'market' and (entry - r['planned_stop']) * r['side_sign'] <= 0:
            rows.append({'position_id': r['position_id'], 'error': 'market fill beyond stop'})
            continue
        res = replay_plan(bars, entry, r['planned_stop'], r['planned_tp1'],
                          int(r['side_sign']), be_at_r)
        res.update(excursions(bars, entry, r['planned_stop'], int(r['side_sign'])))
        res.update({'position_id': r['position_id'], 'symbol': r['symbol'],
                    'side': r['side'], 'is_btc': r['symbol'] == 'BTCUSDT',
                    'has_tp': pd.notna(r['planned_tp1']),
                    'his_outcome': r['outcome'], 'his_r': r['r_from_prices'],
                    'his_exit_type': r['exit_type'], 'confidence_rank': r['confidence_rank']})
        rows.append(res)
    return pd.DataFrame(rows)


def summarize(name: str, rep: pd.DataFrame) -> dict:
    ok = rep.dropna(subset=['r'])
    d = {'variant': name, 'n': len(ok),
         'expectancy_r': ok['r'].mean(), 'median_r': ok['r'].median(),
         'hit_rate_pct': (ok['r'] > 0).mean() * 100,
         'total_r': ok['r'].sum()}
    for reason, cnt in ok['exit_reason'].value_counts().items():
        d[f'end_{reason}'] = cnt
    his = ok.dropna(subset=['his_r'])
    if len(his):
        d['his_expectancy_r'] = his['his_r'].mean()
        d['his_hit_rate_pct'] = (his['his_r'] > 0).mean() * 100
        d['n_with_his_r'] = len(his)
    return d


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pos = load_pack()['positions']
    bt = pos[pos.is_backtestable].copy()
    print(f'{len(bt)} backtestable positions')

    summaries = []
    for hours in (24, 72, 168, 700):
        for be, fill in ((None, 'plan'), (1.0, 'plan'), (None, 'market')):
            name = f'{hours}h' + ('_be1r' if be else '') + ('_mktfill' if fill == 'market' else '')
            rep = run(bt, hours, be, fill)
            rep.to_csv(os.path.join(OUT, f'h0_replay_{name}.csv'), index=False)
            s = summarize(name, rep)
            summaries.append(s)
            print(f"{name:>12}: n={s['n']:3d}  exp {s['expectancy_r']:+.3f}R  "
                  f"hit {s['hit_rate_pct']:.0f}%  total {s['total_r']:+.1f}R  "
                  f"ends {rep.dropna(subset=['r'])['exit_reason'].value_counts().to_dict()}")
    pd.DataFrame(summaries).to_csv(os.path.join(OUT, 'h0_summary.csv'), index=False)
    print('\nwrote results/h0_summary.csv')


if __name__ == '__main__':
    main()
