"""Tests for ``jplus.simulate.today_inputs()`` — the per-day decision
inputs (regime mode, vol-target leverage, R4 gate, EMA position, sub-
sleeve weights) used by the live entry handlers in
``services/jplus_live.py``.

These tests use the real ``data/trader.db`` so the assertions exercise
the same data path the live bot does. Tagged ``slow`` because they pull
the full BTC/ETH history each call.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import clock
from jplus import simulate


@pytest.mark.slow
def test_today_inputs_returns_expected_shape():
    """The dict has all keys the live handlers depend on, and types are
    sane (lev positive float, ema_p ∈ {-1, 0, +1}, mode is one of the
    four regime labels, weights sums to a known per-regime total)."""
    clock.set_simulated_now(datetime(2026, 5, 6, 12, tzinfo=timezone.utc))
    try:
        ti = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert ti is not None, "live trader.db should have enough history"
    assert ti["date"] == "2026-05-06"
    assert ti["mode"] in ("strong_bull", "mild_bull", "uncertain", "bear")
    assert isinstance(ti["lev"], float) and ti["lev"] > 0
    assert ti["ema_p"] in (-1, 0, 1)
    assert isinstance(ti["gated"], bool)
    w = ti["weights"]
    for k in ("ema_btc", "eth_daily", "r4_btc", "r4_eth"):
        assert k in w
        assert isinstance(w[k], (int, float))
        assert w[k] >= 0


@pytest.mark.slow
def test_today_inputs_weights_match_regime_table():
    """Whichever regime today_inputs returns, the weights it returns must
    equal ``REGIME_WEIGHTS_FULL[mode]`` exactly. This is the single
    invariant guarding against drift between today_inputs and the
    simulator's inline allocation."""
    clock.set_simulated_now(datetime(2026, 5, 6, 12, tzinfo=timezone.utc))
    try:
        ti = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert ti is not None
    expected = simulate.REGIME_WEIGHTS_FULL[ti["mode"]]
    assert ti["weights"] == expected


@pytest.mark.slow
def test_today_inputs_lev_within_regime_cap():
    """Vol-target leverage is regime-capped via ``H_CAPS`` in
    ``jplus.voltarget``. Whatever regime today_inputs returns, the lev
    should fall in [LEV_FLOOR, H_CAPS[mode]]."""
    from jplus import voltarget
    clock.set_simulated_now(datetime(2026, 5, 6, 12, tzinfo=timezone.utc))
    try:
        ti = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert ti is not None
    cap = voltarget.H_CAPS[ti["mode"]]
    assert voltarget.LEV_FLOOR <= ti["lev"] <= cap


@pytest.mark.slow
def test_today_inputs_consistent_with_simulator_recent_state():
    """Sanity: today_inputs(clock=T) gives the same regime/lev/ema_p that
    the simulator's last-emitted row uses, when T is a date the simulator
    has already emitted (i.e. T's daily close is in the data and the
    simulator's clock-date guard hasn't excluded it yet).

    Strategy: pick a clock just past 2026-05-04's daily close. The
    simulator emits a row for 2026-05-04 (using inputs derived from
    2026-05-03 and earlier). today_inputs called at clock=2026-05-04
    should return values derived from data through 2026-05-03 — i.e.
    the same det_i = index_of(2026-05-03), giving the same mode."""
    target_iso = "2026-05-04"
    clock.set_simulated_now(datetime(2026, 5, 4, 23, 59, tzinfo=timezone.utc))
    try:
        # The simulator excludes the clock-date row, so set clock to a
        # moment late enough that 2026-05-04 is "yesterday" for both
        # paths. simulate() emits rows < clock_date; today_inputs uses
        # data through yesterday.
        sim_out = simulate.simulate(end_date=target_iso)
    finally:
        clock.set_simulated_now(None)
    # 2026-05-04 should be in the simulator output (it's < 2026-05-05
    # but the clock is 2026-05-04 23:59, so clock_date is 2026-05-04 →
    # excluded). Use 2026-05-03 instead — guaranteed in the output.
    pivot = "2026-05-03"
    assert pivot in sim_out
    sim_row = sim_out[pivot]

    # Now call today_inputs with clock set to 2026-05-03 — its "today"
    # is 2026-05-03, and the inputs it computes should match the
    # simulator's row for 2026-05-03.
    clock.set_simulated_now(datetime(2026, 5, 3, 12, tzinfo=timezone.utc))
    try:
        ti = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert ti is not None
    assert ti["date"] == "2026-05-03"
    assert ti["mode"] == sim_row["mode"]
    # lev tolerances: voltarget uses recent_1x history; the simulator
    # builds it up through 2026-05-02 inclusive when emitting 2026-05-03.
    # today_inputs builds it through 2026-05-02 too (since 2026-05-03 is
    # excluded by the clock-date guard). They should agree exactly.
    assert ti["lev"] == pytest.approx(sim_row["lev"], rel=1e-9)
    assert ti["gated"] == sim_row["gated"]
    assert ti["ema_p"] == sim_row["ema_p"]


@pytest.mark.slow
def test_today_inputs_idempotent_at_same_clock():
    """Two calls at the same simulated clock must return the same dict.
    No randomness, no global state mutation across calls."""
    clock.set_simulated_now(datetime(2026, 5, 6, 12, tzinfo=timezone.utc))
    try:
        a = simulate.today_inputs()
        b = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert a == b


@pytest.mark.slow
def test_today_inputs_rolls_forward_with_clock():
    """today_inputs called at clock=T1 then clock=T2 (T2 a different day)
    returns dicts with date=T1's UTC date and date=T2's UTC date
    respectively. Even if the regime is unchanged, the date field rolls."""
    clock.set_simulated_now(datetime(2026, 5, 5, 12, tzinfo=timezone.utc))
    try:
        a = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    clock.set_simulated_now(datetime(2026, 5, 6, 12, tzinfo=timezone.utc))
    try:
        b = simulate.today_inputs()
    finally:
        clock.set_simulated_now(None)
    assert a is not None and b is not None
    assert a["date"] == "2026-05-05"
    assert b["date"] == "2026-05-06"
