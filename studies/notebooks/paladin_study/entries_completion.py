#!/usr/bin/env python3
"""Completion of the untested/half-tested hypotheses from the pack.

  H1c  round-level anchoring WITH controls, separately for entry / TP1 / stop
  H14  attribution: per-trade alpha = his R minus a passive hold of the same
       symbol over the same window at the same stop distance
  H6f  outcome half of BTC-drives-alts: correlate his alt trade R with BTC's
       move over each holding window vs the alt's BTC-residual move
  H8   weekend seasonality: market-level (BTC forward 24h ret by DOW, 2024->)
       and his weekend-vs-weekday outcomes
  H9   macro events: his signal frequency and half-risk tagging around
       CPI/FOMC/NFP; post-event entry performance
  H15  front-run check: signed 1m drift in the 15/30 min after his BTC/ETH
       posts vs the matched controls

Reads the pack + paladin_ohlcv.db + prod 1m/15m (all read-only).
Writes results/completion_*.csv and prints every verdict.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, HERE)
from paladin_data import load_ohlcv, load_pack  # noqa: E402

OUT = os.path.join(HERE, 'results')
PROD = os.path.join(ROOT, 'data', 'databases', 'prod.db')


def round_dist_pct(p: float) -> float:
    if not np.isfinite(p) or p <= 0:
        return np.nan
    step = 10 ** (np.floor(np.log10(p)) - 2)
    return abs(p - round(p / step) * step) / p * 100


def h1_controls(pos, ctrl):
    bt = pos[pos.is_backtestable]
    print('=== H1c: round-level anchoring vs controls (share within 0.25%) ===')
    rows = {}
    for name, series in (('entry', bt.entry_ref), ('tp1', bt.planned_tp1),
                         ('stop', bt.planned_stop)):
        d = series.dropna().map(round_dist_pct)
        rows[name] = (float((d < 0.25).mean()), len(d))
    # controls: price = last close at control time; synthetic tp/stop at HIS
    # median geometry so distance-to-grid is compared at like-for-like offsets
    med_tp = bt.tp1_dist_pct.median() / 100
    med_stop = bt.risk_dist_pct.median() / 100
    c = ctrl.dropna(subset=['control_price'])
    for name, series in (('entry', c.control_price),
                         ('tp1', c.control_price * (1 + c.side_sign * med_tp)),
                         ('stop', c.control_price * (1 - c.side_sign * med_stop))):
        d = series.map(round_dist_pct)
        his_share, n = rows[name]
        print(f'  {name:>5}: his {his_share:.0%} (n={n})   control {(d < 0.25).mean():.0%}')


def h14_attribution(pos):
    print('\n=== H14: alpha vs passive hold (same window, same stop) ===')
    bt = pos[pos.is_backtestable & pos.r_from_prices.notna()].copy()
    rows = []
    for _, r in bt.iterrows():
        end = r['exit_time_utc'] if pd.notna(r['exit_time_utc']) else \
            r['signal_time_utc'] + pd.Timedelta(hours=72)
        try:
            bars = load_ohlcv(r['symbol'], r['signal_time_utc'], end + pd.Timedelta('15min'), '15m')
        except Exception:
            continue
        if len(bars) < 2:
            continue
        sign, entry, stop = int(r['side_sign']), r['entry_ref'], r['planned_stop']
        risk = abs(entry - stop)
        if not risk:
            continue
        hit = (bars['low'] <= stop) if sign > 0 else (bars['high'] >= stop)
        passive = -1.0 if hit.any() else (bars['close'].iloc[-1] - entry) * sign / risk
        rows.append({'position_id': r['position_id'], 'symbol': r['symbol'],
                     'his_r': r['r_from_prices'], 'passive_r': passive,
                     'alpha_r': r['r_from_prices'] - passive})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'completion_h14_attribution.csv'), index=False)
    print(f'  n={len(df)}  his mean {df.his_r.mean():+.2f}R   passive mean '
          f'{df.passive_r.mean():+.2f}R   alpha mean {df.alpha_r.mean():+.2f}R')
    print(f'  totals: his {df.his_r.sum():+.0f}R = passive {df.passive_r.sum():+.0f}R '
          f'+ alpha {df.alpha_r.sum():+.0f}R   '
          f'(beta share of his R: {df.passive_r.sum() / df.his_r.sum():.0%})')
    print('  NOTE: sample is the 100+ positions where he published enough to compute R '
          '(selection-biased toward wins) — the alpha/beta SPLIT is the finding.')
    return df


def h6_full(pos, h14):
    print('\n=== H6f: does BTC\'s move explain his alt outcomes? ===')
    alts = pos[pos.is_backtestable & pos.r_from_prices.notna()
               & (pos.symbol != 'BTCUSDT')].copy()
    rows = []
    for _, r in alts.iterrows():
        end = r['exit_time_utc'] if pd.notna(r['exit_time_utc']) else \
            r['signal_time_utc'] + pd.Timedelta(hours=72)
        try:
            btc = load_ohlcv('BTCUSDT', r['signal_time_utc'], end + pd.Timedelta('15min'), '15m')
            alt = load_ohlcv(r['symbol'], r['signal_time_utc'], end + pd.Timedelta('15min'), '15m')
        except Exception:
            continue
        if len(btc) < 2 or len(alt) < 2:
            continue
        btc_ret = (btc['close'].iloc[-1] / btc['close'].iloc[0] - 1) * int(r['side_sign'])
        alt_ret = (alt['close'].iloc[-1] / alt['close'].iloc[0] - 1) * int(r['side_sign'])
        rows.append({'r': r['r_from_prices'], 'btc_ret_dir': btc_ret,
                     'resid_ret_dir': alt_ret - btc_ret})
    df = pd.DataFrame(rows)
    print(f'  n={len(df)} alt trades:  corr(R, BTC window move) = '
          f'{df.r.corr(df.btc_ret_dir):+.2f}   corr(R, alt-minus-BTC residual) = '
          f'{df.r.corr(df.resid_ret_dir):+.2f}')


def h8_weekend(pos):
    print('\n=== H8: weekend seasonality ===')
    con = sqlite3.connect(f'file:{PROD}?mode=ro', uri=True)
    d = pd.read_sql("SELECT timestamp, close FROM cd_futures_ohlcv WHERE timestamp >= 1704067200 "
                    "ORDER BY timestamp", con)
    con.close()
    d.index = pd.to_datetime(d.pop('timestamp'), unit='s', utc=True)
    daily = d['close'].resample('1D').last().dropna()
    fwd = daily.pct_change().shift(-1) * 100
    by_dow = fwd.groupby(fwd.index.dayofweek).agg(['mean', 'count'])
    names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    print('  market (BTC next-day ret %, 2024->):',
          '  '.join(f'{names[i]} {by_dow["mean"].get(i, np.nan):+.2f}' for i in range(7)))
    res = pos[pos.outcome.isin(['win', 'loss'])].copy()
    res['is_wkend'] = pd.to_datetime(res.signal_time_utc).dt.dayofweek >= 5
    for tag, g in res.groupby('is_wkend'):
        print(f'  his {"weekend" if tag else "weekday"} trades: n={len(g)}  '
              f'WR {(g.outcome == "win").mean():.0%}')


def h9_events(pos):
    print('\n=== H9: macro events and his behaviour ===')
    con = sqlite3.connect(f'file:{PROD}?mode=ro', uri=True)
    ev = pd.read_sql("SELECT date FROM scheduled_events WHERE event_type IN "
                     "('CPI','FOMC','NFP') AND date BETWEEN '2026-05-01' AND '2026-09-01'", con)
    con.close()
    events = pd.DatetimeIndex(pd.to_datetime(ev['date'])).tz_localize('UTC')
    p = pos[pos.signal_time_utc.notna()].copy()
    ts = pd.to_datetime(p.signal_time_utc)
    nxt = events.values[np.minimum(np.searchsorted(events.values, ts.values), len(events) - 1)]
    p['hrs_to_event'] = (nxt - ts.values) / np.timedelta64(1, 'h')
    p['pre24'] = (p.hrs_to_event >= 0) & (p.hrs_to_event <= 24)
    window_days = (ts.max() - ts.min()).days
    pre_days = len(events) * 1.0
    rate_pre = p.pre24.sum() / pre_days
    rate_all = len(p) / window_days
    print(f'  signal rate in the 24h before an event: {rate_pre:.2f}/day '
          f'vs overall {rate_all:.2f}/day')
    half = p.risk_note.astype(str).str.contains('half|0.5|small|low risk', case=False)
    print(f'  half-risk-tagged: pre-event {half[p.pre24].mean():.0%} '
          f'vs otherwise {half[~p.pre24].mean():.0%}')
    res = p[p.outcome.isin(['win', 'loss'])].copy()
    prev = events.values[np.maximum(np.searchsorted(events.values, ts.loc[res.index].values) - 1, 0)]
    res['hrs_since_event'] = (ts.loc[res.index].values - prev) / np.timedelta64(1, 'h')
    post = res.hrs_since_event <= 24
    print(f'  post-event(24h) WR {(res[post].outcome == "win").mean():.0%} (n={post.sum()}) '
          f'vs other {(res[~post].outcome == "win").mean():.0%} (n={(~post).sum()})')


def h15_frontrun(pos, ctrl):
    print('\n=== H15: does his post move price? (BTC/ETH, 1m) ===')
    bt = pos[pos.is_backtestable & pos.symbol.isin(['BTCUSDT', 'ETHUSDT'])]
    def drift(sym, ts, sign):
        try:
            b = load_ohlcv(sym, ts - pd.Timedelta('35min'), ts + pd.Timedelta('35min'), '1m')
        except Exception:
            return None
        pre = b[b.index < ts]; post = b[b.index >= ts]
        if len(pre) < 20 or len(post) < 30:
            return None
        p0 = pre['close'].iloc[-1]
        return {'pre30': (p0 / pre['close'].iloc[0] - 1) * sign * 1e4,
                'post15': (post['close'].iloc[14] / p0 - 1) * sign * 1e4,
                'post30': (post['close'].iloc[29] / p0 - 1) * sign * 1e4}
    his = [d for _, r in bt.iterrows()
           if (d := drift(r['symbol'], r['signal_time_utc'], int(r['side_sign'])))]
    cc = ctrl[ctrl.symbol.isin(['BTCUSDT', 'ETHUSDT'])]
    ctl = [d for _, r in cc.iterrows()
           if (d := drift(r['symbol'], r['control_time_utc'], int(r['side_sign'])))]
    h, c = pd.DataFrame(his), pd.DataFrame(ctl)
    print(f'  signed drift in bp: n_his={len(h)} n_ctrl={len(c)}')
    for col in ('pre30', 'post15', 'post30'):
        print(f'  {col:>7}: his {h[col].mean():+6.1f}  ctrl {c[col].mean():+6.1f}')


def main():
    os.makedirs(OUT, exist_ok=True)
    d = load_pack()
    pos = d['positions']
    ctrl = pd.read_csv(os.path.join(OUT, 'control_features.csv'),
                       parse_dates=['control_time_utc'])
    # control price = last 15m close before the control time
    prices = []
    cache = {}
    for _, r in ctrl.iterrows():
        sym = r['symbol']
        if sym not in cache:
            try:
                cache[sym] = load_ohlcv(sym, '2026-04-20', '2026-08-22', '15m')
            except Exception:
                cache[sym] = None
        df = cache[sym]
        if df is None:
            prices.append(np.nan); continue
        w = df[df.index + pd.Timedelta('15min') <= r['control_time_utc']]
        prices.append(float(w['close'].iloc[-1]) if len(w) else np.nan)
    ctrl['control_price'] = prices

    h1_controls(pos, ctrl)
    h14 = h14_attribution(pos)
    h6_full(pos, h14)
    h8_weekend(pos)
    h9_events(pos)
    h15_frontrun(pos, ctrl)


if __name__ == '__main__':
    main()
