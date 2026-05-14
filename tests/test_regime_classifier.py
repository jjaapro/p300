"""regime_classifier — standalone BTC 4-label classifier for Thu Bear gate.

Tests cover:
  - _sma helper
  - _rolling_rv_annualized helper
  - _rolling_pct_rank helper
  - classify_regime label assignment logic (bull_trend, bear_trend, chop, sell_off)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from strategies.support.regime_tactical import (
    _sma,
    _rolling_rv_annualized,
    _rolling_pct_rank,
    classify_regime,
    SLOPE_LOOKBACK_DAYS,
)


# ─── _sma ──────────────────────────────────────────────────────────────────────

def test_sma_basic():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = _sma(vals, 3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)
    assert out[4] == pytest.approx(4.0)


def test_sma_window_larger_than_input():
    out = _sma([1.0, 2.0], 5)
    assert all(v is None for v in out)


def test_sma_single_element_window():
    vals = [10.0, 20.0, 30.0]
    out = _sma(vals, 1)
    assert out == [pytest.approx(10.0), pytest.approx(20.0), pytest.approx(30.0)]


# ─── _rolling_rv_annualized ────────────────────────────────────────────────────

def test_rv_constant_returns_zero():
    log_rets = [0.0] * 50
    out = _rolling_rv_annualized(log_rets, 20)
    for v in out[20:]:
        assert v == pytest.approx(0.0)


def test_rv_positive_for_nonzero_returns():
    log_rets = [0.01 * ((-1) ** i) for i in range(50)]
    out = _rolling_rv_annualized(log_rets, 20)
    for v in out[20:]:
        assert v is not None
        assert v > 0


def test_rv_scales_with_sqrt365():
    log_rets = [0.01, -0.01] * 30
    out = _rolling_rv_annualized(log_rets, 10)
    daily_std = 0.01  # approx std of alternating +/-0.01
    expected_order = daily_std * math.sqrt(365)
    last = out[-1]
    assert last == pytest.approx(expected_order, rel=0.2)


# ─── _rolling_pct_rank ────────────────────────────────────────────────────────

def test_pct_rank_monotone_input():
    vals = list(range(1, 21))
    out = _rolling_pct_rank(vals, 10)
    # Last element of a monotone series should be rank 1.0
    assert out[-1] == pytest.approx(1.0)


def test_pct_rank_constant_input():
    vals = [5.0] * 20
    out = _rolling_pct_rank(vals, 10)
    # All values equal → rank = 10/10 = 1.0
    assert out[-1] == pytest.approx(1.0)


def test_pct_rank_minimum_is_at_bottom():
    vals = [10.0] * 19 + [1.0]
    out = _rolling_pct_rank(vals, 10)
    # Last value (1.0) is smallest in its window → rank = 1/10
    assert out[-1] == pytest.approx(0.1)


# ─── classify_regime label logic ───────────────────────────────────────────────

def _make_bars(closes: list[float], start="2022-01-01") -> list[tuple[str, dict]]:
    d0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    bars = []
    for i, c in enumerate(closes):
        dt = d0 + timedelta(days=i)
        bars.append((dt.date().isoformat(), {"open": c, "close": c, "dt": dt}))
    return bars


@patch("strategies.support.regime_tactical.load_daily")
def test_bull_trend_on_sustained_uptrend(mock_ld):
    n = 500
    closes = [100.0 * (1 + 0.002 * i) for i in range(n)]
    mock_ld.return_value = _make_bars(closes)
    result = classify_regime("BTC")
    labels = [r["label"] for r in result if r["label"] is not None]
    assert "bull_trend" in labels
    # Majority of labeled days should be bull_trend
    bull_count = sum(1 for l in labels if l == "bull_trend")
    assert bull_count > len(labels) * 0.5


@patch("strategies.support.regime_tactical.load_daily")
def test_bear_trend_on_sustained_downtrend(mock_ld):
    n = 500
    # -0.15% daily → slope exceeds 0.5% chop band within MA window
    closes = [100.0 * (1 - 0.0015 * i) for i in range(n)]
    mock_ld.return_value = _make_bars(closes)
    # vol_high_threshold > 1 makes sell_off unreachable, isolating bear_trend
    result = classify_regime("BTC", vol_high_threshold=1.1)
    labels = [r["label"] for r in result if r["label"] is not None]
    assert "bear_trend" in labels
    bear_count = sum(1 for l in labels if l == "bear_trend")
    assert bear_count > len(labels) * 0.3


@patch("strategies.support.regime_tactical.load_daily")
def test_chop_on_flat_market(mock_ld):
    n = 500
    closes = [100.0 + 0.01 * ((-1) ** i) for i in range(n)]
    mock_ld.return_value = _make_bars(closes)
    result = classify_regime("BTC")
    labels = [r["label"] for r in result if r["label"] is not None]
    assert "chop" in labels
    chop_count = sum(1 for l in labels if l == "chop")
    assert chop_count > len(labels) * 0.5


@patch("strategies.support.regime_tactical.load_daily")
def test_sell_off_on_volatile_crash(mock_ld):
    n = 500
    # Calm period then violent drop
    closes = [100.0] * 400
    for i in range(100):
        closes.append(closes[-1] * 0.97)
    mock_ld.return_value = _make_bars(closes)
    result = classify_regime("BTC", vol_high_threshold=0.75)
    labels = [r["label"] for r in result if r["label"] is not None]
    assert "sell_off" in labels


@patch("strategies.support.regime_tactical.load_daily")
def test_empty_bars_returns_empty(mock_ld):
    mock_ld.return_value = []
    result = classify_regime("BTC")
    assert result == []


@patch("strategies.support.regime_tactical.load_daily")
def test_all_labels_are_valid(mock_ld):
    n = 500
    closes = [100.0 * (1 + 0.003 * i) for i in range(250)]
    closes += [closes[-1] * (0.995 ** i) for i in range(250)]
    mock_ld.return_value = _make_bars(closes)
    result = classify_regime("BTC")
    for r in result:
        assert r["label"] is None or r["label"] in (
            "bull_trend", "bear_trend", "chop", "sell_off"
        )
