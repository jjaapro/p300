"""P2.4f — concordant-signal detection."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategies.support import signal_aggregator


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "prod.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE trades (
            id                TEXT PRIMARY KEY,
            strategy_variant  TEXT NOT NULL,
            strategy          TEXT NOT NULL,
            asset             TEXT NOT NULL,
            direction         TEXT NOT NULL,
            size_usdt         REAL,
            leverage          REAL,
            entry_price       REAL,
            actual_entry_time TEXT,
            allocation_pct    REAL,
            status            TEXT NOT NULL,
            execution_mode    TEXT NOT NULL
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
        "allocation_pct": 15.0,
        "status": "open",
        "execution_mode": "paper",
    }
    defaults.update(fields)
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO trades (id, strategy_variant, strategy, asset, direction, "
        "size_usdt, leverage, entry_price, actual_entry_time, allocation_pct, "
        "status, execution_mode) "
        "VALUES (:id, :strategy_variant, :strategy, :asset, :direction, "
        ":size_usdt, :leverage, :entry_price, :actual_entry_time, "
        ":allocation_pct, :status, :execution_mode)",
        defaults,
    )
    con.commit()
    con.close()


# ─── detect_concordant_opens ─────────────────────────────────────────────────

def test_no_concordant_when_no_open(temp_db):
    assert signal_aggregator.detect_concordant_opens("V", "BTC", "LONG") == []


def test_returns_same_direction_open(temp_db):
    _insert(temp_db, id="T1", strategy="S-003", direction="LONG")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert len(out) == 1
    assert out[0]["id"] == "T1"


def test_returns_multiple_same_direction_opens_sorted(temp_db):
    _insert(temp_db, id="A", strategy="S-003", direction="LONG",
            actual_entry_time="2026-05-15T01:00:00")
    _insert(temp_db, id="B", strategy="JPLUS_R4_BTC", direction="LONG",
            actual_entry_time="2026-05-15T06:00:00")
    _insert(temp_db, id="C", strategy="EMA_BTC", direction="LONG",
            actual_entry_time="2026-05-15T03:00:00")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert [t["id"] for t in out] == ["A", "C", "B"]


def test_excludes_opposite_direction(temp_db):
    _insert(temp_db, id="T1", strategy="S-003", direction="LONG")
    _insert(temp_db, id="T2", strategy="S-096", direction="SHORT")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert [t["id"] for t in out] == ["T1"]


def test_excludes_closed_trades(temp_db):
    _insert(temp_db, id="T1", direction="LONG", status="open")
    _insert(temp_db, id="T2", direction="LONG", status="closed")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert [t["id"] for t in out] == ["T1"]


def test_excludes_other_variants(temp_db):
    _insert(temp_db, id="T1", strategy_variant="V", direction="LONG")
    _insert(temp_db, id="T2", strategy_variant="OTHER", direction="LONG")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert [t["id"] for t in out] == ["T1"]


def test_excludes_other_assets(temp_db):
    _insert(temp_db, id="T1", asset="BTC", direction="LONG")
    _insert(temp_db, id="T2", asset="ETH", direction="LONG")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "LONG")
    assert [t["id"] for t in out] == ["T1"]


def test_excludes_carry_neutral_pair(temp_db):
    """CARRY's perp SHORT isn't a concordant SHORT signal for another sleeve."""
    _insert(temp_db, id="T1", strategy="CARRY", direction="SHORT")
    _insert(temp_db, id="T2", strategy="S-078", direction="SHORT")
    _insert(temp_db, id="T3", strategy="S-096", direction="SHORT")
    out = signal_aggregator.detect_concordant_opens("V", "BTC", "SHORT")
    assert [t["id"] for t in out] == ["T3"]


def test_invalid_direction_raises(temp_db):
    with pytest.raises(ValueError):
        signal_aggregator.detect_concordant_opens("V", "BTC", "FLAT")


def test_case_insensitive_asset(temp_db):
    _insert(temp_db, id="T1", asset="BTC", direction="LONG")
    out = signal_aggregator.detect_concordant_opens("V", "btc", "LONG")
    assert len(out) == 1


# ─── summarize_concordant ────────────────────────────────────────────────────

def test_summary_empty_when_no_open(temp_db):
    assert signal_aggregator.summarize_concordant("V") == []


def test_summary_skips_solo_buckets(temp_db):
    """A bucket with N=1 isn't stacking; the summary skips it."""
    _insert(temp_db, id="T1", direction="LONG")
    assert signal_aggregator.summarize_concordant("V") == []


def test_summary_flags_two_concordant_longs(temp_db):
    _insert(temp_db, id="A", strategy="S-003", direction="LONG",
            size_usdt=1500, leverage=5, allocation_pct=15.0)
    _insert(temp_db, id="B", strategy="JPLUS_R4_BTC", direction="LONG",
            size_usdt=1000, leverage=2.5, allocation_pct=10.0)
    out = signal_aggregator.summarize_concordant("V")
    assert len(out) == 1
    bucket = out[0]
    assert bucket["asset"] == "BTC"
    assert bucket["direction"] == "LONG"
    assert bucket["n"] == 2
    assert bucket["total_notional_usdt"] == pytest.approx(1500*5 + 1000*2.5)
    assert bucket["total_alloc_pct"] == pytest.approx(25.0)


def test_summary_separate_buckets_per_asset_direction(temp_db):
    # 2 BTC LONGs + 2 ETH SHORTs + 1 BTC SHORT (solo, skipped)
    _insert(temp_db, id="L1", asset="BTC", direction="LONG", strategy="S-003")
    _insert(temp_db, id="L2", asset="BTC", direction="LONG", strategy="EMA_BTC")
    _insert(temp_db, id="S1", asset="ETH", direction="SHORT", strategy="S-096")
    _insert(temp_db, id="S2", asset="ETH", direction="SHORT", strategy="PDO_RETOUCH")
    _insert(temp_db, id="X1", asset="BTC", direction="SHORT", strategy="S-096")
    out = signal_aggregator.summarize_concordant("V")
    keys = sorted((b["asset"], b["direction"]) for b in out)
    assert keys == [("BTC", "LONG"), ("ETH", "SHORT")]


def test_summary_excludes_carry_from_concordant(temp_db):
    """If CARRY is the only opposing partner, no concordant stack flagged."""
    _insert(temp_db, id="C1", strategy="CARRY", direction="SHORT")
    _insert(temp_db, id="L1", strategy="S-003", direction="LONG")
    # Only one non-neutral LONG -> summary empty (n=1 skipped)
    assert signal_aggregator.summarize_concordant("V") == []
