"""Tests for the J+ live entry handlers.

Covers calendar gating, idempotency, sizing math, and OPEN/CLOSE event
timing across the four R4 variants plus the two continuous-position
handlers (EMA_BTC, ETH_DAILY).

Each test sets a synthetic clock, monkey-patches price_feed and
today_inputs, and asserts the resulting trades-table state.

The handlers live in three sleeve folders since restructure step 5:
  strategies/sleeves/r4/        — 4 R4 variants
  strategies/sleeves/ema/       — EMA_BTC
  strategies/sleeves/eth_daily/ — ETH_DAILY
The ``jplus_live`` namespace below collects them under one accessor so
the existing per-test calls (``jplus_live.r4_btc_try_fire(...)`` etc.)
don't have to fan out across N imports.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies.support import clock

from strategies.sleeves.r4 import signal as _r4_signal
from strategies.sleeves.ema import signal as _ema_signal
from strategies.sleeves.eth_daily import signal as _eth_daily_signal


class jplus_live:  # noqa: N801 — test-only convenience namespace
    """Test-only accessor matching the pre-restructure ``services.jplus_live`` shape."""
    r4_btc_try_fire = staticmethod(_r4_signal.r4_btc_try_fire)
    r4_eth_try_fire = staticmethod(_r4_signal.r4_eth_try_fire)
    r4_btc_v2_try_fire = staticmethod(_r4_signal.r4_btc_v2_try_fire)
    r4_eth_v2_try_fire = staticmethod(_r4_signal.r4_eth_v2_try_fire)
    ema_btc_try_fire = staticmethod(_ema_signal.ema_btc_try_fire)
    eth_daily_try_fire = staticmethod(_eth_daily_signal.eth_daily_try_fire)


CAPITAL_USDT = 10_000.0


def _today_inputs_stub(mode="uncertain", lev=2.0, gated=False, ema_p=-1,
                        ema_p_prev=None, mode_prev=None):
    """Build a stub ``today_inputs()`` result with deterministic values.
    Caller passes regime mode; weights are looked up from
    ``simulate.REGIME_WEIGHTS_FULL`` so they always match what the live
    handlers expect.

    The ``ema_p_prev`` / ``mode_prev`` fields default to *fresh-transition*
    sentinels (yesterday differs from today): yesterday's ema_p is the
    sign-opposite of today's, yesterday's mode is the opposite kind. This
    means stubs that don't pass them explicitly behave like "today is a
    fresh cross / regime entry" — which is what most existing tests want.
    Tests for the cold-start guard pass them explicitly."""
    from strategies.support import jplus_inputs as simulate
    if ema_p_prev is None:
        ema_p_prev = -ema_p if ema_p != 0 else 0
    if mode_prev is None:
        mode_prev = ("bear" if mode in ("strong_bull", "mild_bull")
                     else "strong_bull")
    return {
        "date": clock.now_utc().date().isoformat(),
        "mode": mode,
        "mode_prev": mode_prev,
        "lev": lev,
        "gated": gated,
        "ema_p": ema_p,
        "ema_p_prev": ema_p_prev,
        "weights": simulate._cap_core_weights(
            simulate.REGIME_WEIGHTS_FULL[mode]),
        "weights_prev": simulate._cap_core_weights(
            simulate.REGIME_WEIGHTS_FULL[mode_prev]),
    }


@pytest.fixture
def live_env(tmp_path, monkeypatch):
    """Tmp dashboard.db with the canonical schema. Patches DASH_DB so all
    handler writes land in the fixture DB."""
    fixture_db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    return fixture_db


def _variant() -> dict:
    return {"id": "test_live_v1", "capital_usdt": CAPITAL_USDT}


# ─── R4_BTC ─────────────────────────────────────────────────────────────────


def test_r4_btc_skips_on_tuesday(live_env, monkeypatch):
    """Calendar gate: Tuesdays return ``not_calendar_day`` and don't open."""
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
    clock.set_simulated_now(datetime(2026, 5, 18, 6, 1, tzinfo=timezone.utc))  # Mon, day=18
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_wk_1_2"


def test_r4_btc_skips_before_06_00(live_env, monkeypatch):
    """Time gate: returns ``before_open_window`` if hour < 6."""
    clock.set_simulated_now(datetime(2026, 5, 4, 5, 30, tzinfo=timezone.utc))  # Mon 05:30
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "before_open_window"


def test_r4_btc_skips_after_18_00(live_env, monkeypatch):
    """Time gate: returns ``after_close_window`` if hour >= 18."""
    clock.set_simulated_now(datetime(2026, 5, 4, 18, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "after_close_window"


def test_r4_btc_opens_on_monday_within_window(live_env, monkeypatch):
    """Mon wk1-2 between 06:00 and 18:00 with valid inputs and price → opens.
    Verify trade fields: strategy, asset, direction, sizing math, exit_time."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    expected_r4_btc = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["uncertain"])["r4_btc"]
    assert result["weight"] == pytest.approx(expected_r4_btc)

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
    # Notional = capital × capped_weight × stacked_lev. uncertain regime
    # raw r4_btc=0.30, capped by CORE_ALLOC_CAP=0.5 / sum(1.35) → 0.1111
    expected_notional = CAPITAL_USDT * expected_r4_btc * 5.0
    assert row["size_usdt"] == pytest.approx(expected_notional)
    assert row["qty"] == pytest.approx(expected_notional / 70_000.0, rel=1e-6)
    # Exit scheduled at 18:00 same day
    assert row["exit_time"].startswith("2026-05-04T18:00")


def test_r4_btc_idempotent_within_window(live_env, monkeypatch):
    """Calling the handler twice in the same window opens exactly one trade."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    clock.set_simulated_now(datetime(2026, 5, 4, 20, 1, tzinfo=timezone.utc))  # Mon
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_tuesday"


def test_r4_eth_skips_when_next_day_over_14(live_env, monkeypatch):
    """Tue 2026-05-12 → next-day Wed 2026-05-13 day=13 ≤14 ✓ but Tue
    2026-05-19 → next-day Wed 2026-05-20 day=20 >14 → reject."""
    clock.set_simulated_now(datetime(2026, 5, 19, 20, 1, tzinfo=timezone.utc))  # Tue, day=19
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "next_day_not_wk_1_2"


def test_r4_eth_skips_before_20_00(live_env, monkeypatch):
    """Time gate: hour < 20 → before_open_window."""
    clock.set_simulated_now(datetime(2026, 5, 5, 19, 30, tzinfo=timezone.utc))  # Tue 19:30
    try:
        result = jplus_live.r4_eth_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "before_open_window"


def test_r4_eth_opens_tue_20_with_exit_wed_20(live_env, monkeypatch):
    """Tue 2026-05-05 20:01 UTC (next-day Wed=05-06 day=6 ≤14) opens with
    scheduled_exit_dt at Wed 20:00 UTC."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    # uncertain raw r4_eth=0.40, capped by CORE_ALLOC_CAP/1.35 → 0.1481
    from strategies.support import jplus_inputs as _sim
    expected_w = _sim._cap_core_weights(_sim.REGIME_WEIGHTS_FULL["uncertain"])["r4_eth"]
    assert row["size_usdt"] == pytest.approx(CAPITAL_USDT * expected_w * 5.0)
    # Exit at Wed 2026-05-06 20:00 UTC
    assert row["exit_time"].startswith("2026-05-06T20:00")


def test_r4_eth_idempotent_within_window(live_env, monkeypatch):
    """Two calls on the same Tuesday after 20:00 produce one trade."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    # uncertain raw ema_btc=0.30, capped by CORE_ALLOC_CAP/1.35 → 0.1111; lev=2
    expected_w = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["uncertain"])["ema_btc"]
    assert row["size_usdt"] == pytest.approx(CAPITAL_USDT * expected_w * 2.0)
    assert row["leverage"] == pytest.approx(2.0)


def test_ema_btc_opens_short_when_ema_p_negative(live_env, monkeypatch):
    """ema_p=-1 → SHORT direction."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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
    # strong_bull raw eth_daily=0.20, capped by CORE_ALLOC_CAP/1.15 → 0.0870; lev=3
    expected_w = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["strong_bull"])["eth_daily"]
    assert row["size_usdt"] == pytest.approx(CAPITAL_USDT * expected_w * 3.0)


def test_eth_daily_closes_when_regime_exits_bull(live_env, monkeypatch):
    """Open in mild_bull → regime flips to uncertain → CLOSE."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
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


# ─── Cold-start guards ──────────────────────────────────────────────────────

def test_ema_btc_cold_start_skips_when_yesterday_matches_today(live_env, monkeypatch):
    """Variant has no open EMA position AND yesterday's ema_p already
    matched today's: the cross fired before this variant was emitting,
    so the handler must wait for the next cross instead of cold-opening
    at today's price (the SJ-3140 phantom-entry bug)."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=-1, ema_p_prev=-1))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "awaiting_fresh_cross"
    con = sqlite3.connect(str(live_env))
    n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_EMA_BTC'"
    ).fetchone()[0]
    con.close()
    assert n == 0


def test_ema_btc_opens_on_fresh_cross_after_cold_start(live_env, monkeypatch):
    """Day 1 mid-signal (yesterday matches today) — no open. Day 2 the
    weekly EMA flips — handler opens. Confirms the guard releases on
    the next genuine cross, not just on any later tick."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 70_000.0)

    # Day 1: mid-signal cold start — no open.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=-1, ema_p_prev=-1))
    clock.set_simulated_now(datetime(2026, 5, 5, 0, 1, tzinfo=timezone.utc))
    try:
        first = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert first["status"] == "awaiting_fresh_cross"

    # Day 2: ema_p flips +1 (yesterday was -1) — fresh cross, open LONG.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     ema_p=+1, ema_p_prev=-1))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        second = jplus_live.ema_btc_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert second["status"] == "opened"

    con = sqlite3.connect(str(live_env))
    direction = con.execute(
        "SELECT direction FROM trades WHERE strategy='JPLUS_EMA_BTC' "
        "AND status='open'"
    ).fetchone()[0]
    con.close()
    assert direction == "LONG"


def test_eth_daily_cold_start_skips_when_yesterday_already_bull(live_env, monkeypatch):
    """Variant cold-starts mid-bull-regime — handler must wait for the
    next regime exit + reentry rather than chasing into a trend."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="strong_bull", lev=3.0,
                                                     mode_prev="mild_bull"))
    clock.set_simulated_now(datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "awaiting_fresh_bull_entry"
    con = sqlite3.connect(str(live_env))
    n = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_ETH_DAILY'"
    ).fetchone()[0]
    con.close()
    assert n == 0


def test_eth_daily_opens_on_fresh_bull_entry_after_cold_start(live_env, monkeypatch):
    """Day 1 mid-bull cold start — no open. Day 2 still bull, still
    no open (yesterday is now bull too). Day 3 regime exits → still no
    open (not bull). Day 4 regime re-enters bull from non-bull — handler
    opens."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)

    # Day 1: cold-start mid-bull — guard trips.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="strong_bull", lev=3.0,
                                                     mode_prev="strong_bull"))
    clock.set_simulated_now(datetime(2026, 5, 1, 0, 1, tzinfo=timezone.utc))
    try:
        d1 = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert d1["status"] == "awaiting_fresh_bull_entry"

    # Day 2: regime exits to uncertain — also no open, no position needed.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     mode_prev="strong_bull"))
    clock.set_simulated_now(datetime(2026, 5, 2, 0, 1, tzinfo=timezone.utc))
    try:
        d2 = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert d2["status"] == "no_position_needed"

    # Day 3: regime re-enters bull — fresh entry, OPEN.
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="mild_bull", lev=2.5,
                                                     mode_prev="uncertain"))
    clock.set_simulated_now(datetime(2026, 5, 3, 0, 1, tzinfo=timezone.utc))
    try:
        d3 = jplus_live.eth_daily_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert d3["status"] == "opened"

    con = sqlite3.connect(str(live_env))
    n_open = con.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_ETH_DAILY' "
        "AND status='open'"
    ).fetchone()[0]
    con.close()
    assert n_open == 1


# ─── R4_BTC Mon-only (regression for 2026-05-08 V1/V2 split) ───────────────


def test_r4_btc_v1_skips_on_wednesday(live_env, monkeypatch):
    """Post-2026-05-08, R4_BTC fires Mondays only — Wednesdays are
    R4_BTC_V2's territory. Verify the V1 handler skips Wed."""
    clock.set_simulated_now(datetime(2026, 5, 6, 6, 1, tzinfo=timezone.utc))  # Wed
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


# ─── R4_BTC_V2 (Wed+Fri wk1-2 04→14 UTC) ───────────────────────────────────


def test_r4_btc_v2_skips_on_monday(live_env, monkeypatch):
    """V2 fires Wed+Fri only. Monday is V1's day."""
    clock.set_simulated_now(datetime(2026, 5, 4, 4, 1, tzinfo=timezone.utc))  # Mon
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_calendar_day"


def test_r4_btc_v2_skips_on_thursday(live_env, monkeypatch):
    clock.set_simulated_now(datetime(2026, 5, 7, 4, 1, tzinfo=timezone.utc))  # Thu
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_calendar_day"


def test_r4_btc_v2_skips_when_day_over_14(live_env, monkeypatch):
    """wk1-2 filter: day > 14 returns ``not_wk_1_2``."""
    # 2026-05-15 is a Friday but day=15 (out of wk1-2)
    clock.set_simulated_now(datetime(2026, 5, 15, 4, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_wk_1_2"


def test_r4_btc_v2_skips_before_04_00(live_env, monkeypatch):
    clock.set_simulated_now(datetime(2026, 5, 6, 3, 30, tzinfo=timezone.utc))  # Wed 03:30
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "before_open_window"


def test_r4_btc_v2_skips_after_14_00(live_env, monkeypatch):
    clock.set_simulated_now(datetime(2026, 5, 6, 14, 1, tzinfo=timezone.utc))  # Wed 14:01
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "after_close_window"


def test_r4_btc_v2_opens_on_wednesday_within_window(live_env, monkeypatch):
    """Wed wk1-2, 04:00-14:00 UTC, valid inputs → opens BTC LONG.
    Sizing: capital × weights['r4_btc_v2'] × inner_lev × vol_lev."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 80_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0,
                                                     gated=False))
    clock.set_simulated_now(datetime(2026, 5, 6, 4, 1, tzinfo=timezone.utc))  # Wed, day=6
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    assert result["entry_price"] == 80_000.0
    # uncertain regime, capped: r4_btc_v2 raw 0.15 → capped 0.15 × (0.5/1.35)
    expected = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["uncertain"])["r4_btc_v2"]
    assert result["weight"] == pytest.approx(expected)
    # inner_lev 2.5 × vol_lev 2.0
    assert result["stacked_lev"] == pytest.approx(5.0)

    con = sqlite3.connect(str(live_env))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy='JPLUS_R4_BTC_V2' AND status='open'"
    ).fetchone()
    con.close()
    assert row is not None
    assert row["asset"] == "BTC"
    assert row["direction"] == "LONG"
    # uncertain raw r4_btc_v2=0.15, capped by CORE_ALLOC_CAP/1.35 → 0.0556; lev=5
    expected_w_v2 = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["uncertain"])["r4_btc_v2"]
    assert row["size_usdt"] == pytest.approx(CAPITAL_USDT * expected_w_v2 * 5.0)


def test_r4_btc_v2_opens_on_friday_within_window(live_env, monkeypatch):
    """Friday is the second V2 firing day."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 80_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 8, 4, 1, tzinfo=timezone.utc))  # Fri, day=8
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"


def test_r4_btc_v2_idempotent_within_window(live_env, monkeypatch):
    """Two ticks in the same window must not double-open."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 80_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 6, 4, 1, tzinfo=timezone.utc))
    try:
        a = jplus_live.r4_btc_v2_try_fire(_variant(), {})
        clock.set_simulated_now(datetime(2026, 5, 6, 4, 6, tzinfo=timezone.utc))
        b = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert a["status"] == "opened"
    assert b["status"] == "already_open"


def test_r4_btc_v2_skips_in_bear_regime(live_env, monkeypatch):
    """bear regime weight = 0 → no open."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 80_000.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="bear", lev=1.5))
    clock.set_simulated_now(datetime(2026, 5, 6, 4, 1, tzinfo=timezone.utc))
    try:
        result = jplus_live.r4_btc_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "regime_zero_weight"


# ─── R4_ETH_V2 (smoke test — same calendar/window machinery as BTC V2) ─────


def test_r4_eth_v2_opens_on_wednesday_within_window(live_env, monkeypatch):
    """Confirm the ETH V2 sleeve fires on Wed and writes an ETH trade."""
    from strategies.support import price_feed
    from strategies.support import jplus_inputs as core_sim
    monkeypatch.setattr(price_feed, "get_current_price", lambda _a: 3_500.0)
    monkeypatch.setattr(core_sim, "today_inputs",
                         lambda: _today_inputs_stub(mode="uncertain", lev=2.0))
    clock.set_simulated_now(datetime(2026, 5, 6, 4, 1, tzinfo=timezone.utc))  # Wed
    try:
        result = jplus_live.r4_eth_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "opened"
    # uncertain regime, capped: r4_eth_v2 raw 0.20 → capped 0.20 × (0.5/1.35)
    expected = core_sim._cap_core_weights(
        core_sim.REGIME_WEIGHTS_FULL["uncertain"])["r4_eth_v2"]
    assert result["weight"] == pytest.approx(expected)

    con = sqlite3.connect(str(live_env))
    direction = con.execute(
        "SELECT direction, asset FROM trades WHERE strategy='JPLUS_R4_ETH_V2'"
    ).fetchone()
    con.close()
    assert direction[1] == "ETH"
    assert direction[0] == "LONG"


def test_r4_eth_v2_skips_on_thursday(live_env, monkeypatch):
    clock.set_simulated_now(datetime(2026, 5, 7, 4, 1, tzinfo=timezone.utc))  # Thu
    try:
        result = jplus_live.r4_eth_v2_try_fire(_variant(), {})
    finally:
        clock.set_simulated_now(None)
    assert result["status"] == "not_calendar_day"
