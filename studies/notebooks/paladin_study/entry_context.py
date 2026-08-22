#!/usr/bin/env python3
"""What does the market look like at the moment he posts an entry?

Computes, for every backtestable position and for a matched control set
(same symbol, same time-of-day, random dates in the same window):

  H2  sweep      — did the last 6h print a new 1/3/7-day low (longs) / high (shorts)?
  H3  4h close   — minutes since the last UTC 4h boundary
  H4  EMA200     — (entry - 4h EMA200) / 4h ATR14, same for the stop
  H5  stop size  — correlation of his published stop % with 1h ATR14 %
  H10 range pos  — where in the UTC day's range so far the entry sits
  H6/H7 BTC ctx  — BTC 4h/24h return at signal; alt-vs-BTC relative strength 24h
  PDH/PDL        — distance from entry to the prior UTC day's low and high

Every feature uses only bars that closed before the signal. Controls get
entry = last 15m close before the sampled time, and a synthetic stop at the
position's own risk_dist_pct so stop-relative features stay comparable.

Writes results/entry_features.csv (his trades) and results/control_features.csv.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from paladin_data import load_ohlcv, load_pack

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'results')
WINDOW = (pd.Timestamp('2026-05-06', tz='UTC'), pd.Timestamp('2026-08-21', tz='UTC'))
N_CONTROLS = 40
RNG = np.random.default_rng(300)


def prep_symbol(sym: str) -> dict | None:
    """Load once per symbol; precompute 1h/4h frames with ATR + EMA."""
    try:
        m15 = load_ohlcv(sym, '2026-03-01', '2026-08-22', '15m')
    except Exception:
        return None
    if len(m15) < 500:
        return None
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    h1 = m15.resample('1h', label='left', closed='left').agg(agg).dropna(subset=['open'])
    h4 = m15.resample('4h', label='left', closed='left').agg(agg).dropna(subset=['open'])

    def atr(df, n=14):
        tr = pd.concat([df['high'] - df['low'],
                        (df['high'] - df['close'].shift()).abs(),
                        (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / n, adjust=False).mean()

    h1 = h1.assign(atr14=atr(h1))
    h4 = h4.assign(atr14=atr(h4), ema200=h4['close'].ewm(span=200, adjust=False).mean())
    d1 = m15.resample('1D', label='left', closed='left').agg(agg).dropna(subset=['open'])
    return {'m15': m15, 'h1': h1, 'h4': h4, 'd1': d1}


def last_closed(df: pd.DataFrame, ts: pd.Timestamp, bar: pd.Timedelta) -> pd.DataFrame:
    """Bars whose close time is <= ts (strictly no lookahead)."""
    return df[df.index + bar <= ts]


def features_at(data: dict, ts: pd.Timestamp, entry: float, stop: float | None,
                side_sign: int, btc: dict | None) -> dict | None:
    m15 = last_closed(data['m15'], ts, pd.Timedelta('15min'))
    h1 = last_closed(data['h1'], ts, pd.Timedelta('1h'))
    h4 = last_closed(data['h4'], ts, pd.Timedelta('4h'))
    d1 = last_closed(data['d1'], ts, pd.Timedelta('1D'))
    if len(m15) < 96 * 7 or len(h4) < 60 or len(d1) < 8:
        return None
    f: dict = {}

    # H2 — sweep of prior extreme in the 6h before the signal
    recent = m15[m15.index >= ts - pd.Timedelta('6h')]
    if not len(recent):
        return None
    for n in (1, 3, 7):
        prior = m15[(m15.index >= ts - pd.Timedelta(days=n) - pd.Timedelta('6h'))
                    & (m15.index < ts - pd.Timedelta('6h'))]
        if not len(prior):
            f[f'new_low_{n}d'] = f[f'new_high_{n}d'] = np.nan
            continue
        f[f'new_low_{n}d'] = float(recent['low'].min() < prior['low'].min())
        f[f'new_high_{n}d'] = float(recent['high'].max() > prior['high'].max())
    atr1h = h1['atr14'].iloc[-1]
    f['recovery_atr'] = (m15['close'].iloc[-1] - recent['low'].min()) / atr1h if atr1h else np.nan

    # H3 — minutes since last UTC 4h boundary
    f['min_since_4h_close'] = (ts - ts.floor('4h')).total_seconds() / 60

    # H4 — position vs 4h EMA200 in ATR units
    ema, atr4 = h4['ema200'].iloc[-1], h4['atr14'].iloc[-1]
    if atr4:
        f['entry_vs_ema200_atr'] = (entry - ema) / atr4
        f['stop_vs_ema200_atr'] = (stop - ema) / atr4 if stop is not None else np.nan
    f['atr14_1h_pct'] = atr1h / entry * 100 if entry else np.nan

    # H10 — position within today's range so far (UTC day)
    today = m15[m15.index >= ts.floor('1D')]
    if len(today) and today['high'].max() > today['low'].min():
        f['range_position'] = ((entry - today['low'].min())
                               / (today['high'].max() - today['low'].min()))
    else:
        f['range_position'] = np.nan

    # PDH / PDL — signed distance, positive = entry above the level
    pd_high, pd_low = d1['high'].iloc[-1], d1['low'].iloc[-1]
    f['entry_vs_pdl_pct'] = (entry - pd_low) / pd_low * 100
    f['entry_vs_pdh_pct'] = (entry - pd_high) / pd_high * 100

    # H6/H7 — BTC context
    if btc is not None:
        bh1 = last_closed(btc['h1'], ts, pd.Timedelta('1h'))
        if len(bh1) > 30:
            f['btc_ret_4h_pct'] = (bh1['close'].iloc[-1] / bh1['close'].iloc[-5] - 1) * 100
            f['btc_ret_24h_pct'] = (bh1['close'].iloc[-1] / bh1['close'].iloc[-25] - 1) * 100
            alt_ret = (h1['close'].iloc[-1] / h1['close'].iloc[-25] - 1) * 100
            f['rs_24h_pct'] = alt_ret - f['btc_ret_24h_pct']
    return f


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pos = load_pack()['positions']
    bt = pos[pos.is_backtestable].copy()

    cache: dict[str, dict | None] = {}
    def get(sym):
        if sym not in cache:
            cache[sym] = prep_symbol(sym)
        return cache[sym]

    btc = get('BTCUSDT')

    his, ctrl = [], []
    for _, r in bt.iterrows():
        data = get(r['symbol'])
        if data is None:
            continue
        ts = r['signal_time_utc']
        base = {'position_id': r['position_id'], 'symbol': r['symbol'], 'side': r['side'],
                'side_sign': int(r['side_sign']), 'outcome': r['outcome'],
                'risk_dist_pct': r['risk_dist_pct'], 'is_btc': r['symbol'] == 'BTCUSDT'}
        f = features_at(data, ts, r['entry_ref'], r['planned_stop'],
                        int(r['side_sign']), None if r['symbol'] == 'BTCUSDT' else btc)
        if f:
            his.append(base | f)

        # matched controls: same symbol + time-of-day, random dates
        span_days = (WINDOW[1] - WINDOW[0]).days
        offs = RNG.choice(span_days, size=N_CONTROLS, replace=False)
        for off in offs:
            cts = (WINDOW[0] + pd.Timedelta(days=int(off))
                   + pd.Timedelta(hours=ts.hour, minutes=ts.minute))
            m15c = last_closed(data['m15'], cts, pd.Timedelta('15min'))
            if not len(m15c):
                continue
            centry = float(m15c['close'].iloc[-1])
            cstop = (centry * (1 - int(r['side_sign']) * r['risk_dist_pct'] / 100)
                     if pd.notna(r['risk_dist_pct']) else None)
            cf = features_at(data, cts, centry, cstop, int(r['side_sign']),
                             None if r['symbol'] == 'BTCUSDT' else btc)
            if cf:
                ctrl.append(base | cf | {'control_time_utc': cts})

    pd.DataFrame(his).to_csv(os.path.join(OUT, 'entry_features.csv'), index=False)
    pd.DataFrame(ctrl).to_csv(os.path.join(OUT, 'control_features.csv'), index=False)
    print(f'his: {len(his)} rows   controls: {len(ctrl)} rows')

    h, c = pd.DataFrame(his), pd.DataFrame(ctrl)
    hl, cl = h[h.side_sign > 0], c[c.side_sign > 0]
    hs, cs = h[h.side_sign < 0], c[c.side_sign < 0]
    print('\n--- longs: sweep rates (his vs control) ---')
    for n in (1, 3, 7):
        print(f'  new_{n}d_low before long : {hl[f"new_low_{n}d"].mean():.2f}'
              f'  vs ctrl {cl[f"new_low_{n}d"].mean():.2f}   (n={hl[f"new_low_{n}d"].notna().sum()})')
    print('--- shorts: sweep rates ---')
    for n in (1, 3, 7):
        print(f'  new_{n}d_high before short: {hs[f"new_high_{n}d"].mean():.2f}'
              f'  vs ctrl {cs[f"new_high_{n}d"].mean():.2f}   (n={hs[f"new_high_{n}d"].notna().sum()})')
    print('\n--- H10 range position (longs should sit low) ---')
    print(f'  his median {hl.range_position.median():.2f}  ctrl {cl.range_position.median():.2f}')
    print('--- H4 entry vs 4h EMA200, ATR units (longs) ---')
    print(f'  his median {hl.entry_vs_ema200_atr.median():+.2f}  ctrl {cl.entry_vs_ema200_atr.median():+.2f}')
    print('--- H3 minutes since 4h boundary ---')
    print(f'  his median {h.min_since_4h_close.median():.0f}  ctrl {c.min_since_4h_close.median():.0f}')
    print(f'  share in first 30 min: his {(h.min_since_4h_close <= 30).mean():.2f}'
          f'  ctrl {(c.min_since_4h_close <= 30).mean():.2f}')
    print('--- H5 stop%% vs ATR%% correlation ---')
    m = h.dropna(subset=['risk_dist_pct', 'atr14_1h_pct'])
    print(f'  pooled pearson {m.risk_dist_pct.corr(m.atr14_1h_pct):.2f}'
          f'  spearman {m.risk_dist_pct.corr(m.atr14_1h_pct, method="spearman"):.2f}  (n={len(m)})')


if __name__ == '__main__':
    main()
