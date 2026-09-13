"""Look-ahead safety for the ARCHIVED sleeves, across multiple clock positions.

The contract: if we run a signal evaluator at clock=T1 and again at clock=T2
(T2 > T1), the per-date outputs for every date <= min(T1, T2) MUST be
bit-identical. If they differ, the code is peeking at future data.

These four arms lived in tests/test_jplus_lookahead.py until 2026-09-13, when
CPR, PDO, THU_BEAR and FOMC were archived. They were relocated rather than
deleted because three research questions were open on that date — the CPR
TradingView re-validation, the PDO re-check and the THU_BEAR out-of-sample
test — and none of those numbers mean anything until the sleeve is shown to be
clock-safe.

Not collected by a bare `pytest` (pytest.ini: testpaths = tests). Run:

    venv/Scripts/python.exe -m pytest studies/material/archive/tests

Uses the real data/trader.db, so it exercises the actual loaders.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategies.support import clock


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
    from studies.material.archive.cpr.signal import _daily_closes_cache, _load_daily_closes

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
    from studies.material.archive.pdo.signal import _btc_30d_return_pct

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
    from studies.material.archive.thu_bear import signal as tb
    from studies.material.archive.thu_bear.signal import _get_regime_for_prev_day

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
    from studies.material.archive.fomc.signal import evaluate

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
