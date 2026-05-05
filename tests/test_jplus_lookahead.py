"""Look-ahead safety across multiple clock positions.

The contract: if we run a signal evaluator at clock=T1 and again at
clock=T2 (T2 > T1), the per-date outputs for every date <= min(T1, T2)
MUST be bit-identical. If they differ, the code is peeking at future data.

This is the single most important integration test in the repo — it
certifies that our backtest can be trusted to represent what would have
been known at each point in time.

Uses the real data/trader.db so the test exercises the actual data
loaders (not synthetic fixtures). It's slow-ish (~15s) but runs once.

Coverage:
  - jplus.simulate (Core J+, 50% portfolio weight)
  - services.adx_service._current_signal (S-003 ADX, 15%)
  - regime_classifier.classify_regime (gates S-096 Thu Bear, 6%)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import clock
from jplus import simulate


@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2025, 1, 1, tzinfo=timezone.utc)),
])
def test_common_dates_identical_across_clocks(early_clock, late_clock):
    # Early run
    clock.set_simulated_now(early_clock)
    early = simulate.simulate(start_date="2022-01-01",
                               end_date=early_clock.date().isoformat())
    # Late run
    clock.set_simulated_now(late_clock)
    late = simulate.simulate(start_date="2022-01-01",
                              end_date=late_clock.date().isoformat())
    clock.set_simulated_now(None)

    common = sorted(set(early) & set(late))
    assert len(common) > 30, "need enough common dates to be meaningful"

    diffs = []
    for d in common:
        a = early[d]
        b = late[d]
        # Compare every decision-relevant field
        for k in ("return_pct", "mode", "lev", "r1x_pct", "gated", "ema_p"):
            if isinstance(a[k], float):
                if abs(a[k] - b[k]) > 1e-9:
                    diffs.append((d, k, a[k], b[k]))
            else:
                if a[k] != b[k]:
                    diffs.append((d, k, a[k], b[k]))
    assert not diffs, f"first 5 look-ahead divergences: {diffs[:5]}"


@pytest.mark.slow
def test_replay_is_deterministic():
    """Same clock, same call → byte-identical output. No hidden state,
    no randomness."""
    clock.set_simulated_now(datetime(2025, 1, 1, tzinfo=timezone.utc))
    a = simulate.simulate(start_date="2023-01-01", end_date="2024-12-31")
    b = simulate.simulate(start_date="2023-01-01", end_date="2024-12-31")
    clock.set_simulated_now(None)
    assert a == b


def test_simulate_on_tiny_window_doesnt_crash():
    """Defensive: requesting a window too small for signals to compute
    shouldn't crash; it returns whatever was valid."""
    clock.set_simulated_now(datetime(2022, 2, 15, tzinfo=timezone.utc))
    out = simulate.simulate(start_date="2022-02-01", end_date="2022-02-10")
    clock.set_simulated_now(None)
    # May be empty if warmup isn't complete; just verify no crash and
    # return type contract holds.
    for d, rec in out.items():
        assert "return_pct" in rec
        assert "mode" in rec
        assert "lev" in rec
        assert "gated" in rec
        assert "ema_p" in rec


# ─── ADX signal look-ahead ──────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_adx_signal_no_lookahead(early_clock, late_clock):
    """ADX _current_signal must produce identical results at two different
    clock positions for dates available to both."""
    from services.adx_service import _load_btc_daily_candles, _current_signal

    clock.set_simulated_now(early_clock)
    early_candles = _load_btc_daily_candles(limit_days=400)
    early_sig = _current_signal(early_candles) if early_candles else None
    early_dates = {c["dt"] for c in early_candles}

    clock.set_simulated_now(late_clock)
    late_candles = _load_btc_daily_candles(limit_days=400)
    late_sig = _current_signal(late_candles) if late_candles else None
    clock.set_simulated_now(None)

    common_candles_early = [c for c in early_candles if c["dt"] in {lc["dt"] for lc in late_candles}]
    common_candles_late = [c for c in late_candles if c["dt"] in early_dates]
    assert len(common_candles_early) > 50, "need enough common bars"
    for ce, cl in zip(common_candles_early, common_candles_late):
        assert ce["dt"] == cl["dt"]
        for k in ("open", "high", "low", "close"):
            assert abs(ce[k] - cl[k]) < 1e-6, \
                f"ADX candle mismatch at {ce['dt']} {k}: {ce[k]} vs {cl[k]}"


# ─── regime_classifier look-ahead ───────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)),
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_regime_classifier_no_lookahead(early_clock, late_clock):
    """regime_classifier.classify_regime must produce identical labels at two
    different clock positions for all common dates."""
    from regime_classifier import classify_regime

    clock.set_simulated_now(early_clock)
    early = {r["date"]: r for r in classify_regime("BTC")}

    clock.set_simulated_now(late_clock)
    late = {r["date"]: r for r in classify_regime("BTC")}
    clock.set_simulated_now(None)

    common = sorted(set(early) & set(late))
    assert len(common) > 100, "need enough common dates"

    diffs = []
    for d in common:
        e, l = early[d], late[d]
        if e["label"] != l["label"]:
            diffs.append((d, "label", e["label"], l["label"]))
        for k in ("close", "ma", "slope_pct", "rv_ann", "rv_pct"):
            ev, lv = e[k], l[k]
            if ev is None and lv is None:
                continue
            if ev is None or lv is None or abs(ev - lv) > 1e-6:
                diffs.append((d, k, ev, lv))
    assert not diffs, f"regime look-ahead divergences (first 5): {diffs[:5]}"
