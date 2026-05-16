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


# ─── reconcile_intents ──────────────────────────────────────────────────────

from strategies.support.dispatch import reconcile_intents, ReconcileResult


def _intent(asset="BTC", direction="LONG", alloc=15.0, lev=5.0,
            conviction=100, priority=100.0) -> Intent:
    return Intent(asset=asset, direction=direction, allocation_pct=alloc,
                   leverage=lev, conviction=conviction, priority=priority)


def test_reconcile_empty_input():
    assert reconcile_intents([], 0.0, 25_000.0, 10_000.0) == []


def test_reconcile_approves_single_intent_under_cap():
    results = reconcile_intents(
        [("S-003", _intent(alloc=15.0, lev=5.0))],   # 10k × 15% × 5 = 7500
        current_gross_used_usdt=0.0,
        gross_cap_usdt=25_000.0,
        capital_usdt=10_000.0,
    )
    assert len(results) == 1
    assert results[0].status == "approved"
    assert results[0].intent is not None
    assert results[0].sleeve_id == "S-003"


def test_reconcile_priority_order_winner_first():
    """Two intents on same asset, opposing direction. Lower-priority
    one wins; the other gets rejected_directional_conflict."""
    intents = [
        ("S-003", _intent(direction="LONG", priority=100)),
        ("S-096", _intent(direction="SHORT", priority=50)),  # higher priority
    ]
    results = reconcile_intents(intents, 0.0, 25_000.0, 10_000.0)
    # Sort puts S-096 first.
    assert results[0].sleeve_id == "S-096"
    assert results[0].status == "approved"
    assert results[1].sleeve_id == "S-003"
    assert results[1].status == "rejected_directional_conflict"


def test_reconcile_conviction_tiebreaks_equal_priority():
    """Equal priority: higher conviction wins."""
    intents = [
        ("low_conv", _intent(direction="LONG", priority=100, conviction=60)),
        ("hi_conv", _intent(direction="SHORT", priority=100, conviction=90)),
    ]
    results = reconcile_intents(intents, 0.0, 25_000.0, 10_000.0)
    assert results[0].sleeve_id == "hi_conv"
    assert results[0].status == "approved"
    assert results[1].status == "rejected_directional_conflict"


def test_reconcile_margin_reject_when_no_headroom():
    """Variant already over cap → next intent rejected_margin."""
    results = reconcile_intents(
        [("S-003", _intent(alloc=15.0, lev=5.0))],
        current_gross_used_usdt=30_000.0,
        gross_cap_usdt=25_000.0,
        capital_usdt=10_000.0,
    )
    assert results[0].status == "rejected_margin"
    assert "no_headroom" in results[0].reason


def test_reconcile_margin_reduce_when_partial_room():
    """20k used + 7500 candidate over 25k cap. Headroom is 5000. With
    min_reduce_fraction=0.5, 5000 >= 0.5*7500=3750 -> approved_reduced
    to 5000 notional."""
    results = reconcile_intents(
        [("S-003", _intent(alloc=15.0, lev=5.0))],
        current_gross_used_usdt=20_000.0,
        gross_cap_usdt=25_000.0,
        capital_usdt=10_000.0,
    )
    assert results[0].status == "approved_reduced"
    assert results[0].intent is not None
    # New notional = clamped headroom = 5000. alloc = 5000 * 100 / (10k * 5) = 10.0
    assert results[0].intent.allocation_pct == pytest.approx(10.0)


def test_reconcile_margin_reject_when_below_floor():
    """24k used + 7500 candidate. Headroom is 1k. 1k < 0.5*7500=3750 -> reject."""
    results = reconcile_intents(
        [("S-003", _intent(alloc=15.0, lev=5.0))],
        current_gross_used_usdt=24_000.0,
        gross_cap_usdt=25_000.0,
        capital_usdt=10_000.0,
    )
    assert results[0].status == "rejected_margin"
    assert "too_small" in results[0].reason


def test_reconcile_subsequent_intents_see_approved_consumption():
    """Two intents in priority order — first approved at full size,
    second's margin check accounts for the first's notional."""
    intents = [
        ("S-003", _intent(alloc=15.0, lev=5.0, priority=50)),   # 7500
        ("S-096", _intent(direction="SHORT", asset="ETH",
                          alloc=15.0, lev=5.0, priority=100)),  # 7500
    ]
    # current=15k, cap=25k -> headroom=10k. First 7500 fits; second 7500
    # would push to 30k. Headroom after first = 2500. 2500 < 0.5*7500=3750
    # -> rejected.
    results = reconcile_intents(
        intents, current_gross_used_usdt=15_000.0,
        gross_cap_usdt=25_000.0, capital_usdt=10_000.0,
    )
    assert results[0].sleeve_id == "S-003"
    assert results[0].status == "approved"
    assert results[1].sleeve_id == "S-096"
    assert results[1].status == "rejected_margin"


def test_reconcile_carry_does_not_count_for_directional_conflict():
    """CARRY's SHORT BTC perp doesn't conflict with another sleeve's
    LONG BTC. Same exclusion rule as conflict_resolver."""
    intents = [
        ("CARRY", _intent(direction="SHORT", alloc=8.0, lev=5.0, priority=50)),
        ("S-003", _intent(direction="LONG", alloc=15.0, lev=5.0, priority=100)),
    ]
    results = reconcile_intents(intents, 0.0, 25_000.0, 10_000.0)
    statuses = [r.status for r in results]
    assert statuses == ["approved", "approved"]  # neither blocks the other


def test_reconcile_flat_intent_always_passes_through():
    """FLAT direction means 'close existing'; no reconcile gate applies."""
    intents = [("AI_QUANT", _intent(direction="FLAT", alloc=0.0))]
    results = reconcile_intents(intents, 0.0, 25_000.0, 10_000.0)
    assert results[0].status == "approved"
    assert results[0].intent is not None
    assert results[0].intent.direction == "FLAT"


def test_reconcile_concordant_directions_both_approved():
    """Same-direction intents on the same asset don't conflict; both
    approved (subject to margin). Future Stage 2 will pool them; today
    they stack."""
    intents = [
        ("S-003", _intent(direction="LONG", alloc=15.0, lev=5.0)),   # 7500
        ("R4",    _intent(direction="LONG", alloc=10.0, lev=2.5)),   # 2500
    ]
    results = reconcile_intents(intents, 0.0, 25_000.0, 10_000.0)
    assert [r.status for r in results] == ["approved", "approved"]
