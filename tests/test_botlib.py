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


def test_heartbeat_detects_duplicate_instance(tmp_db, monkeypatch):
    """A different pid writing the same name between our writes = duplicate
    instance -> status forced to error (2026-08-15 incident regression)."""
    monkeypatch.setattr(botlib, "_last_hb_write", {})

    monkeypatch.setattr(botlib, "_pid", lambda: 111)
    assert botlib.heartbeat("botA", interval_s=60) is True     # first write

    clock.set_simulated_now(NOW + timedelta(seconds=60))
    monkeypatch.setattr(botlib, "_pid", lambda: 222)           # intruder
    botlib._last_hb_write.pop("botA", None)                    # its first write
    assert botlib.heartbeat("botA", interval_s=60) is True

    clock.set_simulated_now(NOW + timedelta(seconds=90))
    monkeypatch.setattr(botlib, "_pid", lambda: 111)           # original again
    botlib._last_hb_write["botA"] = NOW.isoformat()            # its own memory
    assert botlib.heartbeat("botA", interval_s=60) is False    # detected
    b = botlib.get_heartbeats()[0]
    assert b["status"] == "error"
    assert "DUPLICATE INSTANCE" in b["note"]


def test_heartbeat_restart_takeover_is_not_duplicate(tmp_db, monkeypatch):
    """A fresh process taking over a stale row (normal restart) must NOT
    alarm — detection requires a foreign write since OUR OWN last write."""
    monkeypatch.setattr(botlib, "_last_hb_write", {})
    monkeypatch.setattr(botlib, "_pid", lambda: 111)
    assert botlib.heartbeat("botA", interval_s=60) is True

    # restart: new pid, empty per-process memory
    monkeypatch.setattr(botlib, "_last_hb_write", {})
    monkeypatch.setattr(botlib, "_pid", lambda: 333)
    clock.set_simulated_now(NOW + timedelta(minutes=10))
    assert botlib.heartbeat("botA", interval_s=60) is True
    assert botlib.get_heartbeats()[0]["status"] == "ok"


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


def test_ensure_bot_variant_refreshes_short_name_from_config(tmp_db):
    """The config's SHORT_NAME is the label; a changed label is applied on the
    next start with an audit event, and an unchanged one writes nothing
    (r4 -> "ETH windows", 2026-09-12)."""
    botlib.ensure_bot_variant("bot_x_v1", short_name="Bot X (BTC+ETH)",
                              capital_usdt=5000.0, bot_name="x")
    v = botlib.ensure_bot_variant("bot_x_v1", short_name="Bot X (ETH windows)",
                                  capital_usdt=5000.0, bot_name="x")
    assert v["short_name"] == "Bot X (ETH windows)"
    botlib.ensure_bot_variant("bot_x_v1", short_name="Bot X (ETH windows)",
                              capital_usdt=5000.0, bot_name="x")
    con = sqlite3.connect(str(tmp_db))
    rows = con.execute("SELECT event_type, summary FROM variant_events "
                       "WHERE variant_id='bot_x_v1' ORDER BY id").fetchall()
    con.close()
    assert [r[0] for r in rows] == ["registered", "renamed"]
    assert "ETH windows" in rows[1][1]


# ─── Scheduled-exit backstop ──────────────────────────────────────────────────

def _seed_btc_price(db_path, price=50_000.0):
    """price feed needs a recent 1m bar strictly before clock"""
    _seed_bar = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE IF NOT EXISTS btc_1m (open_time INTEGER, close REAL)")
    con.execute("INSERT INTO btc_1m VALUES (?, ?)", (_seed_bar, price))
    con.commit()
    con.close()


def _open(variant, strategy, *, due, tag, asset="BTC"):
    from strategies import trades
    return trades.open_paper_trade(
        variant=variant, sleeve_name=strategy, asset=asset,
        direction="LONG", entry_price=48_000.0, allocation_pct=100.0,
        leverage=1.0, reason={"t": tag},
        scheduled_exit_dt=NOW + (-timedelta(hours=1) if due else timedelta(hours=24)),
        entry_dt=NOW - timedelta(hours=73), signal_time_iso=f"{strategy}-{tag}")


def _statuses(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        return dict(con.execute("SELECT id, status FROM trades").fetchall())
    finally:
        con.close()


def test_close_due_trades_closes_only_overdue(tmp_db):
    from functools import partial

    from strategies import trades

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    _seed_btc_price(tmp_db)

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

    closed = botlib.close_due_trades(
        variant["id"], now_utc=NOW,
        closers={"TESTSLEEVE": partial(trades.close_perp_trade,
                                       sleeve_name="TESTSLEEVE")})
    assert closed == [overdue]

    con = sqlite3.connect(str(tmp_db))
    rows = dict(con.execute("SELECT id, status FROM trades").fetchall())
    con.close()
    assert rows[overdue] == "closed"
    assert rows[fresh] == "open"


def test_close_due_trades_calls_the_supplied_closer_only_for_due_trades(tmp_db):
    """The backstop books what the sleeve books because it CALLS the sleeve's
    close (BACKLOG 4.4): the closer gets exactly (id, price, 'scheduled_exit'),
    and nothing is closed behind its back at the trades.py defaults."""
    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    _seed_btc_price(tmp_db)
    overdue = _open(variant, "TESTSLEEVE", due=True, tag="overdue")
    _open(variant, "TESTSLEEVE", due=False, tag="fresh")

    calls = []
    closed = botlib.close_due_trades(
        variant["id"], closers={"TESTSLEEVE": lambda *a: calls.append(a)})
    assert calls == [(overdue, 50_000.0, "scheduled_exit")]
    assert closed == [overdue]
    assert set(_statuses(tmp_db).values()) == {"open"}, \
        "the recording closer closed nothing, so nothing else may have"


def test_close_due_trades_refuses_a_strategy_without_a_closer(tmp_db, monkeypatch):
    """A due trade whose strategy has no closer stays open and is named,
    rather than being booked at the trades.py defaults. It is refused before
    any price is fetched for it, and the trade that does have a closer still
    closes."""
    from functools import partial

    from strategies import trades
    from strategies.support import price_feed

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    known = _open(variant, "TESTSLEEVE", due=True, tag="known")
    orphan = _open(variant, "ORPHAN", due=True, tag="orphan", asset="ETH")
    priced = []
    monkeypatch.setattr(price_feed, "get_current_price",
                        lambda a: priced.append(a) or 50_000.0)

    with pytest.raises(botlib.BackstopRefused, match="ORPHAN") as exc:
        botlib.close_due_trades(
            variant["id"],
            closers={"TESTSLEEVE": partial(trades.close_perp_trade,
                                           sleeve_name="TESTSLEEVE")})
    assert exc.value.closed == [known]
    assert [(r[0], r[1]) for r in exc.value.refused] == [(orphan, "ORPHAN")]
    assert exc.value.errors == []
    assert orphan in str(exc.value) and "bot_x_v1" in str(exc.value)
    assert priced == ["BTC"], "no price may be fetched for a refused trade"
    rows = _statuses(tmp_db)
    assert rows[known] == "closed"
    assert rows[orphan] == "open"


def test_close_due_trades_isolates_a_failing_closer(tmp_db, monkeypatch):
    """One closer raising (a locked database, ADX's unfinalised exit minute)
    must not strand the other due trades — R4 can hold two at once (three
    with BTC V2 enabled) and the backstop is its only exit. Whichever trade
    is tried first fails; the second still closes, and the failure is
    reported after the loop."""
    from strategies.support import price_feed

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    a = _open(variant, "TESTSLEEVE", due=True, tag="a")
    b = _open(variant, "TESTSLEEVE", due=True, tag="b")
    monkeypatch.setattr(price_feed, "get_current_price", lambda asset: 50_000.0)

    calls = []

    def flaky(trade_id, price, reason):
        calls.append(trade_id)
        if len(calls) == 1:
            raise RuntimeError("database is locked")

    try:
        with pytest.raises(botlib.BackstopRefused, match="database is locked") as exc:
            botlib.close_due_trades(variant["id"], closers={"TESTSLEEVE": flaky})
    except RuntimeError as escaped:
        raise AssertionError("the first closer's error escaped mid-loop, so the "
                             "second due trade was never tried") from escaped
    assert sorted(calls) == sorted([a, b]), "the second due trade was never tried"
    first, second = calls
    assert [(r[0], r[1]) for r in exc.value.errors] == [(first, "TESTSLEEVE")]
    assert exc.value.closed == [second]
    assert exc.value.refused == []


def test_close_due_trades_isolates_a_failing_price_fetch(tmp_db, monkeypatch):
    """The price read is part of each trade's attempt too: a locked or broken
    ETH 1m table must not strand a due BTC trade, and must surface as a
    BackstopRefused (heartbeat 'error') rather than a raw sqlite error that
    no runner catches."""
    from strategies.support import price_feed

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    eth = _open(variant, "TESTSLEEVE", due=True, tag="eth", asset="ETH")
    btc = _open(variant, "TESTSLEEVE", due=True, tag="btc")

    def price(asset):
        if asset == "ETH":
            raise sqlite3.OperationalError("database is locked")
        return 50_000.0
    monkeypatch.setattr(price_feed, "get_current_price", price)

    calls = []
    try:
        with pytest.raises(botlib.BackstopRefused, match="database is locked") as exc:
            botlib.close_due_trades(variant["id"],
                                    closers={"TESTSLEEVE": lambda *a: calls.append(a)})
    except sqlite3.OperationalError as escaped:
        raise AssertionError("the ETH price read's error escaped mid-loop, so "
                             "the BTC trade may never have been tried") from escaped
    assert calls == [(btc, 50_000.0, "scheduled_exit")]
    assert [(r[0], r[1]) for r in exc.value.errors] == [(eth, "TESTSLEEVE")]
    assert exc.value.closed == [btc]
    assert exc.value.refused == []


def test_close_due_trades_ignores_a_not_yet_due_trade_without_a_closer(tmp_db, monkeypatch):
    """The closer lookup happens only once a trade is due. A trade of a
    strategy with no closer that is not due yet is none of the backstop's
    business; refusing it would hold the heartbeat at 'error' for the
    trade's whole life."""
    from strategies.support import price_feed

    variant = botlib.ensure_bot_variant(
        "bot_x_v1", short_name="Bot X", capital_usdt=10000.0, bot_name="x")
    _open(variant, "ORPHAN", due=False, tag="later")
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: 50_000.0)

    try:
        closed = botlib.close_due_trades(variant["id"], closers={})
    except botlib.BackstopRefused as escaped:
        raise AssertionError("a trade that is not due yet was refused") from escaped
    assert closed == []
    assert set(_statuses(tmp_db).values()) == {"open"}


# ─── open_gross_usdt + enabled backfill (R4 bot, 2026-09-06) ──────────────────

def test_open_gross_usdt_sums_open_paper_notional(tmp_db):
    from strategies import trades
    v = {"id": "bot_gross_v1", "capital_usdt": 10_000.0}
    trades.open_paper_trade(variant=v, sleeve_name="X", asset="BTC",
                            direction="LONG", entry_price=100.0,
                            allocation_pct=20.0, leverage=5.0, reason={},
                            scheduled_exit_dt=NOW + timedelta(hours=1))
    trades.open_paper_trade(variant=v, sleeve_name="Y", asset="ETH",
                            direction="LONG", entry_price=10.0,
                            allocation_pct=10.0, leverage=2.0, reason={},
                            scheduled_exit_dt=NOW + timedelta(hours=2))
    assert botlib.open_gross_usdt("bot_gross_v1") == pytest.approx(10_000 + 2_000)
    assert botlib.open_gross_usdt("nobody") == 0.0


def test_ensure_bot_variant_backfills_enabled_null(tmp_path, monkeypatch):
    """Live prod.db predates the `enabled NOT NULL DEFAULT 1` DDL, so bot rows
    registered there carry NULL. Recreate that legacy shape (registry DDL
    with the constraint stripped) and check ensure_bot_variant heals it."""
    scratch = (tmp_path / "scratch.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, scratch)
    monkeypatch.setattr(trade_db, "DB_PATH", scratch)
    variant_registry.init_schema()
    con = sqlite3.connect(str(scratch))
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='variants'").fetchone()[0]
    con.close()
    assert "enabled INTEGER NOT NULL DEFAULT 1" in ddl
    legacy = (tmp_path / "legacy.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, legacy)
    monkeypatch.setattr(trade_db, "DB_PATH", legacy)
    con = sqlite3.connect(str(legacy))
    con.execute(ddl.replace("enabled INTEGER NOT NULL DEFAULT 1", "enabled INTEGER"))
    con.commit(); con.close()
    trade_db.init_db()
    variant_registry.init_schema()          # CREATE IF NOT EXISTS: legacy shape stays
    botlib.ensure_bot_variant("bot_null_v1", short_name="n", capital_usdt=1.0,
                              bot_name="null")
    con = sqlite3.connect(str(legacy))
    con.execute("UPDATE variants SET enabled=NULL WHERE id='bot_null_v1'")
    con.commit(); con.close()
    v = botlib.ensure_bot_variant("bot_null_v1", short_name="n", capital_usdt=1.0,
                                  bot_name="null")
    assert v["enabled"] is True
    con = sqlite3.connect(str(legacy))
    assert con.execute("SELECT enabled FROM variants WHERE id='bot_null_v1'").fetchone()[0] == 1
    con.close()
