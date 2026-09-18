"""Synthetic fixtures for the ORB reference engine (TEST_PLAN.md section 5).

Every market here is flat at 100.00 (high 100.02, low 99.98) unless a test overrides a
bar; the fifteen range bars reach 100.05 / 99.95, so the opening range is H=100.05,
L=99.95, W=0.10 and each test changes only the bars that matter. Session anchor t0 is
minute 60 of a 2021-06-01 panel.

Run from the repository root:
    venv\\Scripts\\python.exe -m pytest studies/notebooks/orb_study/tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orb_engine as eng  # noqa: E402

T0 = int(pd.Timestamp("2021-06-01", tz="UTC").timestamp() * 1000)
I0 = 60                       # session anchor minute
N = 3 * 1440


def market(bars: dict[int, tuple] | None = None, missing=(), outage=(), funding=(), n=N,
           tick=0.01) -> eng.Market:
    o = np.full(n, 100.0)
    h = np.full(n, 100.02)
    lo = np.full(n, 99.98)
    c = np.full(n, 100.0)
    v = np.ones(n)
    h[I0:I0 + 15] = 100.05                 # the opening range bars set H and L
    lo[I0:I0 + 15] = 99.95
    for i, (bo, bh, bl, bc) in (bars or {}).items():
        o[i], h[i], lo[i], c[i] = bo, bh, bl, bc
    for i in missing:
        o[i] = h[i] = lo[i] = c[i] = np.nan
    for i in outage:                     # archive filler: flat bar with zero volume
        v[i] = 0.0
    panel = {"t0_ms": T0, "open": o, "high": h, "low": lo, "close": c, "volume": v, "quote_volume": v * c}
    f_idx = np.array([f[0] for f in funding], dtype=np.int64)
    f_rate = np.array([f[1] for f in funding], dtype=np.float64)
    return eng.Market.from_panel("TEST", panel, {"time_ms": T0 + f_idx * 60_000, "rate": f_rate},
                                 tick_before=tick, tick_after=tick, tick_change_utc=None)


def sessions(*minutes: int) -> pd.DataFrame:
    rows = []
    for m in minutes or (I0,):
        t = T0 + m * 60_000
        rows.append({"date": pd.Timestamp(t, unit="ms", tz="UTC").date().isoformat(), "t0_ms": t,
                     "early_close": False, "weekday": 1, "ny_minus_ldn_hours": -5.0})
    return pd.DataFrame(rows)


def run(mkt, policy=None, sess=None, end="2021-06-03", **kw):
    p = policy or eng.Policy(id="P0", **kw)
    return eng.run_policy(mkt, sess if sess is not None else sessions(), p, "2021-06-01", end)


def one(mkt, **kw) -> dict:
    out = run(mkt, **kw)
    assert len(out) == 1
    return out.iloc[0].to_dict()


UP = (100.0, 100.20, 100.00, 100.10)        # closes above H = 100.05
DOWN = (100.0, 100.00, 99.80, 99.90)        # closes below L = 99.95


# --- range and trigger -------------------------------------------------------------------

def test_no_breakout_is_a_logged_non_trade():
    r = one(market())
    assert r["status"] == "no_trade" and r["reason"] == "no_trigger"
    assert r["H"] == pytest.approx(100.05) and r["L"] == pytest.approx(99.95)


def test_upside_close_breakout_fills_next_open_with_opposite_stop():
    r = one(market({I0 + 20: UP, I0 + 21: (100.12, 100.15, 100.10, 100.12)}))
    assert r["status"] == "trade" and r["side"] == 1
    assert r["trigger_idx"] == I0 + 20 and r["fill_idx"] == I0 + 21
    assert r["fill_px"] == pytest.approx(100.12) and r["stop_px"] == pytest.approx(99.95)
    assert r["exit_reason"] == "time" and r["exit_idx"] == I0 + 390
    assert r["gross_bp"] == pytest.approx((100.0 - 100.12) / 100.12 * 1e4)


def test_downside_close_breakout_is_short_with_stop_at_high():
    r = one(market({I0 + 30: DOWN}))
    assert r["side"] == -1 and r["stop_px"] == pytest.approx(100.05) and r["fill_idx"] == I0 + 31


def test_close_exactly_on_the_boundary_does_not_trigger():
    r = one(market({I0 + 20: (100.0, 100.20, 100.0, 100.05)}))
    assert r["reason"] == "no_trigger"


def test_move_inside_the_range_window_widens_the_range_instead_of_trading():
    r = one(market({I0 + 14: (100.0, 100.50, 100.0, 100.45)}))
    assert r["H"] == pytest.approx(100.50) and r["reason"] == "no_trigger"


def test_first_eligible_trigger_is_the_bar_at_range_end():
    r = one(market({I0 + 15: UP}))
    assert r["trigger_idx"] == I0 + 15 and r["fill_idx"] == I0 + 16


def test_fill_must_land_strictly_before_the_deadline():
    late = one(market({I0 + 119: UP}))            # fill would be at t0+120 = deadline
    assert late["reason"] == "no_trigger"
    ok = one(market({I0 + 118: UP}))              # fill at t0+119
    assert ok["status"] == "trade" and ok["fill_idx"] == I0 + 119


def test_latency_delays_the_fill_and_moves_the_deadline():
    r = one(market({I0 + 20: UP}), latency_min=2)
    assert r["fill_idx"] == I0 + 23
    assert one(market({I0 + 117: UP}), latency_min=2)["reason"] == "no_trigger"


def test_first_trigger_consumes_the_session_no_reentry_after_stop():
    bars = {I0 + 20: UP, I0 + 21: (100.10, 100.10, 99.90, 99.92),   # stopped in the fill bar
            I0 + 40: UP}                                           # second break is ignored
    out = run(market(bars))
    assert len(out) == 1 and out.iloc[0]["exit_reason"] == "stop" and out.iloc[0]["exit_idx"] == I0 + 21


def test_direction_gate_watches_only_the_candle_direction():
    green = {I0: (99.97, 100.05, 99.95, 99.97), I0 + 14: (100.0, 100.05, 99.95, 100.02)}
    r = one(market({**green, I0 + 20: DOWN, I0 + 40: UP}), direction_gate=True)
    assert r["side"] == 1 and r["trigger_idx"] == I0 + 40


def test_doji_opening_range_skips_under_the_direction_gate():
    r = one(market({I0 + 20: UP}), direction_gate=True)          # range open == range close
    assert r["reason"] == "doji"


def test_breakout_buffer_requires_clearing_the_boundary_by_a_fraction_of_width():
    near = (100.0, 100.20, 100.0, 100.055)                        # 0.005 above H < 0.1 * W
    assert one(market({I0 + 20: near}), buffer_w=0.10)["reason"] == "no_trigger"
    assert one(market({I0 + 20: UP}), buffer_w=0.10)["status"] == "trade"


# --- stops, gaps and targets -----------------------------------------------------------------

def test_stop_touched_in_the_entry_minute_exits_at_the_stop():
    r = one(market({I0 + 20: UP, I0 + 21: (100.10, 100.12, 99.90, 99.95)}))
    assert r["exit_reason"] == "stop" and r["exit_idx"] == I0 + 21 and r["exit_px"] == pytest.approx(99.95)
    assert r["flags"] == ""                                       # fill at the open: not ambiguous


def test_gap_through_protective_stop_exits_at_the_open():
    r = one(market({I0 + 20: UP, I0 + 40: (99.80, 99.85, 99.70, 99.80)}))
    assert r["exit_reason"] == "stop_gap" and r["exit_px"] == pytest.approx(99.80)


def test_delayed_submission_skips_when_last_price_is_already_beyond_the_stop():
    r = one(market({I0 + 20: UP, I0 + 21: (100.1, 100.1, 99.80, 99.90)}), latency_min=1)
    assert r["status"] == "no_trade" and r["reason"] == "beyond_stop_pre_submit"


def test_fill_through_the_stop_is_booked_as_immediate_liquidation():
    r = one(market({I0 + 20: UP, I0 + 22: (99.90, 99.95, 99.85, 99.90)}), latency_min=1)
    assert r["exit_reason"] == "fill_through_stop" and r["gross_bp"] == 0.0 and r["risk_bp"] < 0


def test_stops_round_outward_to_the_tick():
    bars = {I0 + 3: (100.0, 100.057, 99.943, 100.0), I0 + 20: (100.0, 100.3, 100.0, 100.2)}
    long_r = one(market(bars))
    assert long_r["stop_px"] == pytest.approx(99.94)               # 99.943 rounded down
    short_r = one(market({I0 + 3: (100.0, 100.057, 99.943, 100.0), I0 + 20: (100.0, 100.0, 99.7, 99.8)}))
    assert short_r["stop_px"] == pytest.approx(100.06)             # 100.057 rounded up


def test_stop_and_target_in_one_bar_books_the_stop_and_flags_it():
    bars = {I0 + 20: UP, I0 + 30: (100.0, 100.60, 99.90, 100.0)}
    r = one(market(bars), target_r=2.0)
    assert r["exit_reason"] == "stop" and "ambiguous_stop_target" in r["flags"]


def test_target_needs_a_trade_through_by_one_tick():
    fill = 100.0                                                   # default open of the fill bar
    target = fill + 2.0 * (fill - 99.95)                           # 100.10
    touch = one(market({I0 + 20: UP, I0 + 30: (100.0, target, 100.0, 100.0)}), target_r=2.0)
    assert touch["exit_reason"] == "time"
    through = one(market({I0 + 20: UP, I0 + 30: (100.0, target + 0.01, 100.0, 100.0)}), target_r=2.0)
    assert through["exit_reason"] == "target" and through["exit_px"] == pytest.approx(target)


def test_midpoint_stop():
    r = one(market({I0 + 20: UP, I0 + 21: (100.12, 100.15, 100.10, 100.12)}), stop="mid")
    assert r["stop_px"] == pytest.approx(100.00)


def test_exit_at_session_end_ignores_a_stop_touch_in_the_exit_bar():
    r = one(market({I0 + 20: UP, I0 + 390: (100.0, 100.0, 99.0, 99.5)}))
    assert r["exit_reason"] == "time" and r["exit_px"] == pytest.approx(100.0)


def test_entry60_time_exit():
    r = one(market({I0 + 20: UP}), time_exit="entry60")
    assert r["exit_idx"] == I0 + 21 + 60 and r["exit_reason"] == "time"


# --- resting stop entries ------------------------------------------------------------------------

def test_resting_buy_stop_fills_at_level_or_worse_open():
    level = one(market({I0 + 25: (100.0, 100.10, 100.0, 100.08)}), entry="stop")
    assert level["side"] == 1 and level["fill_px"] == pytest.approx(100.06) and level["fill_idx"] == I0 + 25
    gap = one(market({I0 + 24: (100.0, 100.05, 99.95, 100.04), I0 + 25: (100.20, 100.25, 100.15, 100.2)}),
              entry="stop")
    assert gap["fill_px"] == pytest.approx(100.20)


def test_resting_stop_fill_then_stop_touch_in_same_bar_is_ambiguous_and_stopped():
    r = one(market({I0 + 25: (100.0, 100.10, 99.945, 100.0)}), entry="stop")   # low hits L, not L - tick
    assert r["side"] == 1 and r["exit_reason"] == "stop" and r["flags"] == "ambiguous_fill_bar_stop"


def test_bar_breaching_both_boundaries_keeps_the_worse_path():
    r = one(market({I0 + 25: (100.0, 100.30, 99.70, 100.0)}), entry="stop")
    assert "ambiguous_both_boundaries" in r["flags"] and r["exit_reason"] == "stop"
    assert r["gross_bp"] < 0


# --- controls ------------------------------------------------------------------------------------

def test_momentum_control_enters_at_range_end_in_candle_direction():
    red = {I0: (100.03, 100.05, 99.95, 100.03), I0 + 14: (100.0, 100.05, 99.95, 99.98)}
    r = one(market(red), entry="momentum")
    assert r["side"] == -1 and r["fill_idx"] == I0 + 15 and r["stop_px"] == pytest.approx(100.05)


def test_fade_control_reverses_the_break_with_wide_stop_and_mid_target():
    r = one(market({I0 + 20: UP}), entry="fade")
    assert r["side"] == -1 and r["stop_px"] == pytest.approx(100.15) and r["target_px"] == pytest.approx(100.0)


# --- data problems -----------------------------------------------------------------------------

def test_missing_range_minute_invalidates_the_session():
    r = one(market(missing=[I0 + 7]))
    assert r["status"] == "invalid" and r["reason"] == "missing_range_bar"


def test_outage_filler_bar_counts_as_missing():
    r = one(market(outage=[I0 + 7]))
    assert r["status"] == "invalid" and r["reason"] == "missing_range_bar"


def test_missing_trigger_minute_cannot_trigger():
    bars = {I0 + 20: UP, I0 + 40: UP}
    r = one(market(bars, missing=[I0 + 20]))
    assert r["trigger_idx"] == I0 + 40


def test_missing_fill_bar_is_a_logged_skip():
    r = one(market({I0 + 20: UP}, missing=[I0 + 21]))
    assert r["status"] == "no_trade" and r["reason"] == "missing_fill_bar"


def test_gap_while_invested_resumes_at_next_open_and_flags():
    bars = {I0 + 20: UP, I0 + 60: (99.80, 99.85, 99.75, 99.80)}
    r = one(market(bars, missing=range(I0 + 50, I0 + 60)))
    assert r["exit_reason"] == "stop_gap" and r["exit_idx"] == I0 + 60 and "gap_while_invested" in r["flags"]


def test_time_exit_delayed_by_missing_exit_bar():
    r = one(market({I0 + 20: UP}, missing=[I0 + 390, I0 + 391]))
    assert r["exit_idx"] == I0 + 392 and "time_exit_delayed" in r["flags"]


# --- funding -------------------------------------------------------------------------------------

def test_funding_is_side_correct_and_inclusive_at_the_boundaries():
    fill, exit_ = I0 + 21, I0 + 390
    bars = {I0 + 20: UP}
    long_r = one(market(bars, funding=[(fill, 0.0001), (exit_, 0.0001), (exit_ + 1, 0.0001)]))
    assert long_r["funding_bp"] == pytest.approx(-2.0)             # pays at fill and exit minute, not after
    short_r = one(market({I0 + 20: DOWN}, funding=[(I0 + 100, 0.0001)]))
    assert short_r["funding_bp"] == pytest.approx(1.0)             # short receives a positive rate


# --- exit events ---------------------------------------------------------------------------------

def test_reenter_1m_exits_next_open_after_a_close_back_inside():
    bars = {I0 + 20: UP, I0 + 21: (100.10, 100.12, 100.06, 100.10), I0 + 22: (100.10, 100.10, 100.0, 100.02),
            I0 + 23: (100.03, 100.05, 100.0, 100.03)}
    r = one(market(bars), exit_event="reenter_1m")
    assert r["exit_reason"] == "event_reenter_1m" and r["exit_idx"] == I0 + 23 and r["exit_px"] == pytest.approx(100.03)


def test_reenter_15m_only_checks_closes_that_end_a_15_minute_block():
    bars = {I0 + 20: UP, I0 + 21: (100.1, 100.12, 100.06, 100.10), I0 + 22: (100.10, 100.1, 100.0, 100.0)}
    for i in range(I0 + 23, I0 + 29):
        bars[i] = (100.10, 100.12, 100.06, 100.10)
    bars[I0 + 29] = (100.10, 100.12, 100.0, 100.01)                # minute 29 ends block [15, 30)
    r = one(market(bars), exit_event="reenter_15m")
    assert r["exit_reason"] == "event_reenter_15m" and r["exit_idx"] == I0 + 30


def test_trailing_stop_ratchets_from_completed_bars_only():
    bars = {I0 + 20: UP, I0 + 21: (100.10, 100.50, 100.10, 100.45),  # new high 100.50 -> stop 100.40
            I0 + 22: (100.45, 100.46, 100.39, 100.40)}
    r = one(market(bars), stop="trail_w")
    assert r["exit_reason"] == "stop" and r["exit_idx"] == I0 + 22 and r["exit_px"] == pytest.approx(100.40)


def test_vwap_exit_uses_session_vwap_from_the_anchor():
    bars = {I0 + 20: UP, I0 + 21: (100.1, 100.12, 99.96, 99.98)}
    r = one(market(bars), exit_event="vwap")
    assert r["exit_reason"] == "event_vwap" and r["exit_idx"] == I0 + 22


def test_no_time_exit_holds_across_sessions_and_blocks_the_next_one():
    bars = {I0 + 20: UP}
    sess = sessions(I0, I0 + 1440)
    out = run(market(bars), sess=sess, time_exit="none", censor_days=7)
    assert out.iloc[0]["exit_reason"] == "censored_block_end"
    assert out.iloc[1]["reason"] == "position_open"


# --- causality -----------------------------------------------------------------------------------

def test_future_bars_do_not_change_earlier_decisions():
    bars = {I0 + 20: UP, I0 + 200: (100.0, 100.0, 99.0, 99.2)}
    full = one(market(bars))
    truncated_bars = dict(bars)
    truncated_bars.pop(I0 + 200)
    early = one(market(truncated_bars, n=N))
    for k in ("trigger_idx", "side", "fill_idx", "fill_px", "stop_px"):
        assert full[k] == early[k]
    assert full["exit_idx"] == I0 + 200 and early["exit_idx"] == I0 + 390
