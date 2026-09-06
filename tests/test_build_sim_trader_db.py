"""Guards on studies/simulation/build_sim_trader_db.py (review 2026-09-06,
finding 1 + 7): never delete the source, never write the live prod.db,
refuse source tables the plan does not know, build atomically, and say so
when a required table copies nothing."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from studies.simulation import build_sim_trader_db as bld

WINDOW_TS = int(datetime(2024, 6, 3, tzinfo=timezone.utc).timestamp())
OUTSIDE_TS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
ARGS = ["--start", "2024-06-01", "--end", "2024-06-08", "--warmup-days", "10"]


def _make_source(path: Path, extra_ddl: str | None = None) -> Path:
    """A minimal source holding every REQUIRED table (with the right time
    column), one btc_1m row inside the sim window and one far outside."""
    con = sqlite3.connect(str(path))
    for name, mode in bld.TABLE_PLAN:
        if name not in bld.REQUIRED_TABLES:
            continue
        col = {"unix_ms": "open_time", "unix_s": "timestamp"}.get(mode, "date")
        con.execute(f'CREATE TABLE "{name}" ({col} INTEGER, v REAL)')
    con.execute("INSERT INTO btc_1m VALUES (?, 1.0)", (WINDOW_TS * 1000,))
    con.execute("INSERT INTO btc_1m VALUES (?, 2.0)", (OUTSIDE_TS * 1000,))
    if extra_ddl:
        con.execute(extra_ddl)
    con.commit()
    con.close()
    return path


def test_refuses_output_equal_to_source(tmp_path, capsys):
    src = _make_source(tmp_path / "src.db")
    before = src.read_bytes()
    rc = bld.main(ARGS + ["--source", str(src), "--output", str(src)])
    assert rc == 2
    assert "source DB" in capsys.readouterr().err
    assert src.read_bytes() == before


def test_refuses_output_at_prod_db(tmp_path, monkeypatch):
    src = _make_source(tmp_path / "src.db")
    prod = tmp_path / "prod.db"
    prod.write_bytes(b"live")
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod)
    rc = bld.main(ARGS + ["--source", str(src), "--output", str(prod)])
    assert rc == 2
    assert prod.read_bytes() == b"live"


def test_refuses_unplanned_source_table(tmp_path, capsys):
    src = _make_source(tmp_path / "src.db",
                       extra_ddl="CREATE TABLE new_feed_table (timestamp INTEGER)")
    out = tmp_path / "slice.db"
    rc = bld.main(ARGS + ["--source", str(src), "--output", str(out)])
    assert rc == 2
    assert "new_feed_table" in capsys.readouterr().err
    assert not out.exists()
    assert not list(tmp_path.glob("*.building"))


def test_builds_slice_atomically_and_filters_window(tmp_path, capsys):
    src = _make_source(tmp_path / "src.db")
    out = tmp_path / "slice.db"
    out.write_bytes(b"stale previous slice")
    rc = bld.main(ARGS + ["--source", str(src), "--output", str(out)])
    assert rc == 0
    assert not list(tmp_path.glob("*.building"))
    con = sqlite3.connect(str(out))
    try:
        assert con.execute("SELECT v FROM btc_1m").fetchall() == [(1.0,)]
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert set(bld.REQUIRED_TABLES) <= names
    # a required table with nothing in the window is called out, not hidden
    assert "WARNING: required table eth_1m" in capsys.readouterr().out


def test_plan_covers_every_live_table():
    """The live prod.db must not hold a table the plan does not know —
    otherwise the builder (rightly) refuses to run."""
    import pytest
    from strategies.support import db as _db_mod
    if not _db_mod.PROD_DB.exists():
        pytest.skip("requires data/databases/prod.db")
    con = sqlite3.connect(f"file:{_db_mod.PROD_DB.as_posix()}?mode=ro", uri=True)
    try:
        live = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()
    planned = {name for name, _ in bld.TABLE_PLAN}
    assert live <= planned, f"add a TABLE_PLAN entry for: {sorted(live - planned)}"
