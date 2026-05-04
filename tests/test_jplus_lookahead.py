"""jplus.simulate — look-ahead safety across multiple clock positions.

The contract: if we run the simulator at clock=T1 and again at clock=T2
(T2 > T1), the per-date outputs for every date ≤ min(T1, T2) MUST be
bit-identical. If they differ, the simulator is peeking at future data.

This is the single most important integration test in the repo — it
certifies that our backtest can be trusted to represent what would have
been known at each point in time.

Uses the real data/trader.db so the test exercises the actual data
loaders (not synthetic fixtures). It's slow-ish (~15s) but runs once.
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
