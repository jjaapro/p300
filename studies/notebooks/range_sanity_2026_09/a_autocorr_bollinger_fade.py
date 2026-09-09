"""Sanity check (NOT a study): intraday mean-reversion property of BTC by ADX regime,
and a naive Bollinger band-fade under taker (18bp) vs maker-blended cost models."""
import sqlite3, sys, numpy as np, pandas as pd
from pathlib import Path as _P; _R=_P(__file__).resolve(); [_R:=_R.parent for _ in range(4)]; sys.path.insert(0, str(_R))
from studies.lib.regime_adx import classify_regime

c = sqlite3.connect(str(_R / 'data' / 'databases' / 'prod.db'))
df = pd.read_sql("select open_time,open,high,low,close from btc_1m order by open_time", c)
df['ts'] = pd.to_datetime(df.open_time, unit='ms', utc=True)
df = df.set_index('ts').drop(columns='open_time')
daily = df.resample('1D').agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna()
reg = classify_regime(daily).shift(1)          # previous day's label -> no lookahead
reg = reg.fillna('gap')

def bars(rule):
    b = df.resample(rule).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna()
    b['reg'] = reg.reindex(b.index, method='ffill').fillna('gap')
    return b

print("=== 1) Return autocorrelation & variance ratio by regime (2020-2026, BTC)")
for rule, q in (('15min', 4), ('1h', 4), ('4h', 6)):
    b = bars(rule); r = np.log(b.close).diff()
    out = []
    for g in ('range','gap','long','short'):
        x = r[b.reg == g].dropna()
        ac1 = x.autocorr(1)
        # variance ratio: var(q-period sum)/(q*var(1-period)); <1 => mean reversion
        s = x.rolling(q).sum().dropna()
        vr = s.var() / (q * x.var())
        out.append(f"{g}: n={len(x):,} ac1={ac1:+.3f} VR({q})={vr:.2f}")
    print(f"  {rule:6s} " + " | ".join(out))

print("\n=== 2) Naive Bollinger(20,2) band-fade, 1h bars, stop=1 ATR(14) beyond entry, target=SMA20, TIF=24 bars")
b = bars('1h')
sma = b.close.rolling(20).mean(); sd = b.close.rolling(20).std()
tr = np.maximum(b.high-b.low, np.maximum((b.high-b.close.shift()).abs(), (b.low-b.close.shift()).abs()))
atr = tr.rolling(14).mean()
b['up'] = sma+2*sd; b['dn'] = sma-2*sd; b['sma']=sma; b['atr']=atr
sig_long = (b.close < b.dn) & (b.close.shift() >= b.dn.shift())
sig_short = (b.close > b.up) & (b.close.shift() <= b.up.shift())
idx = b.index; o=b.open.values; h=b.high.values; l=b.low.values; cl=b.close.values
sm=b.sma.values; at=b.atr.values; rg=b.reg.values
rows=[]
for side, sig in (('long', sig_long), ('short', sig_short)):
    for i in np.where(sig.values)[0]:
        if i+1 >= len(b) or np.isnan(at[i]) or at[i] <= 0: continue
        # taker arm: market at next open. maker arm: limit at signal close, filled if next bar trades through it
        e_taker = o[i+1]
        lim = cl[i]
        filled = (l[i+1] <= lim) if side=='long' else (h[i+1] >= lim)
        for arm, entry, ok in (('taker', e_taker, True), ('maker', lim, filled)):
            if not ok: rows.append((side, arm, rg[i], False, np.nan, np.nan, np.nan)); continue
            stop = entry - at[i] if side=='long' else entry + at[i]
            tgt = sm[i]  # frozen SMA at signal
            R = abs(entry-stop)
            res=None; exit_px=None
            for j in range(i+1, min(i+1+24, len(b))):
                if j==i+1 and arm=='taker': lo,hi = l[j],h[j]
                else: lo,hi=l[j],h[j]
                if side=='long':
                    if lo <= stop: res,exit_px='stop',stop; break
                    if hi >= tgt: res,exit_px='tp',tgt; break
                else:
                    if hi >= stop: res,exit_px='stop',stop; break
                    if lo <= tgt: res,exit_px='tp',tgt; break
            if res is None:
                res='tif'; exit_px=cl[min(i+24,len(b)-1)]
            pnl = (exit_px-entry) if side=='long' else (entry-exit_px)
            rows.append((side, arm, rg[i], True, pnl/R, R/entry*1e4, res))
t = pd.DataFrame(rows, columns=['side','arm','reg','filled','grossR','stop_bp','res'])
print("  fill rate maker arm:", round(t[t.arm=='maker'].filled.mean(),2))
f = t[t.filled].copy()
# cost models in R: taker RT 18bp; maker-blended: entry 2bp + TP 2bp (limit) or stop 9bp (taker 5 + slip 4)
f['cost_bp'] = np.where(f.arm=='taker', 18.0, np.where(f.res=='stop', 2+9, 2+2))
f['netR'] = f.grossR - f.cost_bp / f.stop_bp
f['tp'] = (f.res=='tp').astype(float)
g = f.groupby(['arm','reg']).agg(n=('grossR','size'), stop_bp=('stop_bp','median'), grossR=('grossR','mean'), netR=('netR','mean'), tp_rate=('tp','mean')).round(3)
print(g.to_string())
print("\n  by side, range regime only:")
print(f[f.reg=='range'].groupby(['arm','side']).agg(n=('grossR','size'), grossR=('grossR','mean'), netR=('netR','mean')).round(3).to_string())
