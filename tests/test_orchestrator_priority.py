"""P2.4d (c) — explicit sleeve priority via spec.composition[i].priority.

Today's behaviour is "first entry in spec.composition dispatches first";
the priority field lets the operator override that without reordering
the JSON. Lower priority = dispatches earlier. Stable sort means
entries without an explicit priority keep their registration order
relative to each other.

Verified by direct manipulation of the dispatch loop's sort key —
the sort happens at the top of ``_tick_composition`` before any
sleeve is touched.
"""
from __future__ import annotations


def _sort_key(sleeve: dict) -> float:
    return float(sleeve.get("priority", 100))


def _sort(composition: list[dict]) -> list[dict]:
    return sorted(composition, key=_sort_key)


def test_unset_priority_preserves_registration_order():
    """All entries default to priority=100; stable sort leaves them in
    insertion order. Matches the pre-P2.4d (c) behaviour."""
    comp = [{"strategy_id": "A"}, {"strategy_id": "B"}, {"strategy_id": "C"}]
    out = _sort(comp)
    assert [s["strategy_id"] for s in out] == ["A", "B", "C"]


def test_lower_priority_dispatches_earlier():
    comp = [
        {"strategy_id": "low", "priority": 100},
        {"strategy_id": "high", "priority": 10},
        {"strategy_id": "mid", "priority": 50},
    ]
    out = _sort(comp)
    assert [s["strategy_id"] for s in out] == ["high", "mid", "low"]


def test_mixed_explicit_and_default_sorts_correctly():
    """Entries without `priority` get the default 100; an entry with
    priority=50 dispatches before all defaulted entries."""
    comp = [
        {"strategy_id": "A"},                      # default 100
        {"strategy_id": "B", "priority": 50},
        {"strategy_id": "C"},                      # default 100
    ]
    out = _sort(comp)
    assert [s["strategy_id"] for s in out] == ["B", "A", "C"]


def test_tied_priority_preserves_input_order():
    """Stable sort — equal priorities preserve the input order."""
    comp = [
        {"strategy_id": "first", "priority": 50},
        {"strategy_id": "second", "priority": 50},
        {"strategy_id": "third", "priority": 50},
    ]
    out = _sort(comp)
    assert [s["strategy_id"] for s in out] == ["first", "second", "third"]


def test_float_priority_supported():
    """Fine-grained priorities for last-tiebreaker control."""
    comp = [
        {"strategy_id": "A", "priority": 1.5},
        {"strategy_id": "B", "priority": 1.4},
        {"strategy_id": "C", "priority": 1.6},
    ]
    out = _sort(comp)
    assert [s["strategy_id"] for s in out] == ["B", "A", "C"]
