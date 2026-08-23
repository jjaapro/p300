#!/usr/bin/env python3
"""Rejection-wick exit study — can his stated exit trigger be automated?

His rule, from the corpus (playbook 'Rejection / wick at a level as the manual
exit trigger', msgs 451/462/633/937): no resting TP; when price wicks into or
rejects from a pre-identified level (round number, resistance, his TP zone),
book immediately. Never used to exit at a loss — losses go to the stop.

Pre-registered mechanical translation (long side; shorts mirrored):
  - Levels: round grid (step = 10^(floor(log10(p))-2), i.e. 1000s on BTC)
    plus the published TP1 when it exists.
  - Armed only while unrealized profit at the bar's extreme >= P_min R.
  - Trigger on bar close: the bar's favourable extreme reaches within TOL of a
    level at-or-beyond entry (or trades through it), closes back on the entry
    side of it, and the rejection shadow is >= WICK_FRAC of the bar's range.
  - Exit at that bar's close. Otherwise the original plan runs (stop / TP1 /
    close at the 72h cap) — identical to the H0 baseline.

Grid: bar_tf {5m (BTC/ETH from 1m), 15m (all)} x P_min {0.3, 0.5}
      x WICK_FRAC {0.4, 0.6}. TOL 0.15%.

Scored on the 173 backtestable positions, gross R (H0 baseline is gross too);
the 18bp cost is ~0.05R at his median 4% risk and identical across policies.

Writes results/wick_exit_summary.csv + per-variant trade CSVs.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')
TOL = 0.0015
CAP_H = 72
OOS_SPLIT = pd.Timestamp('2026-07-01', tz='UTC')   # May-Jun vs Jul-Aug


def round_levels(lo: float, hi: float) -> np.ndarray:
    step = 10 ** (np.floor(np.log10(max(hi, 1e-12))) - 2)
    start = np.floor(lo / step) * step
    return np.arange(start, hi + 2 * step, step)


def replay_with_wick_exit(bars: pd.DataFrame, entry: float, stop: float,
                          tp1: float | None, sign: int,
                          p_min: float, wick_frac: float) -> dict:
    risk = abs(entry - stop)
    if not risk:
        return {}
    levels = round_levels(bars['low'].min(), bars['high'].max())
    if tp1 is not None and np.isfinite(tp1):
        levels = np.append(levels, tp1)
    # candidate exit levels sit at-or-beyond entry in the trade direction
    levels = levels[(levels - entry) * sign >= -TOL * entry]

    for ts, b in bars.iterrows():
        hit_stop = (b['low'] <= stop) if sign > 0 else (b['high'] >= stop)
        hit_tp = tp1 is not None and np.isfinite(tp1) and (
            (b['high'] >= tp1) if sign > 0 else (b['low'] <= tp1))
        if hit_stop:            # conservative: stop wins the bar
            return {'exit_reason': 'stop', 'r': -1.0, 'exit_time': ts}
        ext = b['high'] if sign > 0 else b['low']
        unreal_r = (ext - entry) * sign / risk
        rng = b['high'] - b['low']
        if unreal_r >= p_min and rng > 0:
            shadow = (b['high'] - max(b['open'], b['close']) if sign > 0
                      else min(b['open'], b['close']) - b['low'])
            near = levels[np.abs(ext - levels) <= TOL * entry]
            through = levels[((ext - levels) * sign >= 0)
                             & ((b['close'] - levels) * sign < 0)]
            lvl_hit = len(near) > 0 or len(through) > 0
            if lvl_hit and shadow >= wick_frac * rng:
                r = (b['close'] - entry) * sign / risk
                return {'exit_reason': 'wick', 'r': round(r, 3), 'exit_time': ts}
        if hit_tp:
            return {'exit_reason': 'target',
                    'r': round((tp1 - entry) * sign / risk, 3), 'exit_time': ts}
    last = bars.iloc[-1]
    return {'exit_reason': 'timeout',
            'r': round((last['close'] - entry) * sign / risk, 3),
            'exit_time': bars.index[-1]}


def stop_dodged(bars: pd.DataFrame, exit_time, stop: float, sign: int) -> bool:
    after = bars[bars.index > exit_time]
    if not len(after):
        return False
    return bool(((after['low'] <= stop) if sign > 0
                 else (after['high'] >= stop)).any())


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    pos = load_pack()['positions']
    bt = pos[pos.is_backtestable].copy()
    print(f'{len(bt)} positions')

    rows = []
    for tf in ('5m', '15m'):
        for p_min in (0.3, 0.5):
            for wf in (0.4, 0.6):
                trades = []
                for _, r in bt.iterrows():
                    start = r['signal_time_utc']
                    use_1m = tf == '5m' and r['symbol'] in ('BTCUSDT', 'ETHUSDT')
                    try:
                        if use_1m:
                            m1 = load_ohlcv(r['symbol'], start,
                                            start + pd.Timedelta(hours=CAP_H), '1m')
                            bars = m1.resample('5min', label='left', closed='left').agg(
                                {'open': 'first', 'high': 'max', 'low': 'min',
                                 'close': 'last', 'volume': 'sum'}).dropna(subset=['open'])
                        else:
                            bars = load_ohlcv(r['symbol'], start,
                                              start + pd.Timedelta(hours=CAP_H), '15m')
                    except Exception:
                        continue
                    if len(bars) < 2:
                        continue
                    res = replay_with_wick_exit(
                        bars, r['entry_ref'], r['planned_stop'], r['planned_tp1'],
                        int(r['side_sign']), p_min, wf)
                    if not res:
                        continue
                    res['dodged'] = (res['exit_reason'] == 'wick'
                                     and stop_dodged(bars, res['exit_time'],
                                                     r['planned_stop'], int(r['side_sign'])))
                    res.update({'position_id': r['position_id'],
                                'signal_time': start, 'his_r': r['r_from_prices']})
                    trades.append(res)
                t = pd.DataFrame(trades)
                label = f'{tf}_p{p_min}_w{wf}'
                t.to_csv(os.path.join(OUT, f'wick_exit_{label}.csv'), index=False)
                for split, part in (('IS', t[t.signal_time < OOS_SPLIT]),
                                    ('OOS', t[t.signal_time >= OOS_SPLIT]),
                                    ('ALL', t)):
                    if not len(part):
                        continue
                    rows.append({
                        'variant': label, 'split': split, 'n': len(part),
                        'mean_r': part.r.mean(), 'wr_pct': (part.r > 0).mean() * 100,
                        'total_r': part.r.sum(),
                        'wick_exits': (part.exit_reason == 'wick').sum(),
                        'stops': (part.exit_reason == 'stop').sum(),
                        'stop_dodges': part.dodged.sum()})
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, 'wick_exit_summary.csv'), index=False)
    print(res[res.split == 'ALL'].round(3).to_string(index=False))
    print('\nbaselines (same 173 positions, gross): mechanical 72h +0.003R/trade;'
          ' his recorded exits +1.62R on the self-selected 108')
    return 0


if __name__ == '__main__':
    sys.exit(main())
