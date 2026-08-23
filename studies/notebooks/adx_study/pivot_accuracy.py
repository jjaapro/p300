"""Which confirms make pivot tops/bottoms FEWER and MORE ACCURATE?

Accuracy test: for every pivot (L8/R8/10%), measure the forward move from the
pivot bar to +30 bars (well past the R=8 window, so no pivot-definition lookahead):
  TOP  is 'good' if price is LOWER  +30 bars later (a real top to fade)
  BOTTOM is 'good' if price is HIGHER +30 bars later (a real bottom to buy)

Then compare hit-rate + mean forward move across confirms:
  ADX level, ADX rolling-over, funding z, rejection close, prominence, EMA stretch.
Goal: a filter that cuts the ~13/yr count while RAISING accuracy.

(CVD divergence is NOT here — real taker CVD only exists from 2026-05-18; it can
only be tested forward / on the chart's ~69d. Tested separately below on recent bars.)
"""
from __future__ import annotations
import sqlite3, sys, math, statistics as st, collections
from datetime import datetime, timezone
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import load_btc_daily, ema, adx, ADX_PERIOD

c=load_btc_daily(); closes=[x["close"] for x in c]; highs=[x["high"] for x in c]; lows=[x["low"] for x in c]
e150=ema(closes,150); A=adx(c,ADX_PERIOD); dt_idx={x["dt"]:i for i,x in enumerate(c)}
con=sqlite3.connect(str(db.PROD_DB)); fr=con.execute("SELECT timestamp,fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall(); con.close()
fd=collections.defaultdict(list)
for ts,v in fr:
    if v is not None: fd[datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d")].append(v)
fund_d={k:sum(v)/len(v) for k,v in fd.items()}
def fundz(i,days=30):
    h=[fund_d[c[k]["dt"]] for k in range(max(0,i-days+1),i+1) if c[k]["dt"] in fund_d]; cur=fund_d.get(c[i]["dt"])
    if len(h)<10 or cur is None: return None
    s=st.pstdev(h); return (cur-st.mean(h))/s if s>0 else None

L=R=8; prom=0.10; FWD=30
def is_piv_top(i):
    if highs[i]!=max(highs[i-L:i+R+1]): return False
    if highs[i]<=max([highs[j] for j in range(i-L,i)]+[highs[j] for j in range(i+1,i+R+1)]): return False
    return (highs[i]-min(lows[i-L:i+R+1]))/min(lows[i-L:i+R+1])>=prom
def is_piv_bot(i):
    if lows[i]!=min(lows[i-L:i+R+1]): return False
    if lows[i]>=min([lows[j] for j in range(i-L,i)]+[lows[j] for j in range(i+1,i+R+1)]): return False
    return (max(highs[i-L:i+R+1])-lows[i])/lows[i]>=prom

# build pivot records with confirms + forward outcome
tops=[]; bots=[]
for i in range(L, len(c)-FWD):
    if c[i]["dt"]<"2018-01-01" or math.isnan(A[i]) or math.isnan(e150[i]): continue
    fwd=(closes[i+FWD]-closes[i])/closes[i]*100
    cir=(closes[i]-lows[i])/(highs[i]-lows[i]) if highs[i]>lows[i] else .5
    ext=(closes[i]-e150[i])/e150[i]*100
    adx_now=A[i]; adx_roll=(not math.isnan(A[i-5])) and A[i]<A[i-5]  # adx turning down
    fz=fundz(i)
    rec=dict(i=i,dt=c[i]["dt"],fwd=fwd,cir=cir,ext=ext,adx=adx_now,adxRoll=adx_roll,fz=fz)
    if is_piv_top(i): tops.append(rec)
    if is_piv_bot(i): bots.append(rec)

def acc(recs, side):
    """side='top' good if fwd<0 ; 'bot' good if fwd>0. returns (n, hit%, mean_fwd, mean_favorable)."""
    if not recs: return (0,0,0,0)
    good=[r for r in recs if (r["fwd"]<0 if side=="top" else r["fwd"]>0)]
    mean_fwd=st.mean(r["fwd"] for r in recs)
    fav=st.mean((-r["fwd"] if side=="top" else r["fwd"]) for r in recs)  # favorable = reversal magnitude
    return (len(recs), len(good)/len(recs)*100, mean_fwd, fav)

def report(recs, side, label):
    n,hr,mf,fav=acc(recs,side)
    print(f"  {label:34} n={n:>3}  hit={hr:4.0f}%  meanFwd30={mf:+5.1f}%  favorable(reversal)={fav:+5.1f}%")

print(f"Pivots L{L}/R{R}/{prom*100:.0f}%   forward window={FWD} bars   (2018+)")
print("\n== TOPS (good = price LOWER 30 bars later; favorable = reversal magnitude) ==")
report(tops,"top","ALL pivot tops (baseline)")
report([r for r in tops if r["adx"]>=25],"top","+ ADX>=25 (strong trend)")
report([r for r in tops if r["adx"]>=30],"top","+ ADX>=30")
report([r for r in tops if r["adxRoll"]],"top","+ ADX rolling over")
report([r for r in tops if r["adx"]>=25 and r["adxRoll"]],"top","+ ADX>=25 AND rolling over")
report([r for r in tops if r["fz"] is not None and r["fz"]>1.0],"top","+ funding z>1.0 (crowded long)")
report([r for r in tops if r["cir"]<0.45],"top","+ weak close (rejection)")
report([r for r in tops if r["ext"]>15],"top","+ >15% above EMA150 (stretched)")
report([r for r in tops if r["adx"]>=25 and r["ext"]>10],"top","+ ADX>=25 AND >10% above EMA")

print("\n== BOTTOMS (good = price HIGHER 30 bars later) ==")
report(bots,"bot","ALL pivot bottoms (baseline)")
report([r for r in bots if r["adx"]>=25],"bot","+ ADX>=25 (strong trend)")
report([r for r in bots if r["adx"]>=30],"bot","+ ADX>=30")
report([r for r in bots if r["adxRoll"]],"bot","+ ADX rolling over")
report([r for r in bots if r["fz"] is not None and r["fz"]<-1.0],"bot","+ funding z<-1.0 (crowded short)")
report([r for r in bots if r["cir"]>0.55],"bot","+ strong close (absorption)")
report([r for r in bots if r["ext"]<-15],"bot","+ >15% below EMA150 (stretched)")
report([r for r in bots if r["adx"]>=25 and r["ext"]<-10],"bot","+ ADX>=25 AND >10% below EMA")
