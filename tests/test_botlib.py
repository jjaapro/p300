"""Tests for botlib — the single-strategy-bot platform layer.

Covers the four load-bearing pieces: freshness contracts, heartbeat upsert
semantics, idempotent bot-variant registration, and the scheduled-exit
backstop sweep. All against a tmp_path prod.db (standard monkeypatch of the
strategies.support.db constants)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from strategies.support import clock
from strategies.support import db as _db_mod
from strategies.support import trade_db, variant_registry

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "prod.db"
    monkeypatch.setattr(_db_mod, "PROD_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "DASH_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "TRADER_DB", db_path.resolve())
    monkeypatch.setattr(trade_db, "DB_PATH", db_path.resolve())
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()
    clock.set_simulated_now(NOW)
    yield db_path
    clock.set_simulated_now(None)


def _seed_table(db_path, table, ts_col, values):
    con = sqlite3.connect(str(db_path))
    con.execute(f"CREATE TABLE IF NOT EXISTS {table} ({ts_col} INTEGER)")
    con.executemany(f"INSERT INTO {table} ({ts_col}) VALUES (?)",
                    [(v,) for v in values])
    con.commit()
    con.close()


# ─── Freshness ────────────────────────────────────────────────────────────────

def test_fresh_table_not_flagged(tmp_db):
    _seed_table(tmp_db, "cd_futures_15m", "timestamp",
                [int((NOW - timedelta(minutes=10)).timestamp())])
    assert "cd_futures_15m" not in botlib.stale_tables(["cd_futures_15m"])


def test_stale_table_flagged_with_age(tmp_db):
    _seed_table(tmp_db, "cd_futures_15m", "timestamp",
                [int((NOW - timedelta(hours=2)).timestamp())])
    out = botlib.stale_tables(["cd_futures_15m"])
    assert out["cd_futures_15m"] == pytest.approx(2 * 3600, abs=5)


def test_missing_and_empty_tables_flagged(tmp_db):
    out = botlib.stale_tables(["okx_perp_1h"])          # missing entirely
    assert out["okx_perp_1h"] is None
    _seed_table(tmp_db, "okx_perp_1h", "timestamp", [])  # exists, empty
    out = botlib.stale_tables(["okx_perp_1h"])
    assert out["okx_perp_1h"] is None


def test_ms_unit_table(tmp_db):
    ts_ms = int((NOW - timedelta(minutes=2)).timestamp() * 1000)
    _seed_table(tmp_db, "btc_1m", "open_time", [ts_ms])
    assert "btc_1m" not in botlib.stale_tables(["btc_1m"])


# ─── Heartbeats ───────────────────────────────────────────────────────────────

def test_heartbeat_upsert_preserves_optional_fields(tmp_db):
    botlib.heartbeat("botA", status="ok", interval_s=60,
                     last_eval_utc=NOW.isoformat(), open_trades=2)
    clock.set_simulated_now(NOW + timedelta(minutes=5))
    botlib.heartbeat("botA", status="degraded", note="stale okx")
    rows = botlib.get_heartbeats()
    assert len(rows) == 1
    b = rows[0]
    assert b["status"] == "degraded"
    assert b["note"] == "stale okx"
    assert b["last_eval_utc"] == NOW.isoformat()          # preserved
    assert b["open_trades"] == 2                          # preserved
    assert b["interval_s"] == 60                          # preserved
    assert b["last_tick_utc"] == (NOW + timedelta(minutes=5)).isoformat()


# ─── Bot variant registration ─────────────────────────────────────────────────

def test_ensure_bot_variant_idempotent_and_enabled(tmp_db):
    v1 = botlib.ensure_bot_variant("bot_x_v1", short_name="Bot X",
                                   capital_usdt=5000.0, bot_name="x")
    v2 = botlib.ensure_bot_variant("bot_x_v1", short_name="Bot X",
                                   capital_usdt=9999.0, bot_name="x")
    assert v1["id"] == v2["id"] == "bot_x_v1"
    assert v2["capital_usdt"] == 5000.0        # second call is a no-op
    assert v2["enabled"] == 1
    con = sqlite3.connect(str(tmp_db))
    n = con.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
    con.close()
    assert n == 1


# ─── Scheduled-exit backstop ──────────────────────────────────────────────────

def test_close_due_trades_closes_only_overdue(tmp_db):
    from strategies import trades

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")

    # price feed needs a recent 1m bar strictly before clock
    _seed_bar = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
    con = sqlite3.connect(str(tmp_db))
    con.execute("CREATE TABLE btc_1m (open_time INTEGER, close REAL)")
    con.execute("INSERT INTO btc_1m VALUES (?, ?)", (_seed_bar, 50_000.0))
    con.commit()
    con.close()

    overdue = trades.open_paper_trade(
        variant=variant, sleeve_name="TESTSLEEVE", asset="BTC",
        direction="LONG", entry_price=48_000.0, allocation_pct=100.0,
        leverage=1.0, reason={"t": "overdue"},
        scheduled_exit_dt=NOW - timedelta(hours=1),
        entry_dt=NOW - timedelta(hours=73))
    fresh = trades.open_paper_trade(
        variant=variant, sleeve_name="TESTSLEEVE", asset="BTC",
        direction="LONG", entry_price=49_000.0, allocation_pct=100.0,
        leverage=1.0, reason={"t": "fresh"},
        scheduled_exit_dt=NOW + timedelta(hours=24),
        entry_dt=NOW - timedelta(hours=1))

    closed = botlib.close_due_trades(variant["id"], now_utc=NOW)
    assert closed == [overdue]

    con = sqlite3.connect(str(tmp_db))
    rows = dict(con.execute("SELECT id, status FROM trades").fetchall())
    con.close()
    assert rows[overdue] == "closed"
    assert rows[fresh] == "open"
