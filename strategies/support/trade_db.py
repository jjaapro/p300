"""Trade log + runtime config for the P-300 standalone bot.

Minimal subset of the original dashboard `trade_db.py`:
  - init_db() creates the `trades` table with the exact schema the 5 live
    services INSERT into, plus the `config` table for paper_account_usdt.
  - get_config / set_config are the only API calls the services + variant
    engine make at runtime.

All dashboard-only extras (fills, audit_log, heartbeat, recovery_log,
chart markers/bands/hover helpers) are omitted — P-300 paper never calls
them. Re-add on demand.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "dashboard.db"


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _column_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def init_db() -> None:
    """Create trades + config + trade_adjustments tables. Idempotent."""
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

    # Mutable position-state columns added after the original schema. Each
    # tracks the live state of a position that supports partial fills and
    # leverage adjustments — null on legacy rows that pre-date the migration.
    #
    # avg_entry_price tracks the running weighted-average basis as a
    # position grows via SCALE_UP. SCALE_DOWN doesn't move it (remaining
    # qty keeps its basis). On legacy rows it stays NULL, so the close
    # path falls back to the immutable entry_price.
    for col, ddl in [
        ("parent_position_id",  "TEXT"),
        ("current_qty",         "REAL"),
        ("current_leverage",    "REAL"),
        ("current_size_usdt",   "REAL"),
        ("realized_pnl_usdt",   "REAL DEFAULT 0"),
        ("avg_entry_price",     "REAL"),
        # AI_QUANT M2a (2026-05-16): stable join from a trade row back
        # to the ai_quant_decisions row that spawned it. NULL on every
        # non-AI_QUANT trade and on AI_QUANT trades opened pre-migration
        # (the backfill tool in studies/tools/backfill_ai_quant_decision_id.py
        # fuzzy-matches those).
        ("ai_quant_decision_id", "INTEGER"),
    ]:
        if not _column_exists(con, "trades", col):
            con.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")

    # Adjustment-event ledger: one row per OPEN / SCALE_UP / SCALE_DOWN /
    # LEVERAGE_ADJUST / FLIP / CLOSE event on a position. Idempotency via the
    # UNIQUE(trade_id, event_date, event_type) key — re-running the emitter
    # for the same date never duplicates events.
    con.execute("""
        CREATE TABLE IF NOT EXISTS trade_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            seq INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            event_date TEXT NOT NULL,
            qty_delta REAL DEFAULT 0,
            qty_after REAL,
            leverage_before REAL,
            leverage_after REAL,
            margin_delta_usdt REAL DEFAULT 0,
            size_usdt_after REAL,
            price REAL,
            fee_usdt REAL DEFAULT 0,
            realized_pnl_delta_usdt REAL DEFAULT 0,
            notes_json TEXT,
            UNIQUE(trade_id, seq),
            UNIQUE(trade_id, event_date, event_type)
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_adj_trade ON trade_adjustments(trade_id, seq)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_adj_date "
        "ON trade_adjustments(event_date, event_type)"
    )
    # AI_QUANT decision audit table — one row per daily LLM call (the
    # AI_QUANT sleeve). Persisted regardless of whether a trade is
    # ultimately opened, so we can see every decision the model made
    # including FLATs and errors. context_json may be truncated to keep
    # the row from blowing up; tool_calls_json is the full per-call
    # audit. trade_action is set by the sleeve after reconciliation
    # ('opened:SJ-1234' / 'closed:SJ-1234' / 'flipped:SJ-1234' / 'noop' /
    # 'error'). See strategies.sleeves.ai_quant.journal for the writer.
    con.execute("""
        CREATE TABLE IF NOT EXISTS ai_quant_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_utc INTEGER NOT NULL,
            decision_date TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            decided TEXT NOT NULL,
            conviction INTEGER,
            time_horizon_days INTEGER,
            key_drivers_json TEXT,
            exit_conditions TEXT,
            confidence_caveats TEXT,
            rationale_md TEXT,
            context_json TEXT,
            tool_calls_json TEXT,
            model_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            cost_usd REAL,
            turns INTEGER,
            trade_action TEXT,
            error TEXT,
            defer_until_utc INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_quant_variant_date "
        "ON ai_quant_decisions(variant_id, decision_date DESC)"
    )
    # Migrate existing DBs that pre-date the defer feature.
    cols = {r[1] for r in con.execute(
        "PRAGMA table_info(ai_quant_decisions)").fetchall()}
    if "defer_until_utc" not in cols:
        con.execute(
            "ALTER TABLE ai_quant_decisions ADD COLUMN defer_until_utc INTEGER"
        )
    # Crypto Fear & Greed index — daily time series from alternative.me.
    # Migrated from data/fear_greed.json on 2026-05-16 so daily lookups
    # join naturally with other date-keyed tables in prod.db (FOMC
    # observer, sleeve PnL, regime classifications) and per-row upserts
    # replace whole-file rewrites. See data.sources.sentiment.
    con.execute("""
        CREATE TABLE IF NOT EXISTS fear_greed_index (
            date TEXT PRIMARY KEY,
            value INTEGER NOT NULL,
            classification TEXT
        )
    """)
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
    try:
        row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        con.close()


def set_config(key: str, value: str) -> None:
    con = _con()
    try:
        con.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')",
            (key, value, value),
        )
        con.commit()
    finally:
        con.close()


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
