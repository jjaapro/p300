"""open_paper_trade under fleet concurrency (review 2026-09-06, findings
4 + 5): the ID mint runs inside SQLite's write lock, and a stable signal
key turns two executions of the same signal into one row."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies import trades
from strategies.support import clock, trade_db
from strategies.support import db as _db_mod

T0 = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)
VARIANT = {"id": "v_test", "capital_usdt": 10_000.0}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "ledger.db"
    monkeypatch.setattr(_db_mod, "DASH_DB", p)
    monkeypatch.setattr(trade_db, "DB_PATH", p)
    trade_db.init_db()
    clock.set_simulated_now(T0)
    yield p
    clock.set_simulated_now(None)


def _open(**kw):
    args = dict(variant=VARIANT, sleeve_name="TEST", asset="BTC",
                direction="LONG", entry_price=100.0, allocation_pct=10.0,
                leverage=1.0, reason={})
    args.update(kw)
    return trades.open_paper_trade(**args)


def _count(ledger) -> int:
    con = sqlite3.connect(str(ledger))
    try:
        return con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        con.close()


def test_same_signal_at_different_clocks_is_one_row(ledger):
    key = T0.isoformat()
    a = _open(signal_time_iso=key)
    clock.set_simulated_now(T0 + timedelta(seconds=1))
    b = _open(signal_time_iso=key)
    assert a == b
    assert _count(ledger) == 1


def test_without_signal_key_fill_time_is_the_key(ledger):
    a = _open()
    clock.set_simulated_now(T0 + timedelta(seconds=1))
    b = _open()
    assert a != b
    assert _count(ledger) == 2


def test_different_signals_get_distinct_ids(ledger):
    a = _open(signal_time_iso="sig-1")
    b = _open(signal_time_iso="sig-2", sleeve_name="OTHER")
    assert {a, b} == {"SJ-0001", "SJ-0002"}


def test_id_mint_holds_the_write_lock(ledger, monkeypatch):
    """While one process mints its ID, a second writer must be blocked —
    otherwise both read the same MAX(id) and the loser fails on trades.id."""
    real_mint = trades._next_sj_id
    seen = {}

    def mint_and_probe(con):
        tid = real_mint(con)
        other = sqlite3.connect(str(ledger), timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
        seen["tid"] = tid
        return tid

    monkeypatch.setattr(trades, "_next_sj_id", mint_and_probe)
    assert _open(signal_time_iso="sig-1") == seen["tid"]
