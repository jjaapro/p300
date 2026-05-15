"""One-shot migration: SHADOW -> paper in dashboard.db / sim DBs (P2.5).

The P2.5 code rename swaps every code-side literal ``'SHADOW'`` for
``'paper'`` (``trades.execution_mode`` and ``variants.status``). This
script does the matching DB UPDATE on a target dashboard.db so existing
trade + variant rows agree with the new code.

Run order:
  1. Stop the bot (the migration is not safe mid-tick).
  2. Back up the DB: ``cp data/dashboard.db data/dashboard.db.pre_paper_rename``.
  3. ``python studies/simulation/migrate_shadow_to_paper.py --dash-db data/dashboard.db``
  4. Restart the bot.

The script is idempotent — re-running on an already-migrated DB updates
zero rows. ``--dry-run`` reports the row counts without writing.

Tables and columns rewritten:
  trades.execution_mode   'SHADOW' -> 'paper'
  variants.status         'SHADOW' -> 'paper'
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def migrate(dash_db: Path, dry_run: bool = False) -> dict:
    """Apply the rename and return a summary of rows touched.

    Returns a dict like::

        {'trades_affected': 1234, 'variants_affected': 5, 'dry_run': False}
    """
    if not dash_db.exists():
        raise SystemExit(f"DB does not exist: {dash_db}")
    con = sqlite3.connect(str(dash_db))
    try:
        # Count first (idempotent — reports zero on a re-run).
        n_trades = con.execute(
            "SELECT COUNT(*) FROM trades WHERE execution_mode = 'SHADOW'"
        ).fetchone()[0]
        n_variants = con.execute(
            "SELECT COUNT(*) FROM variants WHERE status = 'SHADOW'"
        ).fetchone()[0]
        if dry_run:
            return {
                "trades_affected": n_trades,
                "variants_affected": n_variants,
                "dry_run": True,
            }
        con.execute(
            "UPDATE trades SET execution_mode = 'paper' "
            "WHERE execution_mode = 'SHADOW'"
        )
        con.execute(
            "UPDATE variants SET status = 'paper' "
            "WHERE status = 'SHADOW'"
        )
        con.commit()
    finally:
        con.close()
    return {
        "trades_affected": n_trades,
        "variants_affected": n_variants,
        "dry_run": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dash-db", required=True, type=Path,
        help="Path to the dashboard.db to migrate.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report counts without writing.",
    )
    args = ap.parse_args(argv)
    summary = migrate(args.dash_db, dry_run=args.dry_run)
    action = "would update" if args.dry_run else "updated"
    print(
        f"[{args.dash_db}] {action}: "
        f"trades={summary['trades_affected']:,} "
        f"variants={summary['variants_affected']:,}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
