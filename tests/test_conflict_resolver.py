"""P2.4e — directional conflict detection."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategies.support import conflict_resolver


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE trades (
            id               TEXT PRIMARY KEY,
            strategy_variant TEXT NOT NULL,
            strategy         TEXT NOT NULL,
            asset            TEXT NOT NULL,
            direction        TEXT NOT NULL,
            size_usdt        REAL,
            leverage         REAL,
            entry_price      REAL,
            actual_entry_time TEXT,
            status           TEXT NOT NULL,
            execution_mode   TEXT NOT NULL
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
        "leverage": 5.0,
        "entry_price": 100000.0,
        "actual_entry_time": "2026-05-15T00:05:00",
        "status": "open",
        "execution_mode": "paper",
    }
    defaults.update(fields)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO trades (id, strategy_variant, strategy, asset, direction, "
        "size_usdt, leverage, entry_price, actual_entry_time, status, execution_mode) "
        "VALUES (:id, :strategy_variant, :strategy, :asset, :direction, "
        ":size_usdt, :leverage, :entry_price, :actual_entry_time, :status, :execution_mode)",
        defaults,
    )
    con.commit()
    con.close()


# ─── detect_opposing_open ────────────────────────────────────────────────────

def test_no_opposing_when_no_open_trades(temp_db):
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_no_opposing_when_same_direction_open(temp_db):
    """An open LONG on BTC is not a conflict for another LONG candidate."""
    _insert(temp_db, id="T1", direction="LONG")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_detects_short_open_when_candidate_is_long(temp_db):
    _insert(temp_db, id="T1", strategy="S-096", direction="SHORT")
    out = conflict_resolver.detect_opposing_open("V", "BTC", "LONG")
    assert out is not None
    assert out["id"] == "T1"
    assert out["strategy"] == "S-096"
    assert out["direction"] == "SHORT"


def test_detects_long_open_when_candidate_is_short(temp_db):
    _insert(temp_db, id="T1", strategy="S-003", direction="LONG")
    out = conflict_resolver.detect_opposing_open("V", "BTC", "SHORT")
    assert out is not None
    assert out["direction"] == "LONG"


def test_ignores_closed_trades(temp_db):
    _insert(temp_db, id="T1", direction="SHORT", status="closed")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_ignores_other_variants(temp_db):
    _insert(temp_db, id="T1", strategy_variant="OTHER", direction="SHORT")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_ignores_other_assets(temp_db):
    """A SHORT ETH open is not a conflict for a LONG BTC candidate."""
    _insert(temp_db, id="T1", asset="ETH", direction="SHORT")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_carry_neutral_pair_does_not_conflict(temp_db):
    """CARRY (S-078) is delta-neutral; its perp SHORT shouldn't flag
    against a directional LONG candidate from another sleeve."""
    _insert(temp_db, id="T1", strategy="CARRY", direction="SHORT")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None
    _insert(temp_db, id="T2", strategy="S-078", direction="SHORT")
    assert conflict_resolver.detect_opposing_open("V", "BTC", "LONG") is None


def test_returns_earliest_when_multiple_conflicts(temp_db):
    """If multiple sleeves are SHORT, the function picks the earliest entry."""
    _insert(temp_db, id="T_late",  strategy="S-096", direction="SHORT",
            actual_entry_time="2026-05-15T02:00:00")
    _insert(temp_db, id="T_early", strategy="THU_BEAR", direction="SHORT",
            actual_entry_time="2026-05-15T01:00:00")
    out = conflict_resolver.detect_opposing_open("V", "BTC", "LONG")
    assert out["id"] == "T_early"


def test_invalid_direction_raises(temp_db):
    with pytest.raises(ValueError):
        conflict_resolver.detect_opposing_open("V", "BTC", "FLAT")


def test_case_insensitive_asset(temp_db):
    """Pass `btc` lowercase, still matches the canonical BTC row."""
    _insert(temp_db, id="T1", asset="BTC", direction="SHORT")
    out = conflict_resolver.detect_opposing_open("V", "btc", "LONG")
    assert out is not None


# ─── summarize_conflicts ─────────────────────────────────────────────────────

def test_summarize_empty_when_no_open(temp_db):
    assert conflict_resolver.summarize_conflicts("V") == []


def test_summarize_empty_when_only_long(temp_db):
    _insert(temp_db, id="T1", direction="LONG")
    _insert(temp_db, id="T2", direction="LONG")
    assert conflict_resolver.summarize_conflicts("V") == []


def test_summarize_flags_long_plus_short_pair(temp_db):
    _insert(temp_db, id="T_long",  strategy="S-003", direction="LONG")
    _insert(temp_db, id="T_short", strategy="S-096", direction="SHORT")
    out = conflict_resolver.summarize_conflicts("V")
    assert len(out) == 1
    assert out[0]["asset"] == "BTC"
    assert {t["id"] for t in out[0]["long_trades"]} == {"T_long"}
    assert {t["id"] for t in out[0]["short_trades"]} == {"T_short"}


def test_summarize_excludes_carry_pair(temp_db):
    """CARRY's SHORT-perp + directional LONG-BTC from another sleeve is
    NOT a conflict (CARRY's leg is collateral, not a directional bet)."""
    _insert(temp_db, id="T_carry", strategy="CARRY", direction="SHORT")
    _insert(temp_db, id="T_long",  strategy="S-003", direction="LONG")
    assert conflict_resolver.summarize_conflicts("V") == []


def test_summarize_handles_multi_asset(temp_db):
    """BTC + ETH conflicts are reported separately."""
    _insert(temp_db, id="A1", asset="BTC", direction="LONG", strategy="S-003")
    _insert(temp_db, id="A2", asset="BTC", direction="SHORT", strategy="S-096")
    _insert(temp_db, id="B1", asset="ETH", direction="LONG", strategy="PDO_RETOUCH")
    _insert(temp_db, id="B2", asset="ETH", direction="SHORT", strategy="S-096")
    out = conflict_resolver.summarize_conflicts("V")
    assets = {row["asset"] for row in out}
    assert assets == {"BTC", "ETH"}


# ─── current_directional_opens (P2.4e/f Stage 2 ADX migration) ──────────────

def test_current_directional_opens_empty(temp_db):
    assert conflict_resolver.current_directional_opens("V") == {}


def test_current_directional_opens_returns_per_asset_direction(temp_db):
    _insert(temp_db, id="T1", asset="BTC", direction="LONG", strategy="S-003")
    _insert(temp_db, id="T2", asset="ETH", direction="SHORT", strategy="S-096",
            actual_entry_time="2026-05-15T01:00:00")
    out = conflict_resolver.current_directional_opens("V")
    assert out == {"BTC": "LONG", "ETH": "SHORT"}


def test_current_directional_opens_excludes_carry(temp_db):
    """CARRY's perp SHORT is excluded (delta-neutral)."""
    _insert(temp_db, id="T_carry", asset="BTC", direction="SHORT", strategy="CARRY")
    assert conflict_resolver.current_directional_opens("V") == {}


def test_current_directional_opens_carry_does_not_mask_directional(temp_db):
    """CARRY SHORT on BTC + directional LONG on BTC — the dict reflects
    only the directional sleeve (LONG)."""
    _insert(temp_db, id="T_carry", asset="BTC", direction="SHORT",
            strategy="CARRY", actual_entry_time="2026-05-15T00:00:00")
    _insert(temp_db, id="T_long",  asset="BTC", direction="LONG",
            strategy="S-003", actual_entry_time="2026-05-15T01:00:00")
    assert conflict_resolver.current_directional_opens("V") == {"BTC": "LONG"}


def test_current_directional_opens_ignores_closed_and_other_variants(temp_db):
    _insert(temp_db, id="T_closed", asset="BTC", direction="LONG",
            strategy="S-003", status="closed")
    _insert(temp_db, id="T_other_v", asset="BTC", direction="SHORT",
            strategy="S-096", strategy_variant="OTHER")
    assert conflict_resolver.current_directional_opens("V") == {}


def test_current_directional_opens_earliest_wins_on_race(temp_db):
    """If a malformed state shows two directions on one asset, the
    earlier open wins the slot (deterministic tie-break)."""
    _insert(temp_db, id="T_short_first", asset="BTC", direction="SHORT",
            strategy="S-096", actual_entry_time="2026-05-15T00:00:00")
    _insert(temp_db, id="T_long_later", asset="BTC", direction="LONG",
            strategy="S-003", actual_entry_time="2026-05-15T01:00:00")
    assert conflict_resolver.current_directional_opens("V") == {"BTC": "SHORT"}
