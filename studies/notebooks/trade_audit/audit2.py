"""Fix PnL scoring (use pnl_pct for sim), drill live R4 anomalies, check daily-return sources."""
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

DB = r"c:\Source\Repos\p300\data\databases\prod.db"
LIVE = "p300_aggressive_v2_v1_0"
SIM = "p300_aggressive_v2_v1_0__replay_full_v9_consume_open"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql, a=(): con.execute(sql, a).fetchall()
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except Exception:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


print("=== variant_daily_returns: sources + does LIVE have a daily-return series? ===")
for r in q("SELECT source, COUNT(*) n FROM variant_daily_returns GROUP BY source"):
    print(f"  source={r['source']!r}: {r['n']}")
for r in q("SELECT variant_id, source, COUNT(*) n, MIN(date) mn, MAX(date) mx FROM variant_daily_returns WHERE variant_id=? GROUP BY source", (LIVE,)):
    print(f"  LIVE {r['variant_id']} source={r['source']!r} n={r['n']} {r['mn']}..{r['mx']}")


def results_pct(variant, label):
    rows = q("SELECT * FROM trades WHERE strategy_variant=? AND status='closed'", (variant,))
    by = defaultdict(list)
    for t in rows:
        by[t["strategy"]].append(t)
    print(f"\n=== RESULTS (pnl_pct-based) — {label} ===")
    print(f"  {'sleeve':<18} {'n':>4} {'WR%':>6} {'mean%':>8} {'sum%':>8}")
    out = {}
    for s in sorted(by):
        pct = [t["pnl_pct"] for t in by[s] if t["pnl_pct"] is not None]
        if not pct:
            print(f"  {s:<18} {len(by[s]):>4}   (no pnl_pct)")
            continue
        wins = sum(1 for p in pct if p > 0)
        wr = 100.0 * wins / len(pct)
        out[s] = dict(n=len(pct), wr=wr, mean=sum(pct) / len(pct), tot=sum(pct))
        print(f"  {s:<18} {len(pct):>4} {wr:>5.1f}% {sum(pct)/len(pct):>7.3f}% {sum(pct):>7.2f}%")
    return out


sim = results_pct(SIM, "SIM v9")
live = results_pct(LIVE, "LIVE")

print("\n=== LIVE R4 trades — drill (weekday / hold / pnl) ===")
for r in q("""SELECT strategy, entry_time, exit_time, asset, direction, pnl_pct, status
              FROM trades WHERE strategy_variant=? AND strategy LIKE 'JPLUS_R4%'
              ORDER BY strategy, entry_time""", (LIVE,)):
    et, xt = parse(r["entry_time"]), parse(r["exit_time"])
    wd = WD[et.weekday()] if et else "?"
    hold = f"{(xt-et).total_seconds()/3600:.1f}h" if et and xt else "open"
    pct = f"{r['pnl_pct']:.3f}%" if r["pnl_pct"] is not None else "-"
    print(f"  {r['strategy']:<16} {r['entry_time'][:16]} {wd} {r['direction']:<5} hold={hold:<7} pnl={pct:<9} {r['status']}")

con.close()
