"""Tests for bots/r4 — the standalone R4 calendar bot.

The sleeve's own gates (calendar, idempotency, regime sign) are covered by
tests/test_r4.py and tests/test_jplus_live.py; these tests cover what the
BOT adds: stale-input policy, per-variant sizing and the co-fire budget, the
late-entry guard, per-day idempotency across ticks/restarts, and calendar
parity between bots/r4/windows.py and the sleeve's decide functions.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from bots.r4 import windows as r4cal
from bots.r4 import config as botcfg
from bots.r4 import runner
from strategies.sleeves.timing_anomalies.internal.r4.config import (
    STRATEGY_R4_BTC, STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2,
)
from strategies.support import clock
from strategies.support import db as _db_mod
from strategies.support import jplus_inputs, price_feed, trade_db, variant_registry
from strategies.support.dispatch import Intent

CAPITAL = 10_000.0
MON = datetime(2026, 9, 7, tzinfo=timezone.utc)     # Monday, day 7
TUE = datetime(2026, 9, 8, tzinfo=timezone.utc)     # Tuesday, day 8
WED = datetime(2026, 9, 9, tzinfo=timezone.utc)     # Wednesday, day 9
PRICES = {"BTC": 70_000.0, "ETH": 3_000.0}


def _inputs_stub(lev=2.0, gated=False, mode="uncertain", weight=0.1):
    """Minimal today_inputs() result: the sleeve reads mode / gated / lev and
    the regime weight SIGN (bear => zero weight). The bot discards the
    weight magnitude, so any positive value works."""
    keys = ("r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2")
    w = {k: (0.0 if mode == "bear" else weight) for k in keys}
    return {"date": clock.now_utc().date().isoformat(), "mode": mode,
            "lev": lev, "gated": gated, "weights": w}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Tmp prod.db + stubbed inputs, prices, fresh tables, tmp diag file."""
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()
    monkeypatch.setattr(jplus_inputs, "today_inputs", lambda: _inputs_stub())
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: PRICES[a])
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    monkeypatch.setattr(botcfg, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(botcfg, "DIAG_PATH", tmp_path / "logs" / "diag.jsonl")
    monkeypatch.setattr(botcfg, "ENABLED", {k: True for k in botcfg.ENABLED})
    runner._missed.clear()
    yield db_path
    clock.set_simulated_now(None)
    runner._missed.clear()


def _variant():
    return {"id": "bot_r4_test", "capital_usdt": CAPITAL}


def _rows(db_path, strategy=None):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        sql = ("SELECT id, strategy, asset, size_usdt, leverage, status, "
               "exit_time, notes FROM trades WHERE strategy_variant='bot_r4_test'")
        if strategy:
            sql += f" AND strategy='{strategy}'"
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _at(dt: datetime):
    clock.set_simulated_now(dt)


def _mk_intent(strategy=STRATEGY_R4_BTC, leverage=5.0, exit_dt=None):
    exit_dt = exit_dt or (MON + timedelta(hours=18))
    return Intent(asset="BTC", direction="LONG", allocation_pct=11.1,
                  leverage=leverage, conviction=100, priority=100.0,
                  reason={"_strategy": strategy, "_entry_price": 70_000.0,
                          "_exit_dt_iso": exit_dt.isoformat(),
                          "_mode": "uncertain", "_now_iso": MON.isoformat()},
                  scheduled_exit_dt=exit_dt)


# ─── sizing ──────────────────────────────────────────────────────────────────

def test_size_intent_keeps_sleeve_leverage_when_budget_free():
    resized, info = runner.size_intent(_mk_intent(leverage=5.0), CAPITAL, 0.0, late_s=90)
    assert info["notional"] == pytest.approx(CAPITAL * 0.20 * 5.0)
    assert resized.allocation_pct == pytest.approx(20.0)
    assert resized.leverage == pytest.approx(5.0)
    assert not info["capped"]
    assert resized.reason["bot_weight"] == 0.20
    assert resized.reason["entry_latency_s"] == 90
    assert resized.reason["budget_capped"] is False


def test_size_intent_caps_sleeve_leverage():
    resized, info = runner.size_intent(_mk_intent(leverage=9.0), CAPITAL, 0.0, late_s=0)
    assert info["lev"] == botcfg.LEV_CAP
    assert info["notional"] == pytest.approx(CAPITAL * 0.20 * botcfg.LEV_CAP)


def test_size_intent_scales_down_to_remaining_budget():
    open_gross = botcfg.GROSS_MAX_X * CAPITAL - 5_000.0
    resized, info = runner.size_intent(_mk_intent(leverage=5.0), CAPITAL, open_gross, late_s=0)
    assert info["capped"]
    assert info["notional"] == pytest.approx(5_000.0)
    assert resized.leverage == pytest.approx(5_000.0 / (CAPITAL * 0.20))
    assert resized.reason["budget_capped"] is True


def test_size_intent_exhausted_below_min_notional():
    open_gross = botcfg.GROSS_MAX_X * CAPITAL - 100.0
    resized, info = runner.size_intent(_mk_intent(), CAPITAL, open_gross, late_s=0)
    assert resized is None
    assert info["budget"] == pytest.approx(100.0)


# ─── tick: stale policy ───────────────────────────────────────────────────────

def test_tick_stale_mgmt_never_calls_decide(env, monkeypatch):
    def boom():
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr(runner, "deciders", boom)
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"eth_1m": 9_999.0}
                        if "eth_1m" in (tables or []) else {})
    out = runner.tick(_variant(), {})
    assert out["status"] == "stale_mgmt_inputs"
    assert out["hb_status"] == "degraded"
    assert out["evaluated"] is False


def test_tick_entry_blocked_then_recovers_inside_grace(env, monkeypatch):
    _at(MON.replace(hour=6, minute=1))
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"ca_long_short_ratio": 100_000.0}
                        if "ca_long_short_ratio" in (tables or []) else {})
    out = runner.tick(_variant(), {})
    assert out["status"] == "entry_blocked_stale_inputs"
    assert out["hb_status"] == "degraded"
    assert _rows(env) == []
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    _at(MON.replace(hour=6, minute=3))
    out = runner.tick(_variant(), {})
    assert out["status"] == "opened"
    assert len(_rows(env, STRATEGY_R4_BTC)) == 1


# ─── tick: open / idempotency / late guard ────────────────────────────────────

def test_opens_monday_within_grace_with_bot_sizing(env):
    _at(MON.replace(hour=6, minute=2))
    out = runner.tick(_variant(), {})
    assert out["status"] == "opened"
    assert out["evaluated"] is True
    rows = _rows(env, STRATEGY_R4_BTC)
    assert len(rows) == 1
    r = rows[0]
    # stub: inner 2.5 (ungated) x vol_lev 2.0 = 5.0x; weight 0.20 -> $10,000
    assert r["size_usdt"] == pytest.approx(CAPITAL * 0.20 * 5.0)
    assert r["leverage"] == pytest.approx(5.0)
    assert r["exit_time"].startswith("2026-09-07T18:00")
    notes = json.loads(r["notes"])
    assert notes["bot_weight"] == 0.20
    assert notes["entry_latency_s"] == 120
    assert notes["budget_capped"] is False
    # only the Monday window fires on a Monday
    assert out["detail"][STRATEGY_R4_ETH] == "not_tuesday"
    assert out["detail"][STRATEGY_R4_BTC_V2] == "not_calendar_day"


def test_idempotent_per_day_across_ticks_and_restart(env):
    _at(MON.replace(hour=6, minute=1))
    assert runner.tick(_variant(), {})["status"] == "opened"
    _at(MON.replace(hour=6, minute=2))
    out = runner.tick(_variant(), {})
    assert out["status"] == "no_action"
    assert out["detail"][STRATEGY_R4_BTC] == "already_open"
    runner._missed.clear()                       # simulate a process restart
    _at(MON.replace(hour=6, minute=4))
    runner.tick(_variant(), {})
    assert len(_rows(env, STRATEGY_R4_BTC)) == 1


def test_late_entry_is_a_logged_miss_not_a_fill(env):
    _at(MON.replace(hour=6, minute=11))          # 660s > 300s grace
    out = runner.tick(_variant(), {})
    assert out["status"] == "missed_window"
    assert out["detail"][STRATEGY_R4_BTC] == "missed_window"
    assert "missed_window" in out["hb_note"]
    assert _rows(env) == []
    lines = botcfg.DIAG_PATH.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "missed_window"
    # a second tick the same day does not re-log
    _at(MON.replace(hour=6, minute=12))
    runner.tick(_variant(), {})
    assert len(botcfg.DIAG_PATH.read_text().splitlines()) == 1


def test_eth_v1_after_window_is_covered_by_the_guard(env):
    """The sleeve has no after-window check for R4_ETH (fires until 23:59
    Tuesday); the bot's late-entry guard turns that into a miss."""
    _at(TUE.replace(hour=23, minute=30))
    out = runner.tick(_variant(), {})
    assert out["detail"][STRATEGY_R4_ETH] == "missed_window"
    assert _rows(env) == []


def test_disabled_variant_is_never_evaluated(env, monkeypatch):
    monkeypatch.setitem(botcfg.ENABLED, STRATEGY_R4_BTC, False)
    _at(MON.replace(hour=6, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "no_action"
    assert STRATEGY_R4_BTC not in out["detail"]
    assert _rows(env) == []


def test_bear_regime_opens_nothing(env, monkeypatch):
    monkeypatch.setattr(jplus_inputs, "today_inputs", lambda: _inputs_stub(mode="bear"))
    _at(MON.replace(hour=6, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "no_action"
    assert out["detail"][STRATEGY_R4_BTC] == "regime_zero_weight"
    assert out["evaluated"] is True


# ─── tick: Wednesday co-fire budget ───────────────────────────────────────────

def _seed_open_eth_v1(size_usdt: float):
    from strategies import trades
    _at(TUE.replace(hour=20, minute=1))
    trades.open_paper_trade(
        variant=_variant(), sleeve_name=STRATEGY_R4_ETH, asset="ETH",
        direction="LONG", entry_price=3_000.0, allocation_pct=20.0,
        leverage=size_usdt / (CAPITAL * 0.20), reason={"seed": True},
        scheduled_exit_dt=WED.replace(hour=20), regime_value="uncertain",
        entry_dt=clock.now_utc())


def test_wednesday_cofire_scales_third_position_to_budget(env, monkeypatch):
    # stacked 7.5x (gate off, vol_lev 3.0) -> each V2 wants 0.20 x 7.5 = 1.5x capital
    monkeypatch.setattr(jplus_inputs, "today_inputs", lambda: _inputs_stub(lev=3.0))
    _seed_open_eth_v1(12_000.0)
    _at(WED.replace(hour=4, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "opened"
    btc = _rows(env, STRATEGY_R4_BTC_V2)[0]
    eth = _rows(env, STRATEGY_R4_ETH_V2)[0]
    assert btc["size_usdt"] == pytest.approx(15_000.0)          # budget 30k-12k=18k, fits
    assert json.loads(btc["notes"])["budget_capped"] is False
    assert eth["size_usdt"] == pytest.approx(3_000.0)           # remaining 18k-15k
    assert json.loads(eth["notes"])["budget_capped"] is True
    assert botlib.open_gross_usdt("bot_r4_test") == pytest.approx(30_000.0)


def test_budget_exhausted_opens_nothing(env, monkeypatch):
    monkeypatch.setattr(jplus_inputs, "today_inputs", lambda: _inputs_stub(lev=3.0))
    _seed_open_eth_v1(botcfg.GROSS_MAX_X * CAPITAL - 100.0)
    _at(WED.replace(hour=4, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "budget_exhausted"
    assert _rows(env, STRATEGY_R4_BTC_V2) == []
    assert _rows(env, STRATEGY_R4_ETH_V2) == []


# ─── backstop closes ETH off eth_1m price ─────────────────────────────────────

def test_backstop_closes_due_eth_trade(env):
    _seed_open_eth_v1(6_000.0)
    _at(WED.replace(hour=20, minute=1))
    out = runner.tick(_variant(), {})
    assert out.get("backstop_closed")
    assert _rows(env, STRATEGY_R4_ETH)[0]["status"] == "closed"


# ─── calendar parity with the sleeve ──────────────────────────────────────────

def test_calendar_matches_sleeve_decides_2025_2026(env):
    """For every day in 2025-2026, the windows listed by bots/r4/windows.py
    are exactly the ones whose decide function returns an Intent at
    open+1min (inputs and price stubbed, nothing executed)."""
    calendar_fires, sleeve_fires = set(), set()
    day = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 12, 31, tzinfo=timezone.utc)
    while day <= end:
        for w in r4cal.windows_on(day):
            calendar_fires.add((w["strategy"], day.date()))
        for strategy, decide in runner.deciders().items():
            open_dt = day.replace(hour={
                STRATEGY_R4_BTC: 6, STRATEGY_R4_ETH: 20,
                STRATEGY_R4_BTC_V2: 4, STRATEGY_R4_ETH_V2: 4}[strategy])
            _at(open_dt + timedelta(minutes=1))
            intents, _ = decide(_variant(), {})
            if intents:
                sleeve_fires.add((strategy, day.date()))
                assert r4cal.window_open_for(
                    strategy, intents[0].scheduled_exit_dt) == open_dt
        day += timedelta(days=1)
    assert calendar_fires == sleeve_fires
    assert len(calendar_fires) > 100


def test_next_windows_are_future_and_sorted():
    now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
    ws = r4cal.next_windows(now, 6)
    assert len(ws) == 6
    assert all(w["close_utc"] > now for w in ws)
    assert [w["open_utc"] for w in ws] == sorted(w["open_utc"] for w in ws)
    assert ws[0]["strategy"] == STRATEGY_R4_BTC and ws[0]["open_utc"] == MON.replace(hour=6)


# ─── shipped config: ETH windows only (2026-09-12) ────────────────────────────

def test_shipped_config_enables_only_the_eth_windows():
    """User decision 2026-09-12 (docs/calibration/r4.md): the BTC windows stay
    wired but off. The `env` fixture forces all four on, so this test reads
    the module as shipped."""
    from bots.r4 import config as shipped
    on = {k for k, v in shipped.ENABLED.items() if v}
    assert on == {STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2}
    assert set(shipped.ENABLED) == {STRATEGY_R4_BTC, STRATEGY_R4_ETH,
                                    STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH_V2}
    assert "ETH" in shipped.SHORT_NAME and "BTC" not in shipped.SHORT_NAME
    # the weight table still covers every window, so re-enabling one is a flag flip
    assert set(shipped.VARIANT_WEIGHT) == set(shipped.ENABLED)


def test_next_windows_lists_only_the_requested_strategies():
    now = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
    eth_only = r4cal.next_windows(now, 6, strategies=[STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2])
    assert len(eth_only) == 6
    assert {w["strategy"] for w in eth_only} <= {STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2}
    assert all(w["asset"] == "ETH" for w in eth_only)
    assert eth_only[0]["strategy"] == STRATEGY_R4_ETH
    assert eth_only[0]["open_utc"] == TUE.replace(hour=20)
    # the default still lists every window, so the calendar-parity test keeps its meaning
    assert {w["strategy"] for w in r4cal.next_windows(now, 8)} == {
        STRATEGY_R4_BTC, STRATEGY_R4_ETH, STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH_V2}


def test_shipped_config_skips_monday_and_trades_the_eth_pair(env, monkeypatch):
    monkeypatch.setattr(botcfg, "ENABLED", {
        STRATEGY_R4_BTC: False, STRATEGY_R4_ETH: True,
        STRATEGY_R4_BTC_V2: False, STRATEGY_R4_ETH_V2: True})
    _at(MON.replace(hour=6, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "no_action"
    assert STRATEGY_R4_BTC not in out["detail"] and STRATEGY_R4_BTC_V2 not in out["detail"]
    assert _rows(env) == []
    _at(TUE.replace(hour=20, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "opened"
    rows = _rows(env, STRATEGY_R4_ETH)
    assert len(rows) == 1 and rows[0]["asset"] == "ETH"
    assert rows[0]["exit_time"].startswith("2026-09-09T20:00")
    # Wednesday: only the ETH V2 joins the open ETH V1 — two legs, never three
    _at(WED.replace(hour=4, minute=1))
    out = runner.tick(_variant(), {})
    assert out["status"] == "opened"
    assert _rows(env, STRATEGY_R4_BTC_V2) == []
    assert len(_rows(env, STRATEGY_R4_ETH_V2)) == 1
    assert botlib.open_gross_usdt("bot_r4_test") <= botcfg.GROSS_MAX_X * CAPITAL
