"""One-shot: put the (trade_id, event_date, event_type) idempotency key back on
prod.db's trade_adjustments as the named unique index uix_adj_trade_date_type.

Background — strategies/support/trade_db.py declared the key inline as
UNIQUE(trade_id, event_date, event_type). The 2026-05-18 PK rebuild
(data/migrations/2026_05_18_add_table_pks.py) re-created the table with
UNIQUE(trade_id, seq) only, and CREATE TABLE IF NOT EXISTS never repairs an
existing table, so prod ran without the key. record_adjustment treats the
key's IntegrityError as an idempotent retry; without it a retry lands a
second row at seq+1. The one live path that could do that was the
close_carry_trade double-close race (fixed in persist_close the same day).

trade_db.init_db() now declares the key ONLY as
CREATE UNIQUE INDEX IF NOT EXISTS uix_adj_trade_date_type, so any bot start
creates it. This script applies it now, without waiting for a restart, and
with a duplicate check the operator can read first.

Modes (the target defaults to strategies.support.db.PROD_DB):
  (no flag)   dry run, read-only: row count, whether the index exists and
              has the exact shape, and every duplicate group.
  --apply     BEGIN IMMEDIATE; duplicate check under the lock; CREATE UNIQUE
              INDEX; verify index_list / index_info under the lock; COMMIT.
  --rollback  BEGIN IMMEDIATE; DROP INDEX; verify it is gone; COMMIT.

Bots do NOT need to be stopped. BEGIN IMMEDIATE takes SQLite's write lock,
so every ledger writer (each a short transaction on its own connection)
waits for this one, and a writer that commits a duplicate first is caught by
the check under the lock. On 354 rows the index build takes a few ms. The
catch for --rollback: trade_db.init_db() re-creates the index at the next
bot start unless that code is reverted first.

Not in scope, recorded elsewhere: the same 2026-05-18 rebuild also dropped
the DEFAULT 0s and REFERENCES trades(id) on this table (harmless: every
writer supplies the columns and foreign keys are off), the NOT NULL/DEFAULTs
on trades, the created_at DEFAULT on ai_quant_decisions; live variants has
no PRIMARY KEY; 288 adjustment rows (SJ-3156..SJ-3299) have no trades row.

Exit codes: 0 done or nothing to do, 2 refused (no such file, not an SQLite
database, no such table; argparse usage errors exit 2 as well), 3 duplicate
groups found (nothing changed), 4 an index with this name exists with the
wrong shape, or verification failed (nothing changed), 5 another connection
kept the write lock past LOCK_TIMEOUT_S (rolled back, nothing changed; rerun).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

INDEX = "uix_adj_trade_date_type"
COLUMNS = ("trade_id", "event_date", "event_type")
# Verbatim from strategies/support/trade_db.py init_db(). SQLite stores the
# statement without IF NOT EXISTS, so both leave identical sqlite_master SQL.
CREATE_SQL = ("CREATE UNIQUE INDEX IF NOT EXISTS uix_adj_trade_date_type "
              "ON trade_adjustments(trade_id, event_date, event_type)")

DUPLICATES_SQL = """
    SELECT trade_id, event_date, event_type, COUNT(*) AS n,
           group_concat(id) AS ids, group_concat(seq) AS seqs
    FROM trade_adjustments
    GROUP BY trade_id, event_date, event_type
    HAVING COUNT(*) > 1
    ORDER BY trade_id, event_date, event_type
"""

REMINDER = ("trade_db.init_db() runs CREATE UNIQUE INDEX IF NOT EXISTS "
            f"{INDEX} at every bot start")

# How long --apply / --rollback wait for a bot or the feed to release the
# write lock. Their transactions last milliseconds; 30 s is a stuck writer.
LOCK_TIMEOUT_S = 30
SQLITE_HEADER = b"SQLite format 3\x00"


def _uri(path: Path, mode: str) -> str:
    # mode=ro / mode=rw never create a missing file, unlike a plain path.
    return f"{path.resolve().as_uri()}?mode={mode}"


def _has_table(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='trade_adjustments'").fetchone() is not None


def index_shape(con: sqlite3.Connection) -> tuple[bool, bool, tuple[str, ...]] | None:
    """(unique, partial, columns) of the index named INDEX, or None."""
    for _seq, name, unique, _origin, partial in con.execute(
            "PRAGMA index_list(trade_adjustments)"):
        if name == INDEX:
            cols = tuple(r[2] for r in con.execute(
                f"PRAGMA index_info({INDEX})"))
            return bool(unique), bool(partial), cols
    return None


def _is_exact(shape) -> bool:
    return shape == (True, False, COLUMNS)


def _print_duplicates(dupes: list[tuple]) -> None:
    print(f"{len(dupes)} duplicate (trade_id, event_date, event_type) group(s):")
    for trade_id, event_date, event_type, n, ids, seqs in dupes:
        print(f"  {trade_id} {event_date} {event_type}: {n} rows, "
              f"ids {ids}, seqs {seqs}")
    print("Nothing changed. Dedupe these deliberately, then rerun. Until then "
          "every bot start fails in trade_db.init_db() with the same list.")


def dry_run(path: Path) -> int:
    con = sqlite3.connect(_uri(path, "ro"), uri=True)
    try:
        if not _has_table(con):
            print(f"refusing: {path} has no trade_adjustments table",
                  file=sys.stderr)
            return 2
        n = con.execute("SELECT COUNT(*) FROM trade_adjustments").fetchone()[0]
        shape = index_shape(con)
        dupes = con.execute(DUPLICATES_SQL).fetchall()
    finally:
        con.close()
    print(f"target: {path}")
    print(f"trade_adjustments rows: {n}")
    if _is_exact(shape):
        print(f"{INDEX} present: UNIQUE on {COLUMNS}. Nothing to do.")
        return 0
    if shape is not None:
        print(f"{INDEX} exists with the wrong shape (unique, partial, columns) "
              f"= {shape}; --apply will refuse. Drop it with --rollback first.")
        return 4
    print(f"{INDEX} absent.")
    if dupes:
        _print_duplicates(dupes)
        return 3
    print("0 duplicate groups. --apply would run, under BEGIN IMMEDIATE:")
    print(f"  {CREATE_SQL}")
    print("Bots need not be stopped: the write lock serialises this against "
          f"their writes. Note that {REMINDER}.")
    return 0


def _connect_rw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(_uri(path, "rw"), uri=True, isolation_level=None,
                           timeout=LOCK_TIMEOUT_S)


def _refuse_busy(path: Path, e: sqlite3.OperationalError) -> int:
    """Exit 5: another connection kept the write lock past LOCK_TIMEOUT_S
    (SQLITE_BUSY). The caller has rolled back, so nothing changed."""
    print(f"refusing: no write lock on {path} within {LOCK_TIMEOUT_S} s ({e}). "
          f"Nothing changed; rerun.", file=sys.stderr)
    return 5


def apply(path: Path) -> int:
    con = _connect_rw(path)
    try:
        t_wait = time.perf_counter()
        con.execute("BEGIN IMMEDIATE")
        t_lock = time.perf_counter()
        if not _has_table(con):
            con.execute("ROLLBACK")
            print(f"refusing: {path} has no trade_adjustments table",
                  file=sys.stderr)
            return 2
        shape = index_shape(con)
        if _is_exact(shape):
            con.execute("ROLLBACK")
            print(f"{INDEX} already present: UNIQUE on {COLUMNS}. Nothing to do.")
            return 0
        if shape is not None:
            con.execute("ROLLBACK")
            print(f"refusing: {INDEX} exists with the wrong shape "
                  f"(unique, partial, columns) = {shape}; CREATE ... IF NOT "
                  f"EXISTS would keep it. Drop it with --rollback first.",
                  file=sys.stderr)
            return 4
        dupes = con.execute(DUPLICATES_SQL).fetchall()
        if dupes:
            con.execute("ROLLBACK")
            _print_duplicates(dupes)
            return 3
        con.execute(CREATE_SQL)
        shape = index_shape(con)
        if not _is_exact(shape):
            con.execute("ROLLBACK")
            print(f"verification failed after CREATE: {INDEX} shape {shape}; "
                  f"rolled back, nothing changed", file=sys.stderr)
            return 4
        con.execute("COMMIT")
        t_done = time.perf_counter()
    except sqlite3.OperationalError as e:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if not getattr(e, "sqlite_errorname", "").startswith("SQLITE_BUSY"):
            raise
        return _refuse_busy(path, e)
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    print(f"created {INDEX}: UNIQUE on {COLUMNS}")
    print(f"waited {(t_lock - t_wait) * 1000:.1f} ms for the write lock, "
          f"held it {(t_done - t_lock) * 1000:.1f} ms")
    print(f"Rollback: rerun with --rollback. Note that {REMINDER}.")
    return 0


def rollback(path: Path) -> int:
    con = _connect_rw(path)
    try:
        t_wait = time.perf_counter()
        con.execute("BEGIN IMMEDIATE")
        t_lock = time.perf_counter()
        if not _has_table(con):
            con.execute("ROLLBACK")
            print(f"refusing: {path} has no trade_adjustments table",
                  file=sys.stderr)
            return 2
        if index_shape(con) is None:
            con.execute("ROLLBACK")
            print(f"{INDEX} absent. Nothing to do.")
            return 0
        con.execute(f"DROP INDEX {INDEX}")
        if index_shape(con) is not None:
            con.execute("ROLLBACK")
            print(f"verification failed: {INDEX} still present; rolled back",
                  file=sys.stderr)
            return 4
        con.execute("COMMIT")
        t_done = time.perf_counter()
    except sqlite3.OperationalError as e:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if not getattr(e, "sqlite_errorname", "").startswith("SQLITE_BUSY"):
            raise
        return _refuse_busy(path, e)
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    print(f"dropped {INDEX}")
    print(f"waited {(t_lock - t_wait) * 1000:.1f} ms for the write lock, "
          f"held it {(t_done - t_lock) * 1000:.1f} ms")
    print(f"This lasts only until the next bot start: {REMINDER}. Revert that "
          f"code first if the rollback must stick.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=f"Create (--apply) or drop (--rollback) {INDEX} on "
                    f"trade_adjustments. Read-only dry run by default.")
    p.add_argument("--db", default=None,
                   help="target DB (default: strategies.support.db.PROD_DB)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="create the index (default is a read-only dry run)")
    mode.add_argument("--rollback", action="store_true",
                      help="drop the index")
    args = p.parse_args(argv)
    if args.db is None:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from strategies.support import db
        path = Path(db.PROD_DB)
    else:
        path = Path(args.db)
    if not path.is_file():
        print(f"refusing: {path} does not exist", file=sys.stderr)
        return 2
    with path.open("rb") as f:
        if f.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            print(f"refusing: {path} is not an SQLite database", file=sys.stderr)
            return 2
    if args.apply:
        return apply(path)
    if args.rollback:
        return rollback(path)
    return dry_run(path)


if __name__ == "__main__":
    raise SystemExit(main())
