"""Trade audit: behavior-vs-spec compliance + live-paper results + paper-vs-sim.

LIVE  = enabled variant 'p300_aggressive_v2_v1_0'.
SIM   = canonical full replay 'p300_aggressive_v2_v1_0__replay_full_v9_consume_open'.
Read-only.
"""
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

DB = r"c:\Source\Repos\p300\data\databases\prod.db"
LIVE = "p300_aggressive_v2_v1_0"
SIM = "p300_aggressive_v2_v1_0__replay_full_v9_consume_open"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql, a=(): con.execute(sql, a).fetchall()


def parse(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except Exception:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


# Documented rules. wd: Mon=0..Sun=6. hr in UTC. hold_h = (min,max) hours.
RULES = {
    "JPLUS_R4_BTC":    dict(asset={"BTC"}, dir={"LONG"}, entry_wd={0}, entry_hr={6}, hold_h=(11, 13)),
    "JPLUS_R4_ETH":    dict(asset={"ETH"}, dir={"LONG"}, entry_wd={1}, entry_hr={20}, hold_h=(23, 25)),
    "JPLUS_R4_BTC_V2": dict(asset={"BTC"}, dir={"LONG"}, entry_wd={2, 4}, entry_hr={4}, hold_h=(9, 11)),
    "JPLUS_R4_ETH_V2": dict(asset={"ETH"}, dir={"LONG"}, entry_wd={2, 4}, entry_hr={4}, hold_h=(9, 11)),
    "THU_BEAR":        dict(asset={"BTC", "ETH"}, dir={"SHORT"}, entry_wd={3}, hold_h=(20, 28)),
    "FOMC":            dict(asset={"BTC"}, dir={"LONG"}, entry_hr={8, 9}, hold_h=(8, 13)),
    "CARRY":           dict(asset={"BTC"}, dir={"LONG"}),
    "PDO_RETOUCH":     dict(asset={"BTC", "ETH"}, dir={"LONG"}),
    "CPR":             dict(asset={"BTC", "ETH"}, dir={"LONG"}),
    "CHENTO_TRIPLE_V3": dict(asset={"BTC"}, dir={"LONG", "SHORT"}, entry_min15=True),
    "ADX":             dict(asset={"BTC"}, dir={"LONG", "SHORT"}),
    "JPLUS_EMA_BTC":   dict(asset={"BTC"}, dir={"LONG", "SHORT"}),
    "JPLUS_ETH_DAILY": dict(asset={"ETH"}, dir={"LONG"}),
    "AI_QUANT":        dict(asset={"BTC"}, dir={"LONG", "SHORT", "FLAT"}),
    "SHORT_SQUEEZE":   dict(asset={"BTC"}, dir={"LONG"}),
}


def violations(strategy, t):
    r = RULES.get(strategy)
    if not r:
        return ["NO_RULE"]
    v = []
    asset = (t["asset"] or "").upper()
    direction = (t["direction"] or "").upper()
    if r.get("asset") and asset not in r["asset"]:
        v.append(f"asset:{asset}")
    if r.get("dir") and direction not in r["dir"]:
        v.append(f"dir:{direction}")
    et = parse(t["entry_time"])
    if et:
        if "entry_wd" in r and et.weekday() not in r["entry_wd"]:
            v.append(f"entry_wd:{et.weekday()}")
        if "entry_hr" in r and et.hour not in r["entry_hr"]:
            v.append(f"entry_hr:{et.hour}")
        if r.get("entry_min15") and et.minute % 15 != 0:
            v.append(f"entry_min:{et.minute}")
    xt = parse(t["exit_time"])
    if et and xt and "hold_h" in r:
        h = (xt - et).total_seconds() / 3600.0
        lo, hi = r["hold_h"]
        if not (lo <= h <= hi):
            v.append(f"hold_h:{h:.1f}")
    return v


def audit(variant, label):
    rows = q("SELECT * FROM trades WHERE strategy_variant=? AND status='closed'", (variant,))
    print(f"\n########## BEHAVIOR AUDIT — {label} ({variant}) — {len(rows)} closed trades ##########")
    by = defaultdict(list)
    for t in rows:
        by[t["strategy"]].append(t)
    print(f"  {'sleeve':<18} {'n':>4} {'clean':>5} {'viol':>4}  top violations")
    for s in sorted(by):
        ts = by[s]
        vc = defaultdict(int)
        nviol = 0
        for t in ts:
            vs = violations(s, t)
            if vs:
                nviol += 1
                for x in vs:
                    vc[x.split(":")[0]] += 1
        clean = len(ts) - nviol
        top = ", ".join(f"{k}×{n}" for k, n in sorted(vc.items(), key=lambda x: -x[1])[:4])
        print(f"  {s:<18} {len(ts):>4} {clean:>5} {nviol:>4}  {top}")


def results(variant, label):
    rows = q("SELECT * FROM trades WHERE strategy_variant=? AND status='closed'", (variant,))
    by = defaultdict(list)
    for t in rows:
        by[t["strategy"]].append(t)
    print(f"\n########## RESULTS — {label} ({variant}) ##########")
    print(f"  {'sleeve':<18} {'n':>4} {'WR%':>6} {'meanPnL%':>9} {'totPnL$':>10}")
    out = {}
    for s in sorted(by):
        ts = by[s]
        pnl = [(t["realized_pnl_usdt"] if t["realized_pnl_usdt"] is not None else (t["pnl_usdt"] or 0.0)) for t in ts]
        pct = [t["pnl_pct"] for t in ts if t["pnl_pct"] is not None]
        wins = sum(1 for p in pnl if p > 0)
        wr = 100.0 * wins / len(ts) if ts else 0
        mpct = sum(pct) / len(pct) if pct else float("nan")
        print(f"  {s:<18} {len(ts):>4} {wr:>5.1f}% {mpct:>8.3f}% {sum(pnl):>10.2f}")
        out[s] = dict(n=len(ts), wr=wr, mpct=mpct, tot=sum(pnl))
    return out


# Coverage matrix
print("=== strategy coverage (closed-trade counts) ===")
live_s = {r["strategy"]: r["n"] for r in q("SELECT strategy, COUNT(*) n FROM trades WHERE strategy_variant=? AND status='closed' GROUP BY strategy", (LIVE,))}
sim_s = {r["strategy"]: r["n"] for r in q("SELECT strategy, COUNT(*) n FROM trades WHERE strategy_variant=? AND status='closed' GROUP BY strategy", (SIM,))}
alls = sorted(set(RULES) | set(live_s) | set(sim_s))
print(f"  {'sleeve':<18} {'live':>5} {'sim_v9':>7}")
for s in alls:
    print(f"  {s:<18} {live_s.get(s, 0):>5} {sim_s.get(s, 0):>7}")

audit(SIM, "SIM v9")
audit(LIVE, "LIVE")
sim_res = results(SIM, "SIM v9")
live_res = results(LIVE, "LIVE")

print("\n########## PAPER vs SIM (per sleeve, WR & mean pnl%) ##########")
print(f"  {'sleeve':<18} {'live n':>6} {'live WR':>8} {'live%':>8}   {'sim n':>6} {'sim WR':>7} {'sim%':>8}")
for s in sorted(set(live_res) | set(sim_res)):
    lv = live_res.get(s)
    sm = sim_res.get(s)
    lp = f"{lv['n']:>6} {lv['wr']:>7.1f}% {lv['mpct']:>7.3f}%" if lv else f"{'-':>6} {'-':>8} {'-':>8}"
    sp = f"{sm['n']:>6} {sm['wr']:>6.1f}% {sm['mpct']:>7.3f}%" if sm else f"{'-':>6} {'-':>7} {'-':>8}"
    print(f"  {s:<18} {lp}   {sp}")

con.close()
