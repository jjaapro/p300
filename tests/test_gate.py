"""jplus.gate — rule-based R4 de-lever gate (T-1 vol-percentile).

Properties under test:
  - Returns bool per date
  - Fires when 30d vol as of T-1 is at/above 75th pct of trailing 365d
  - Uses ONLY data through T-1 (no look-ahead) — verified by running on
    a truncated series and comparing against the same dates in the full run
  - Gracefully returns False on warmup
"""
from __future__ import annotations

import math

from strategies.support import gate


def _mk_dates(n: int, start="2020-01-01") -> list[str]:
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start)
    return [(d0 + timedelta(days=i)).date().isoformat() for i in range(n)]


def test_gate_warmup_returns_false():
    # Fewer than VOL_WINDOW (30) days → gate can't compute vol yet.
    dates = _mk_dates(20)
    bc = [100.0 + i * 0.1 for i in range(20)]
    g = gate.compute_gate_map(dates, bc)
    assert len(g) == 20
    for d in dates:
        assert g[d] is False


def test_gate_fires_on_high_vol_spike():
    # Build 400 days: first 390 calm, last 10 volatile.
    # Last 10 days' 30d vol >> prior 365d → should fire True.
    dates = _mk_dates(400)
    bc = [100.0 + (i * 0.01) for i in range(390)]  # calm ramp
    for i in range(390, 400):
        # alternate ±3% daily — very high vol
        bc.append(bc[-1] * (1.03 if i % 2 == 0 else 0.97))
    g = gate.compute_gate_map(dates, bc)
    # Check the last few days — vol at T-1 should be elevated
    last_5_fires = sum(1 for d in dates[-5:] if g[d])
    assert last_5_fires >= 3, f"expected ≥3 of last 5 days to fire, got {last_5_fires}"


def test_gate_t_minus_one_not_t():
    """Gate for date T uses vol AT T-1, not vol AT T. Put a vol spike at
    day T-1, see that the gate fires at day T (not T-1)."""
    dates = _mk_dates(400)
    bc = [100.0 + i * 0.01 for i in range(400)]  # calm throughout
    # Inject a vol bump in the last available 30d prior to day 399
    # to push 30d vol high at T-1=398.
    for i in range(370, 399):
        bc[i] = bc[i - 1] * (1.05 if i % 2 == 0 else 0.95)
    # Now bc[:399] has elevated vol. The gate at date 399 should fire.
    g = gate.compute_gate_map(dates, bc)
    # Date index 399 is the last date:
    assert g[dates[-1]] is True


def test_gate_is_deterministic():
    dates = _mk_dates(400)
    bc = [100.0 + math.sin(i * 0.1) * 5 + i * 0.02 for i in range(400)]
    a = gate.compute_gate_map(dates, bc)
    b = gate.compute_gate_map(dates, bc)
    assert a == b


def test_gate_truncated_matches_full_run_on_common_dates():
    """Look-ahead safety: gate(bc[:N]) == gate(bc[:N+extra])[:N] for every
    common date. If the gate peeks at future data, truncating would
    change earlier dates' outputs."""
    dates = _mk_dates(500)
    bc = [100.0 + math.sin(i * 0.1) * 5 + i * 0.02 for i in range(500)]

    # Full run
    full = gate.compute_gate_map(dates, bc)
    # Truncated run (first 300 days only)
    trunc_dates = dates[:300]
    trunc_bc = bc[:300]
    trunc = gate.compute_gate_map(trunc_dates, trunc_bc)
    # Every date in trunc must match full
    for d in trunc_dates:
        assert trunc[d] == full[d], f"mismatch at {d}: trunc={trunc[d]} full={full[d]}"


def test_gate_fire_rate_approximately_twentyfive_percent():
    """Over a sufficiently long stationary series, vol will be in top 25%
    roughly 25% of days by construction. Verify the rule behaves as
    designed on a stationary process."""
    import random
    rng = random.Random(42)
    n = 1500
    dates = _mk_dates(n)
    bc = [100.0]
    for _ in range(1, n):
        bc.append(bc[-1] * (1 + rng.gauss(0, 0.02)))  # daily σ ≈ 2%
    g = gate.compute_gate_map(dates, bc)
    # Exclude warmup (first VOL_RANK_WINDOW dates have small history)
    post_warmup = [d for d in dates[gate.VOL_RANK_WINDOW:] if d in g]
    fire_rate = sum(1 for d in post_warmup if g[d]) / len(post_warmup)
    # Allow 20-35% window for the stochastic fixture.
    assert 0.18 < fire_rate < 0.35, f"fire rate out of range: {fire_rate:.2%}"
