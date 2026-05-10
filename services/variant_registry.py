"""Variant Registry — single source of truth for tracked portfolio variants.

Ported from the original dashboard with dashboard-only helpers trimmed
(metrics / attribution / get_events). The P-300 bot only needs register /
list / get / get_active_shadows.

A "variant" is a portfolio (or portfolio modification) that the bot can
track. SHADOW variants produce phantom trades tagged with their variant_id
and never touch an exchange.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _con() -> sqlite3.Connection:
    """Open a connection to the dashboard DB. Reads ``services.db.DASH_DB``
    at call time so a sim-mode redirection (``run.py --mode sim`` mutates
    that constant at startup) propagates here. The previous module-local
    ``DB_PATH = .../dashboard.db`` shadowed the redirection, causing
    sim runs to read variant config from the live DB while writing
    trades to the sim DB."""
    from services import db as _db_mod
    con = sqlite3.connect(str(_db_mod.DASH_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_schema() -> None:
    """Create variants + variant_events + variant_daily_returns tables. Idempotent."""
    con = _con()
    con.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            id TEXT PRIMARY KEY,
            short_name TEXT NOT NULL,
            long_name TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('full_portfolio', 'signal_overlay')),
            parent_variant_id TEXT,
            version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'SHADOW',
            is_primary INTEGER NOT NULL DEFAULT 0,
            capital_usdt REAL,
            color TEXT,
            spec_json TEXT NOT NULL,
            notes TEXT,
            superseded_by TEXT,
            reconcile_against TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_variants_primary "
        "ON variants(is_primary) WHERE is_primary = 1"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS variant_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT,
            summary TEXT
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_variant_events_ts "
        "ON variant_events(timestamp DESC)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_variant_events_variant "
        "ON variant_events(variant_id, timestamp DESC)"
    )
    # Used by equity_source='daily_returns' seeded variants (like P-300)
    con.execute("""
        CREATE TABLE IF NOT EXISTS variant_daily_returns (
            variant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            return_1x_pct REAL NOT NULL,
            source TEXT,
            regime TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (variant_id, date)
        )
    """)
    con.commit()
    con.close()


# ─── Core API ────────────────────────────────────────────────────────────────

def list_variants(enabled_only: bool = False, include_rejected: bool = True) -> list[dict]:
    con = _con()
    sql = "SELECT * FROM variants WHERE 1=1"
    if enabled_only:
        sql += " AND enabled = 1"
    if not include_rejected:
        sql += " AND status != 'REJECTED'"
    sql += " ORDER BY is_primary DESC, created_at DESC"
    rows = con.execute(sql).fetchall()
    con.close()
    return [_row_to_dict(r) for r in rows]


def get_variant(variant_id: str) -> dict | None:
    con = _con()
    row = con.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
    con.close()
    return _row_to_dict(row) if row else None


def get_primary() -> dict | None:
    con = _con()
    row = con.execute("SELECT * FROM variants WHERE is_primary = 1").fetchone()
    con.close()
    return _row_to_dict(row) if row else None


def get_active_shadows() -> list[dict]:
    """Enabled SHADOW variants that should tick each minute."""
    con = _con()
    rows = con.execute(
        "SELECT * FROM variants WHERE enabled = 1 AND is_primary = 0 "
        "AND status = 'SHADOW' "
        "ORDER BY created_at"
    ).fetchall()
    con.close()
    return [_row_to_dict(r) for r in rows]


def register_variant(
    *,
    variant_id: str,
    short_name: str,
    kind: str,
    version: str,
    spec: dict,
    long_name: str | None = None,
    status: str = "SHADOW",
    is_primary: bool = False,
    capital_usdt: float | None = None,
    color: str | None = None,
    notes: str | None = None,
    actor: str = "user",
) -> None:
    con = _con()
    try:
        con.execute("""
            INSERT INTO variants (id, short_name, long_name, kind, version, status,
                is_primary, capital_usdt, color, spec_json, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (variant_id, short_name, long_name, kind, version, status,
              1 if is_primary else 0, capital_usdt, color,
              json.dumps(spec), notes))
        con.commit()
    finally:
        con.close()
    _record_event(variant_id, "registered", actor,
                  summary=f"Registered {short_name} ({kind}, status={status})")


def _record_event(variant_id: str, event_type: str, actor: str,
                  summary: str = "", details: dict | None = None) -> None:
    con = None
    try:
        # Use the canonical clock so events recorded during a sim run land
        # at simulated time (event-log lines up with the simulated bot
        # history) rather than wall time. Live runs are unaffected —
        # clock.now_utc() returns datetime.now(timezone.utc) when no
        # simulated clock is set.
        from services import clock
        con = _con()
        con.execute(
            "INSERT INTO variant_events (timestamp, variant_id, event_type, actor, "
            "details_json, summary) VALUES (?, ?, ?, ?, ?, ?)",
            (clock.now_utc().isoformat(), variant_id, event_type, actor,
             json.dumps(details, default=str) if details else None, summary),
        )
        con.commit()
    except Exception as e:
        import sys
        print(f"[variant_registry] failed to record event: {e}", file=sys.stderr)
    finally:
        if con is not None:
            con.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["enabled"] = bool(d.get("enabled"))
    d["is_primary"] = bool(d.get("is_primary"))
    if d.get("spec_json"):
        try:
            d["spec"] = json.loads(d["spec_json"])
        except json.JSONDecodeError:
            d["spec"] = {}
    return d


# Initialise schema on import so any caller can rely on the tables.
init_schema()
