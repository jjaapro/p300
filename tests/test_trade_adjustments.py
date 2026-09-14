"""trade_adjustments idempotency key: uix_adj_trade_date_type.

record_adjustment treats a UNIQUE violation as an idempotent retry. That only
means something while the (trade_id, event_date, event_type) key exists, and
prod lost it in the 2026-05-18 PK rebuild without anything noticing: no test
ever recorded the same event twice. These tests do, on a fresh init_db()
ledger and on the exact table shape that rebuild left behind, and they run
the one-off migration that puts the key back on prod against a tmp copy of
that shape.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from strategies.support import db as _db_mod
from strategies.support import trade_db
from strategies.support.trade_adjustments import record_adjustment

REPO = Path(__file__).resolve().parents[1]
MIGRATION = REPO / "data" / "migrations" / "2026_09_14_trade_adjustments_unique_index.py"
KEY = ("trade_id", "event_date", "event_type")


def _load(path: Path, name: str):
    """Import a data/migrations script (its filename is not an identifier).
    Registered in sys.modules first: @dataclass looks its module up there.
    No bytecode, so nothing lands in data/migrations/__pycache__."""
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            del sys.modules[name]
            raise
        finally:
            sys.dont_write_bytecode = dont_write
    return sys.modules[name]


def old_shape_ddl(table: str) -> list[str]:
    """CREATE TABLE + indexes for `table` exactly as the 2026-05-18 PK rebuild
    left it in prod — read from that migration, not retyped."""
    pk_rebuild = _load(REPO / "data" / "migrations" / "2026_05_18_add_table_pks.py",
                       "_pk_rebuild_2026_05_18")
    (plan,) = [r for r in pk_rebuild.PLAN if r.table == table]
    return [plan.create_sql.format(new=table), *plan.post_indexes]


def _point_at(monkeypatch, path: Path) -> None:
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)


@pytest.fixture(autouse=True)
def _default_target_is_never_live(tmp_path, monkeypatch):
    """The migration tests run --apply and --rollback, which change the schema,
    and reach their tmp ledger only through --db. Should main() ever stop
    honouring --db, its default target is this absent tmp path (exit 2), never
    the live prod.db. _point_at still overrides it where a test needs a
    ledger."""
    absent = tmp_path / "default_target_absent.db"
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, absent)


def _unique_indexes_on_key(path: Path) -> list[tuple[str, int]]:
    """(name, partial) of every unique index whose columns are exactly KEY."""
    con = sqlite3.connect(str(path))
    try:
        out = []
        for _seq, name, unique, _origin, partial in con.execute(
                "PRAGMA index_list(trade_adjustments)"):
            cols = tuple(r[2] for r in con.execute(f"PRAGMA index_info('{name}')"))
            if unique and cols == KEY:
                out.append((name, partial))
        return out
    finally:
        con.close()


def _snapshot(path: Path) -> tuple[list, list, list]:
    """Rows, index_list and schema SQL of trade_adjustments."""
    con = sqlite3.connect(str(path))
    try:
        return (
            con.execute("SELECT * FROM trade_adjustments ORDER BY id").fetchall(),
            con.execute("PRAGMA index_list(trade_adjustments)").fetchall(),
            con.execute("SELECT type, name, sql FROM sqlite_master "
                        "WHERE tbl_name='trade_adjustments' ORDER BY name").fetchall(),
        )
    finally:
        con.close()


def _insert_event(con, trade_id, seq, event_type, event_time):
    con.execute(
        "INSERT INTO trade_adjustments (trade_id, seq, event_type, event_time, "
        "event_date, qty_delta, fee_usdt, realized_pnl_delta_usdt) "
        "VALUES (?, ?, ?, ?, ?, 1.0, 0.0, 0.0)",
        (trade_id, seq, event_type, event_time, event_time[:10]))


def _old_shape_ledger(path: Path, *, duplicate: bool = False) -> None:
    """Old-shape trades + trade_adjustments with a few events. `duplicate`
    plants the retry the missing key let through: a second same-day OPEN at
    seq+1, which also leaves no seq gap."""
    con = sqlite3.connect(str(path))
    for table in ("trades", "trade_adjustments"):
        for sql in old_shape_ddl(table):
            con.execute(sql)
    con.execute("INSERT INTO trades (id, status) VALUES ('SJ-0001', 'closed')")
    con.execute("INSERT INTO trades (id, status) VALUES ('SJ-0002', 'open')")
    _insert_event(con, "SJ-0001", 0, "OPEN", "2026-08-21T06:15:25+00:00")
    _insert_event(con, "SJ-0001", 1, "CLOSE", "2026-08-21T19:45:00+00:00")
    _insert_event(con, "SJ-0002", 0, "OPEN", "2026-08-22T06:15:00+00:00")
    if duplicate:
        _insert_event(con, "SJ-0002", 1, "OPEN", "2026-08-22T06:15:33+00:00")
    con.commit()
    con.close()


# ─── T1: the key, on a fresh ledger ─────────────────────────────────────────

def test_same_day_retry_is_a_noop(tmp_path, monkeypatch):
    p = tmp_path / "ledger.db"
    _point_at(monkeypatch, p)
    trade_db.init_db()

    def rec(event_type, event_time):
        return record_adjustment(trade_id="SJ-0001", event_type=event_type,
                                 event_time=event_time, qty_delta=1.0)

    assert rec("OPEN", "2026-09-14T10:00:00+00:00") is True
    # A retry later the same UTC day is the case the key exists for.
    assert rec("OPEN", "2026-09-14T18:00:00+00:00") is False
    con = sqlite3.connect(str(p))
    assert con.execute("SELECT COUNT(*) FROM trade_adjustments").fetchone()[0] == 1
    con.close()
    # Only the full triple collides: another type the same day, and the same
    # type the next UTC day, are new events.
    assert rec("CLOSE", "2026-09-14T19:00:00+00:00") is True
    assert rec("OPEN", "2026-09-15T00:30:00+00:00") is True
    con = sqlite3.connect(str(p))
    rows = con.execute("SELECT seq, event_type, event_date FROM trade_adjustments "
                       "ORDER BY seq").fetchall()
    con.close()
    assert rows == [(0, "OPEN", "2026-09-14"), (1, "CLOSE", "2026-09-14"),
                    (2, "OPEN", "2026-09-15")]
    # Exactly one unique index carries the key, and it is the named one — an
    # inline UNIQUE left in the CREATE TABLE as well would show up here twice.
    assert _unique_indexes_on_key(p) == [("uix_adj_trade_date_type", 0)]


# ─── T1b: init_db heals the 2026-05-18 shape, and refuses duplicates ────────

def test_init_db_heals_old_shape_ledger(tmp_path, monkeypatch):
    p = tmp_path / "old.db"
    _old_shape_ledger(p)
    _point_at(monkeypatch, p)
    assert _unique_indexes_on_key(p) == []
    rows_before = _snapshot(p)[0]

    trade_db.init_db()

    assert _unique_indexes_on_key(p) == [("uix_adj_trade_date_type", 0)]
    assert _snapshot(p)[0] == rows_before
    # And the healed ledger now refuses the retry it used to accept.
    assert record_adjustment(trade_id="SJ-0002", event_type="OPEN",
                             event_time="2026-08-22T06:15:33+00:00") is False


def test_old_shape_ledger_accepts_a_same_day_retry(tmp_path, monkeypatch):
    """The twin that shows what the heal is for: on the shape prod has
    carried since 2026-05-18, the same retry lands a second row."""
    p = tmp_path / "old.db"
    _old_shape_ledger(p)
    _point_at(monkeypatch, p)
    assert record_adjustment(trade_id="SJ-0002", event_type="OPEN",
                             event_time="2026-08-22T06:15:33+00:00") is True


def test_init_db_refuses_to_start_on_duplicate_events(tmp_path, monkeypatch):
    p = tmp_path / "dupes.db"
    _old_shape_ledger(p, duplicate=True)
    _point_at(monkeypatch, p)
    before = _snapshot(p)
    opened = []
    real_con = trade_db._con

    def spy_con():
        opened.append(real_con())
        return opened[-1]

    monkeypatch.setattr(trade_db, "_con", spy_con)

    with pytest.raises(sqlite3.IntegrityError) as e:
        trade_db.init_db()

    msg = str(e.value)
    assert "1 duplicate" in msg
    assert "SJ-0002 2026-08-22 OPEN x2" in msg
    assert "uix_adj_trade_date_type" in msg
    # Every bot sharing the ledger stops at its next start on this, so the
    # message names the remedy and the read-only listing tool.
    assert "python backup.py first" in msg
    assert "python data/migrations/2026_09_14_trade_adjustments_unique_index.py" in msg
    # trade_adjustments is untouched: same rows, same indexes, same schema.
    assert _snapshot(p) == before
    # And init_db left no transaction or lock behind for the next writer.
    con = sqlite3.connect(str(p), timeout=0)
    con.execute("BEGIN IMMEDIATE")
    con.rollback()
    con.close()
    # Nor an open connection: the failed CREATE runs in autocommit, so a
    # leaked one holds no lock and the check above cannot see it. The
    # traceback keeps it alive, and on Windows its file handle with it.
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


# ─── The one-off migration, on tmp copies of the old shape ──────────────────

@pytest.fixture
def migration():
    return _load(MIGRATION, "_adj_unique_index_2026_09_14")


def test_migration_applies_verifies_and_rolls_back(tmp_path, monkeypatch, migration, capsys):
    p = tmp_path / "old.db"
    _old_shape_ledger(p)
    rows = _snapshot(p)[0]

    assert migration.main(["--db", str(p)]) == 0              # dry run
    assert "absent" in capsys.readouterr().out
    assert _unique_indexes_on_key(p) == []                     # dry run wrote nothing

    assert migration.main(["--db", str(p), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "created uix_adj_trade_date_type" in out
    assert "held it" in out and "init_db()" in out
    assert _unique_indexes_on_key(p) == [("uix_adj_trade_date_type", 0)]
    assert _snapshot(p)[0] == rows

    # Same index, byte for byte, as init_db() builds on a fresh ledger.
    fresh = tmp_path / "fresh.db"
    _point_at(monkeypatch, fresh)
    trade_db.init_db()
    assert ([s for s in _snapshot(p)[2] if s[1] == "uix_adj_trade_date_type"]
            == [s for s in _snapshot(fresh)[2] if s[1] == "uix_adj_trade_date_type"])

    applied = _snapshot(p)
    assert migration.main(["--db", str(p), "--apply"]) == 0   # idempotent rerun
    assert "Nothing to do" in capsys.readouterr().out
    assert _snapshot(p) == applied
    assert migration.main(["--db", str(p)]) == 0              # post-check dry run
    assert "present" in capsys.readouterr().out

    assert migration.main(["--db", str(p), "--rollback"]) == 0
    assert "next bot start" in capsys.readouterr().out
    assert _unique_indexes_on_key(p) == []
    assert _snapshot(p)[0] == rows
    assert migration.main(["--db", str(p), "--rollback"]) == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_migration_refuses_duplicates_with_exit_3(tmp_path, migration, capsys):
    p = tmp_path / "dupes.db"
    _old_shape_ledger(p, duplicate=True)
    before = _snapshot(p)

    assert migration.main(["--db", str(p)]) == 3
    assert "SJ-0002 2026-08-22 OPEN: 2 rows" in capsys.readouterr().out
    assert migration.main(["--db", str(p), "--apply"]) == 3
    out = capsys.readouterr().out
    assert "1 duplicate" in out
    assert "SJ-0002 2026-08-22 OPEN: 2 rows, ids 3,4, seqs 0,1" in out
    assert _snapshot(p) == before


@pytest.mark.parametrize("ddl", [
    "CREATE INDEX uix_adj_trade_date_type "
    "ON trade_adjustments(trade_id, event_date, event_type)",
    "CREATE UNIQUE INDEX uix_adj_trade_date_type "
    "ON trade_adjustments(trade_id, event_date, event_type) WHERE event_type = 'OPEN'",
    "CREATE UNIQUE INDEX uix_adj_trade_date_type "
    "ON trade_adjustments(event_type, event_date, trade_id)",
], ids=["plain", "partial", "reordered"])
def test_migration_refuses_a_wrong_shape_index_with_exit_4(tmp_path, migration, capsys, ddl):
    """CREATE ... IF NOT EXISTS matches by NAME, so any other index already
    holding the name would make the build a silent no-op. A plain one keys
    nothing; a partial one leaves CLOSE and SCALE retries unkeyed; a reordered
    one is not the declared index, and tests/test_live_schema_constraints.py
    reports it as drift. The dry run says so too."""
    p = tmp_path / "wrong.db"
    _old_shape_ledger(p)
    con = sqlite3.connect(str(p))
    con.execute(ddl)
    con.commit()
    con.close()
    before = _snapshot(p)
    assert migration.main(["--db", str(p)]) == 4
    assert "wrong shape" in capsys.readouterr().out
    assert migration.main(["--db", str(p), "--apply"]) == 4
    assert "wrong shape" in capsys.readouterr().err
    assert _snapshot(p) == before


def test_migration_refuses_a_missing_target(tmp_path, monkeypatch, migration, capsys):
    p = tmp_path / "nope.db"
    for mode in ([], ["--apply"], ["--rollback"]):
        assert migration.main(["--db", str(p), *mode]) == 2
    assert "does not exist" in capsys.readouterr().err
    # Without --db the target is db.PROD_DB, resolved at run time. Checked
    # with the read-only dry run only: should this patch ever stop reaching
    # the default (a PROD_DB captured at import), the call reads the live DB
    # and fails, instead of dropping its index.
    monkeypatch.setattr(_db_mod, "PROD_DB", p)
    assert migration.main([]) == 2
    assert str(p) in capsys.readouterr().err
    assert not p.exists()


def test_migration_refuses_a_file_that_is_not_sqlite(tmp_path, migration, capsys):
    p = tmp_path / "notes.json"
    p.write_bytes(b'{"not": "a database"}\n')
    for mode in ([], ["--apply"], ["--rollback"]):
        assert migration.main(["--db", str(p), *mode]) == 2
    assert "is not an SQLite database" in capsys.readouterr().err
    assert p.read_bytes() == b'{"not": "a database"}\n'


def test_migration_refuses_a_db_without_the_ledger_table(tmp_path, migration, capsys):
    """--db pointed at the wrong SQLite file (a market-data or scratch DB): a
    clean exit 2 in every mode, not a 'no such table' traceback."""
    p = tmp_path / "market.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE btc_1m (ts INTEGER)")
    con.commit()
    con.close()
    before = p.read_bytes()
    for mode in ([], ["--apply"], ["--rollback"]):
        assert migration.main(["--db", str(p), *mode]) == 2
    assert capsys.readouterr().err.count("has no trade_adjustments table") == 3
    assert p.read_bytes() == before


def test_migration_waits_for_the_write_lock_and_checks_under_it(tmp_path, migration, capsys):
    """Bots keep running during --apply. One holds the write lock when --apply
    starts and commits a duplicate as it lets go: --apply must wait for the
    lock rather than fail, and run the duplicate check only once it holds it,
    so it refuses with exit 3 instead of building on a stale read."""
    p = tmp_path / "busy.db"
    _old_shape_ledger(p)
    holder = sqlite3.connect(str(p), isolation_level=None, check_same_thread=False)
    holder.execute("PRAGMA journal_mode=WAL")      # as prod
    holder.execute("BEGIN IMMEDIATE")

    def release():
        _insert_event(holder, "SJ-0002", 1, "OPEN", "2026-08-22T06:15:33+00:00")
        holder.execute("COMMIT")

    timer = threading.Timer(1.0, release)
    timer.start()
    try:
        rc = migration.main(["--db", str(p), "--apply"])
    finally:
        timer.join()
        holder.close()
    assert rc == 3
    assert "SJ-0002 2026-08-22 OPEN: 2 rows" in capsys.readouterr().out
    assert _unique_indexes_on_key(p) == []


@pytest.mark.parametrize("mode", ["--apply", "--rollback"])
def test_migration_exits_5_when_the_write_lock_stays_taken(tmp_path, monkeypatch, migration,
                                                           capsys, mode):
    """A writer that keeps the lock past LOCK_TIMEOUT_S (0 here): exit 5 with
    nothing changed, and BEGIN IMMEDIATE is the only statement sent, so no
    check ever runs on a read taken before the lock."""
    p = tmp_path / "old.db"
    _old_shape_ledger(p)
    if mode == "--rollback":
        assert migration.main(["--db", str(p), "--apply"]) == 0
    before = _snapshot(p)
    sent, busy_timeout_ms = [], []
    real_connect_rw = migration._connect_rw

    def traced_connect_rw(path):
        con = real_connect_rw(path)
        busy_timeout_ms.append(con.execute("PRAGMA busy_timeout").fetchone()[0])
        con.set_trace_callback(sent.append)
        return con

    monkeypatch.setattr(migration, "_connect_rw", traced_connect_rw)
    monkeypatch.setattr(migration, "LOCK_TIMEOUT_S", 0)
    holder = sqlite3.connect(str(p), isolation_level=None)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("BEGIN IMMEDIATE")
    try:
        rc = migration.main(["--db", str(p), mode])
    finally:
        holder.rollback()
        holder.close()
    assert rc == 5
    # LOCK_TIMEOUT_S reaches the connection; without timeout= sqlite3's 5 s
    # default would show here as 5000 and --apply would give up after 5 s.
    assert busy_timeout_ms == [0]
    assert sent == ["BEGIN IMMEDIATE"]
    assert "Nothing changed; rerun" in capsys.readouterr().err
    assert _snapshot(p) == before


def test_migration_cli_runs_from_the_repo_root(tmp_path):
    """The operator runs it as a script, not through pytest's sys.path. A
    subprocess cannot be monkeypatched, so the read-only dry run goes first
    and must name the tmp target before --apply is allowed to run."""
    p = tmp_path / "old.db"
    _old_shape_ledger(p)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def cli(*mode):
        return subprocess.run([sys.executable, "-B", str(MIGRATION), "--db", str(p), *mode],
                              cwd=str(REPO), env=env, capture_output=True, text=True,
                              timeout=120)

    r = cli()
    assert r.returncode == 0, r.stderr
    assert f"target: {p}" in r.stdout
    r = cli("--apply")
    assert r.returncode == 0, r.stderr
    assert _unique_indexes_on_key(p) == [("uix_adj_trade_date_type", 0)]
