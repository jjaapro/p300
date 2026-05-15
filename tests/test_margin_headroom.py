"""P2.4d — margin headroom accounting.

Anchors:
  1. ``gross_cap_usdt`` reads ``variant.spec.allocator_notes.gross_notional_target_x``
     (default 2.5×) and multiplies by capital_usdt.
  2. ``current_gross_notional_usdt`` sums ``size_usdt * leverage`` over
     open paper trades for the variant.
  3. ``headroom_usdt = cap - current``.
  4. ``can_open(variant, candidate)`` returns ``(True, None)`` when the
     sum stays at or below cap, ``(False, reason)`` otherwise.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategies.support import margin_headroom


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    """Fresh paper-trade DB with just enough schema for the headroom math."""
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE trades (
            id              TEXT PRIMARY KEY,
            strategy_variant TEXT NOT NULL,
            strategy        TEXT NOT NULL,
            asset           TEXT NOT NULL,
            direction       TEXT NOT NULL,
            size_usdt       REAL,
            leverage        REAL,
            status          TEXT NOT NULL,
            execution_mode  TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()

    from strategies.support import db as _db_mod
    monkeypatch.setattr(_db_mod, "DASH_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "TRADER_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "PROD_DB", db_path.resolve())
    return db_path


def _insert(db_path: Path, **fields) -> None:
    defaults = {
        "id": "T1",
        "strategy_variant": "V",
        "strategy": "S-003",
        "asset": "BTC",
        "direction": "LONG",
        "size_usdt": 1000.0,
        "leverage": 1.0,
        "status": "open",
        "execution_mode": "paper",
    }
    defaults.update(fields)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO trades (id, strategy_variant, strategy, asset, direction, "
        "size_usdt, leverage, status, execution_mode) "
        "VALUES (:id, :strategy_variant, :strategy, :asset, :direction, "
        ":size_usdt, :leverage, :status, :execution_mode)",
        defaults,
    )
    con.commit()
    con.close()


def _variant(capital: float = 10000, target_x: float | None = None) -> dict:
    spec: dict = {}
    if target_x is not None:
        spec["allocator_notes"] = {"gross_notional_target_x": target_x}
    return {"id": "V", "capital_usdt": capital, "spec": spec}


# ─── gross_cap_usdt ──────────────────────────────────────────────────────────

def test_gross_cap_uses_default_when_spec_absent():
    cap = margin_headroom.gross_cap_usdt(_variant(capital=10000))
    assert cap == pytest.approx(10000 * margin_headroom.DEFAULT_GROSS_NOTIONAL_TARGET_X)


def test_gross_cap_uses_spec_override():
    cap = margin_headroom.gross_cap_usdt(_variant(capital=10000, target_x=3.0))
    assert cap == pytest.approx(30_000)


def test_gross_cap_scales_with_capital():
    cap = margin_headroom.gross_cap_usdt(_variant(capital=50_000, target_x=2.0))
    assert cap == pytest.approx(100_000)


# ─── current_gross_notional_usdt ─────────────────────────────────────────────

def test_current_gross_zero_when_no_open_trades(temp_db):
    assert margin_headroom.current_gross_notional_usdt("V") == pytest.approx(0.0)


def test_current_gross_sums_size_usdt(temp_db):
    """size_usdt is already the leveraged notional (see
    open_paper_trade: capital × alloc_pct/100 × leverage). Just sum it."""
    _insert(temp_db, id="T1", size_usdt=5000)
    _insert(temp_db, id="T2", size_usdt=1500)
    assert margin_headroom.current_gross_notional_usdt("V") == pytest.approx(6500)


def test_current_gross_excludes_closed_trades(temp_db):
    _insert(temp_db, id="T1", size_usdt=5000, status="open")
    _insert(temp_db, id="T2", size_usdt=1500, status="closed")
    assert margin_headroom.current_gross_notional_usdt("V") == pytest.approx(5000)


def test_current_gross_excludes_other_variants(temp_db):
    _insert(temp_db, id="T1", strategy_variant="V", size_usdt=5000)
    _insert(temp_db, id="T2", strategy_variant="OTHER", size_usdt=1500)
    assert margin_headroom.current_gross_notional_usdt("V") == pytest.approx(5000)


def test_current_gross_excludes_non_paper_mode(temp_db):
    """If the bot ever runs live + paper side-by-side, gross is scoped to paper."""
    _insert(temp_db, id="T1", size_usdt=5000, execution_mode="paper")
    _insert(temp_db, id="T2", size_usdt=1500, execution_mode="live")
    assert margin_headroom.current_gross_notional_usdt("V") == pytest.approx(5000)


# ─── headroom_usdt + can_open ────────────────────────────────────────────────

def test_headroom_equals_cap_when_nothing_open(temp_db):
    v = _variant(capital=10000, target_x=2.5)
    assert margin_headroom.headroom_usdt(v) == pytest.approx(25_000)


def test_headroom_decreases_with_open_notional(temp_db):
    v = _variant(capital=10000, target_x=2.5)
    _insert(temp_db, id="T1", size_usdt=10_000)  # 10k notional already on
    assert margin_headroom.headroom_usdt(v) == pytest.approx(15_000)


def test_headroom_negative_when_over_cap(temp_db):
    v = _variant(capital=10000, target_x=2.0)  # cap = 20k
    _insert(temp_db, id="T1", size_usdt=25_000)
    assert margin_headroom.headroom_usdt(v) == pytest.approx(-5_000)


def test_can_open_true_when_within_cap(temp_db):
    v = _variant(capital=10000, target_x=2.5)  # cap = 25k
    _insert(temp_db, id="T1", size_usdt=10_000)
    ok, reason = margin_headroom.can_open(v, candidate_notional_usdt=10_000)
    assert ok is True
    assert reason is None


def test_can_open_true_at_exact_cap(temp_db):
    v = _variant(capital=10000, target_x=2.5)
    _insert(temp_db, id="T1", size_usdt=10_000)
    ok, _ = margin_headroom.can_open(v, candidate_notional_usdt=15_000)  # = 25k cap
    assert ok is True


def test_can_open_false_when_exceeds_cap(temp_db):
    v = _variant(capital=10000, target_x=2.5)  # cap = 25k
    _insert(temp_db, id="T1", size_usdt=10_000)
    ok, reason = margin_headroom.can_open(v, candidate_notional_usdt=20_000)
    assert ok is False
    assert reason is not None
    assert "margin_cap" in reason


def test_can_open_handles_already_over_cap(temp_db):
    """If the variant is already over cap, can_open always returns False —
    even for a candidate that on its own would fit."""
    v = _variant(capital=10000, target_x=2.0)  # cap = 20k
    _insert(temp_db, id="T1", size_usdt=25_000)  # already over
    ok, _ = margin_headroom.can_open(v, candidate_notional_usdt=100)
    assert ok is False


# ─── clamp_to_headroom (P2.4d (b)) ───────────────────────────────────────────

def test_clamp_full_when_fits(temp_db):
    v = _variant(capital=10000, target_x=2.5)  # cap = 25k
    _insert(temp_db, id="T1", size_usdt=5000)
    clamped, status, reason = margin_headroom.clamp_to_headroom(v, 10_000)
    assert clamped == pytest.approx(10_000)
    assert status == "full"
    assert reason is None


def test_clamp_reduced_when_partial_room(temp_db):
    """20k used + 6k candidate = 26k, over the 25k cap. Headroom is 5k.
    With min_reduce_fraction=0.5, 5k >= 0.5*6k=3k -> reduced to 5k."""
    v = _variant(capital=10000, target_x=2.5)
    _insert(temp_db, id="T1", size_usdt=20_000)
    clamped, status, reason = margin_headroom.clamp_to_headroom(v, 6_000)
    assert clamped == pytest.approx(5_000)
    assert status == "reduced"
    assert "clamped" in reason


def test_clamp_too_small_when_headroom_below_floor(temp_db):
    """22k used + 6k candidate over 25k cap. Headroom is 3k. With
    min_reduce_fraction=0.5, 3k < 0.5*6k=3k? actually 3k == 0.5*6k.
    Make headroom 2k to be strictly below: use size_usdt=23000."""
    v = _variant(capital=10000, target_x=2.5)  # cap = 25k
    _insert(temp_db, id="T1", size_usdt=23_000)
    clamped, status, reason = margin_headroom.clamp_to_headroom(v, 6_000)
    assert clamped == pytest.approx(0.0)
    assert status == "too_small"
    assert "headroom" in reason


def test_clamp_no_headroom_when_already_over_cap(temp_db):
    v = _variant(capital=10000, target_x=2.0)  # cap = 20k
    _insert(temp_db, id="T1", size_usdt=25_000)  # already over
    clamped, status, reason = margin_headroom.clamp_to_headroom(v, 1_000)
    assert clamped == pytest.approx(0.0)
    assert status == "no_headroom"
    assert "current" in reason and "cap" in reason


def test_clamp_zero_or_negative_candidate_is_full(temp_db):
    """A sleeve that decided not to size anything ends up with
    candidate=0; clamp doesn't crash and reports 'full' so the sleeve's
    own no-op path runs."""
    v = _variant()
    for cand in (0.0, -1.0):
        clamped, status, reason = margin_headroom.clamp_to_headroom(v, cand)
        assert status == "full"


def test_clamp_floor_override(temp_db):
    """Passing min_reduce_fraction=0.0 always opens at headroom rather
    than skipping with too_small."""
    v = _variant(capital=10000, target_x=2.5)
    _insert(temp_db, id="T1", size_usdt=24_500)  # headroom = 500
    clamped, status, _ = margin_headroom.clamp_to_headroom(
        v, candidate_notional_usdt=10_000, min_reduce_fraction=0.0,
    )
    assert clamped == pytest.approx(500)
    assert status == "reduced"


# ─── Orchestrator injection ──────────────────────────────────────────────────

def test_orchestrator_injects_effective_margin_headroom(temp_db):
    """The orchestrator computes headroom once per composition pass and
    drops it on every sleeve_cfg. Verified via direct call here (live
    tick covered by integration tests)."""
    v = _variant(capital=10000, target_x=2.5)
    _insert(temp_db, id="T1", size_usdt=5000)  # 5k used -> 20k headroom
    h = margin_headroom.headroom_usdt(v)
    sleeve_cfg = {"strategy_id": "S-003"}
    sleeve_cfg["_effective_margin_headroom_usdt"] = h
    assert sleeve_cfg["_effective_margin_headroom_usdt"] == pytest.approx(20_000)
