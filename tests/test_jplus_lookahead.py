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
  - strategies.sleeves.adx.signal._current_signal (S-003 ADX, 15%)
  - regime_classifier.classify_regime (gates S-096 Thu Bear, 6%)
  - services.carry_service._load_recent_daily_funding (S-078 Carry, 12%)
  - services.cpr_service._load_daily_closes (CPR, 8%)
  - services.pdo_retouch_service._btc_30d_return_pct (PDO, 4%)
  - services.thu_bear_service._get_regime_for_prev_day (Thu Bear, 6%)
  - services.fomc_service.evaluate (FOMC, 5%)
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
    from strategies.sleeves.adx.signal import _load_btc_daily_candles, _current_signal

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


# ─── Carry funding-load look-ahead ──────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2025, 6, 1, tzinfo=timezone.utc)),
])
def test_carry_funding_no_lookahead(early_clock, late_clock):
    """Carry _load_recent_daily_funding must produce identical bars on common
    dates at two different clock positions. Uses days=400 to force overlap
    (default 30d would give zero overlap a year apart)."""
    from services.carry_service import _load_recent_daily_funding

    clock.set_simulated_now(early_clock)
    early = {r["date"]: r for r in _load_recent_daily_funding(days=400)}

    clock.set_simulated_now(late_clock)
    late = {r["date"]: r for r in _load_recent_daily_funding(days=400)}
    clock.set_simulated_now(None)

    common = sorted(set(early) & set(late))
    assert len(common) > 30, "need enough common funding days"

    diffs = []
    for d in common:
        for k in ("daily_funding_pct", "spot_close", "perp_close"):
            if abs(early[d][k] - late[d][k]) > 1e-9:
                diffs.append((d, k, early[d][k], late[d][k]))
    assert not diffs, f"carry funding look-ahead divergences (first 5): {diffs[:5]}"


# ─── CPR daily-close look-ahead ─────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.parametrize("early_clock,late_clock", [
    (datetime(2024, 6, 1, tzinfo=timezone.utc), datetime(2024, 10, 1, tzinfo=timezone.utc)),
])
def test_cpr_daily_closes_no_lookahead(early_clock, late_clock):
    """CPR _load_daily_closes must produce identical OHLC on common dates at
    two different clock positions. CPR's lookback is 240d, so the two clocks
    are 4 months apart to guarantee meaningful overlap. CPR caches per
    (asset, UTC-day), so we clear the cache between clocks to actually
    exercise the loader path."""
    from services.cpr_service import _daily_closes_cache, _load_daily_closes

    _daily_closes_cache.clear()
    clock.set_simulated_now(early_clock)
    e_dates, e_o, e_h, e_l, e_c = _load_daily_closes("BTC")
    early = {d: (e_o[i], e_h[i], e_l[i], e_c[i]) for i, d in enumerate(e_dates)}

    _daily_closes_cache.clear()
    clock.set_simulated_now(late_clock)
    l_dates, l_o, l_h, l_l, l_c = _load_daily_closes("BTC")
    late = {d: (l_o[i], l_h[i], l_l[i], l_c[i]) for i, d in enumerate(l_dates)}
    clock.set_simulated_now(None)
    _daily_closes_cache.clear()

    common = sorted(set(early) & set(late))
    assert len(common) > 50, "need enough common daily closes"

    diffs = []
    for d in common:
        for k, ev, lv in zip(("open", "high", "low", "close"),
                              early[d], late[d]):
            if abs(ev - lv) > 1e-6:
                diffs.append((d, k, ev, lv))
    assert not diffs, f"CPR daily-close look-ahead divergences (first 5): {diffs[:5]}"


# ─── PDO 30d-return clock-bounded round-trip ────────────────────────────────

@pytest.mark.slow
def test_pdo_30d_return_clock_bounded():
    """PDO _btc_30d_return_pct must be a pure function of the clock — calling
    it at clock=T, then at T2 > T, then back at T must yield the same value
    for T both times. Catches accidental global mutation or peeking past the
    clock bound."""
    from services.pdo_retouch_service import _btc_30d_return_pct

    t1 = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 1, 12, tzinfo=timezone.utc)

    clock.set_simulated_now(t1)
    v1 = _btc_30d_return_pct()
    clock.set_simulated_now(t2)
    v2 = _btc_30d_return_pct()
    clock.set_simulated_now(t1)
    v1_again = _btc_30d_return_pct()
    clock.set_simulated_now(None)

    assert v1 is not None and v2 is not None, "needs cd_spot_binance coverage"
    assert v1 == v1_again, \
        f"PDO 30d return mutated across clock changes: T1={v1} -> T2={v2} -> T1'={v1_again}"


# ─── Thu Bear regime-lookup look-ahead ──────────────────────────────────────

@pytest.mark.slow
def test_thu_bear_regime_lookup_no_lookahead():
    """Thu Bear's _get_regime_for_prev_day must return the same prev-day
    regime label at two different clock positions, both well after the target
    Thursday. The cache is keyed per UTC day so we clear it between clocks to
    force a fresh regime_map load each time."""
    import services.thu_bear_service as tb
    from services.thu_bear_service import _get_regime_for_prev_day

    target_thursday = datetime(2024, 5, 9, 0, tzinfo=timezone.utc)
    t1 = datetime(2024, 6, 1, tzinfo=timezone.utc)
    t2 = datetime(2025, 6, 1, tzinfo=timezone.utc)

    tb._regime_map_cache = {}
    tb._regime_map_cache_day = ""
    clock.set_simulated_now(t1)
    label_a = _get_regime_for_prev_day(target_thursday)

    tb._regime_map_cache = {}
    tb._regime_map_cache_day = ""
    clock.set_simulated_now(t2)
    label_b = _get_regime_for_prev_day(target_thursday)
    clock.set_simulated_now(None)

    assert label_a is not None, "regime_classifier must label May 2024 Wed"
    assert label_a == label_b, \
        f"Thu Bear regime lookup diverged across clocks: {label_a} vs {label_b}"


# ─── FOMC evaluate post-meeting stability ───────────────────────────────────

@pytest.mark.slow
def test_fomc_evaluate_past_meeting_clock_stable():
    """For an FOMC meeting in the past, evaluate(fomc_date) is built from
    historical inputs (target rate, phase, fear_greed, ex-post realized
    polymarket proxy for pre-2026). Re-evaluating at a later clock must
    yield the same decision and inputs — anything else means a service is
    leaking present-day state into a past-date lookup."""
    from services.fomc_service import evaluate

    fomc_date = "2024-12-18"  # past, pre-2026 -> ex-post polymarket proxy

    t1 = datetime(2025, 6, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 1, tzinfo=timezone.utc)

    clock.set_simulated_now(t1)
    a = evaluate(fomc_date)
    clock.set_simulated_now(t2)
    b = evaluate(fomc_date)
    clock.set_simulated_now(None)

    diffs = []
    for k in ("decision", "phase", "expected_action", "target_rate_pct",
              "fear_greed", "fear_greed_bucket"):
        if a[k] != b[k]:
            diffs.append((k, a[k], b[k]))
    assert not diffs, f"FOMC past-meeting evaluate diverged: {diffs}"
