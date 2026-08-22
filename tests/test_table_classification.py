"""Phase-0 data-integrity hardening (2026-08-22): freshness contracts,
ISO-date age parsing, and the table-classification completeness invariant.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from strategies.support import db


def _tmp_db(tmp_path, name="t.db"):
    path = tmp_path / name
    return path, sqlite3.connect(str(path))


def test_latest_age_s_numeric(tmp_path, monkeypatch):
    path, con = _tmp_db(tmp_path)
    con.execute("CREATE TABLE cd_funding_rate_eth (timestamp INTEGER)")
    con.execute("INSERT INTO cd_funding_rate_eth VALUES (?)",
                (int(time.time()) - 3600,))
    con.commit()
    age = botlib.latest_age_s("cd_funding_rate_eth", con)
    con.close()
    assert age == pytest.approx(3600, abs=60)


def test_latest_age_s_iso_text_naive_is_utc(tmp_path):
    """fear_greed_index.date is a TEXT ISO date; naive parses as UTC midnight."""
    path, con = _tmp_db(tmp_path)
    con.execute("CREATE TABLE fear_greed_index (date TEXT)")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    con.execute("INSERT INTO fear_greed_index VALUES (?)", (yesterday,))
    con.commit()
    age = botlib.latest_age_s("fear_greed_index", con)
    con.close()
    assert age is not None
    assert 86400 <= age < 2 * 86400 + 60   # 1-2 days depending on time of day


def test_latest_age_s_missing_table_is_none(tmp_path):
    path, con = _tmp_db(tmp_path)
    assert botlib.latest_age_s("fear_greed_index", con) is None
    con.close()


def test_every_registry_is_disjoint():
    """A table classified twice is a registry bug."""
    groups = [set(botlib.FRESHNESS_CONTRACTS), set(botlib.FROZEN_TABLES),
              botlib.GATED_TABLES, botlib.STATE_TABLES, botlib.STATIC_TABLES]
    seen: set[str] = set()
    for g in groups:
        assert not (g & seen), f"tables classified twice: {g & seen}"
        seen |= g


def test_live_prod_db_fully_classified():
    """The invariant monitor.py enforces hourly, asserted at test time too:
    every table in prod.db is contracted or declared, and no registry entry
    points at a table that no longer exists."""
    if not db.PROD_DB.exists():
        pytest.skip("prod.db not present")
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    classified = (set(botlib.FRESHNESS_CONTRACTS) | set(botlib.FROZEN_TABLES)
                  | botlib.GATED_TABLES | botlib.STATE_TABLES
                  | botlib.STATIC_TABLES)
    assert not (tables - classified), f"unclassified: {tables - classified}"
    assert not (classified - tables), f"ghost entries: {classified - tables}"
