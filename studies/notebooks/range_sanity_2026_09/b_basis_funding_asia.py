"""Sanity checks (NOT a study), BTC, prod.db read-only:
  (A) intraday perp-spot basis reversion at 15m
  (B) price drift around 8h funding settlements (00/08/16 UTC), 1m bars
  (C) Asia-session (00-07 UTC) range first-break FADE in London/NY, 1m bars
Run from anywhere: python studies/notebooks/range_sanity_2026_09/b_basis_funding_asia.py
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_R = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_R))
from studies.lib.regime_adx import classify_regime  # noqa: E402

c = sqlite3.connect(str(_R / 'data' / 'databases' / 'prod.db'))

print("=== A) Perp-spot basis at 15m (Binance), by year")
b = pd.read_sql(
    "select f.timestamp, f.close fut, s.close spot from cd_futures_15m f "
    "join cd_spot_15m s on s.timestamp=f.timestamp "
    "where f.close>0 and s.close>0 order by f.timestamp", c)
b['ts'] = pd.to_datetime(b.timestamp, unit='s', utc=True)
b = b.set_index('ts')
b['basis'] = (b.fut - b.spot) / b.spot * 1e4
for yr in (2022, 2023, 2024, 2025, 2026):
    x = b.basis[b.index.year == yr]
    dev = x - x.rolling(96).median()   # deviation from 24h rolling median (removes slow carry premium)
    phi = dev.autocorr(1)
    hl = -np.log(2) / np.log(phi) if 0 < phi < 1 else np.nan
    ext = int((dev.abs() > 15).sum())
    ix = np.where(dev.abs().values > 15)[0]
    ix = ix[ix + 4 < len(dev)]
    rev = float(np.mean(np.abs(dev.values[ix + 4]) <= 0.5 * np.abs(dev.values[ix]))) if len(ix) else np.nan
    print(f"  {yr}: basis mean={x.mean():+.1f}bp sd={x.std():.1f}bp | dev sd={dev.std():.1f}bp "
          f"ac1={phi:.2f} half-life={hl * 15:.0f}min | bars |dev|>15bp: {ext} ({ext / len(x) * 100:.2f}%) "
          f"| 1h 50%-reversion rate={rev:.2f}")

print("\n=== B) Drift around 8h funding settlement, BTC 1m, 2020->now (bp; t = mean/se)")
m = pd.read_sql("select open_time, close from btc_1m order by open_time", c)
m['ts'] = pd.to_datetime(m.open_time, unit='ms', utc=True)
m = m.set_index('ts').close
fr = pd.read_sql("select timestamp, fr_close from cd_funding_rate order by timestamp", c)
fr['ts'] = pd.to_datetime(fr.timestamp, unit='s', utc=True)
fr = fr.set_index('ts').fr_close
settle = fr[(fr.index.hour % 8 == 0) & (fr.index.minute == 0) & (fr.index >= '2020-01-01')]
rows = []
for T, f in settle.items():
    p0 = m.get(T - pd.Timedelta('60min'))
    p1 = m.get(T)
    p2 = m.get(T + pd.Timedelta('60min'))
    p3 = m.get(T + pd.Timedelta('240min'))
    if None in (p0, p1, p2, p3) or any(np.isnan([p0, p1, p2, p3])):
        continue
    rows.append((T, f * 100, (p1 / p0 - 1) * 1e4, (p2 / p1 - 1) * 1e4, (p3 / p1 - 1) * 1e4))
s = pd.DataFrame(rows, columns=['T', 'fr_pct', 'pre60_bp', 'post60_bp', 'post240_bp'])
s['bucket'] = pd.cut(s.fr_pct, [-9, -0.01, 0.01, 0.03, 0.06, 9],
                     labels=['<-0.01%', '-0.01..0.01', '0.01..0.03', '0.03..0.06', '>0.06%'])


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x))


for label, sub in (("all", s), ("2024->now", s[s['T'] >= '2024-01-01'])):
    g = sub.groupby('bucket', observed=True).agg(
        n=('pre60_bp', 'size'), pre60=('pre60_bp', 'mean'), post60=('post60_bp', 'mean'),
        post240=('post240_bp', 'mean'), t_pre=('pre60_bp', tstat), t_post=('post60_bp', tstat))
    print(f"  {label}:")
    print(g.round(2).to_string())

print("\n=== C) Asia range (00-07 UTC) first-break FADE: short break of high / long break of low; "
      "stop 0.25*width beyond, target Asia mid, TIF 21:00 UTC")
o = pd.read_sql("select open_time, open, high, low, close from btc_1m order by open_time", c)
o['ts'] = pd.to_datetime(o.open_time, unit='ms', utc=True)
o = o.set_index('ts').drop(columns='open_time')
daily = o.resample('1D').agg(open=('open', 'first'), high=('high', 'max'),
                             low=('low', 'min'), close=('close', 'last')).dropna()
reg = classify_regime(daily).shift(1).fillna('gap')   # previous day's ADX label -> no lookahead
rows = []
for d, day in o.groupby(o.index.date):
    asia = day.between_time('00:00', '06:59')
    rest = day.between_time('07:00', '20:59')
    if len(asia) < 300 or len(rest) < 600:
        continue
    ah, al = asia.high.max(), asia.low.min()
    mid = (ah + al) / 2
    w = (ah - al) / mid * 1e4
    up = rest.index[rest.high > ah]
    dn = rest.index[rest.low < al]
    first = None
    if len(up) and (not len(dn) or up[0] < dn[0]):
        first = ('up', up[0])
    elif len(dn):
        first = ('dn', dn[0])
    if first is None:
        continue
    lab = reg.get(pd.Timestamp(d, tz='UTC'), 'gap')
    after = rest[rest.index > first[1]]
    res = None
    if first[0] == 'up':
        back_mid = float((after.low <= mid).any())
        back_in = float((after.close < ah).any())
        stop = ah + 0.25 * (ah - al)
        for _, r in after.iterrows():
            if r.high >= stop:
                res = 'stop'
                break
            if r.low <= mid:
                res = 'tp'
                break
    else:
        back_mid = float((after.high >= mid).any())
        back_in = float((after.close > al).any())
        stop = al - 0.25 * (ah - al)
        for _, r in after.iterrows():
            if r.low <= stop:
                res = 'stop'
                break
            if r.high >= mid:
                res = 'tp'
                break
    rows.append((d, lab, w, first[0], back_mid, back_in, res or 'tif'))
a = pd.DataFrame(rows, columns=['d', 'reg', 'width_bp', 'first', 'back_to_mid', 'back_inside', 'fade_res'])
a['tp'] = (a.fade_res == 'tp').astype(float)
a['sl'] = (a.fade_res == 'stop').astype(float)
a['grossR'] = 2 * a.tp - a.sl                       # target 0.5w vs stop 0.25w => 2R wins, TIF ~0
a['costR_taker'] = 18 / (0.25 * a.width_bp)
a['costR_maker'] = (2 + (9 * a.sl + 2 * a.tp)) / (0.25 * a.width_bp)   # entry maker 2bp; TP maker 2bp; stop taker 9bp


def summ(x):
    return pd.Series(dict(
        n=len(x), width=x.width_bp.median(), p_back_inside=x.back_inside.mean(),
        p_back_to_mid=x.back_to_mid.mean(), tp=x.tp.mean(), sl=x.sl.mean(),
        grossR=x.grossR.mean(), netR_taker=(x.grossR - x.costR_taker).mean(),
        netR_maker=(x.grossR - x.costR_maker).mean()))


print("  by prev-day ADX regime, 2020->now:")
print(a.groupby('reg').apply(summ).round(2).to_string())
print("  2025->now:")
print(a[a.d >= pd.Timestamp('2025-01-01').date()].groupby('reg').apply(summ).round(2).to_string())
a['yr'] = [x.year for x in a.d]
print("  by year:")
print(a.groupby('yr').apply(summ).round(2).to_string())
