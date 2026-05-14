"""Shared pytest fixtures.

Tests default to a temporary in-memory copy of the clock state so live/replay
interactions are isolated per-test. DB-dependent tests either use the real
data/trader.db in read-only mode or a temporary sqlite fixture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Repo root on path so "from strategies import ..." etc. work regardless of CWD.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from strategies.support import clock  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_clock():
    """Ensure every test starts in live mode and restores on exit."""
    before = clock._simulated_now
    clock.set_simulated_now(None)
    yield
    clock.set_simulated_now(before)


@pytest.fixture(autouse=True)
def _clean_env_stop_semantics(monkeypatch):
    """Default SL semantic to 'price_move' for every test; individual tests
    that need 'margin' can override via monkeypatch."""
    monkeypatch.delenv("P300_STOP_SEMANTICS", raising=False)
    yield


@pytest.fixture
def tmp_trader_db(tmp_path):
    """A fresh empty sqlite file playable as TRADER_DB for tests that need
    DB access but don't want the real data."""
    import sqlite3
    p = tmp_path / "trader.db"
    con = sqlite3.connect(str(p))
    con.close()
    return p
