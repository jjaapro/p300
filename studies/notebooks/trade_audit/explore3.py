"""Live (enabled) variant per-sleeve results + locate sim baselines."""
import sqlite3

DB = r"c:\Source\Repos\p300\data\databases\prod.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
q = lambda sql, a=(): con.execute(sql, a).fetchall()

live = [r["id"] for r in q("SELECT id FROM variants WHERE enabled=1")]
print("LIVE variants (enabled=1):", live)
ph = ",".join("?" * len(live))

print("\n=== LIVE paper trades per sleeve (status split) ===")
for r in q(f"""SELECT strategy, status, COUNT(*) n,
                 MIN(entry_time) mn, MAX(entry_time) mx
              FROM trades WHERE strategy_variant IN ({ph})
              GROUP BY strategy, status ORDER BY strategy, status""", live):
    print(f"  {str(r['strategy']):<18} {str(r['status']):<8} n={r['n']:<4} {r['mn']}..{r['mx']}")

print("\n=== LIVE closed trades: WR + total PnL per sleeve ===")
for r in q(f"""SELECT strategy, COUNT(*) n,
                 SUM(CASE WHEN COALESCE(realized_pnl_usdt, pnl_usdt, 0) > 0 THEN 1 ELSE 0 END) wins,
                 SUM(COALESCE(realized_pnl_usdt, pnl_usdt, 0)) pnl
              FROM trades WHERE strategy_variant IN ({ph}) AND status='closed'
              GROUP BY strategy ORDER BY pnl""", live):
    wr = 100.0 * r["wins"] / r["n"] if r["n"] else 0
    print(f"  {str(r['strategy']):<18} n={r['n']:<4} WR={wr:>5.1f}%  pnl={r['pnl']:>10.2f}")

print("\n=== variant_daily_returns schema + samples ===")
print("  cols: " + ", ".join(r["name"] for r in q("PRAGMA table_info(variant_daily_returns)")))
for r in q("SELECT * FROM variant_daily_returns LIMIT 3"):
    print("   ", {k: r[k] for k in r.keys()})

print("\n=== 'full' / 2y replay candidates (trade counts) ===")
for r in q("""SELECT strategy_variant, COUNT(*) n, MIN(entry_time) mn, MAX(entry_time) mx
              FROM trades
              WHERE strategy_variant LIKE '%full%' OR strategy_variant LIKE '%2y%'
                 OR strategy_variant LIKE '%current_v5%' OR strategy_variant LIKE '%v9%'
              GROUP BY strategy_variant ORDER BY n DESC"""):
    print(f"  {r['strategy_variant']:<55} n={r['n']:<5} {r['mn'][:10]}..{r['mx'][:10]}")

con.close()
