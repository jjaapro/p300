"""jplus.regime — 4-state classifier + LS circuit breaker + peak-DD override.

Tests construct synthetic BC and LS series to exercise each branch.
"""
from __future__ import annotations

import math

import pytest

from strategies.support import regime_jplus as regime


def _mk_dates(n: int, start="2020-01-01") -> list[str]:
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start)
    return [(d0 + timedelta(days=i)).date().isoformat() for i in range(n)]


# ─── ema_calc ───────────────────────────────────────────────────────────────

def test_ema_shorter_than_period_is_all_nan():
    out = regime.ema_calc([1.0, 2.0], 10)
    assert all(math.isnan(x) for x in out)


def test_ema_seed_is_sma_of_first_period():
    vals = [10.0, 12.0, 14.0, 16.0, 18.0]
    out = regime.ema_calc(vals, 5)
    assert math.isnan(out[0])
    assert math.isnan(out[3])
    assert out[4] == pytest.approx(sum(vals) / 5)


def test_ema_recursion_matches_reference():
    vals = [100.0] * 20 + [110.0] * 20  # step
    out = regime.ema_calc(vals, 5)
    # Spot-check: after a sustained move, EMA approaches new level.
    assert out[-1] > 105.0
    assert out[-1] < 110.01
    # Monotone in the step-up region.
    for i in range(25, 40):
        assert out[i] >= out[i - 1]


# ─── classify_series: branch coverage ───────────────────────────────────────

def test_insufficient_history_returns_uncertain():
    # Warmup uses det_i < 50 → must be uncertain.
    dates = _mk_dates(10)
    bc = [100.0] * 10
    modes = regime.classify_series(dates, bc, {})
    for d in dates[1:10]:
        assert modes[d] == "uncertain"


def test_strong_bull_requires_all_four_conditions():
    """close > EMA50 AND close > EMA20 AND m30 > 0 AND m7 > 0."""
    dates = _mk_dates(120)
    # Ramp up steadily — should trigger strong_bull
    bc = [100.0 * (1 + 0.001 * i) for i in range(120)]
    modes = regime.classify_series(dates, bc, {})
    # After warmup + 30d momentum, should see strong_bull
    late = modes[dates[-1]]
    assert late == "strong_bull", f"steady climb should yield strong_bull, got {late}"


def test_bear_requires_below_ema50_and_negative_m30():
    dates = _mk_dates(120)
    # Climb then collapse
    bc = [100.0 + i * 0.5 for i in range(60)] + [130.0 - (i - 60) * 0.4 for i in range(60, 120)]
    modes = regime.classify_series(dates, bc, {})
    # Find the transition
    bear_days = sum(1 for m in modes.values() if m == "bear")
    assert bear_days > 5, f"expected some bear days in a crash, got {bear_days}"


def test_peak_dd_override_demotes_bullish_to_uncertain():
    # Construct: climb 50d, then pull back 6% over 3d. The peak DD > 5%
    # override should demote strong_bull/mild_bull to uncertain.
    dates = _mk_dates(80)
    bc = [100.0 + i * 0.5 for i in range(60)]  # 100 → 130
    bc.append(bc[-1] * 0.99)
    bc.append(bc[-1] * 0.97)
    bc.append(bc[-1] * 0.97)
    # pad to 80
    while len(bc) < 80:
        bc.append(bc[-1])
    modes = regime.classify_series(dates, bc, {})
    # Last day should be uncertain (6-7% off peak in a bullish setup)
    assert modes[dates[-1]] == "uncertain"


def test_ls_circuit_breaker_forces_uncertain_for_seven_days():
    """A LS shift < -15 over 7 days forces uncertain for 7 calendar days."""
    dates = _mk_dates(120)
    bc = [100.0 * (1 + 0.001 * i) for i in range(120)]  # would be strong_bull
    ls_d: dict[str, float] = {}
    # Populate LS_d with a steep drop centered on day 70
    for i, d in enumerate(dates):
        if i < 60:
            ls_d[d] = 55.0
        elif 60 <= i < 63:
            ls_d[d] = 55.0
        else:
            ls_d[d] = 30.0  # drop of 25 pts — shift < -15
    modes = regime.classify_series(dates, bc, ls_d)
    # Days 63..69 should be uncertain due to CB (the classifier uses T-1
    # data, so CB kicks in at day 63+).
    cb_active_days = [d for d in dates[65:75] if modes[d] == "uncertain"]
    assert len(cb_active_days) >= 5, (
        f"CB should force uncertain, got modes near day 70: "
        f"{[(d, modes[d]) for d in dates[65:75]]}"
    )


def test_stable_regime_is_deterministic():
    """Same inputs → same outputs. Verifies pure-function contract."""
    dates = _mk_dates(80)
    bc = [100.0 + i * 0.5 for i in range(80)]
    ls = {d: 50.0 + (i % 5) for i, d in enumerate(dates)}
    a = regime.classify_series(dates, bc, ls)
    b = regime.classify_series(dates, bc, ls)
    assert a == b
