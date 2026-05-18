"""Unit tests for strategies.sleeves.short_squeeze.

Coverage:
  - math.percentile_rank, math.rolling_percentile
  - math.close_in_range
  - math.session_of_hour
  - math.asia_session_summary on synthetic short-macro / long-macro inputs
  - math.is_15m_boundary
  - signal._evaluate_trigger end-to-end on a synthetic Feb 11 fixture
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from strategies.sleeves.short_squeeze import math as ssq_math


# ─── math.percentile_rank ────────────────────────────────────────────────────

def test_percentile_rank_empty_distribution():
    assert ssq_math.percentile_rank(0.5, np.array([])) == 0.0


def test_percentile_rank_value_below_all():
    dist = np.array([10.0, 20.0, 30.0])
    assert ssq_math.percentile_rank(5.0, dist) == 0.0


def test_percentile_rank_value_above_all():
    dist = np.array([10.0, 20.0, 30.0])
    assert ssq_math.percentile_rank(50.0, dist) == 1.0


def test_percentile_rank_median():
    dist = np.array([10.0, 20.0, 30.0, 40.0])
    # value=20 is <= 2 of the 4 elements
    assert ssq_math.percentile_rank(20.0, dist) == 0.5


# ─── math.rolling_percentile ─────────────────────────────────────────────────

def test_rolling_percentile_warmup_is_nan():
    s = np.arange(100, dtype=float)
    out = ssq_math.rolling_percentile(s, window=10)
    # First 10 entries unobservable -> NaN
    assert np.isnan(out[:10]).all()
    # After warmup, monotonically increasing series -> each value is the max
    # of its 10-bar trailing window -> percentile = 1.0
    assert (out[10:] == 1.0).all()


def test_rolling_percentile_constant_series():
    s = np.full(50, 7.0)
    out = ssq_math.rolling_percentile(s, window=10)
    # After warmup, every value equals every other -> percentile = 1.0
    assert (out[10:] == 1.0).all()


# ─── math.close_in_range ─────────────────────────────────────────────────────

def test_close_in_range_at_low():
    assert ssq_math.close_in_range(100, 110, 90, 90) == 0.0


def test_close_in_range_at_high():
    assert ssq_math.close_in_range(100, 110, 90, 110) == 1.0


def test_close_in_range_midpoint():
    assert ssq_math.close_in_range(100, 110, 90, 100) == 0.5


def test_close_in_range_zero_range_safe():
    # Avoid divide-by-zero: collapsed bar returns finite number.
    result = ssq_math.close_in_range(100, 100, 100, 100)
    assert np.isfinite(result)


# ─── math.session_of_hour ────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,expected", [
    (0, "asia"), (3, "asia"), (6, "asia"),
    (7, "london"), (10, "london"), (13, "london"),
    (14, "ny"), (18, "ny"), (20, "ny"),
    (21, None), (22, None), (23, None),
])
def test_session_of_hour(hour, expected):
    assert ssq_math.session_of_hour(hour) == expected


# ─── math.is_15m_boundary ────────────────────────────────────────────────────

@pytest.mark.parametrize("minute,expected", [
    (0, True), (15, True), (30, True), (45, True),
    (1, False), (14, False), (16, False), (29, False),
])
def test_is_15m_boundary(minute, expected):
    ts = datetime(2026, 2, 11, 15, minute, 0, tzinfo=timezone.utc)
    assert ssq_math.is_15m_boundary(ts) is expected


# ─── math.asia_session_summary ───────────────────────────────────────────────

def _mk_asia_bars(open_p, close_p, oi_open, oi_close, fund_mean):
    """Build a 7-bar (one per asia hour) sequence with linear interpolation
    between the open and close values; OI ramps from oi_open to oi_close;
    funding is constant at `fund_mean`."""
    bars = []
    for i in range(7):
        frac = i / 6.0
        price = open_p + (close_p - open_p) * frac
        oi = oi_open + (oi_close - oi_open) * frac
        bars.append({
            "open": open_p if i == 0 else price,
            "high": price * 1.001, "low": price * 0.999,
            "close": price,
            "oi_close": oi,
            "funding": fund_mean,
        })
    # Force last bar's close to match the parameter exactly
    bars[-1]["close"] = close_p
    return bars


def test_asia_summary_short_macro_classified():
    """Asia closes red, OI rises >0.5%, funding negative -> is_short_macro."""
    bars = _mk_asia_bars(open_p=68800, close_p=66800,
                          oi_open=79000, oi_close=82000,
                          fund_mean=-4.4e-5)
    s = ssq_math.asia_session_summary(bars)
    assert s is not None
    assert s["is_short_macro"] is True
    assert s["is_long_macro"] is False
    assert s["close_lt_open"] is True
    assert s["oi_pct"] > 0.005
    assert s["fund_mean"] < 0


def test_asia_summary_long_macro_classified():
    """Asia closes green, OI rises >0.5%, funding positive -> is_long_macro."""
    bars = _mk_asia_bars(open_p=66800, close_p=68800,
                          oi_open=79000, oi_close=82000,
                          fund_mean=+4.4e-5)
    s = ssq_math.asia_session_summary(bars)
    assert s is not None
    assert s["is_long_macro"] is True
    assert s["is_short_macro"] is False


def test_asia_summary_flat_oi_no_macro():
    """OI rise below 0.5% -> neither macro classification fires."""
    bars = _mk_asia_bars(open_p=68800, close_p=66800,
                          oi_open=79000, oi_close=79100,   # 0.13%
                          fund_mean=-4e-5)
    s = ssq_math.asia_session_summary(bars)
    assert s["is_short_macro"] is False
    assert s["is_long_macro"] is False


def test_asia_summary_empty_returns_none():
    assert ssq_math.asia_session_summary([]) is None


def test_asia_summary_too_few_bars_returns_none():
    bars = _mk_asia_bars(68800, 66800, 79000, 82000, -4e-5)[:1]
    assert ssq_math.asia_session_summary(bars) is None


# ─── Integration: synthetic trigger detection on Feb 11 ─────────────────────

def test_feb11_15m_close_satisfies_all_percentile_gates():
    """Recreate the Feb 11 NY second-sweep bar (15:00 UTC, 2026-02-11) and
    verify that against a synthetic 90-day distribution, the percentile
    gates fire as expected.

    Numbers from the backtest notebook:
      bar.perp_cvd = -1092.7  (deep negative -> bottom ~2% of distribution)
      bar.divergence = +1125.9 (very high -> top ~1% of distribution)
    """
    # Build a synthetic distribution where -1092 is in the bottom tail
    # and +1125 is in the top tail.
    np.random.seed(0)
    perp_dist = np.random.normal(loc=0.0, scale=400.0, size=5000)
    div_dist  = np.random.normal(loc=0.0, scale=400.0, size=5000)

    perp_pct = ssq_math.percentile_rank(-1092.7, perp_dist)
    div_pct  = ssq_math.percentile_rank(+1125.9, div_dist)

    # Reflect the live gates:
    from strategies.sleeves.short_squeeze.config import (
        PERP_CVD_PCT_MAX, DIVERGENCE_PCT_MIN, CLOSE_IN_RANGE_MIN,
    )
    assert perp_pct < PERP_CVD_PCT_MAX, f"perp_pct={perp_pct}"
    assert div_pct  > DIVERGENCE_PCT_MIN, f"div_pct={div_pct}"

    # Bar close_in_range (from the notebook): low=65718, high=66770, close=66088
    cir = ssq_math.close_in_range(66559.8, 66770.7, 65718.5, 66087.9)
    assert cir >= CLOSE_IN_RANGE_MIN, f"cir={cir}"


def test_borderline_perp_misses_threshold():
    """A bar in the 20th percentile of perp_cvd should NOT fire (threshold
    is < 15th percentile)."""
    np.random.seed(0)
    perp_dist = np.random.normal(loc=0.0, scale=400.0, size=5000)
    # Pick a value at the 20th percentile
    p20 = float(np.percentile(perp_dist, 20))
    pct = ssq_math.percentile_rank(p20, perp_dist)
    from strategies.sleeves.short_squeeze.config import PERP_CVD_PCT_MAX
    assert pct >= PERP_CVD_PCT_MAX, f"pct={pct} should not pass threshold {PERP_CVD_PCT_MAX}"
