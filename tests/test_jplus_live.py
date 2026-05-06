"""Tests for the live entry handlers in services/jplus_live.

Covers calendar gating, idempotency, sizing math, and the OPEN/CLOSE
event timing for R4_BTC and R4_ETH (Phase 2). Continuous-position
handlers (EMA_BTC, ETH_DAILY) arrive in Phase 3.

Each test sets a synthetic clock, monkey-patches price_feed and
today_inputs, and asserts the resulting trades-table state.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services import clock


CAPITAL_USDT = 10_000.0


def _today_inputs_stub(mode="uncertain", lev=2.0, gated=False, ema_p=-1):
    """Build a stub ``today_inputs()`` result with deterministic values.
    Caller passes regime mode; weights are looked up from
    ``simulate.REGIME_WEIGHTS_FULL`` so they always match what the live
    handlers expect."""
    from jplus import simulate
    return {
        "date": clock.now_utc().date().isoformat(),
        "mode": mode,
        "lev": lev,
        "gated": gated,
        "ema_p": ema_p,
        "weights": dict(simulate.REGIME_WEIGHTS_FULL[mode]),
    }


@pytest.fixture
def live_env(tmp_path, monkeypatch):
    """Tmp dashboard.db with the canonical schema. Patches DASH_DB so all
    handler writes land in the fixture DB."""
    fixture_db = tmp_path / "dashboard.db"
    from services import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    return fixture_db


def _variant() -> dict:
    return {"id": "test_live_v1", "capital_usdt": CAPITAL_USDT}


# ─── R4_BTC ─────────────────────────────────────────────────────────────────


def test_r4_btc_skips_on_tuesday(live_env, monkeypatch):
    """Calendar gate: Tuesdays return ``not_calendar_day`` and don't open."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 5, 6, 1, tzinfo=timezone.utc))  # Tue
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_calendar_day"
    con = sqlite3.connect(str(live_env))
    n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_R4_BTC'"
    ).fetchone()[0]
    con.close()
    assert n == 0


def test_r4_btc_skips_when_day_over_14(live_env, monkeypatch):
    """Calendar gate: day-of-month > 14 returns ``not_wk_1_2``."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 18, 6, 1, tzinfo=timezone.utc))  # Mon, day=18
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_wk_1_2"


def test_r4_btc_skips_before_06_00(live_env, monkeypatch):
    """Time gate: returns ``before_open_window`` if hour < 6."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 4, 5, 30, tzinfo=timezone.utc))  # Mon 05:30
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "before_open_window"


def test_r4_btc_skips_after_18_00(live_env, monkeypatch):
    """Time gate: returns ``after_close_window`` if hour >= 18."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 4, 18, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "after_close_window"


def test_r4_btc_opens_on_monday_within_window(live_env, monkeypatch):
    """Mon wk1-2 between 06:00 and 18:00 with valid inputs and price → opens.
    Verify trade fields: strategy, asset, direction, sizing math, exit_time."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     gated=False))
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))  # Mon, day=4
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    assert result["entry_price"] == 70_000.0
    assert result["stacked_lev"] == pytest.approx(5.0)  # 2.5 inner * 2.0 vol
    assert result["weight"] == pytest.approx(0.30)      # uncertain regime

    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_R4_BTC' AND status='open'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row["asset"] == "BTC"
    assert row["direction"] == "LONG"
    assert row["leverage"] == pytest.approx(5.0)
    assert row["entry_price"] == 70_000.0
    # Notional = capital × weight × stacked_lev = 10_000 × 0.30 × 5.0 = 15_000
    assert row["size_usdt"] == pytest.approx(15_000.0)
    # qty = size / price = 15_000 / 70_000 ≈ 0.2143
    assert row["qty"] == pytest.approx(15_000.0 / 70_000.0, rel=1e-6)
    # Exit scheduled at 18:00 same day
    assert row["exit_time"].startswith("2026-05-04T18:00")


def test_r4_btc_idempotent_within_window(live_env, monkeypatch):
    """Calling the handler twice in the same window opens exactly one trade."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))
    try:
        a = jplus_live.r4_btc_try_fire(_variant(), {})
        # Advance the clock 5 minutes
        clock.set_simulated_now(datetime(2026, 5, 4, 6, 6, tzinfo=timezone.utc))
        b = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert a["status"] == "opened"
    assert b["status"] == "already_open"
    con = sqlite3.connect(str(live_env))
    n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_R4_BTC'"
    ).fetchone()[0]
    con.close()
    assert n == 1


def test_r4_btc_skips_when_regime_zero_weight(live_env, monkeypatch):
    """In bear regime weights['r4_btc'] = 0 → handler returns
    ``regime_zero_weight`` without opening."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="bear", lev=1.5))
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "regime_zero_weight"
    assert result["mode"] == "bear"


def test_r4_btc_no_inputs_returns_status(live_env, monkeypatch):
    """If today_inputs() returns None (cold DB), handler reports
    ``no_inputs`` without crashing."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs", lambda: None)
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "no_inputs"


def test_r4_btc_no_price_returns_status(live_env, monkeypatch):
    """Stale data → price_feed returns None → handler reports ``no_price``."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: None)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "no_price"


def test_r4_btc_inner_lev_collapses_when_gated(live_env, monkeypatch):
    """When today_inputs.gated=True, inner_lev is 1.0× instead of 2.5×.
    Stacked leverage should equal vol_lev directly."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     gated=True))
    clock.set_simulated_now(datetime(2026, 5, 4, 6, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    assert result["stacked_lev"] == pytest.approx(2.0)  # 1.0 × 2.0


# ─── R4_ETH ─────────────────────────────────────────────────────────────────


def test_r4_eth_skips_on_monday(live_env, monkeypatch):
    """Calendar gate: only Tuesdays."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 4, 20, 1, tzinfo=timezone.utc))  # Mon
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_tuesday"


def test_r4_eth_skips_when_next_day_over_14(live_env, monkeypatch):
    """Tue 2026-05-12 → next-day Wed 2026-05-13 day=13 ≤14 ✓ but Tue
    2026-05-19 → next-day Wed 2026-05-20 day=20 >14 → reject."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 19, 20, 1, tzinfo=timezone.utc))  # Tue, day=19
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "next_day_not_wk_1_2"


def test_r4_eth_skips_before_20_00(live_env, monkeypatch):
    """Time gate: hour < 20 → before_open_window."""
    from services import jplus_live
    clock.set_simulated_now(datetime(2026, 5, 5, 19, 30, tzinfo=timezone.utc))  # Tue 19:30
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "before_open_window"


def test_r4_eth_opens_tue_20_with_exit_wed_20(live_env, monkeypatch):
    """Tue 2026-05-05 20:01 UTC (next-day Wed=05-06 day=6 ≤14) opens with
    scheduled_exit_dt at Wed 20:00 UTC."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 5, 20, 1, tzinfo=timezone.utc))  # Tue 20:01
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"

    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_R4_ETH' AND status='open'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row["asset"] == "ETH"
    assert row["direction"] == "LONG"
    # Notional = 10_000 × 0.40 × 5.0 = 20_000 (uncertain weight 0.40 × 5x)
    assert row["size_usdt"] == pytest.approx(20_000.0)
    # Exit at Wed 2026-05-06 20:00 UTC
    assert row["exit_time"].startswith("2026-05-06T20:00")


def test_r4_eth_idempotent_within_window(live_env, monkeypatch):
    """Two calls on the same Tuesday after 20:00 produce one trade."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 5, 20, 1, tzinfo=timezone.utc))
    try:
        a = jplus_live.r4_eth_try_fire(_variant(), {})
        clock.set_simulated_now(datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc))
        b = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert a["status"] == "opened"
    assert b["status"] == "already_open"
    con = sqlite3.connect(str(live_env))
    n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_R4_ETH'"
    ).fetchone()[0]
    con.close()
    assert n == 1


def test_r4_eth_bear_regime_skips(live_env, monkeypatch):
    """In bear regime weights['r4_eth'] = 0 → handler skips."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="bear", lev=1.5))
    clock.set_simulated_now(datetime(2026, 5, 5, 20, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "regime_zero_weight"


# ─── EMA_BTC ────────────────────────────────────────────────────────────────


def test_ema_btc_opens_when_no_position(live_env, monkeypatch):
    """No open EMA_BTC + ema_p=+1 → OPEN LONG. Sized at
    capital × weight × lev / price."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=+1))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"

    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_EMA_BTC' AND status='open'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row["asset"] == "BTC"
    assert row["direction"] == "LONG"
    # Notional = 10_000 × 0.30 × 2.0 = 6_000 (uncertain weight 0.30, lev 2)
    assert row["size_usdt"] == pytest.approx(6_000.0)
    assert row["leverage"] == pytest.approx(2.0)


def test_ema_btc_opens_short_when_ema_p_negative(live_env, monkeypatch):
    """ema_p=-1 → SHORT direction."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=-1))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    con = sqlite3.connect(str(live_env))
    direction = con.execute(
        "SELECT direction FROM trades WHERE strategy='JPLUS_EMA_BTC'"
    ).fetchone()[0]
    con.close()
    assert direction == "SHORT"


def test_ema_btc_no_position_when_ema_p_zero(live_env, monkeypatch):
    """ema_p=0 (warmup edge) → no_position_needed; nothing opened."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=0))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "no_position_needed"


def test_ema_btc_flips_on_direction_change(live_env, monkeypatch):
    """Existing LONG + ema_p flips to -1 → FLIP event; new trade opens
    with parent_position_id linkage."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    # Day 1: open LONG.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=+1))
    clock.set_simulated_now(datetime(2026, 5, 4, 0, 1, tzinfo=timezone.utc))
    try:
        first = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert first["status"] == "opened"

    # Day 2: ema_p flipped to -1 — FLIP.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=-1))
    clock.set_simulated_now(datetime(2026, 5, 5, 0, 1, tzinfo=timezone.utc))
    try:
        second = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert second["status"] == "flipped"

    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    open_row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_EMA_BTC' AND status='open'"
    ).fetchone()
    closed_row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_EMA_BTC' "
        "AND status='closed' ORDER BY actual_exit_time DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert open_row is not None
    assert open_row["direction"] == "SHORT"
    assert open_row["parent_position_id"] is not None
    assert closed_row is not None
    assert closed_row["resolution"] == "filled_flipped"


def test_ema_btc_idempotent_within_same_day(live_env, monkeypatch):
    """Second tick on the same UTC day after the first SCALE/LEV_ADJ
    triggers no-op — UNIQUE constraint on adjustment events."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    # Pre-stage an open EMA_BTC trade with non-matching qty/lev so the
    # first call would emit SCALE+LEVERAGE_ADJUST.
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="strong_bull", lev=3.0,
                                                     ema_p=+1))
    clock.set_simulated_now(datetime(2026, 5, 1, 0, 1, tzinfo=timezone.utc))
    try:
        # Open initial position (uncertain regime).
        monkeypatch.setattr(core_sim, "today_inputs",
                             lambda: _today_inputs_stub(mode="uncertain",
                                                         lev=2.0, ema_p=+1))
        opened = jplus_live.ema_btc_try_fire(_variant(), {})
        assert opened["status"] == "opened"
        # Simulate next day with regime change + lev change.
        clock.set_simulated_now(datetime(2026, 5, 2, 0, 1, tzinfo=timezone.utc))
        monkeypatch.setattr(core_sim, "today_inputs",
                             lambda: _today_inputs_stub(mode="strong_bull",
                                                         lev=3.0, ema_p=+1))
        first_call = jplus_live.ema_btc_try_fire(_variant(), {})
        assert first_call["status"] == "rebalanced"
        # Second call same day: idempotent no-op.
        clock.set_simulated_now(datetime(2026, 5, 2, 0, 5, tzinfo=timezone.utc))
        second_call = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert second_call["status"] == "in_sync"


# ─── ETH_DAILY ──────────────────────────────────────────────────────────────


def test_eth_daily_no_action_in_uncertain(live_env, monkeypatch):
    """Uncertain regime weight=0 + nothing open → no_position_needed."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "no_position_needed"


def test_eth_daily_opens_in_strong_bull(live_env, monkeypatch):
    """Strong_bull → eth_daily weight=0.20, lev=3 (cap), opens LONG."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="strong_bull",
                                                     lev=3.0))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_ETH_DAILY' AND status='open'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row["direction"] == "LONG"
    # Notional = 10_000 × 0.20 × 3.0 = 6_000
    assert row["size_usdt"] == pytest.approx(6_000.0)


def test_eth_daily_closes_when_regime_exits_bull(live_env, monkeypatch):
    """Open in mild_bull → regime flips to uncertain → CLOSE."""
    from services import jplus_live, price_feed
    from jplus import simulate as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="mild_bull", lev=2.5))
    clock.set_simulated_now(datetime(2026, 5, 1, 0, 1, tzinfo=timezone.utc))
    try:
        opened = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert opened["status"] == "opened"

    # Regime exits bull next day.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 2, 0, 1, tzinfo=timezone.utc))
    try:
        closed = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert closed["status"] == "closed"

    con = sqlite3.connect(str(live_env))
    n_open = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_ETH_DAILY' "
        "AND status='open'"
    ).fetchone()[0]
    con.close()
    assert n_open == 0
