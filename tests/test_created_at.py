"""BACKLOG item 20: created_at is written on every trade and variant insert, and read defensively.

The 2026-05-18 PK rebuild dropped created_at's DEFAULT on trades and variants; nothing wrote the column, so every
bot-era row carried NULL and ledger_coherence — which keys its OPEN/CLOSE/seq checks on created_at — audited no
bot-era trade for four months. tests/test_ledger_coherence.py covers the reader's fallback; this file covers the
writers, and the backfill's plan.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies import trades
from strategies.support import clock
from strategies.support import db as _db_mod
from strategies.support import trade_db, variant_registry

NOW = datetime(2026, 9, 19, 1, 30, tzinfo=timezone.utc)

VARIANTS_DDL = """
    CREATE TABLE IF NOT EXISTS variants (
        id TEXT, short_name TEXT, long_name TEXT, kind TEXT, parent_variant_id TEXT, version TEXT,
        status TEXT, is_primary INT, capital_usdt REAL, color TEXT, spec_json TEXT, notes TEXT,
        superseded_by TEXT, reconcile_against TEXT, enabled INT, created_at TEXT)"""
EVENTS_DDL = """
    CREATE TABLE IF NOT EXISTS variant_events (
        id INTEGER PRIMARY KEY, timestamp TEXT, variant_id TEXT, event_type TEXT, actor TEXT,
        details_json TEXT, summary TEXT)"""


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "prod.db"
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    trade_db.init_db()
    con = sqlite3.connect(path)
    try:
        import bootstrap
        con.execute(bootstrap.SCHEMAS.get("variants", VARIANTS_DDL))
        con.execute(bootstrap.SCHEMAS.get("variant_events", EVENTS_DDL))
        con.commit()
    finally:
        con.close()
    clock.set_simulated_now(NOW)
    yield path
    clock.set_simulated_now(None)


def _variant(vid="bot_test_v1"):
    return {"id": vid, "capital_usdt": 10_000.0, "short_name": "test", "kind": "bot",
            "version": "1", "status": "paper", "enabled": 1}


def test_open_paper_trade_writes_created_at(ledger):
    tid = trades.open_paper_trade(
        variant=_variant(), sleeve_name="CARRY", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"trigger": "test"}, scheduled_exit_dt=NOW + timedelta(days=1),
        entry_dt=NOW, signal_time_iso="2026-09-19")
    row = sqlite3.connect(ledger).execute(
        "SELECT created_at, actual_entry_time FROM trades WHERE id = ?", (tid,)).fetchone()
    assert row[0] is not None, "created_at is still NULL on a fresh paper open"
    assert row[0] == row[1], "created_at should be the open's own clock, as the backfill assumes"
    assert datetime.fromisoformat(row[0]) == NOW


def test_register_variant_writes_created_at(ledger):
    variant_registry.register_variant(variant_id="bot_new_v1", short_name="new", kind="bot",
                                      version="1", spec={"x": 1})
    row = sqlite3.connect(ledger).execute(
        "SELECT created_at FROM variants WHERE id = 'bot_new_v1'").fetchone()
    assert row and row[0] is not None, "created_at is still NULL on a fresh variant"
    assert datetime.fromisoformat(row[0]) == NOW


def test_backfill_plan_uses_the_rows_own_times(ledger):
    """The migration fills trades from actual_entry_time and variants from their
    `registered` event — the same values the writers now store."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "mig", Path(__file__).resolve().parents[1] / "data" / "migrations" / "2026_09_19_created_at_backfill.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    con = sqlite3.connect(ledger)
    # created_at set to NULL explicitly: init_db's canonical DDL still carries the DEFAULT that prod's
    # rebuilt table lost (item 19), and the backfill exists precisely for rows without it.
    con.execute("""INSERT INTO trades (id, series, asset, direction, strategy, entry_time, status,
                   execution_mode, strategy_variant, actual_entry_time, created_at)
                   VALUES ('SJ-1', 'SJ', 'BTC', 'LONG', 'X', '2026-06-01T00:00:00+00:00', 'closed',
                           'paper', 'bot_old_v1', '2026-06-01T00:00:05+00:00', NULL)""")
    con.execute("INSERT INTO variants (id, created_at) VALUES ('bot_old_v1', NULL)")
    con.execute("""INSERT INTO variant_events (timestamp, variant_id, event_type, actor)
                   VALUES ('2026-05-30T12:00:00+00:00', 'bot_old_v1', 'registered', 'user')""")
    con.commit()
    p = mig.plan(con)
    con.close()
    assert p["trades"] == [{"id": "SJ-1", "created_at": "2026-06-01T00:00:05+00:00", "source": "actual_entry_time"}]
    assert p["variants"] == [{"id": "bot_old_v1", "created_at": "2026-05-30T12:00:00+00:00", "source": "registered_event"}]
