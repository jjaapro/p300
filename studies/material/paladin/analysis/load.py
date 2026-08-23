#!/usr/bin/env python3
"""Reference loader + the two joins that matter, for the Paladin analysis pack.

Drop your OHLCV in beside this and fill in `load_ohlcv`. Everything else runs.

    python load.py            # sanity report on the pack alone
    python load.py --replay   # mechanical-plan replay, needs load_ohlcv implemented
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------- load --
def load_pack(fmt: str = 'parquet') -> dict[str, pd.DataFrame]:
    """Load every table. Parquet keeps dtypes; CSV is the portable fallback."""
    names = ['positions', 'actions', 'events', 'price_observations', 'unresolved_positions']
    out = {}
    for n in names:
        path = os.path.join(HERE, f'{n}.{fmt}')
        df = pd.read_parquet(path) if fmt == 'parquet' else pd.read_csv(path)
        for col in df.columns:
            if col.endswith('_utc'):
                df[col] = pd.to_datetime(df[col], utc=True, errors='coerce')
        out[n] = df
    return out


def load_ohlcv(symbol: str, start, end, interval: str = '1h') -> pd.DataFrame:
    """YOUR DATA GOES HERE.

    Must return a DataFrame indexed by a tz-aware UTC DatetimeIndex with columns
    open, high, low, close, volume — bars fully inside [start, end], ascending.

    `symbol` arrives as the pair used in the channel (BTCUSDT, ZECUSDT, ...).
    Map it to your own convention here. asset_class == 'crypto' rows only;
    GOLD / OIL positions will ask for XAUUSD / WTIUSD.
    """
    raise NotImplementedError('point load_ohlcv at your historical data')


# ------------------------------------------------------- feature primitives --
def add_signal_features(pos: pd.DataFrame) -> pd.DataFrame:
    """Cheap features that need no price data — a starting point for H1, H8, H10."""
    p = pos.copy()
    p['round_dist_pct'] = np.nan
    for i, r in p.iterrows():
        e = r['entry_ref']
        if pd.isna(e) or e <= 0:
            continue
        # nearest "round" level, at the granularity a human would actually quote:
        # two significant figures below the leading digit (1000 for 76k, 0.01 for 0.6)
        step = 10 ** (np.floor(np.log10(e)) - 2)
        p.at[i, 'round_dist_pct'] = abs(e - round(e / step) * step) / e * 100
    p['is_weekend'] = p['signal_dow'].isin(['Sat', 'Sun'])
    p['stop_pct_bucket'] = pd.cut(p['risk_dist_pct'], [0, 1.5, 3, 5, 8, 100],
                                  labels=['<1.5%', '1.5-3%', '3-5%', '5-8%', '>8%'])
    return p


def excursions(bars: pd.DataFrame, entry: float, stop: float, sign: int) -> dict:
    """MFE / MAE in R units over the supplied bars. Feeds H11."""
    risk = abs(entry - stop)
    if not risk:
        return {}
    fav = ((bars['high'] - entry) if sign > 0 else (entry - bars['low'])) / risk
    adv = ((bars['low'] - entry) if sign > 0 else (entry - bars['high'])) / risk
    return {'mfe_r': float(fav.max()), 'mae_r': float(adv.min()),
            'mfe_time_utc': fav.idxmax(), 'mae_time_utc': adv.idxmin()}


def replay_plan(bars: pd.DataFrame, entry: float, stop: float, target: float | None,
                sign: int, move_stop_to_be_at_r: float | None = None) -> dict:
    """Trade the published plan mechanically, bar by bar. Feeds H0 and H12.

    Conservative on ambiguity: if a bar's range covers both stop and target, the
    stop is taken. Returns the realised R and what ended it.
    """
    risk = abs(entry - stop)
    if not risk:
        return {'exit_reason': 'no_risk'}
    cur_stop = stop
    moved = False
    for ts, b in bars.iterrows():
        hit_stop = (b['low'] <= cur_stop) if sign > 0 else (b['high'] >= cur_stop)
        hit_tp = target is not None and ((b['high'] >= target) if sign > 0 else (b['low'] <= target))
        if hit_stop:
            return {'exit_reason': 'breakeven' if moved and cur_stop == entry else 'stop',
                    'exit_price': cur_stop, 'exit_time_utc': ts,
                    'r': round((cur_stop - entry) * sign / risk, 3)}
        if hit_tp:
            return {'exit_reason': 'target', 'exit_price': target, 'exit_time_utc': ts,
                    'r': round((target - entry) * sign / risk, 3)}
        if move_stop_to_be_at_r is not None and not moved:
            reached = ((b['high'] - entry) if sign > 0 else (entry - b['low'])) / risk
            if reached >= move_stop_to_be_at_r:
                cur_stop, moved = entry, True
    last = bars.iloc[-1]['close']
    return {'exit_reason': 'timeout', 'exit_price': float(last), 'exit_time_utc': bars.index[-1],
            'r': round((last - entry) * sign / risk, 3)}


def run_replay(pos: pd.DataFrame, hours: int = 168, interval: str = '1h',
               move_stop_to_be_at_r: float | None = None) -> pd.DataFrame:
    """H0: replay every backtestable position's published plan."""
    rows = []
    bt = pos[pos.is_backtestable].copy()
    for _, r in bt.iterrows():
        start = r['signal_time_utc']
        try:
            bars = load_ohlcv(r['symbol'], start, start + pd.Timedelta(hours=hours), interval)
        except Exception as exc:                                  # noqa: BLE001
            rows.append({'position_id': r['position_id'], 'error': str(exc)})
            continue
        if bars is None or not len(bars):
            rows.append({'position_id': r['position_id'], 'error': 'no bars'})
            continue
        res = replay_plan(bars, r['entry_ref'], r['planned_stop'], r['planned_tp1'],
                          int(r['side_sign']), move_stop_to_be_at_r)
        res.update(excursions(bars, r['entry_ref'], r['planned_stop'], int(r['side_sign'])))
        res.update({'position_id': r['position_id'], 'symbol': r['symbol'], 'side': r['side'],
                    'his_outcome': r['outcome'], 'his_r': r['r_from_prices'],
                    'his_exit_type': r['exit_type']})
        rows.append(res)
    return pd.DataFrame(rows)


def compare(replay: pd.DataFrame) -> None:
    """The headline comparison: mechanical plan against his own execution."""
    ok = replay.dropna(subset=['r'])
    print(f'\nmechanical replay: n={len(ok)}')
    print(f"  expectancy      {ok['r'].mean():+.3f}R    median {ok['r'].median():+.3f}R")
    print(f"  hit rate        {(ok['r'] > 0).mean() * 100:.1f}%")
    print('  ended by:', ok['exit_reason'].value_counts().to_dict())
    his = ok.dropna(subset=['his_r'])
    if len(his):
        print(f"\nhis own exits on the same {len(his)} positions:")
        print(f"  expectancy      {his['his_r'].mean():+.3f}R    median {his['his_r'].median():+.3f}R")
        print(f"  hit rate        {(his['his_r'] > 0).mean() * 100:.1f}%")
        print(f"\n  edge left on the table: mean MFE {his['mfe_r'].mean():+.2f}R "
              f"vs his realised {his['his_r'].mean():+.2f}R")
        print(f"  worst drawdown inside winners: mean MAE {his[his.his_r > 0]['mae_r'].mean():+.2f}R")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--replay', action='store_true')
    ap.add_argument('--fmt', default='parquet', choices=['parquet', 'csv'])
    ap.add_argument('--hours', type=int, default=168)
    a = ap.parse_args()

    d = load_pack(a.fmt)
    pos, act = d['positions'], d['actions']
    print(f"positions {len(pos)}  ({int(pos.is_backtestable.sum())} backtestable)")
    print(f"actions   {len(act)}  across {act.action.nunique()} action types")
    print(f"window    {pos.signal_time_utc.min()}  ->  {pos.signal_time_utc.max()}  (UTC)")
    print(f"\noutcomes:\n{pos.outcome.value_counts().to_string()}")
    print(f"\nmedian published stop distance: {pos.risk_dist_pct.median():.2f}%   "
          f"median published R:R: {pos.planned_rr.median():.2f}")
    feats = add_signal_features(pos)
    bt = feats[feats.is_backtestable]
    print(f"\nH1 round-number distance at entry (backtestable): "
          f"median {bt.round_dist_pct.median():.2f}%  |  share within 0.25%: "
          f"{(bt.round_dist_pct < 0.25).mean() * 100:.0f}%")
    print(f"H8 weekend share of signals: {bt.is_weekend.mean() * 100:.0f}%")

    if a.replay:
        rep = run_replay(pos, hours=a.hours)
        rep.to_csv(os.path.join(HERE, 'replay_results.csv'), index=False)
        compare(rep)
        print('\nwrote replay_results.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
