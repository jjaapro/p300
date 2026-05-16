"""Tests for the sim.py isolation guard (added 2026-05-16).

Sim must never run against the live prod.db — passing it as either
``--trader-db`` or ``--dash-db`` would mix sim trade rows into the
live ledger. The guard refuses to proceed in that case and exits 2.
"""
from __future__ import annotations

import sys

import pytest

from studies.simulation import sim


def test_sim_rejects_trader_db_pointing_at_prod_db(tmp_path, caplog, monkeypatch):
    """Passing --trader-db = prod.db must exit 2 without touching the DB."""
    prod_path = tmp_path / "prod.db"
    prod_path.write_bytes(b"")  # so .resolve() returns the canonical path
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod_path)
    rc = sim.main([
        "--start", "2024-01-01", "--end", "2024-01-02",
        "--trader-db", str(prod_path),
        "--dash-db", str(tmp_path / "sim_dash.db"),
    ])
    assert rc == 2


def test_sim_rejects_dash_db_pointing_at_prod_db(tmp_path, monkeypatch):
    """Passing --dash-db = prod.db must exit 2 — the catastrophic case
    (would inject sim trade rows into the live ledger)."""
    prod_path = tmp_path / "prod.db"
    prod_path.write_bytes(b"")
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod_path)
    rc = sim.main([
        "--start", "2024-01-01", "--end", "2024-01-02",
        "--trader-db", str(tmp_path / "sim_trader.db"),
        "--dash-db", str(prod_path),
    ])
    assert rc == 2


def test_sim_accepts_isolated_dbs(tmp_path, monkeypatch):
    """Sanity check: when both paths differ from prod.db, the guard
    passes through (the sim proceeds; here we stub run_sim to a no-op
    so we don't actually loop). Exits 0.

    sim.main() mutates ``db.TRADER_DB`` and ``db.DASH_DB`` directly
    after the guard passes, so we pre-monkeypatch those to their
    current values; pytest's teardown then restores them, preventing
    test-order pollution into downstream tests like test_today_inputs.
    """
    from strategies.support import db as _db_mod
    monkeypatch.setattr("strategies.support.db.TRADER_DB", _db_mod.TRADER_DB)
    monkeypatch.setattr("strategies.support.db.DASH_DB", _db_mod.DASH_DB)

    prod_path = tmp_path / "prod.db"
    prod_path.write_bytes(b"")
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod_path)
    # Stub everything past the guard so the test is fast and offline.
    monkeypatch.setattr(sim.sim_loop, "run_sim",
                         lambda *a, **kw: 0)
    monkeypatch.setattr(sim, "_print_health_report", lambda: None)
    monkeypatch.setattr(sim, "_ensure_variant_registered", lambda: None)
    monkeypatch.setattr(sim.trade_db, "init_db", lambda: None)
    monkeypatch.setattr(sim.variant_registry, "init_schema", lambda: None)
    rc = sim.main([
        "--start", "2024-01-01", "--end", "2024-01-02",
        "--trader-db", str(tmp_path / "sim_trader.db"),
        "--dash-db", str(tmp_path / "sim_dash.db"),
    ])
    assert rc == 0
