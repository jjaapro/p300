"""Trade log + runtime config for the P-300 standalone bot.

Minimal subset of the original dashboard `trade_db.py`:
  - init_db() creates the `trades` table with the exact schema the 5 live
    services INSERT into, plus the `config` table for paper_account_usdt.
  - get_config / set_config are the only API calls the services + variant
    engine make at runtime.

All dashboard-only extras (fills, audit_log, heartbeat, recovery_log,
chart markers/bands/hover helpers) are omitted — P-300 SHADOW never calls
them. Re-add on demand.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db() -> None:
    """Create trades + config tables. Idempotent."""
    con = _con()
    con.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            series TEXT NOT NULL,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            strategy TEXT NOT NULL,
            regime TEXT,
            allocation_pct REAL,
            leverage REAL DEFAULT 1.0,
            entry_time TEXT,
            exit_time TEXT,
            actual_entry_time TEXT,
            actual_exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            size_usdt REAL,
            qty REAL,
            pnl_usdt REAL,
            pnl_pct REAL,
            status TEXT DEFAULT 'pending',
            venue TEXT DEFAULT 'MEXC',
            order_ids TEXT,
            execution_mode TEXT DEFAULT 'PAPER',
            strategy_variant TEXT DEFAULT 'prod',
            resolution TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_variant_strategy "
        "ON trades(strategy_variant, strategy, status)"
    )
    con.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    for key, val in [
        ("execution_mode", "PAPER"),
        ("paper_account_usdt", "10000"),
        ("max_leverage", "5.0"),
    ]:
        con.execute(
            "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
            (key, val),
        )
    con.commit()
    con.close()


def get_config(key: str) -> str | None:
    con = _con()
    row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    con.close()
    return row["value"] if row else None


def set_config(key: str, value: str) -> None:
    con = _con()
    con.execute(
        "INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')",
        (key, value, value),
    )
    con.commit()


# ─── Per-trade close-log formatter (shared by every sleeve) ─────────────────

def format_close_summary(*, trade_id: str, asset: str, direction: str,
                          entry_price: float, exit_price: float,
                          pnl_pct: float, pnl_usdt: float,
                          entry_time_iso: str, exit_time_iso: str,
                          reason: str, leverage: float | None = None) -> str:
    """One canonical receipt line printed by every sleeve when it closes.

    Format:
      CLOSE SJ-id ASSET DIR  $entry -> $exit  pnl=±X.XX% (±$X.XX)  hold=Yd Zh  k=Kx  reason=...

    Hold duration falls back to "?" if either timestamp is unparseable. The
    `reason` is sleeve-specific (e.g. 'target_hit@29900', 'stop_loss',
    'eod', 'scheduled_exit', 'time_stop_15d', 'fomc_T+0.5h')."""
    from datetime import datetime, timezone
    try:
        a = datetime.fromisoformat(entry_time_iso)
        b = datetime.fromisoformat(exit_time_iso)
        if a.tzinfo is None: a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None: b = b.replace(tzinfo=timezone.utc)
        delta = b - a
        days = delta.days
        hours = (delta.seconds // 3600)
        if days > 0:
            hold = f"{days}d{hours:02d}h"
        elif hours > 0:
            hold = f"{hours}h{(delta.seconds % 3600) // 60:02d}m"
        else:
            hold = f"{delta.seconds // 60}m"
    except (TypeError, ValueError):
        hold = "?"
    sign = "+" if pnl_pct >= 0 else ""
    pnl_dollar_sign = "+" if pnl_usdt >= 0 else "-"
    lev_part = f"  k={leverage:.1f}x" if leverage is not None else ""
    return (f"CLOSE {trade_id} {asset} {direction}  "
            f"${entry_price:,.2f} -> ${exit_price:,.2f}  "
            f"pnl={sign}{pnl_pct:.2f}% ({pnl_dollar_sign}${abs(pnl_usdt):,.2f})  "
            f"hold={hold}{lev_part}  reason={reason}")
    con.close()


# Initialise on import so any caller that imports trade_db can rely on
# the tables being present.
init_db()
