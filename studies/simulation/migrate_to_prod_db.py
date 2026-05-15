"""One-shot migration: data/trader.db + data/dashboard.db -> data/prod.db (P2.6).

The two-DB split (market data vs bot state) is historical and adds
friction: two paths to monkeypatch in tests, two backups to take, two
schemas to keep in sync. P2.6 consolidates them into a single
``prod.db``.

Schema audit (2026-05-15) confirmed no table-name collisions between
the two sources; both sets of tables move to ``prod.db`` unchanged.

Run order:
  1. Stop the bot.
  2. Back up: ``cp data/trader.db data/trader.db.bak_pre_prod_consol;
                cp data/dashboard.db data/dashboard.db.bak_pre_prod_consol``.
  3. ``python studies/simulation/migrate_to_prod_db.py``
  4. Update strategies/support/db.py to point at prod.db (or rely on
     this commit having already done so).
  5. Restart.

The script is idempotent — re-running on an already-populated prod.db
overwrites it. ``--dry-run`` reports the table list and row counts
without writing.

Implementation: ``ATTACH DATABASE`` both sources into a fresh prod.db
and run ``CREATE TABLE prod.<t> AS SELECT * FROM src.<t>`` per table.
The CREATE TABLE AS preserves columns + data but NOT indexes — those
get re-created from each source DB's ``sqlite_master`` after the copy.
Row counts post-migration are compared to source counts; mismatch
fails the script.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
DEFAULT_TRADER = REPO / "data" / "trader.db"
DEFAULT_DASH = REPO / "data" / "dashboard.db"
DEFAULT_PROD = REPO / "data" / "prod.db"


def _table_names(con: sqlite3.Connection, schema: str = "main") -> list[str]:
    rows = con.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
        f"AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _row_count(con: sqlite3.Connection, schema: str, table: str) -> int:
    return con.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"').fetchone()[0]


def _index_ddls(src_path: Path) -> list[str]:
    """Pull every CREATE INDEX statement from the source DB."""
    con = sqlite3.connect(str(src_path))
    try:
        return [r[0] for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()]
    finally:
        con.close()


def _copy_one_source(prod_path: Path,
                     src_path: Path,
                     src_alias: str,
                     dry_run: bool) -> dict[str, tuple[int, int]]:
    """ATTACH src and copy all tables to prod. Return {table: (src_n, prod_n)}."""
    con = sqlite3.connect(str(prod_path))
    counts: dict[str, tuple[int, int]] = {}
    try:
        con.execute(f"ATTACH DATABASE ? AS {src_alias}", (str(src_path),))
        tables = _table_names(con, src_alias)
        for t in tables:
            src_n = _row_count(con, src_alias, t)
            if dry_run:
                counts[t] = (src_n, -1)
                continue
            # Drop existing prod table if any (idempotent re-run).
            con.execute(f'DROP TABLE IF EXISTS main."{t}"')
            con.execute(f'CREATE TABLE main."{t}" AS SELECT * FROM {src_alias}."{t}"')
            prod_n = _row_count(con, "main", t)
            counts[t] = (src_n, prod_n)
        con.commit()
        con.execute(f"DETACH DATABASE {src_alias}")
    finally:
        con.close()
    return counts


def _recreate_indexes(prod_path: Path, src_path: Path) -> int:
    """Recreate each index from src_path inside prod.db."""
    ddls = _index_ddls(src_path)
    if not ddls:
        return 0
    con = sqlite3.connect(str(prod_path))
    try:
        n_created = 0
        for ddl in ddls:
            try:
                con.execute(ddl)
                n_created += 1
            except sqlite3.OperationalError as e:
                # Likely "index already exists" if a re-run — safe to skip.
                if "already exists" not in str(e):
                    raise
        con.commit()
    finally:
        con.close()
    return n_created


def consolidate(trader_db: Path,
                dash_db: Path,
                prod_db: Path,
                dry_run: bool = False) -> dict:
    """Run the migration. Returns a structured summary."""
    if not trader_db.exists():
        raise SystemExit(f"trader DB missing: {trader_db}")
    if not dash_db.exists():
        raise SystemExit(f"dashboard DB missing: {dash_db}")
    if not dry_run and prod_db.exists():
        # Fresh start each run to keep idempotency simple.
        prod_db.unlink()
    if not dry_run:
        prod_db.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file so ATTACH inside _copy_one_source can connect.
        sqlite3.connect(str(prod_db)).close()

    trader_counts = _copy_one_source(prod_db, trader_db, "trader", dry_run)
    dash_counts = _copy_one_source(prod_db, dash_db, "dash", dry_run)

    n_idx_trader = 0
    n_idx_dash = 0
    if not dry_run:
        n_idx_trader = _recreate_indexes(prod_db, trader_db)
        n_idx_dash = _recreate_indexes(prod_db, dash_db)

    # Verify counts match.
    mismatches: list[str] = []
    for src_name, counts in (("trader", trader_counts), ("dash", dash_counts)):
        for t, (src_n, prod_n) in counts.items():
            if dry_run:
                continue
            if src_n != prod_n:
                mismatches.append(f"{src_name}.{t}: src={src_n} prod={prod_n}")

    return {
        "trader_tables": len(trader_counts),
        "dash_tables": len(dash_counts),
        "trader_rows": sum(s for s, _ in trader_counts.values()),
        "dash_rows": sum(s for s, _ in dash_counts.values()),
        "trader_indexes": n_idx_trader,
        "dash_indexes": n_idx_dash,
        "mismatches": mismatches,
        "dry_run": dry_run,
        "trader_counts": trader_counts,
        "dash_counts": dash_counts,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trader-db", type=Path, default=DEFAULT_TRADER)
    ap.add_argument("--dash-db", type=Path, default=DEFAULT_DASH)
    ap.add_argument("--prod-db", type=Path, default=DEFAULT_PROD)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    summary = consolidate(args.trader_db, args.dash_db, args.prod_db,
                           dry_run=args.dry_run)
    action = "would consolidate" if args.dry_run else "consolidated"
    print(f"\n{action}: {args.trader_db} + {args.dash_db} -> {args.prod_db}")
    print(f"  trader: {summary['trader_tables']} tables, "
          f"{summary['trader_rows']:,} rows, "
          f"{summary['trader_indexes']} indexes recreated")
    print(f"  dash:   {summary['dash_tables']} tables, "
          f"{summary['dash_rows']:,} rows, "
          f"{summary['dash_indexes']} indexes recreated")
    if summary["mismatches"]:
        print("ROW-COUNT MISMATCHES:")
        for m in summary["mismatches"]:
            print(f"  !! {m}")
        return 2
    if not args.dry_run:
        print("OK — row counts match between source and prod.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
