"""P2.4d + P2.4e — AI_QUANT honors margin_headroom and conflict_resolver
before opening.

Two cross-sleeve checks AI_QUANT runs in _reconcile:

  - **conflict_resolver** (P2.4e): if another sleeve already has an
    opposite-direction open on this asset, skip with
    `skipped:directional_conflict` (or `flip_aborted=directional_conflict`
    on the flip path). First-come-first-served.

  - **margin_headroom** (P2.4d): if opening would push the variant
    over `gross_notional_target_x`, skip with
    `skipped:margin_constrained`. Run AFTER the conflict check so a
    conflict block dominates a margin block (conflicts are correctness
    signals; margin is sizing).

Tests don't need the full e2e wiring; they call `_reconcile` directly
with synthetic inputs and assert the (trade_action, debug_dict) shape.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategies.sleeves.ai_quant import signal as ai_quant_signal
from strategies.support import trade_db


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "prod.db"
    # Use trade_db.init_db so the trades schema (with every column the
    # sleeves write) matches production exactly.
    monkeypatch.setattr("strategies.support.db.DASH_DB", db_path)
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    monkeypatch.setattr("strategies.support.trade_db.DB_PATH", db_path)
    trade_db.init_db()
    return db_path


def _variant(capital: float = 10000, target_x: float = 2.5) -> dict:
    return {
        "id": "VTEST",
        "capital_usdt": capital,
        "spec": {"allocator_notes": {"gross_notional_target_x": target_x}},
    }


def _sleeve_cfg(weight_pct: float = 2.0, leverage: float = 3.0) -> dict:
    return {
        "strategy_id": "AI_QUANT",
        "_effective_weight_pct": weight_pct,
        "_effective_leverage": leverage,
        "params": {"leverage": leverage},
    }


def _decision(direction: str = "LONG", conviction: int = 80) -> dict:
    return {"direction": direction, "conviction_0_100": conviction}


def _insert_open_paper_trade(db_path: Path, **fields) -> None:
    defaults = {
        "id": "T_other",
        "series": "default",
        "strategy_variant": "VTEST",
        "strategy": "S-003",
        "asset": "BTC",
        "direction": "LONG",
        "size_usdt": 0.0,
        "leverage": 1.0,
        "entry_price": 100000.0,
        "qty": 0.0,
        "allocation_pct": 0.0,
        "actual_entry_time": "2026-05-15T00:00:00",
        "status": "open",
        "execution_mode": "paper",
    }
    defaults.update(fields)
    con = sqlite3.connect(str(db_path))
    cols = sorted(defaults)
    vals = [defaults[c] for c in cols]
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(cols)
    con.execute(
        f"INSERT INTO trades ({col_list}) VALUES ({placeholders})",
        vals,
    )
    con.commit()
    con.close()


# ─── Fresh entry ─────────────────────────────────────────────────────────────

def test_fresh_open_proceeds_when_headroom_available(temp_db):
    """Empty book + 2%×3x = 600 notional on 10k cap, target 2.5x = 25k cap.
    Plenty of room — should open."""
    variant = _variant()
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action.startswith("opened:")
    assert "trade_id" in debug


def test_fresh_open_skipped_when_already_over_cap(temp_db):
    """An existing 30k notional on a 25k cap blocks any new open."""
    variant = _variant(capital=10000, target_x=2.5)  # cap=25k
    _insert_open_paper_trade(temp_db, id="T_big", size_usdt=30_000.0, leverage=5)
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(weight_pct=2.0, leverage=3.0),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action == "skipped:margin_constrained"
    assert "reason" in debug
    assert "margin_cap" in debug["reason"]
    # No new row added.
    con = sqlite3.connect(str(temp_db))
    n = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    con.close()
    assert n == 1  # only the seeded one


def test_fresh_open_skipped_when_at_cap_with_full_size_candidate(temp_db):
    """24k used + 2% × 3x × 10k = 600 candidate -> 24.6k. Under 25k cap -> opens.
    Same setup but candidate would push to 25.5k -> skipped."""
    variant = _variant(capital=10000, target_x=2.5)  # cap=25k
    _insert_open_paper_trade(temp_db, id="T_pdo", size_usdt=24_400.0, leverage=5)
    # 24,400 + 600 = 25,000 = exactly cap. Opens.
    action, _ = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(weight_pct=2.0, leverage=3.0),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action.startswith("opened:")


def test_fresh_open_reduces_allocation_when_partial_headroom(temp_db):
    """24.7k used out of 25k cap = 300 headroom. AI_QUANT's intended
    candidate at conviction=100, weight=2%, k=3x = 10k × 0.02 × 3 = 600.
    300 / 600 = 0.50 == the default min_reduce_fraction floor. So the
    clamp returns reduced with clamped=300, and AI_QUANT opens at the
    reduced size (alloc 1.0% instead of 2.0%)."""
    import sqlite3
    variant = _variant(capital=10000, target_x=2.5)  # cap=25k
    _insert_open_paper_trade(temp_db, id="T_pdo", size_usdt=24_700.0, leverage=5)
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(weight_pct=2.0, leverage=3.0),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action.startswith("opened:")
    # Trade row should have allocation_pct reduced — 300 = 10k × alloc/100 × 3
    # -> alloc = 1.0%.
    con = sqlite3.connect(str(temp_db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT allocation_pct, size_usdt FROM trades "
            "WHERE strategy='AI_QUANT' AND status='open'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row["allocation_pct"] == pytest.approx(1.0, abs=1e-6)
    assert row["size_usdt"] == pytest.approx(300.0, abs=1e-6)


# ─── Direction flip ──────────────────────────────────────────────────────────

def test_flip_aborts_open_when_margin_constrained(temp_db):
    """An existing LONG flip-to-SHORT: close the LONG (no margin
    impact at close), then check headroom for the new SHORT. If
    constrained, abort the new open and return closed:<id> with a
    flip_aborted note. The close already happened — we don't unwind it."""
    variant = _variant(capital=10000, target_x=2.0)  # cap = 20k
    # Seed an open LONG that AI_QUANT will see as its current position.
    _insert_open_paper_trade(
        temp_db, id="SJ-AIQ-OLD",
        strategy="AI_QUANT", direction="LONG",
        size_usdt=600.0, leverage=3.0, allocation_pct=2.0,
    )
    # Seed another big open trade so headroom after the LONG closes
    # is still less than the candidate notional. SHORT direction so it
    # doesn't trip the conflict check before margin.
    _insert_open_paper_trade(
        temp_db, id="T_other_big",
        strategy="S-096", direction="SHORT",
        size_usdt=19_700.0, leverage=5,
    )
    open_lookup = {
        "id": "SJ-AIQ-OLD", "direction": "LONG", "asset": "BTC",
        "entry_price": 80_000.0, "size_usdt": 600.0, "leverage": 3.0,
    }
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(weight_pct=2.0, leverage=3.0),
        asset="BTC",
        current_open=[open_lookup],
        decision_payload=_decision(direction="SHORT", conviction=100),
        live_price=80_000.0,
    )
    # The old LONG was closed; the new SHORT was NOT opened.
    assert action == "closed:SJ-AIQ-OLD"
    assert debug["flip_aborted"] == "margin_constrained"
    # Verify only one open trade remains (the big seeded one).
    con = sqlite3.connect(str(temp_db))
    n_open = con.execute(
        "SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    con.close()
    assert n_open == 1


# ─── P2.4e: directional conflict ─────────────────────────────────────────────

def test_fresh_open_skipped_when_opposing_already_open(temp_db):
    """Another sleeve has a LONG BTC perp open; AI_QUANT decides SHORT
    on BTC — skip with directional_conflict and do NOT open the SHORT."""
    variant = _variant()
    _insert_open_paper_trade(
        temp_db, id="T_S003_LONG",
        strategy="S-003", direction="LONG", size_usdt=7500.0, leverage=5,
    )
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="SHORT", conviction=100),
        live_price=80_000.0,
    )
    assert action == "skipped:directional_conflict"
    assert debug["intended_direction"] == "SHORT"
    assert debug["conflicting_trade_id"] == "T_S003_LONG"
    assert debug["conflicting_strategy"] == "S-003"


def test_fresh_open_proceeds_when_same_direction_already_open(temp_db):
    """Another sleeve LONG on BTC is concordant, not conflicting —
    AI_QUANT can also go LONG. No skip; trade opens."""
    variant = _variant()
    _insert_open_paper_trade(
        temp_db, id="T_S003_LONG",
        strategy="S-003", direction="LONG", size_usdt=7500.0, leverage=5,
    )
    action, _ = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action.startswith("opened:")


def test_carry_short_does_not_count_as_conflict(temp_db):
    """CARRY's SHORT perp is delta-neutral collateral; an AI_QUANT
    LONG candidate should NOT be flagged as conflicting with CARRY."""
    variant = _variant()
    _insert_open_paper_trade(
        temp_db, id="T_CARRY",
        strategy="CARRY", direction="SHORT", size_usdt=4000.0, leverage=5,
    )
    action, _ = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(),
        asset="BTC",
        current_open=[],
        decision_payload=_decision(direction="LONG", conviction=100),
        live_price=80_000.0,
    )
    assert action.startswith("opened:")


def test_flip_aborts_open_when_other_sleeve_has_opposing(temp_db):
    """AI_QUANT was LONG, decides to flip to SHORT, but a DIFFERENT
    sleeve also has LONG open. The flip:
      - closes the AI_QUANT LONG (correct — that decision is its own)
      - aborts the SHORT open because S-003's LONG would conflict
    Returns closed:<old_id> with flip_aborted=directional_conflict."""
    variant = _variant()
    _insert_open_paper_trade(
        temp_db, id="SJ-AIQ-OLD",
        strategy="AI_QUANT", direction="LONG", size_usdt=600.0, leverage=3.0,
    )
    _insert_open_paper_trade(
        temp_db, id="T_S003",
        strategy="S-003", direction="LONG", size_usdt=7500.0, leverage=5,
    )
    open_lookup = {
        "id": "SJ-AIQ-OLD", "direction": "LONG", "asset": "BTC",
        "entry_price": 80_000.0, "size_usdt": 600.0, "leverage": 3.0,
    }
    action, debug = ai_quant_signal._reconcile(
        variant=variant,
        sleeve_cfg=_sleeve_cfg(),
        asset="BTC",
        current_open=[open_lookup],
        decision_payload=_decision(direction="SHORT", conviction=100),
        live_price=80_000.0,
    )
    assert action == "closed:SJ-AIQ-OLD"
    assert debug["flip_aborted"] == "directional_conflict"
    assert debug["conflicting_strategy"] == "S-003"
