"""Tests for the AI_QUANT M2a backfill tool — fuzzy-matches existing
trade rows to their decision rows by (variant, asset, time-within-±2min)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from studies.simulation.backfill_ai_quant_decision_id import backfill


def _make_dash_db(tmp_path: Path) -> Path:
    """Minimal dashboard.db with trades + ai_quant_decisions tables."""
    p = tmp_path / "dashboard.db"
    con = sqlite3.connect(str(p))
    try:
        con.executescript("""
            CREATE TABLE trades (
                id TEXT PRIMARY KEY,
                strategy_variant TEXT,
                strategy TEXT,
                asset TEXT,
                actual_entry_time TEXT,
                ai_quant_decision_id INTEGER
            );
            CREATE TABLE ai_quant_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_utc INTEGER NOT NULL,
                decision_date TEXT,
                variant_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                decided TEXT
            );
        """)
        con.commit()
    finally:
        con.close()
    return p


def _insert_trade(p: Path, **kw) -> None:
    defaults = {
        "id": "SJ-1", "strategy_variant": "V", "strategy": "AI_QUANT",
        "asset": "BTC", "actual_entry_time": "2026-05-08T00:07:00+00:00",
        "ai_quant_decision_id": None,
    }
    defaults.update(kw)
    con = sqlite3.connect(str(p))
    try:
        cols = list(defaults.keys())
        con.execute(
            f"INSERT INTO trades ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            tuple(defaults[c] for c in cols),
        )
        con.commit()
    finally:
        con.close()


def _insert_decision(p: Path, *, did: int, decision_utc: int,
                      variant_id: str = "V", asset: str = "BTC",
                      decided: str = "LONG") -> None:
    con = sqlite3.connect(str(p))
    try:
        con.execute(
            "INSERT INTO ai_quant_decisions "
            "(id, decision_utc, decision_date, variant_id, asset, decided) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (did, decision_utc, "2026-05-08", variant_id, asset, decided),
        )
        con.commit()
    finally:
        con.close()


# 2026-05-08 00:07:00 UTC
ENTRY_TS = 1778198820


def test_backfill_dry_run_matches_but_does_not_commit(tmp_path):
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30)  # 30s earlier
    r = backfill(str(p), apply=False)
    assert r["scanned"] == 1
    assert r["matched"] == 1
    assert r["applied"] is False
    # DB unchanged in dry-run.
    con = sqlite3.connect(str(p))
    try:
        v = con.execute(
            "SELECT ai_quant_decision_id FROM trades WHERE id='SJ-1'"
        ).fetchone()[0]
    finally:
        con.close()
    assert v is None


def test_backfill_apply_writes_decision_id(tmp_path):
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30)
    r = backfill(str(p), apply=True)
    assert r["matched"] == 1
    assert r["applied"] is True
    con = sqlite3.connect(str(p))
    try:
        v = con.execute(
            "SELECT ai_quant_decision_id FROM trades WHERE id='SJ-1'"
        ).fetchone()[0]
    finally:
        con.close()
    assert v == 42


def test_backfill_skips_already_tagged_trades(tmp_path):
    """Already-linked trades are not in the scan candidate set."""
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-PRE", ai_quant_decision_id=99)
    _insert_decision(p, did=99, decision_utc=ENTRY_TS - 30)
    r = backfill(str(p), apply=True)
    assert r["scanned"] == 0
    assert r["matched"] == 0


def test_backfill_skips_when_no_decision_within_window(tmp_path):
    """Decision is 10 minutes away → outside the ±2min default
    tolerance → no match."""
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 600)
    r = backfill(str(p), apply=True)
    assert r["scanned"] == 1
    assert r["matched"] == 0
    assert r["skipped_no_match"] == 1


def test_backfill_skips_when_ambiguous(tmp_path):
    """Two decisions within the window for the same (variant, asset, time)
    -> skipped_ambiguous; nothing written."""
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30)
    _insert_decision(p, did=43, decision_utc=ENTRY_TS + 30)
    r = backfill(str(p), apply=True)
    assert r["scanned"] == 1
    assert r["matched"] == 0
    assert r["skipped_ambiguous"] == 1
    con = sqlite3.connect(str(p))
    try:
        v = con.execute(
            "SELECT ai_quant_decision_id FROM trades WHERE id='SJ-1'"
        ).fetchone()[0]
    finally:
        con.close()
    assert v is None


def test_backfill_matches_only_same_variant_and_asset(tmp_path):
    """Cross-variant or cross-asset decisions are not candidates."""
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1", strategy_variant="V", asset="BTC")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30,
                      variant_id="OTHER", asset="BTC")
    _insert_decision(p, did=43, decision_utc=ENTRY_TS - 30,
                      variant_id="V", asset="ETH")
    r = backfill(str(p), apply=True)
    assert r["matched"] == 0
    assert r["skipped_no_match"] == 1


def test_backfill_skips_unparseable_entry_time(tmp_path):
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1", actual_entry_time=None)
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30)
    r = backfill(str(p), apply=True)
    assert r["scanned"] == 1
    assert r["matched"] == 0
    assert r["skipped_no_time"] == 1


def test_backfill_idempotent_second_run_is_noop(tmp_path):
    """Run apply twice — second run finds 0 candidates."""
    p = _make_dash_db(tmp_path)
    _insert_trade(p, id="SJ-1")
    _insert_decision(p, did=42, decision_utc=ENTRY_TS - 30)
    r1 = backfill(str(p), apply=True)
    assert r1["matched"] == 1
    r2 = backfill(str(p), apply=True)
    assert r2["scanned"] == 0
    assert r2["matched"] == 0
