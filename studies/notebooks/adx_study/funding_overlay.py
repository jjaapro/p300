"""Funding overlay on the SHORT trades.

The live sleeve keeps counter-trend shorts (no short trend filter) because the
2026-05-04 funding-aware replay found they earn perp funding in bull markets.
The price-only sweep says shorts above EMA150 are SL fodder. This script
settles it: estimate cumulative funding income per short and see whether it
flips the filtered shorts from net-negative to net-positive — and, separately,
whether they still drive the price drawdown (funding can't undo a -10% SL).

Funding convention: a SHORT receives notional*fr each interval when fr>0
(longs pay shorts). Cumulative funding fraction over the hold = sum(fr_close)
across the funding rows in [entry_ts, exit_ts]. cadence is self-consistent
(each row = one interval's rate; 1h early, 8h post-2026-04-13).
"""
from __future__ import annotations
import sqlite3, sys
from datetime import datetime, timezone
sys.path.insert(0, r"c:\Source\Repos\p300")
from strategies.support import db
from harness import run, load_btc_daily, ema

c = load_btc_daily()
closes=[x["close"] for x in c]
e150 = ema(closes, 150)
dt_idx={x["dt"]:i for i,x in enumerate(c)}

# load funding
con=sqlite3.connect(str(db.PROD_DB))
fr=con.execute("SELECT timestamp, fr_close FROM cd_funding_rate ORDER BY timestamp").fetchall()
con.close()
fr_ts=[r[0] for r in fr]; fr_v=[r[1] for r in fr]
import bisect
def funding_sum(ts0, ts1):
    """Sum fr_close over [ts0, ts1). Returns (pct, covered_fraction)."""
    if ts1<=ts0: return 0.0, 0.0
    lo=bisect.bisect_left(fr_ts, ts0); hi=bisect.bisect_left(fr_ts, ts1)
    if lo>=len(fr_ts): return 0.0, 0.0
    s=sum(fr_v[lo:hi])
    # coverage: does funding data span the hold?
    cov = 1.0 if (fr_ts[lo] <= ts0+86400 and (hi==0 or fr_ts[min(hi,len(fr_ts)-1)]>=ts1-2*86400)) else 0.5
    return s*100, cov

def ts_of(dt):
    d=str(dt).replace(" (open)","")
    i=dt_idx.get(d)
    if i is not None: return c[i]["ts"]
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())

base=run(c,"2018-01-01")
shorts=[t for t in base["trades"] if t["dir"]=="short"]

print("="*104)
print("SHORT trades: price P&L vs price+funding, and whether filtered by close<EMA150")
print("="*104)
print(f"{'entry':11} {'exit':12} {'days':>4} {'price%':>8} {'fund%':>7} {'net%':>8} "
      f"{'<E150?':>7} {'reason':10}")
tot_price=tot_fund=0.0
filt_price=filt_fund=0.0
kept_price=kept_fund=0.0
for t in shorts:
    ts0=ts_of(t["entry_dt"]); ts1=ts_of(t["exit_dt"])
    days=max(1,(ts1-ts0)//86400)
    f,cov=funding_sum(ts0,ts1)
    net=t["net_pct"]+f
    i=dt_idx.get(t["entry_dt"])
    below=closes[i]<e150[i] if i is not None else None
    keep = below  # filter keeps only shorts below EMA150
    tag = "keep" if keep else "FILTER"
    covtag="" if cov>=1.0 else " (no-fund-data)"
    print(f"{t['entry_dt']:11} {str(t['exit_dt']):12} {days:>4} {t['net_pct']:>+7.2f}% "
          f"{f:>+6.2f}% {net:>+7.2f}% {str(below):>7} {t['reason']:10}{covtag}")
    tot_price+=t["net_pct"]; tot_fund+=f
    if keep: kept_price+=t["net_pct"]; kept_fund+=f
    else:    filt_price+=t["net_pct"]; filt_fund+=f

print("-"*104)
print(f"ALL shorts (n={len(shorts)}):    sum price {tot_price:+.1f}%   sum funding {tot_fund:+.1f}%   "
      f"sum net {tot_price+tot_fund:+.1f}%")
print(f"KEPT  (<EMA150):     sum price {kept_price:+.1f}%   sum funding {kept_fund:+.1f}%   "
      f"sum net {kept_price+kept_fund:+.1f}%")
print(f"FILTERED (>EMA150):  sum price {filt_price:+.1f}%   sum funding {filt_fund:+.1f}%   "
      f"sum net {filt_price+filt_fund:+.1f}%   <-- these are the ones we'd drop")
print()
print("Interpretation:")
print(" - If FILTERED 'sum net' is clearly negative, dropping them helps even WITH funding.")
print(" - Even if ~breakeven, they still cause the -10% SL price drawdowns the filter removes.")
