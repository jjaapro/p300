"""Backfill created_at on the trades and variants the 2026-05-18 PK rebuild left NULL (BACKLOG item 20).

The rebuild dropped created_at's DEFAULT and nothing wrote the column, so every bot-era trade and variant carried
NULL — and ledger_coherence, which keys its OPEN/CLOSE/seq checks on created_at, audited none of them for months.
The writers now set it (strategies/trades.py, variant_registry.py, same commit) and the reader falls back to
actual_entry_time; this fills the rows already there from the times they certainly have:

    trades.created_at   <- actual_entry_time   (the paper open's own clock; identical to what the writer now stores)
    variants.created_at <- the variant's `registered` event in variant_events (written by the same call)

    python data/migrations/2026_09_19_created_at_backfill.py --dry-run
    python data/migrations/2026_09_19_created_at_backfill.py

Writes results to data/diagnostics/created_at_backfill_20260919.json (the ids touched, so a revert is
`UPDATE ... SET created_at = NULL WHERE id IN (...)`). One transaction; safe alongside the running bots, which
never read created_at.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "diagnostics" / "created_at_backfill_20260919.json"


def plan(con: sqlite3.Connection) -> dict:
    trades = con.execute(
        "SELECT id, actual_entry_time, entry_time FROM trades WHERE created_at IS NULL ORDER BY id").fetchall()
    variants = con.execute("""
        SELECT v.id, (SELECT MIN(e.timestamp) FROM variant_events e
                      WHERE e.variant_id = v.id AND e.event_type = 'registered') AS registered,
               (SELECT MIN(COALESCE(t.actual_entry_time, t.entry_time)) FROM trades t
                      WHERE t.strategy_variant = v.id) AS first_trade
        FROM variants v WHERE v.created_at IS NULL ORDER BY v.id""").fetchall()
    return {
        "trades": [{"id": i, "created_at": a or e, "source": "actual_entry_time" if a else "entry_time"}
                   for i, a, e in trades],
        "variants": [{"id": i, "created_at": r or f, "source": "registered_event" if r else ("first_trade" if f else None)}
                     for i, r, f in variants],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    con = sqlite3.connect(str(args.db or db.PROD_DB), timeout=30)
    try:
        p = plan(con)
        unfillable = [r for r in p["trades"] + p["variants"] if not r["created_at"]]
        print(f"trades with NULL created_at: {len(p['trades'])}; variants: {len(p['variants'])}; "
              f"rows with no source at all: {len(unfillable)}")
        for r in p["trades"][:3] + p["variants"][:3]:
            print("  e.g.", r)
        if args.dry_run:
            print("dry run — nothing written")
            return 0
        con.execute("BEGIN")
        nt = sum(con.execute("UPDATE trades SET created_at = ? WHERE id = ? AND created_at IS NULL",
                             (r["created_at"], r["id"])).rowcount for r in p["trades"] if r["created_at"])
        nv = sum(con.execute("UPDATE variants SET created_at = ? WHERE id = ? AND created_at IS NULL",
                             (r["created_at"], r["id"])).rowcount for r in p["variants"] if r["created_at"])
        con.execute("COMMIT")
        left = con.execute("SELECT (SELECT COUNT(*) FROM trades WHERE created_at IS NULL), "
                           "(SELECT COUNT(*) FROM variants WHERE created_at IS NULL)").fetchone()
        print(f"backfilled {nt} trades and {nv} variants; NULL left: trades {left[0]}, variants {left[1]}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"applied_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "db": str(args.db or db.PROD_DB), **p}, indent=1), encoding="utf-8")
        print("wrote", OUT)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
