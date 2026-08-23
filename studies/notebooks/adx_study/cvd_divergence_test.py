"""Test CVD divergence as a pivot confirm — now possible on REAL history.

cd_futures_15m has true taker volume_buy/volume_sell 100% since 2019-09 (the
'~69d' limit is only TV-Pine's requestVolumeDelta proxy). So we can build daily
perp CVD and test divergence over 6.5y.

Divergence (regular):
  bearish (top): price higher than K bars ago BUT cvd lower  -> hidden selling
  bullish (bot): price lower  than K bars ago BUT cvd higher -> hidden buying

Same forward-accuracy frame as pivot_accuracy.py (fwd30; top good if price lower,
bottom good if higher). Compare hit% + reversal magnitude with/without the CVD
confirm, and vs the funding / ADX-roll confirms found earlier.
"""
from __future__ import annotations
import sqlite3, sys, math, statistics as st, collections
from datetime import datetime, timezone
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import load_btc_daily, ema, adx, ADX_PERIOD

c=load_btc_daily(); closes=[x["close"] for x in c]; highs=[x["high"] for x in c]; lows=[x["low"] for x in c]
A=adx(c,ADX_PERIOD); dt_idx={x["dt"]:i for i,x in enumerate(c)}

# daily perp CVD from cd_futures_15m
con=sqlite3.connect(str(db.PROD_DB))
rows=con.execute("SELECT timestamp, volume_buy, volume_sell FROM cd_futures_15m WHERE volume_buy>0 ORDER BY timestamp").fetchall()
fr=con.execute("SELECT timestamp,fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall()
con.close()
dayd=collections.defaultdict(float)
for ts,vb,vs in rows:
    if vb is None or vs is None: continue
    d=datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d"); dayd[d]+=(vb-vs)
# cumulative CVD aligned to candle dates
cvd=[float("nan")]*len(c); run=0.0; started=False
for i,x in enumerate(c):
    if x["dt"] in dayd: started=True
    if started: run+=dayd.get(x["dt"],0.0); cvd[i]=run
fd=collections.defaultdict(list)
for ts,v in fr:
    if v is not None: fd[datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d")].append(v)
fund_d={k:sum(v)/len(v) for k,v in fd.items()}
def fundz(i,days=30):
    h=[fund_d[c[k]["dt"]] for k in range(max(0,i-days+1),i+1) if c[k]["dt"] in fund_d]; cur=fund_d.get(c[i]["dt"])
    if len(h)<10 or cur is None: return None
    s=st.pstdev(h); return (cur-st.mean(h))/s if s>0 else None

L=R=8; prom=0.10; FWD=30; K=10
def piv_top(i):
    return highs[i]==max(highs[i-L:i+R+1]) and highs[i]>max([highs[j] for j in range(i-L,i)]+[highs[j] for j in range(i+1,i+R+1)]) and (highs[i]-min(lows[i-L:i+R+1]))/min(lows[i-L:i+R+1])>=prom
def piv_bot(i):
    return lows[i]==min(lows[i-L:i+R+1]) and lows[i]<min([lows[j] for j in range(i-L,i)]+[lows[j] for j in range(i+1,i+R+1)]) and (max(highs[i-L:i+R+1])-lows[i])/lows[i]>=prom

tops=[]; bots=[]
for i in range(max(L,K), len(c)-FWD):
    if c[i]["dt"]<"2019-11-01" or math.isnan(cvd[i]) or math.isnan(cvd[i-K]): continue
    fwd=(closes[i+FWD]-closes[i])/closes[i]*100
    cvdBear = closes[i]>closes[i-K] and cvd[i]<cvd[i-K]
    cvdBull = closes[i]<closes[i-K] and cvd[i]>cvd[i-K]
    fz=fundz(i); adxRoll=(not math.isnan(A[i-5])) and not math.isnan(A[i]) and A[i]<A[i-5]
    rec=dict(i=i,dt=c[i]["dt"],fwd=fwd,cvdBear=cvdBear,cvdBull=cvdBull,fz=fz,adxRoll=adxRoll)
    if piv_top(i): tops.append(rec)
    if piv_bot(i): bots.append(rec)

def rep(recs, side, label):
    if not recs: print(f"  {label:36} n=  0"); return
    good=[r for r in recs if (r["fwd"]<0 if side=="top" else r["fwd"]>0)]
    fav=st.mean((-r["fwd"] if side=="top" else r["fwd"]) for r in recs)
    print(f"  {label:36} n={len(recs):>3}  hit={len(good)/len(recs)*100:4.0f}%  reversal={fav:+5.1f}%")

print(f"Pivots L8/R8/10%, fwd30, CVD div K={K}  (2019-11+, where CVD exists)")
print("\n== TOPS ==")
rep(tops,"top","ALL pivot tops (baseline)")
rep([r for r in tops if r["cvdBear"]],"top","+ CVD bearish divergence")
rep([r for r in tops if not r["cvdBear"]],"top","   (no CVD div — for contrast)")
rep([r for r in tops if r["fz"] is not None and r["fz"]>1.0],"top","+ funding z>1.0")
rep([r for r in tops if r["cvdBear"] and r["fz"] is not None and r["fz"]>1.0],"top","+ CVD div AND funding hot")
rep([r for r in tops if r["cvdBear"] or (r["fz"] is not None and r["fz"]>1.0)],"top","+ CVD div OR funding hot")

print("\n== BOTTOMS ==")
rep(bots,"bot","ALL pivot bottoms (baseline)")
rep([r for r in bots if r["cvdBull"]],"bot","+ CVD bullish divergence")
rep([r for r in bots if not r["cvdBull"]],"bot","   (no CVD div — for contrast)")
rep([r for r in bots if r["adxRoll"]],"bot","+ ADX rolling over")
rep([r for r in bots if r["cvdBull"] and r["adxRoll"]],"bot","+ CVD div AND ADX rolling over")
rep([r for r in bots if r["cvdBull"] or r["adxRoll"]],"bot","+ CVD div OR ADX rolling over")
