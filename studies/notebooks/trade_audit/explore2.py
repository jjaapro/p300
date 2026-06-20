"""Identify LIVE paper vs REPLAY/sim variants, and locate sim daily-return data."""
import sqlite3

DB = r"c:\Source\Repos\p300\data\databases\prod.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql, a=(): con.execute(sql, a).fetchall()


def days_between(a, b):
    from datetime import datetime
    def parse(s):
        s = s.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None
    da, db_ = parse(a), parse(b)
    if da is None or db_ is None:
        return None
    return abs((da - db_).total_seconds()) / 86400.0


print("=== variants table columns ===")
print("  " + ", ".join(r["name"] for r in q("PRAGMA table_info(variants)")))

print("\n=== variants rows (id, name + any status-ish cols) ===")
cols = [r["name"] for r in q("PRAGMA table_info(variants)")]
sel = [c for c in cols if c in ("id", "name", "status", "is_live", "active",
                                 "mode", "enabled", "execution_mode", "created_at")]
for r in q(f"SELECT {', '.join(sel)} FROM variants"):
    print("  " + " | ".join(f"{c}={r[c]!r}" for c in sel))

print("\n=== per strategy_variant: live-like vs replay (created_at vs entry_time) ===")
print(f"  {'variant':<55} {'n':>4} {'live%':>6}  entry_range / created_range")
rows = q("""SELECT strategy_variant, entry_time, created_at FROM trades""")
from collections import defaultdict
agg = defaultdict(list)
for r in rows:
    agg[r["strategy_variant"]].append((r["entry_time"], r["created_at"]))
for sv in sorted(agg):
    items = agg[sv]
    gaps = [days_between(e, c) for e, c in items if e and c]
    gaps = [g for g in gaps if g is not None]
    live_like = sum(1 for g in gaps if g < 2.0)
    pct = 100.0 * live_like / len(gaps) if gaps else 0.0
    ents = sorted(e for e, c in items if e)
    crts = sorted(c for e, c in items if c)
    er = f"{ents[0][:10]}..{ents[-1][:10]}" if ents else "?"
    cr = f"{crts[0][:10]}..{crts[-1][:10]}" if crts else "?"
    print(f"  {sv:<55} {len(items):>4} {pct:>5.0f}%  entry {er}  created {cr}")

print("\n=== variant_daily_returns schema + coverage ===")
print("  " + ", ".join(r["name"] for r in q("PRAGMA table_info(variant_daily_returns)")))
for r in q("""SELECT strategy_variant, COUNT(*) n, MIN(date) mn, MAX(date) mx
              FROM variant_daily_returns GROUP BY strategy_variant ORDER BY n DESC LIMIT 15"""):
    print(f"  {str(r['strategy_variant']):<55} n={r['n']:<6} {r['mn']}..{r['mx']}")

con.close()
