"""Shared plumbing for single-strategy bots, the feed daemon, and monitor.py.

Part of the 2026-07 bot-extraction architecture
(studies/material/plans/bot_extraction_plan.md): each strategy runs as its
own process against the shared market-data platform (prod.db). This module
is the whole "platform library" — deliberately one flat file:

  - freshness contracts + checks   every table a bot reads has a max age;
                                   bots refuse to evaluate on stale inputs
                                   (loud degradation, never a silent NaN
                                   gate-block — the CHENTO/okx lesson)
  - bot_heartbeats table           every process upserts a row per tick;
                                   monitor.py alerts on stale rows
  - WAL mode                       multi-process safety (feed + N bots +
                                   monitor all touch prod.db)
  - ensure_bot_variant             one variants row per bot = its ledger
                                   scope now, its sub-account at go-live
  - close_due_trades               scheduled-exit backstop (defense in
                                   depth behind each sleeve's own sweep)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from strategies.support import clock, db

log = logging.getLogger("botlib")

# ─── Freshness contracts ──────────────────────────────────────────────────────
# table -> (timestamp column, multiplier to seconds, max age in seconds).
# Ages are measured latest-row vs wall clock. Limits leave headroom over the
# natural cadence (a 15m table is stale at 45m = 3 missed bars).

FRESHNESS_CONTRACTS: dict[str, tuple[str, float, int]] = {
    "cd_futures_15m":      ("timestamp", 1.0,   45 * 60),
    "cd_spot_15m":         ("timestamp", 1.0,   45 * 60),
    "cd_futures_ohlcv":    ("timestamp", 1.0,   2 * 3600 + 900),
    "cd_spot_binance":     ("timestamp", 1.0,   2 * 3600 + 900),
    "btc_1m":              ("open_time", 0.001, 10 * 60),
    "eth_1m":              ("open_time", 0.001, 10 * 60),
    "okx_perp_1h":         ("timestamp", 1.0,   3 * 3600),
    "cd_open_interest":    ("timestamp", 1.0,   3 * 3600),
    "ca_long_short_ratio": ("timestamp", 1.0,   26 * 3600),
    "cd_funding_rate":     ("timestamp", 1.0,   9 * 3600),
}


def latest_age_s(table: str, con: sqlite3.Connection | None = None) -> float | None:
    """Age in seconds of the newest row in `table`, or None if the table is
    missing/empty. Uses the contract's column/unit spec."""
    ts_col, mult, _ = FRESHNESS_CONTRACTS[table]
    own = con is None
    if own:
        con = sqlite3.connect(str(db.PROD_DB))
    try:
        try:
            row = con.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None or row[0] is None:
            return None
        latest_s = float(row[0]) * mult
        return clock.now_utc().timestamp() - latest_s
    finally:
        if own:
            con.close()


def stale_tables(tables: list[str] | None = None) -> dict[str, float | None]:
    """Contract breaches among `tables` (default: every contracted table).
    Returns {table: age_seconds_or_None}; empty dict means all fresh."""
    tables = list(FRESHNESS_CONTRACTS) if tables is None else tables
    out: dict[str, float | None] = {}
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        for t in tables:
            age = latest_age_s(t, con)
            limit = FRESHNESS_CONTRACTS[t][2]
            if age is None or age > limit:
                out[t] = age
    finally:
        con.close()
    return out


# ─── Multi-process safety ─────────────────────────────────────────────────────

def ensure_wal() -> None:
    """Switch prod.db to WAL journal mode (persistent, idempotent). Required
    now that feed daemon + bots + monitor are separate processes sharing the
    file; WAL lets readers proceed during writes."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if mode.lower() != "wal":
            log.warning(f"journal_mode is {mode!r}, expected WAL")
    finally:
        con.close()


# ─── Heartbeats ───────────────────────────────────────────────────────────────

def init_heartbeat_schema() -> None:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bot_heartbeats (
                name            TEXT PRIMARY KEY,
                last_tick_utc   TEXT NOT NULL,
                last_eval_utc   TEXT,
                last_signal_utc TEXT,
                open_trades     INTEGER,
                interval_s      INTEGER,
                status          TEXT NOT NULL DEFAULT 'ok',
                note            TEXT
            )
        """)
        con.commit()
    finally:
        con.close()


def heartbeat(name: str, *, status: str = "ok", note: str = "",
              interval_s: int | None = None,
              last_eval_utc: str | None = None,
              last_signal_utc: str | None = None,
              open_trades: int | None = None) -> None:
    """Upsert this process's heartbeat row. `last_tick_utc` is always set to
    now; the optional fields keep their previous value when passed None."""
    now_iso = clock.now_utc().isoformat()
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        con.execute("""
            INSERT INTO bot_heartbeats
                (name, last_tick_utc, last_eval_utc, last_signal_utc,
                 open_trades, interval_s, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_tick_utc   = excluded.last_tick_utc,
                last_eval_utc   = COALESCE(excluded.last_eval_utc,   bot_heartbeats.last_eval_utc),
                last_signal_utc = COALESCE(excluded.last_signal_utc, bot_heartbeats.last_signal_utc),
                open_trades     = COALESCE(excluded.open_trades,     bot_heartbeats.open_trades),
                interval_s      = COALESCE(excluded.interval_s,      bot_heartbeats.interval_s),
                status          = excluded.status,
                note            = excluded.note
        """, (name, now_iso, last_eval_utc, last_signal_utc,
              open_trades, interval_s, status, note))
        con.commit()
    finally:
        con.close()


def get_heartbeats() -> list[dict]:
    con = sqlite3.connect(str(db.PROD_DB))
    con.row_factory = sqlite3.Row
    try:
        try:
            rows = con.execute(
                "SELECT * FROM bot_heartbeats ORDER BY name").fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]
    finally:
        con.close()


# ─── Bot variant registration ─────────────────────────────────────────────────

def ensure_bot_variant(variant_id: str, *, short_name: str,
                       capital_usdt: float, bot_name: str,
                       notes: str = "") -> dict:
    """Idempotently register the bot's variants row and return it as a dict.

    One variant per bot = the bot's ledger scope in the shared trades table
    today, and its 1:1 exchange sub-account at go-live. `composition` is
    explicitly empty so the legacy orchestrator (if ever started) no-ops on
    this variant instead of dispatching sleeves into it.

    `kind` must satisfy the variants CHECK constraint
    ('full_portfolio'|'signal_overlay') — a single-strategy bot IS a full
    portfolio of one strategy, so 'full_portfolio' with spec.bot set.
    """
    from strategies.support import variant_registry
    variant_registry.init_schema()
    v = variant_registry.get_variant(variant_id)
    if v is None:
        variant_registry.register_variant(
            variant_id=variant_id,
            short_name=short_name,
            kind="full_portfolio",
            version="1.0",
            spec={"bot": bot_name, "composition": [],
                  "architecture": "single_strategy_bot"},
            status="paper",
            capital_usdt=capital_usdt,
            notes=notes or f"Single-strategy bot ({bot_name}). "
                            f"Extraction plan 2026-07: one bot = one variant "
                            f"= one future sub-account.",
            actor="bot",
        )
        v = variant_registry.get_variant(variant_id)
        log.info(f"registered bot variant {variant_id}")
    return v


# ─── Scheduled-exit backstop ──────────────────────────────────────────────────

def close_due_trades(variant_id: str,
                     now_utc: datetime | None = None) -> list[str]:
    """Close this variant's open paper trades whose exit_time has passed.

    Defense-in-depth behind the sleeve's own sweep (which normally closes
    stop/target/time-stop first with better prices). Ports the semantics of
    orchestrator._close_due_paper_trades scoped to one variant.
    """
    from strategies import trades
    from strategies.support.price_feed import get_current_price

    now_utc = now_utc or clock.now_utc()
    con = sqlite3.connect(str(db.PROD_DB))
    con.row_factory = sqlite3.Row
    try:
        opens = con.execute(
            "SELECT id, asset, exit_time, strategy FROM trades "
            "WHERE strategy_variant = ? AND execution_mode = 'paper' "
            "  AND status = 'open'", (variant_id,)
        ).fetchall()
    finally:
        con.close()

    closed: list[str] = []
    for t in opens:
        exit_time = t["exit_time"]
        if not exit_time:
            continue
        try:
            exit_dt = datetime.fromisoformat(exit_time)
            if exit_dt.tzinfo is None:
                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now_utc < exit_dt:
            continue
        price = get_current_price(t["asset"])
        if price is None:
            log.warning(f"[backstop] no price for {t['id']} {t['asset']} — skip")
            continue
        trades.close_perp_trade(t["id"], price, "scheduled_exit",
                                sleeve_name=t["strategy"])
        closed.append(t["id"])
        log.info(f"[backstop] closed overdue {t['id']} ({t['strategy']})")
    return closed


def size_intent_fixed_r(intent, capital: float, *, risk_pct: float,
                        notional_max_x: float):
    """Fixed-R sizing shared by all bots: risk `risk_pct`% of capital over
    the stop distance the sleeve itself computed into intent.reason
    (`_entry_price` / `_stop_price`).

        notional = capital × risk_pct% / stop_pct   (≤ notional_max_x cap)

    open_paper_trade sizes as capital × alloc% × leverage, so alloc is
    pinned to 100% and the notional expressed through leverage. R-space
    results are unchanged by this; only $ per R changes. Returns
    (resized_intent, {stop_pct, notional, at_cap}).
    """
    import dataclasses
    reason = intent.reason or {}
    entry = float(reason["_entry_price"])
    stop = float(reason["_stop_price"])
    stop_pct = abs(entry - stop) / entry
    if stop_pct <= 0:
        raise ValueError(
            f"non-positive stop distance (entry={entry}, stop={stop})")
    notional = capital * (risk_pct / 100.0) / stop_pct
    capped = min(notional, notional_max_x * capital)
    resized = dataclasses.replace(
        intent, allocation_pct=100.0, leverage=capped / capital)
    return resized, {"stop_pct": stop_pct, "notional": capped,
                     "at_cap": capped < notional}


def count_open_trades(variant_id: str) -> int:
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy_variant = ? "
            "AND execution_mode = 'paper' AND status = 'open'",
            (variant_id,),
        ).fetchone()[0]
    finally:
        con.close()
