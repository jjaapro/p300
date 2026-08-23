"""Characterize the LONG trades — why do a few stop out in bull runs?

For every long entry: entry ADX, momentum (ret30/ret14), extension above
EMA50/EMA150, RSI14, recent funding, DVOL, and the post-entry path (max adverse
excursion before exit). Goal: is there a tell that separates the SL/loss longs
from the winners — or do winners share the same 'overextended' look (in which
case a filter would just kill winners too)?

n is tiny (3 SL longs, ~15 longs total). This is EXPLORATORY. Any separation
found must be sanity-checked against killing winners, not treated as a rule.
"""
from __future__ import annotations
import sqlite3, sys, bisect, math
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import run, load_btc_daily, ema, adx, atr_series, ADX_PERIOD

c = load_btc_daily()
closes=[x["close"] for x in c]
highs=[x["high"] for x in c]; lows=[x["low"] for x in c]
a=adx(c,ADX_PERIOD); e50=ema(closes,50); e150=ema(closes,150); e200=ema(closes,200)
dt_idx={x["dt"]:i for i,x in enumerate(c)}

def rsi(vals, n=14):
    out=[float("nan")]*len(vals)
    if len(vals)<n+1: return out
    gains=[0.0]*len(vals); losses=[0.0]*len(vals)
    for i in range(1,len(vals)):
        d=vals[i]-vals[i-1]; gains[i]=max(d,0); losses[i]=max(-d,0)
    ag=sum(gains[1:n+1])/n; al=sum(losses[1:n+1])/n
    out[n]=100-100/(1+ag/al) if al>0 else 100
    for i in range(n+1,len(vals)):
        ag=(ag*(n-1)+gains[i])/n; al=(al*(n-1)+losses[i])/n
        out[i]=100-100/(1+ag/al) if al>0 else 100
    return out
r14=rsi(closes,14)

# funding + dvol
con=sqlite3.connect(str(db.PROD_DB))
fr=con.execute("SELECT timestamp,fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall()
dv=con.execute("SELECT timestamp,close FROM cd_dvol WHERE asset='BTC' ORDER BY timestamp").fetchall()
con.close()
fr_ts=[r[0] for r in fr]; fr_v=[r[1] for r in fr]
dv_ts=[r[0] for r in dv]; dv_v=[r[1] for r in dv]
def fund7(ts):
    lo=bisect.bisect_left(fr_ts,ts-7*86400); hi=bisect.bisect_left(fr_ts,ts)
    if hi<=lo: return None
    return sum(fr_v[lo:hi])/(hi-lo)   # avg per-interval rate
def dvol_at(ts):
    p=bisect.bisect_right(dv_ts,ts)-1
    return dv_v[p] if p>=0 else None

base=run(c,"2018-01-01")
longs=[t for t in base["trades"] if t["dir"]=="long"]

print("="*120)
print("LONG trades — entry context + post-entry path")
print("="*120)
hdr=f"{'entry':11} {'outcome':8} {'pnl':>7} {'eADX':>5} {'ret30':>6} {'ret14':>6} {'xEMA50':>7} {'xEMA150':>8} {'RSI':>4} {'fund%/8h':>9} {'DVOL':>5} {'MAE':>6}"
print(hdr); print("-"*len(hdr))
def tag(t):
    if t["reason"]=="SL": return "SL"
    if t["net_pct"]<=0: return "loss"
    return "win"
rows=[]
for t in longs:
    i=dt_idx.get(t["entry_dt"]);
    if i is None: continue
    ts=c[i]["ts"]
    ret30=(closes[i]-closes[i-30])/closes[i-30]*100 if i>=30 else float('nan')
    ret14=(closes[i]-closes[i-14])/closes[i-14]*100 if i>=14 else float('nan')
    x50=(closes[i]-e50[i])/e50[i]*100
    x150=(closes[i]-e150[i])/e150[i]*100 if not math.isnan(e150[i]) else float('nan')
    f7=fund7(ts); fpct=(f7*100 if f7 is not None else None)
    dvv=dvol_at(ts)
    # MAE: worst drawdown vs entry before exit
    ei=dt_idx.get(str(t["exit_dt"]).replace(" (open)",""), len(c)-1)
    ep=t["entry_price"]; mae=0.0
    for k in range(i, (ei or i)+1):
        adv=(lows[k]-ep)/ep*100
        mae=min(mae,adv)
    o=tag(t)
    rows.append((o,ret30,ret14,x50,x150,r14[i],fpct,dvv))
    print(f"{t['entry_dt']:11} {o:8} {t['net_pct']:>+6.1f}% {t.get('entry_adx'):>5} "
          f"{ret30:>+5.0f}% {ret14:>+5.0f}% {x50:>+6.1f}% {x150:>+7.1f}% {r14[i]:>4.0f} "
          f"{(f'{fpct:+.3f}' if fpct is not None else '   n/a'):>9} "
          f"{(f'{dvv:.0f}' if dvv is not None else ' n/a'):>5} {mae:>+5.0f}%")

# group means
print("\nGroup means (win vs SL/loss):")
import statistics as st
def grp(pred,label):
    sel=[r for r in rows if pred(r[0])]
    if not sel: print(f"  {label}: none"); return
    def m(j):
        vals=[r[j] for r in sel if r[j] is not None and not (isinstance(r[j],float) and math.isnan(r[j]))]
        return st.mean(vals) if vals else float('nan')
    print(f"  {label:10} n={len(sel):>2}  ret30={m(1):+5.0f}%  ret14={m(2):+5.0f}%  "
          f"xEMA50={m(3):+5.1f}%  xEMA150={m(4):+6.1f}%  RSI={m(5):4.0f}  "
          f"fund={m(6):+.3f}  DVOL={m(7):4.0f}")
grp(lambda o:o=="win","WINNERS")
grp(lambda o:o in("SL","loss"),"LOSERS")
grp(lambda o:o=="SL","  of which SL")
