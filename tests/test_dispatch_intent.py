"""P2.4e/f Stage 2 scaffold — Intent dataclass shape."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from strategies.support.dispatch import Intent


def test_intent_minimal_construction():
    """Asset / direction / allocation_pct are required positional;
    everything else has a sensible default."""
    i = Intent(asset="BTC", direction="LONG", allocation_pct=15.0)
    assert i.asset == "BTC"
    assert i.direction == "LONG"
    assert i.allocation_pct == 15.0
    assert i.leverage == 1.0
    assert i.conviction == 100
    assert i.priority == 100.0
    assert i.reason is None
    assert i.scheduled_exit_dt is None


def test_intent_full_construction():
    exit_dt = datetime(2026, 5, 16, 18, 0, tzinfo=timezone.utc)
    i = Intent(
        asset="ETH", direction="SHORT", allocation_pct=3.0, leverage=5.0,
        conviction=85, priority=50.0,
        reason={"trigger": "S-096_v4", "regime_prev_day": "bear_trend"},
        scheduled_exit_dt=exit_dt,
    )
    assert i.leverage == 5.0
    assert i.conviction == 85
    assert i.priority == 50.0
    assert i.reason["trigger"] == "S-096_v4"
    assert i.scheduled_exit_dt == exit_dt


def test_intent_is_frozen():
    """Reconcile pass needs to safely cache + reorder intents."""
    i = Intent(asset="BTC", direction="LONG", allocation_pct=15.0)
    with pytest.raises(Exception):  # FrozenInstanceError
        i.allocation_pct = 20.0  # type: ignore[misc]
