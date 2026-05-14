"""strategies.support.sleeves — small runtime helpers shared across sleeve dispatchers."""
from __future__ import annotations

import pytest

from strategies.support.sleeves import is_sl_hit, live_pnl_pct


# ─── live_pnl_pct ────────────────────────────────────────────────────────────

def test_long_pnl_positive_when_price_rises():
    assert live_pnl_pct("LONG", 100.0, 110.0) == pytest.approx(10.0)


def test_long_pnl_negative_when_price_falls():
    assert live_pnl_pct("LONG", 100.0, 90.0) == pytest.approx(-10.0)


def test_short_pnl_positive_when_price_falls():
    assert live_pnl_pct("SHORT", 100.0, 90.0) == pytest.approx(10.0)


def test_short_pnl_negative_when_price_rises():
    assert live_pnl_pct("SHORT", 100.0, 110.0) == pytest.approx(-10.0)


def test_direction_case_insensitive():
    assert live_pnl_pct("long", 100.0, 110.0) == pytest.approx(10.0)
    assert live_pnl_pct("Short", 100.0, 90.0) == pytest.approx(10.0)


# ─── is_sl_hit ───────────────────────────────────────────────────────────────

def test_sl_not_hit_when_pnl_above_minus_threshold():
    """SHORT, +1% PnL, 5% SL threshold → not hit."""
    hit, pnl = is_sl_hit("SHORT", 100.0, 99.0, sl_threshold_pct=5.0)
    assert not hit
    assert pnl == pytest.approx(1.0)


def test_sl_hit_exactly_at_threshold():
    """LONG, -5% PnL, 5% SL threshold → hit (boundary case)."""
    hit, pnl = is_sl_hit("LONG", 100.0, 95.0, sl_threshold_pct=5.0)
    assert hit
    assert pnl == pytest.approx(-5.0)


def test_sl_hit_well_past_threshold():
    """SHORT, +10% adverse (price went up 10%) → hit at 5% SL."""
    hit, pnl = is_sl_hit("SHORT", 100.0, 110.0, sl_threshold_pct=5.0)
    assert hit
    assert pnl == pytest.approx(-10.0)


def test_sl_threshold_zero_disables_via_zero_pnl_only_match():
    """When SL threshold is 0, only an exact-zero PnL or worse triggers.
    Documents the math; in practice callers should treat sl=0 as "disabled"
    upstream rather than relying on this edge case."""
    hit_at_zero, _ = is_sl_hit("LONG", 100.0, 100.0, sl_threshold_pct=0.0)
    assert hit_at_zero  # 0 <= -0 is True (boundary)
    hit_above, _ = is_sl_hit("LONG", 100.0, 100.01, sl_threshold_pct=0.0)
    assert not hit_above
