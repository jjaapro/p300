"""Explore prod.db: trade table shape, per-sleeve counts, sim-result tables.

Read-only. Run: venv\Scripts\python.exe studies\notebooks\trade_audit\explore.py
"""
import os
import sqlite3

DB = r"c:\Source\Repos\p300\data\databases\prod.db"
SIM = r"c:\Source\Repos\p300\data\databases\sim_dash.db"


def dump(db_path, label):
    print(f"\n########## {label}: {db_path} ##########")
    if not os.path.exists(db_path):
        print("  (file does not exist)")
        return
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    q = lambda sql, a=(): con.execute(sql, a).fetchall()

    print("\n=== TABLES ===")
    for r in q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        try:
            n = q(f"SELECT COUNT(*) c FROM \"{r['name']}\"")[0]["c"]
        except Exception:
            n = "?"
        print(f"  {r['name']:<28} rows={n}")

    if not q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trades'"):
        con.close()
        return

    print("\n=== trades columns ===")
    print("  " + ", ".join(r["name"] for r in q("PRAGMA table_info(trades)")))

    print("\n=== trades.execution_mode ===")
    for r in q("SELECT execution_mode, COUNT(*) n FROM trades GROUP BY execution_mode"):
        print(f"  {r['execution_mode']!r}: {r['n']}")

    print("\n=== trades.status ===")
    for r in q("SELECT status, COUNT(*) n FROM trades GROUP BY status"):
        print(f"  {r['status']!r}: {r['n']}")

    print("\n=== trades.strategy_variant ===")
    for r in q("SELECT strategy_variant, COUNT(*) n FROM trades GROUP BY strategy_variant"):
        print(f"  {r['strategy_variant']!r}: {r['n']}")

    print("\n=== trades by strategy x mode x status (entry_time range) ===")
    for r in q("""SELECT strategy, execution_mode, status, COUNT(*) n,
                         MIN(entry_time) mn, MAX(entry_time) mx
                  FROM trades GROUP BY strategy, execution_mode, status
                  ORDER BY strategy, execution_mode, status"""):
        print(f"  {str(r['strategy']):<18} {str(r['execution_mode']):<7} "
              f"{str(r['status']):<9} n={r['n']:<5} {r['mn']} -> {r['mx']}")

    if q("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_adjustments'"):
        print("\n=== trade_adjustments by event_type ===")
        for r in q("SELECT event_type, COUNT(*) n FROM trade_adjustments GROUP BY event_type"):
            print(f"  {r['event_type']!r}: {r['n']}")

    con.close()


dump(DB, "PROD")
dump(SIM, "SIM")
