"""Characterize the two problems the user named:
  (1) losing entries  — which trades lose, and is there a pattern?
  (2) missed entries   — ADX>=25 crossings where no trade fired, and why.

Also splits performance by direction and tags each trade with regime context
(BTC 30d return, above/below 200D EMA) to see where losers concentrate.
"""
from __future__ import annotations
import math
from harness import (load_btc_daily, run, fmt, adx, ema, di_series,
                     ADX_PERIOD, ADX_LOW, ADX_HIGH, EMA_LEN, TREND_EMA_LEN)

c = load_btc_daily()
closes = [x["close"] for x in c]
a = adx(c, ADX_PERIOD)
e50 = ema(closes, EMA_LEN)
e150 = ema(closes, TREND_EMA_LEN)
e200 = ema(closes, 200)
pdi, mdi = di_series(c, ADX_PERIOD)
START = "2018-01-01"

base = run(c, START)
print("="*92)
print(fmt(base, "BASELINE (all)"))

# ── (A) direction split ───────────────────────────────────────────────────────
longs  = [t for t in base["trades"] if t["dir"] == "long"]
shorts = [t for t in base["trades"] if t["dir"] == "short"]
def agg(ts):
    if not ts: return "n=0"
    w = [t for t in ts if t["net_pct"] > 0]
    cum = 1.0
    for t in ts: cum *= (1+t["net_pct"]/100)
    sl = [t for t in ts if t["reason"] == "SL"]
    return (f"n={len(ts):>2}  WR={len(w)/len(ts)*100:3.0f}%  "
            f"cum={cum*100-100:+8.0f}%  SLhits={len(sl)}  "
            f"sumPnl={sum(t['net_pct'] for t in ts):+7.1f}%")
print("\n── direction split ──")
print(f"  LONG :  {agg(longs)}")
print(f"  SHORT:  {agg(shorts)}")

# ── (B) regime context per trade ──────────────────────────────────────────────
dt_idx = {x["dt"]: i for i, x in enumerate(c)}
def ctx_at(dt):
    i = dt_idx.get(dt)
    if i is None or i < 30: return None
    ret30 = (closes[i]-closes[i-30])/closes[i-30]*100
    above200 = (not math.isnan(e200[i])) and closes[i] > e200[i]
    return ret30, above200
print("\n── losers with regime context (ret30d at entry, above/below 200D) ──")
print(f"  {'dir':5} {'entry':11} {'pnl':>8}  {'ret30d':>7}  200D  entryADX")
losers = [t for t in base["trades"] if t["net_pct"] <= 0]
for t in losers:
    cx = ctx_at(t["entry_dt"])
    r30 = f"{cx[0]:+.0f}%" if cx else "?"
    a2 = "above" if (cx and cx[1]) else "below"
    print(f"  {t['dir']:5} {t['entry_dt']:11} {t['net_pct']:>+7.2f}%  {r30:>7}  {a2:5} {t.get('entry_adx')}")

print("\n── winners with regime context ──")
for t in [t for t in base["trades"] if t["net_pct"] > 0]:
    cx = ctx_at(t["entry_dt"])
    r30 = f"{cx[0]:+.0f}%" if cx else "?"
    a2 = "above" if (cx and cx[1]) else "below"
    print(f"  {t['dir']:5} {t['entry_dt']:11} {t['net_pct']:>+7.2f}%  {r30:>7}  {a2:5} {t.get('entry_adx')}")

# ── (C) missed ADX>=25 crossings ──────────────────────────────────────────────
# Replay the state machine but record EVERY entry_event and why it did / didn't
# produce a trade.
print("\n── missed / blocked entry events (ADX crossed 25 from compression) ──")
was_low = False
in_pos = None  # 'long'/'short'
events = []
for i, bar in enumerate(c):
    if math.isnan(a[i]) or math.isnan(e50[i]): continue
    if bar["dt"] < START:
        if a[i] < ADX_LOW: was_low = True
        continue
    if a[i] < ADX_LOW:
        was_low = True
        in_pos = None  # ADX<20 exits
    if was_low and a[i] >= ADX_HIGH:
        new_dir = "long" if closes[i] > e50[i] else "short"
        di_dir = "long" if pdi[i] >= mdi[i] else "short"
        block = None
        if new_dir == "long" and not math.isnan(e150[i]) and bar["close"] <= e150[i]:
            block = "trendfilter(long<EMA150)"
        events.append(dict(dt=bar["dt"], dir=new_dir, di=di_dir, adx=round(a[i],1),
                           block=block, agree=(new_dir==di_dir)))
        was_low = False
        if block is None:
            in_pos = new_dir

n_block = sum(1 for e in events if e["block"])
n_disagree = sum(1 for e in events if not e["agree"])
print(f"  total entry events: {len(events)}   trend-filter-blocked: {n_block}   "
      f"DI disagrees w/ EMA50 dir: {n_disagree}")
for e in events:
    tag = e["block"] or ("DI-DISAGREE" if not e["agree"] else "")
    print(f"  {e['dt']:11} {e['dir']:5} adx={e['adx']:>4}  DI={e['di']:5} {tag}")

# ── (D) big BTC moves while flat (true missed opportunity) ────────────────────
# 30-bar forward move >35% where strategy held no position at the start bar.
print("\n── 30d BTC moves >35% (context for 'missed many entries') ──")
# build in-position timeline from baseline trades
pos_days = set()
for t in base["trades"]:
    si = dt_idx.get(t["entry_dt"]);
    ei = dt_idx.get(str(t["exit_dt"]).replace(" (open)",""))
    if si is None: continue
    if ei is None: ei = len(c)-1
    for k in range(si, ei+1): pos_days.add(k)
moves = []
i = 0
while i < len(c)-30:
    if c[i]["dt"] < START: i+=1; continue
    fwd = (closes[i+30]-closes[i])/closes[i]*100
    if abs(fwd) > 35:
        flat = i not in pos_days
        moves.append((c[i]["dt"], fwd, "FLAT" if flat else "in-pos", round(a[i],1)))
        i += 30
    else:
        i += 1
for dt, fwd, st, adxv in moves:
    print(f"  {dt:11} fwd30={fwd:+6.0f}%  {st:6} adx@start={adxv}")
