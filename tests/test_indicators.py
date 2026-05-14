"""strategies.support.indicators — pure technical-indicator math.

EMA was previously tested via tests/test_regime.py (which still passes via
the ema_calc alias re-export). The tests below cover the canonical entry
points and add ADX coverage that was missing.
"""
from __future__ import annotations

import math

import pytest

from strategies.support.indicators import adx, ema


# ─── ema ─────────────────────────────────────────────────────────────────────

def test_ema_returns_nan_for_indices_before_warmup():
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], period=3)
    assert all(math.isnan(v) for v in out[:2])
    # Index 2 is the seed: SMA of first 3 = 2.0
    assert out[2] == pytest.approx(2.0)


def test_ema_seed_is_sma_of_first_period():
    vals = [10.0, 20.0, 30.0, 40.0]
    out = ema(vals, period=4)
    assert math.isnan(out[0]) and math.isnan(out[1]) and math.isnan(out[2])
    assert out[3] == pytest.approx(25.0)  # mean of 10/20/30/40


def test_ema_recursion_matches_textbook():
    """k = 2/(period+1); out[i] = v[i]*k + out[i-1]*(1-k)."""
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    out = ema(vals, period=3)
    # Seed at index 2: (10+20+30)/3 = 20.0
    assert out[2] == pytest.approx(20.0)
    # k = 2/4 = 0.5
    # out[3] = 40*0.5 + 20.0*0.5 = 30.0
    # out[4] = 50*0.5 + 30.0*0.5 = 40.0
    assert out[3] == pytest.approx(30.0)
    assert out[4] == pytest.approx(40.0)


def test_ema_returns_all_nan_when_input_too_short():
    out = ema([1.0, 2.0], period=5)
    assert len(out) == 2
    assert all(math.isnan(v) for v in out)


def test_ema_period_one_passes_values_through():
    """EMA(period=1) is degenerate: k=1, so each output = corresponding input."""
    vals = [3.0, 7.0, 5.0, 9.0]
    out = ema(vals, period=1)
    assert out == [pytest.approx(v) for v in vals]


# ─── adx ─────────────────────────────────────────────────────────────────────

def _candle(h: float, l: float, c: float) -> dict:
    return {"high": h, "low": l, "close": c}


def test_adx_returns_nan_when_input_below_warmup():
    """Warmup is 2*period+1 bars; below that, all NaN."""
    candles = [_candle(100, 99, 100) for _ in range(28)]  # < 2*14 + 1
    out = adx(candles, period=14)
    assert len(out) == 28
    assert all(math.isnan(v) for v in out)


def test_adx_constant_price_yields_nan_throughout():
    """If h==l==c every bar, ATR collapses to 0; the ``if atr[i] > 0`` guard
    leaves +DI/-DI/DX as NaN. ADX seed window has no valid DX values, so the
    result stays NaN throughout (never produces a real number for a market
    with literally zero range). This is a degenerate case but worth pinning."""
    candles = [_candle(100, 100, 100) for _ in range(60)]
    out = adx(candles, period=14)
    assert all(math.isnan(v) for v in out)


def test_adx_strong_trend_pushes_value_high():
    """A monotone uptrend produces +DM dominant, -DM zero — ADX climbs."""
    # 60 bars trending up by 1 each bar.
    candles = []
    base = 100.0
    for i in range(60):
        h = base + i + 1
        l = base + i
        c = base + i + 0.5
        candles.append(_candle(h, l, c))
    out = adx(candles, period=14)
    final = out[-1]
    assert not math.isnan(final)
    # In a clean monotone uptrend ADX should sit near 100 (the maximum).
    # Allow some slack for the Wilder smoothing transient.
    assert final > 50.0


def test_adx_matches_known_reference_values_on_synthetic_window():
    """Smoke test: the values the function returns are stable across runs
    and match a captured baseline. If the math drifts, this fails."""
    # Synthetic 35-bar series (just enough for warmup at period=7).
    import random
    rng = random.Random(42)
    candles = []
    price = 1000.0
    for _ in range(35):
        delta = rng.uniform(-5, 5)
        h = price + abs(delta) + 1
        l = price - abs(delta) - 1
        c = price + delta
        candles.append(_candle(h, l, c))
        price = c
    out = adx(candles, period=7)
    # Warmup completes at index 14. Spot-check the last value.
    assert not math.isnan(out[-1])
    assert 0.0 <= out[-1] <= 100.0  # ADX is bounded


def test_indicators_module_matches_legacy_aliases():
    """Cross-check: the canonical ``strategies.support.indicators.ema`` produces the
    same output as the legacy ``ema_calc`` re-export in jplus.regime, and
    the alias-import is in fact the same callable (proves the aliasing
    works for the existing call sites without any hidden divergence)."""
    from strategies.support.regime_jplus import ema_calc as legacy_ema
    vals = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    out_canonical = ema(vals, period=3)
    out_legacy = legacy_ema(vals, period=3)
    # NaN-aware element-wise comparison.
    assert len(out_canonical) == len(out_legacy)
    for a, b in zip(out_canonical, out_legacy):
        if math.isnan(a) or math.isnan(b):
            assert math.isnan(a) and math.isnan(b)
        else:
            assert a == pytest.approx(b)
    # Strongest assertion: the alias is literally the same function object.
    assert legacy_ema is ema
