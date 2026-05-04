"""jplus.voltarget — per-day leverage clamp from 30d realized vol.

Properties under test:
  - Warmup (< vol_window returns) → min(1.0, cap)
  - Leverage ∈ [LEV_FLOOR=0.5, H_CAPS[mode]]
  - With realized vol = target_vol → leverage ≈ 1.0 (within cap)
  - With realized vol >> target_vol → floor (0.5)
  - With realized vol → 0 (calm) → cap
  - Regime cap is monotone: strong_bull > mild_bull > uncertain > bear
"""
from __future__ import annotations

import math

import pytest

from jplus import voltarget


def test_warmup_returns_min_one_cap():
    # Fewer than vol_window returns → min(1.0, cap).
    short = [0.5, -0.3, 0.2]
    assert voltarget.leverage_for_day(short, "strong_bull") == 1.0
    assert voltarget.leverage_for_day(short, "bear") == 1.0  # cap=1.5, min(1.0,1.5)
    # No history at all
    assert voltarget.leverage_for_day([], "strong_bull") == 1.0


def test_h_caps_are_monotone_descending():
    assert voltarget.H_CAPS["strong_bull"] > voltarget.H_CAPS["mild_bull"]
    assert voltarget.H_CAPS["mild_bull"] > voltarget.H_CAPS["uncertain"]
    assert voltarget.H_CAPS["uncertain"] > voltarget.H_CAPS["bear"]


def test_zero_realized_vol_returns_cap():
    # 30 zero returns → vol=0 → defensive branch returns cap.
    zeros = [0.0] * 30
    assert voltarget.leverage_for_day(zeros, "strong_bull") == 3.0
    assert voltarget.leverage_for_day(zeros, "bear") == 1.5


def test_realized_vol_matches_target_returns_approx_one():
    # Target 50% annualized → daily σ ≈ 50/sqrt(365) ≈ 2.617 %
    # Build returns with daily σ ≈ 2.617
    # Alternating +/- around zero with that magnitude.
    daily_sigma = 50.0 / math.sqrt(365)  # in percent
    returns = []
    for i in range(30):
        returns.append(+daily_sigma if i % 2 == 0 else -daily_sigma)
    # With this construction the sample std (n-1) is exactly daily_sigma × sqrt(n/(n-1))
    # ≈ 2.66%. Annualized ≈ 50.9%. Leverage ≈ 50/50.9 ≈ 0.98.
    lev = voltarget.leverage_for_day(returns, "strong_bull")
    assert 0.9 < lev < 1.1, f"expected ≈1.0, got {lev}"


def test_high_vol_floors_at_lev_floor():
    # Huge returns → very high vol → leverage floored at 0.5
    big = [20.0 * (1 if i % 2 == 0 else -1) for i in range(30)]  # ~20% daily
    assert voltarget.leverage_for_day(big, "strong_bull") == pytest.approx(voltarget.LEV_FLOOR)


def test_low_vol_capped_at_regime_cap():
    # Tiny returns → low vol → leverage → cap
    tiny = [0.001 * (1 if i % 2 == 0 else -1) for i in range(30)]  # ~0.001% daily
    assert voltarget.leverage_for_day(tiny, "mild_bull") == voltarget.H_CAPS["mild_bull"]
    assert voltarget.leverage_for_day(tiny, "bear") == voltarget.H_CAPS["bear"]


def test_unknown_mode_defaults_to_uncertain_cap():
    """Defensive: unexpected mode label should NOT blow up; use default cap."""
    zeros = [0.0] * 30
    lev = voltarget.leverage_for_day(zeros, "garbage_mode")
    # Default path uses H_CAPS.get(mode, 2.0) → 2.0
    assert lev == 2.0
