"""Tests for bots/carry — the standalone CARRY (S-078) bot runner.

The exit rule itself is covered by tests/test_carry_exit_rule.py. These cover
what the RUNNER adds around the sleeve: the scheduled-exit backstop and a
decide() that raises.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from bots.carry import config as botcfg
from bots.carry import runner

NOW = datetime(2026, 3, 10, 0, 5, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Tmp prod.db, fresh tables, a simulated clock, funding stubbed at +0.10%
    for the short perp leg (-0.10% for a long, so a directional close shows
    it), and a $10,000 CARRY trade whose exit time passed a minute ago.

    CARRY trades carry the 2099 no-exit placeholder, so the backstop cannot
    reach one today; this forces a real exit time."""
    from strategies import trades
    from strategies.support import clock, funding, price_feed, trade_db, variant_registry
    from strategies.support import db as _db_mod
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    clock.set_simulated_now(NOW)
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    monkeypatch.setattr(funding, "accrued_pct",
                        lambda asset, a, b, d: -0.10 if str(d).upper() == "LONG" else 0.10)
    # The backstop needs a quote; a delta-neutral close ignores its level.
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: 50_500.0)
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    tid = trades.open_paper_trade(
        variant=variant, sleeve_name="CARRY", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"trigger": "carry_fr_positive"},
        scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(days=3), signal_time_iso="2026-03-07")
    yield db_path, variant, tid
    clock.set_simulated_now(None)


def _closed_row(db_path, tid):
    con = sqlite3.connect(str(db_path))
    try:
        status, pnl, notes = con.execute(
            "SELECT status, pnl_usdt, notes FROM trades WHERE id=?", (tid,)).fetchone()
        fee = con.execute(
            "SELECT fee_usdt FROM trade_adjustments WHERE trade_id=? "
            "AND event_type='CLOSE'", (tid,)).fetchone()
    finally:
        con.close()
    return status, pnl, notes, (fee[0] if fee else None)


def test_backstop_closes_an_overdue_carry_trade_as_delta_neutral(env, monkeypatch):
    """Until 2026-09-14 the backstop closed CARRY through the generic perp
    close: a directional long at 15 bp with long-side funding, 100 - 15 - 10 =
    75.00 on this trade. The sleeve's own close books funding collected on the
    short leg minus 0.20% fees and 0.04% slippage, with no price P&L."""
    db_path, variant, tid = env
    # The sleeve's own exit must not get there first.
    monkeypatch.setattr("bots.carry.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_action"}))

    out = runner.tick(variant)

    assert out.get("backstop_closed") == [tid], out
    assert out["hb_status"] == "ok"
    status, pnl, notes, fee = _closed_row(db_path, tid)
    assert status == "closed"
    # $10,000 x (0.10% funding - 0.24% cost)
    assert pnl == pytest.approx(-14.00)
    assert fee == pytest.approx(24.00)
    assert notes.endswith(
        "\nCARRY_EXIT: scheduled_exit; funding=0.100% (per-settlement), "
        "fees=0.20%, slip=0.04%, net=-0.140%")


def test_decide_raising_still_runs_the_backstop(env, monkeypatch):
    """The error reaches the heartbeat, no entry is attempted, and the
    overdue trade still closes."""
    db_path, variant, tid = env

    def broken_decide(*a, **k):
        raise RuntimeError("funding history unavailable")
    monkeypatch.setattr("bots.carry.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except RuntimeError as escaped:
        raise AssertionError("decide's error escaped the tick, so the backstop "
                             "never ran") from escaped

    assert out.get("backstop_closed") == [tid], out
    assert _closed_row(db_path, tid)[0] == "closed"
    assert out["status"] == "decide_error"
    assert out["hb_status"] == "error"
    assert out["hb_note"] == repr(RuntimeError("funding history unavailable"))
    assert out["evaluated"] is False and "opened" not in out


def test_backstop_refusal_keeps_closed_ids_and_turns_the_heartbeat_error(env, monkeypatch):
    """A due trade of a strategy CARRY has no closer for stays open, is named
    in the heartbeat note with status 'error', and does not escape the tick;
    the CARRY trade that did close is still reported."""
    from strategies import trades
    db_path, variant, own = env
    foreign = trades.open_paper_trade(
        variant=variant, sleeve_name="SQUEEZE_BULL", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"t": "foreign"}, scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso="foreign")
    monkeypatch.setattr("bots.carry.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_action"}))

    try:
        out = runner.tick(variant)
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the refusal escaped the tick") from escaped

    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out
    assert out["hb_status"] == "error" and "SQUEEZE_BULL" in out["hb_note"]
    assert _closed_row(db_path, own)[0] == "closed"
    assert _closed_row(db_path, foreign)[0] == "open"


def test_decide_error_and_backstop_refusal_both_reach_the_heartbeat(env, monkeypatch):
    """On a tick where decide raises AND the backstop refuses a trade, the
    note keeps the decide error first and appends the refusal."""
    from strategies import trades
    db_path, variant, own = env
    foreign = trades.open_paper_trade(
        variant=variant, sleeve_name="SQUEEZE_BULL", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"t": "foreign"}, scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso="foreign")

    def broken_decide(*a, **k):
        raise RuntimeError("funding history unavailable")
    monkeypatch.setattr("bots.carry.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except (RuntimeError, botlib.BackstopRefused) as escaped:
        raise AssertionError("an error escaped the tick") from escaped

    assert out["status"] == "decide_error" and out["hb_status"] == "error"
    assert out["hb_note"].startswith(
        repr(RuntimeError("funding history unavailable"))
        + "; backstop left 1 due trade(s) open in "), out["hb_note"]
    assert "SQUEEZE_BULL" in out["hb_note"]
    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out


def test_stale_mgmt_tables_skip_the_backstop(env, monkeypatch):
    """Kept on purpose (BACKLOG 18): the backstop prices off btc_1m and the
    carry close books funding from cd_funding_rate, so a stale-mgmt tick
    leaves a due trade open."""
    db_path, variant, tid = env

    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr("bots.carry.strategy.signal.decide", boom)
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"cd_funding_rate": 99_999.0}
                        if tables == botcfg.MGMT_TABLES else {})

    out = runner.tick(variant)

    assert out["status"] == "stale_mgmt_inputs" and out["hb_status"] == "degraded"
    assert "backstop_closed" not in out, out
    assert _closed_row(db_path, tid)[0] == "open"
