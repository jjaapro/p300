"""Duplicate-instance stand-down.

`botlib.heartbeat` has detected a second live process sharing a bot's
name-keyed heartbeat row since the 2026-08-15..24 incident, but every runner
discarded its return value, so the detection annotated the doubling without
stopping it. These tests pin the refusal: a detected duplicate blocks new
ENTRIES and leaves EXITS alone.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

import botlib
from strategies import trades
from strategies.support import clock, instance_guard, trade_db
from strategies.support import db as _db_mod

T0 = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
VARIANT = {"id": "v_test", "capital_usdt": 10_000.0}


@pytest.fixture(autouse=True)
def _clean_guard():
    instance_guard.resume()
    yield
    instance_guard.resume()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "ledger.db"
    monkeypatch.setattr(_db_mod, "DASH_DB", p)
    monkeypatch.setattr(_db_mod, "PROD_DB", p)
    monkeypatch.setattr(trade_db, "DB_PATH", p)
    trade_db.init_db()
    botlib.init_heartbeat_schema()
    botlib._last_hb_write.clear()
    clock.set_simulated_now(T0)
    yield p
    clock.set_simulated_now(None)
    botlib._last_hb_write.clear()


def _open(**kw):
    args = dict(variant=VARIANT, sleeve_name="TEST", asset="BTC",
                direction="LONG", entry_price=100.0, allocation_pct=10.0,
                leverage=1.0, reason={}, signal_time_iso="sig-1")
    args.update(kw)
    return trades.open_paper_trade(**args)


def test_entries_refused_while_standing_down(ledger):
    instance_guard.stand_down("another process is writing the 'adx' heartbeat")
    with pytest.raises(instance_guard.DuplicateInstanceError):
        _open()
    con = sqlite3.connect(str(ledger))
    try:
        assert con.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    finally:
        con.close()


def test_entries_allowed_again_after_resume(ledger):
    instance_guard.stand_down("duplicate")
    instance_guard.resume()
    assert _open().startswith("SJ-")


def test_heartbeat_stands_down_when_another_pid_takes_the_row(ledger, monkeypatch):
    """Two live instances of one bot: we write, a different pid writes after
    us, then we write again and must detect it."""
    monkeypatch.setattr(botlib, "_pid", lambda: 1111)
    assert botlib.heartbeat("adx") is True
    assert not instance_guard.is_standing_down()

    # A second process takes over the row.
    clock.set_simulated_now(T0.replace(minute=1))
    monkeypatch.setattr(botlib, "_pid", lambda: 2222)
    botlib._last_hb_write.pop("adx", None)      # its first write of the run
    assert botlib.heartbeat("adx") is True

    # Our next write sees a newer row from a pid that is not ours.
    clock.set_simulated_now(T0.replace(minute=2))
    monkeypatch.setattr(botlib, "_pid", lambda: 1111)
    botlib._last_hb_write["adx"] = T0.isoformat()
    assert botlib.heartbeat("adx") is False
    assert instance_guard.is_standing_down()

    with pytest.raises(instance_guard.DuplicateInstanceError):
        _open()

    row = sqlite3.connect(str(ledger)).execute(
        "SELECT status, note FROM bot_heartbeats WHERE name='adx'").fetchone()
    assert row[0] == "error"
    assert "DUPLICATE INSTANCE" in row[1]


def test_a_normal_restart_does_not_stand_down(ledger, monkeypatch):
    """Taking over a stale row is an ordinary restart, not a duplicate: the
    new process has no previous write of its own to compare against."""
    monkeypatch.setattr(botlib, "_pid", lambda: 1111)
    assert botlib.heartbeat("adx") is True

    botlib._last_hb_write.clear()                # fresh process
    monkeypatch.setattr(botlib, "_pid", lambda: 3333)
    clock.set_simulated_now(T0.replace(minute=5))
    assert botlib.heartbeat("adx") is True
    assert not instance_guard.is_standing_down()
    assert _open().startswith("SJ-")


def test_exits_are_not_refused_while_standing_down(ledger):
    """Both instances must keep managing open positions. Suspending exits on
    both would leave positions unmanaged — worse than the duplication."""
    tid = _open()
    instance_guard.stand_down("duplicate")
    trades.close_perp_trade(tid, 110.0, "time_stop", sleeve_name="TEST")
    row = sqlite3.connect(str(ledger)).execute(
        "SELECT status FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == "closed"
