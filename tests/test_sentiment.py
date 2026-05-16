"""Tests for data.sources.sentiment after the 2026-05-16 migration to
``prod.db.fear_greed_index``. Covers the DB-backed read surface, the
legacy-JSON backfill, and the bucket() classifier."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from data.sources import sentiment


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    db_path = tmp_path / "prod.db"
    monkeypatch.setattr("strategies.support.db.PROD_DB", db_path)
    monkeypatch.setattr("strategies.support.db.TRADER_DB", db_path)
    monkeypatch.setattr("strategies.support.db.DASH_DB", db_path)
    # Make sure no legacy JSON in the way for the empty cases.
    monkeypatch.setattr(sentiment, "LEGACY_JSON_PATH",
                         tmp_path / "fear_greed.json")
    return db_path


def _seed_rows(db_path: Path, rows: list[tuple[str, int, str | None]]) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS fear_greed_index (
                date TEXT PRIMARY KEY,
                value INTEGER NOT NULL,
                classification TEXT
            )
        """)
        con.executemany(
            "INSERT OR REPLACE INTO fear_greed_index VALUES (?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


# ─── Read API ───────────────────────────────────────────────────────────────

def test_get_value_returns_none_when_db_empty(empty_db):
    assert sentiment.get_value("2026-05-15") is None


def test_get_latest_returns_none_when_db_empty(empty_db):
    assert sentiment.get_latest() is None


def test_get_value_returns_seeded_value(empty_db):
    _seed_rows(empty_db, [("2026-05-15", 34, "Fear")])
    assert sentiment.get_value("2026-05-15") == 34


def test_get_value_unknown_date_returns_none(empty_db):
    _seed_rows(empty_db, [("2026-05-15", 34, "Fear")])
    assert sentiment.get_value("2024-01-01") is None


def test_get_latest_returns_most_recent_by_date(empty_db):
    _seed_rows(empty_db, [
        ("2026-05-15", 34, "Fear"),
        ("2026-05-14", 38, "Fear"),
        ("2018-02-01", 30, "Fear"),
    ])
    assert sentiment.get_latest() == ("2026-05-15", 34)


# ─── Bucket classifier (boundary tests) ─────────────────────────────────────

def test_bucket_extreme_fear_inclusive_at_25():
    assert sentiment.bucket(25) == "extreme_fear"
    assert sentiment.bucket(0) == "extreme_fear"


def test_bucket_fear_band():
    assert sentiment.bucket(26) == "fear"
    assert sentiment.bucket(45) == "fear"


def test_bucket_neutral_band():
    assert sentiment.bucket(46) == "neutral"
    assert sentiment.bucket(55) == "neutral"


def test_bucket_greed_band():
    assert sentiment.bucket(56) == "greed"
    assert sentiment.bucket(75) == "greed"


def test_bucket_extreme_greed_band():
    assert sentiment.bucket(76) == "extreme_greed"
    assert sentiment.bucket(100) == "extreme_greed"


def test_bucket_none_returns_unknown():
    assert sentiment.bucket(None) == "unknown"


# ─── Legacy JSON backfill ───────────────────────────────────────────────────

def test_backfill_reads_legacy_json(empty_db, tmp_path, monkeypatch):
    """If the table is empty and data/fear_greed.json exists, the
    backfill populates the table on the first refresh()-equivalent
    call. Driven directly here since refresh() also hits the network."""
    legacy = tmp_path / "fear_greed.json"
    payload = {
        "data": [
            # alternative.me schema: value is string, timestamp is unix-secs str.
            {"value": "34", "value_classification": "Fear",
             "timestamp": "1747267200"},  # 2025-05-15
            {"value": "60", "value_classification": "Greed",
             "timestamp": "1747353600"},  # 2025-05-16
        ]
    }
    legacy.write_text(json.dumps(payload))
    monkeypatch.setattr(sentiment, "LEGACY_JSON_PATH", legacy)
    n = sentiment._backfill_from_legacy_json()
    assert n == 2
    assert sentiment.get_value("2025-05-15") == 34
    assert sentiment.get_value("2025-05-16") == 60


def test_backfill_no_op_when_legacy_json_missing(empty_db):
    """No legacy file → backfill is a no-op, returns 0."""
    assert sentiment._backfill_from_legacy_json() == 0


def test_backfill_silently_handles_malformed_rows(empty_db, tmp_path, monkeypatch):
    """Rows missing 'value' or 'timestamp' are skipped, not raised."""
    legacy = tmp_path / "fear_greed.json"
    payload = {
        "data": [
            {"value": "34", "timestamp": "1747267200"},  # ok
            {"value": "garbage", "timestamp": "1747353600"},  # bad value
            {"timestamp": "1747353600"},                   # missing value
            {"value": "60"},                                # missing timestamp
        ]
    }
    legacy.write_text(json.dumps(payload))
    monkeypatch.setattr(sentiment, "LEGACY_JSON_PATH", legacy)
    n = sentiment._backfill_from_legacy_json()
    assert n == 1  # only the first row parses


# ─── Sim mode network isolation ─────────────────────────────────────────────

def test_refresh_is_no_op_in_sim(empty_db, monkeypatch):
    """clock.is_simulated() True → refresh() returns False without
    touching the network or writing rows."""
    monkeypatch.setattr("strategies.support.clock.is_simulated", lambda: True)
    assert sentiment.refresh() is False
    assert sentiment.get_latest() is None  # nothing written
