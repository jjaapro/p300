"""Can the microstructure sleeves' EXHAUSTION features detect HTF tops/bottoms
that ADX walks into? Build a history-rich, daily-computable exhaustion battery
from the reusable primitives found across chento_triple_v3 / chento_limit_bid /
short_squeeze, and evaluate it at every ADX entry.

History-rich daily primitives we CAN backtest (CVD is excluded — split-volume
only exists from 2026-05-18, useless at Oct-2025):
  - FUNDING extreme  (cd_funding_rate, 2019+)  crowded longs(+) / shorts(-)
  - OI build/flush   (cd_open_interest notional, 2022+)
  - LSR extreme      (ca_long_short_ratio long_pct, 2021+)  crowd euphoric/flushed
  - PRICE GEOMETRY   distance from trailing N-day high/low, close-in-range,
                     extension above EMA150  (any history)

For each ADX entry we ask: is this entry AGAINST an HTF exhaustion extreme?
  LONG  into a top  = near a multi-month HIGH + crowded-long  (funding/OI/LSR hot)
  SHORT into a bottom = near a multi-month LOW + crowded-short (funding/OI/LSR cold)
"""
from __future__ import annotations
import sqlite3, sys, bisect, math, statistics as st
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import run, load_btc_daily, ema, ADX_PERIOD

c = load_btc_daily()
closes=[x["close"] for x in c]; highs=[x["high"] for x in c]; lows=[x["low"] for x in c]
e150=ema(closes,150)
dt_idx={x["dt"]:i for i,x in enumerate(c)}

con=sqlite3.connect(str(db.PROD_DB))
fr=con.execute("SELECT timestamp,fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall()
oi=con.execute("SELECT timestamp,oi_value_close FROM cd_open_interest ORDER BY timestamp").fetchall()
ls=con.execute("SELECT timestamp,long_pct FROM ca_long_short_ratio WHERE asset='BTC' ORDER BY timestamp").fetchall()
con.close()

def daily_series(rows, agg="last"):
    """Collapse (ts,val) rows to {date: val}."""
    import collections
    from datetime import datetime, timezone
    d=collections.defaultdict(list)
    for ts,v in rows:
        if v is None: continue
        dt=datetime.fromtimestamp(ts,tz=timezone.utc).strftime("%Y-%m-%d")
        d[dt].append(v)
    out={}
    for k,vs in d.items():
        out[k]= (sum(vs)/len(vs) if agg=="mean" else vs[-1])
    return out
fund_d=daily_series(fr,"mean")
oi_d=daily_series(oi,"last")
lsr_d=daily_series(ls,"last")

def hist_vals(series_d, end_dt, days):
    """Trailing `days` daily values strictly before/at end_dt from a {date:val} map,
    walking the candle dates so gaps are handled."""
    i=dt_idx.get(end_dt)
    if i is None: return []
    out=[]
    for k in range(max(0,i-days+1), i+1):
        v=series_d.get(c[k]["dt"])
        if v is not None: out.append(v)
    return out

def zscore(series_d, end_dt, days=30):
    h=hist_vals(series_d,end_dt,days)
    if len(h)<10: return None
    m=st.mean(h); s=st.pstdev(h)
    cur=series_d.get(end_dt)
    if cur is None or s==0: return None
    return (cur-m)/s

def pctile(series_d, end_dt, days=90):
    h=hist_vals(series_d,end_dt,days)
    cur=series_d.get(end_dt)
    if len(h)<20 or cur is None: return None
    return sum(1 for x in h if x<=cur)/len(h)

def oi_chg(end_dt, days=30):
    i=dt_idx.get(end_dt)
    if i is None: return None
    cur=oi_d.get(end_dt); past=None
    for k in range(i-days, i-days-7,-1):
        if k<0: break
        if c[k]["dt"] in oi_d: past=oi_d[c[k]["dt"]]; break
    if cur is None or not past: return None
    return (cur-past)/past*100

def dist_from_high(end_dt, days=180):
    i=dt_idx.get(end_dt)
    if i is None or i<days: return None
    hh=max(highs[i-days:i+1])
    return (closes[i]-hh)/hh*100   # 0 = at the high, negative = below
def dist_from_low(end_dt, days=180):
    i=dt_idx.get(end_dt)
    if i is None or i<days: return None
    ll=min(lows[i-days:i+1])
    return (closes[i]-ll)/ll*100   # 0 = at the low
def cir(end_dt):
    i=dt_idx.get(end_dt)
    h,l,cl=highs[i],lows[i],closes[i]
    return (cl-l)/(h-l) if h>l else 0.5

base=run(c,"2018-01-01")
print("="*140)
print("ADX entries vs HTF exhaustion battery   (fundZ=30d funding z, OIΔ30d %, LSR%ile=90d, fromHigh/Low=180d %, CIR=close-in-range)")
print("="*140)
hdr=f"{'entry':11} {'dir':5} {'out':5} {'pnl':>7} | {'fundZ':>6} {'OIΔ30':>6} {'LSR%':>5} | {'fromHi':>6} {'fromLo':>6} {'xE150':>6} {'CIR':>4} | verdict"
print(hdr); print("-"*len(hdr))
def verdict(d, fz, oic, lp, fh, fl):
    flags=[]
    if d=="long":
        if fh is not None and fh>-3: flags.append("@HIGH")
        if fz is not None and fz>1.0: flags.append("fundHOT")
        if oic is not None and oic>25: flags.append("OIparab")
        if lp is not None and lp>0.85: flags.append("LSRhot")
    else:
        if fl is not None and fl<3: flags.append("@LOW")
        if fz is not None and fz<-1.0: flags.append("fundCOLD")
        if lp is not None and lp<0.15: flags.append("LSRcold")
    return flags
rows=[]
for t in base["trades"]:
    d=t["dir"]; dt=t["entry_dt"]
    fz=zscore(fund_d,dt,30); oic=oi_chg(dt,30); lp=pctile(lsr_d,dt,90)
    fh=dist_from_high(dt); fl=dist_from_low(dt); x=(closes[dt_idx[dt]]-e150[dt_idx[dt]])/e150[dt_idx[dt]]*100 if not math.isnan(e150[dt_idx[dt]]) else None
    ci=cir(dt)
    out = "SL" if t["reason"]=="SL" else ("win" if t["net_pct"]>0 else "loss")
    flags=verdict(d,fz,oic,lp,fh,fl)
    # "against-trend exhaustion" = entering INTO an extreme that favors reversal
    against = ((d=="long" and "@HIGH" in flags and (len(flags)>=2)) or
               (d=="short" and "@LOW" in flags and (len(flags)>=2)))
    rows.append((out, against, d))
    def f(v,fmt="{:>6.2f}"): return fmt.format(v) if v is not None else "   n/a"
    print(f"{dt:11} {d:5} {out:5} {t['net_pct']:>+6.1f}% | {f(fz)} {f(oic,'{:>+6.0f}')} "
          f"{f(lp,'{:>5.2f}')} | {f(fh,'{:>+6.0f}')} {f(fl,'{:>+6.0f}')} {f(x,'{:>+6.1f}')} {ci:>4.2f} | "
          f"{'AGAINST-EXTREME' if against else ''} {' '.join(flags)}")

# summary: does 'against-extreme' separate outcomes?
print("\n── does 'entering AGAINST an HTF exhaustion extreme' predict trouble? ──")
ae=[r for r in rows if r[1]]; ok=[r for r in rows if not r[1]]
def wr(rs):
    if not rs: return "none"
    w=sum(1 for r in rs if r[0]=="win"); sl=sum(1 for r in rs if r[0]=="SL")
    return f"n={len(rs):>2}  win%={w/len(rs)*100:3.0f}%  SLrate={sl/len(rs)*100:3.0f}%"
print(f"  AGAINST-EXTREME entries: {wr(ae)}")
print(f"  normal entries:          {wr(ok)}")

# deep dive: Oct-2025 long vs Nov-2024 long (both near-ATH breakout longs)
print("\n── DEEP DIVE: two near-ATH long breakouts, opposite outcomes ──")
for dt,label in [("2024-11-09","WON +28%"),("2025-10-05","SL -10% (Oct-10 crash)")]:
    fz=zscore(fund_d,dt,30); oic=oi_chg(dt,30); lp=pctile(lsr_d,dt,90)
    fh=dist_from_high(dt); ci=cir(dt)
    print(f"  {dt} ({label}): fundZ={fz if fz is None else round(fz,2)}  "
          f"OIΔ30d={oic if oic is None else round(oic,0)}%  LSR%ile={lp if lp is None else round(lp,2)}  "
          f"fromHigh={fh if fh is None else round(fh,1)}%  CIR={round(ci,2)}")
