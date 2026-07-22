"""Archive-then-delete backtest replay variants out of the live prod.db.

Replay variants (`%__replay%`) are backtest artifacts: 50+ disabled rows
plus their trades — including 400+ permanently-"open" phantom positions —
clutter every operator query. They are NOT reproducible by rerunning
(the code has moved under them), so rows are copied to a standalone,
SQL-queryable archive DB before deletion (D6 decision: archive-then-delete,
SQLite not flat export).

Safety: refuses to run without a fresh (<24h) backup.py snapshot.
Dry-run by default:
  python studies/simulation/archive_replay_variants.py           # report
  python studies/simulation/archive_replay_variants.py --apply   # do it

Idempotent: INSERT OR IGNORE into the archive; re-running after a partial
apply completes the job. The archive lives at data/archive/replay_archive.db
— ATTACH it from audit scripts to query historical replays.
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from strategies.support import db  # noqa: E402

log = logging.getLogger("archive_replays")

ARCHIVE_PATH = REPO / "data" / "archive" / "replay_archive.db"
BACKUP_GLOB = REPO / "data" / "backups"
PATTERN = "%__replay%"

# (table, variant-id column) — every table holding per-variant rows.
# trade_adjustments MUST precede trades: it resolves its rows via the trades
# join, so trades must still be present in main when it runs (first apply on
# 2026-07-22 had them reversed and orphaned 1,731 rows; the archive-aware
# _where below recovers that case on re-run).
TABLES = [
    ("variants", "id"),
    ("trade_adjustments", "trade_id"),      # special: joined via trades
    ("trades", "strategy_variant"),
    ("variant_events", "variant_id"),
    ("variant_daily_returns", "variant_id"),  # may not exist; skipped if so
]


def _fresh_backup_exists() -> bool:
    files = sorted(BACKUP_GLOB.glob("prod-*.db"))
    if not files:
        return False
    age_h = (time.time() - files[-1].stat().st_mtime) / 3600
    log.info(f"newest backup: {files[-1].name} ({age_h:.1f}h old)")
    return age_h < 24


def _table_exists(con: sqlite3.Connection, schema: str, table: str) -> bool:
    return con.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _ensure_archive_table(con: sqlite3.Connection, table: str) -> None:
    """Mirror the live table's DDL into the archive schema (no indexes —
    the archive is cold storage)."""
    if _table_exists(con, "archive", table):
        return
    ddl = con.execute(
        "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()[0]
    m = re.match(r"CREATE TABLE (IF NOT EXISTS )?(.*)", ddl,
                 re.IGNORECASE | re.DOTALL)
    con.execute(f"CREATE TABLE archive.{m.group(2)}")


def _where(table: str, col: str, archive_attached: bool = False) -> str:
    if table == "trade_adjustments":
        sub = (f"SELECT id FROM main.trades "
               f"WHERE strategy_variant LIKE '{PATTERN}'")
        if archive_attached:
            # recovery path: parent trades may already sit in the archive
            sub += (f" UNION SELECT id FROM archive.trades "
                    f"WHERE strategy_variant LIKE '{PATTERN}'")
        return f"{col} IN ({sub})"
    return f"{col} LIKE '{PATTERN}'"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Copy to archive and DELETE from live "
                         "(default: dry-run report).")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.apply and not _fresh_backup_exists():
        log.error("REFUSED: no <24h backup found — run `python backup.py` first")
        return 2

    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        con.execute("ATTACH DATABASE ? AS archive", (str(ARCHIVE_PATH),))

        n_variants = con.execute(
            "SELECT COUNT(*) FROM main.variants WHERE id LIKE ?",
            (PATTERN,)).fetchone()[0]
        log.info(f"replay variants in live DB: {n_variants}")

        total_moved = 0
        for table, col in TABLES:
            if not _table_exists(con, "main", table):
                log.info(f"  {table:22} (absent — skipped)")
                continue
            where = _where(table, col,
                           archive_attached=_table_exists(
                               con, "archive", "trades"))
            n = con.execute(
                f"SELECT COUNT(*) FROM main.{table} WHERE {where}"
            ).fetchone()[0]
            if not args.apply:
                log.info(f"  {table:22} would archive+delete {n:>7,} rows")
                continue

            _ensure_archive_table(con, table)
            cols = [r[1] for r in con.execute(
                f"PRAGMA main.table_info({table})").fetchall()]
            collist = ", ".join(f'"{c}"' for c in cols)
            con.execute(
                f"INSERT OR IGNORE INTO archive.{table} ({collist}) "
                f"SELECT {collist} FROM main.{table} WHERE {where}")
            in_archive = con.execute(
                f"SELECT COUNT(*) FROM archive.{table} WHERE {where}"
            ).fetchone()[0]
            if in_archive < n:
                log.error(f"  {table}: archive holds {in_archive} < live {n} "
                          f"— NOT deleting")
                con.rollback()
                return 1
            con.execute(f"DELETE FROM main.{table} WHERE {where}")
            log.info(f"  {table:22} archived+deleted {n:>7,} rows "
                     f"(archive now {in_archive:,})")
            total_moved += n

        if args.apply:
            con.commit()
            remaining = con.execute(
                "SELECT COUNT(*) FROM main.variants").fetchone()[0]
            opens = con.execute(
                "SELECT COUNT(*) FROM main.trades WHERE status='open'"
            ).fetchone()[0]
            log.info(f"\ndone: {total_moved:,} rows moved to "
                     f"{ARCHIVE_PATH.name}; variants remaining {remaining}; "
                     f"open trades remaining {opens}")
        else:
            log.info("dry-run only — rerun with --apply")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
