"""Pivot-anchored top/bottom detector — validation harness.

Replaces the '180-day-extreme' anchor (which only saw absolute cycle highs and
missed every real bottom) with SWING PIVOTS: a confirmed local pivot high/low
(fractal of `left`/`right` bars) with a prominence filter. Optional micro
confirms (funding z, rejection close) just SCORE it; the pivot is the anchor.

Goal check: do pivot TOPS now line up with the local highs the two successful
ADX shorts (2026-01-12, 2026-05-31) faded, and do pivot LOWS catch the real
bottoms (2020 COVID, 2022 cycle) the old version missed?
"""
from __future__ import annotations
import sqlite3, sys, math, statistics as st, collections
from datetime import datetime, timezone
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import load_btc_daily, ema, run

c = load_btc_daily()
closes=[x["close"] for x in c]; highs=[x["high"] for x in c]; lows=[x["low"] for x in c]
e150=ema(closes,150); dt_idx={x["dt"]:i for i,x in enumerate(c)}

con=sqlite3.connect(str(db.PROD_DB))
fr=con.execute("SELECT timestamp,fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall(); con.close()
fd=collections.defaultdict(list)
for ts,v in fr:
    if v is None: continue
    fd[datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d")].append(v)
fund_d={k:sum(v)/len(v) for k,v in fd.items()}
def fundz(i,days=30):
    h=[fund_d[c[k]["dt"]] for k in range(max(0,i-days+1),i+1) if c[k]["dt"] in fund_d]
    cur=fund_d.get(c[i]["dt"])
    if len(h)<10 or cur is None: return None
    s=st.pstdev(h); return (cur-st.mean(h))/s if s>0 else None

def pivots(L, R, prom):
    """Return (tops, bots) lists of bar indices. Pivot high i = high[i] strictly
    the max over [i-L, i+R]; prominence = pivot stands >= prom above the min
    low in that window (tops) / below max high (bots)."""
    tops=[]; bots=[]
    for i in range(L, len(c)-R):
        if c[i]["dt"]<"2018-01-01": continue
        win_lo=min(lows[i-L:i+R+1]); win_hi=max(highs[i-L:i+R+1])
        if highs[i]==win_hi and highs[i] > max([highs[j] for j in range(i-L,i)]+[highs[j] for j in range(i+1,i+R+1)]):
            if (highs[i]-win_lo)/win_lo >= prom: tops.append(i)
        if lows[i]==win_lo and lows[i] < min([lows[j] for j in range(i-L,i)]+[lows[j] for j in range(i+1,i+R+1)]):
            if (win_hi-lows[i])/lows[i] >= prom: bots.append(i)
    return tops, bots

def show(L,R,prom):
    tops,bots=pivots(L,R,prom)
    print(f"\n==== pivots L={L} R={R} prominence={prom*100:.0f}%  =>  {len(tops)} tops, {len(bots)} bottoms ====")
    print(" TOPS:    "+", ".join(f"{c[i]['dt']}(${highs[i]:,.0f})" for i in tops))
    print(" BOTTOMS: "+", ".join(f"{c[i]['dt']}(${lows[i]:,.0f})" for i in bots))
    # alignment with the two successful ADX shorts + major bottoms
    base=run(c,"2018-01-01")
    shorts=[t for t in base["trades"] if t["dir"]=="short" and t["net_pct"]>10]
    print(" -- ADX winning-short alignment (top flagged within 45d BEFORE entry?) --")
    for t in shorts[-3:]:
        i0=dt_idx[t["entry_dt"]]
        near=[c[i]['dt'] for i in tops if 0 <= (i0-i) <= 45]
        print(f"    SHORT {t['entry_dt']} {t['net_pct']:+.0f}%  ->  preceding top: {near if near else 'NONE'}")
    # major bottoms present?
    majors={"2020 COVID":"2020-03","2022 cycle":"2022-11"}
    for name,mo in majors.items():
        hit=[c[i]['dt'] for i in bots if c[i]['dt'].startswith(mo)]
        print(f"    {name} bottom ({mo}): {hit if hit else 'NONE'}")

for (L,R,prom) in [(8,8,0.10),(10,10,0.12),(6,6,0.08)]:
    show(L,R,prom)
