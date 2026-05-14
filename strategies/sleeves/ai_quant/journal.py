"""Persistence layer for AI_QUANT decisions.

Writes to the ai_quant_decisions table in dashboard.db. Schema lives in
services.trade_db.init_db() but this module also has _ensure_schema() so
it works against test fixtures that haven't called init_db.

Single-row-per-(variant, day) is NOT enforced at the schema level — the
sleeve's idempotency gate uses get_today_decision() to check before
calling. This is a deliberate looseness so we can record an "ERROR" row
on a failed turn AND still let the sleeve retry on a later tick of the
same day.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from services import clock, db

log = logging.getLogger("p300.ai_quant.journal")

# Per-row context_json is truncated to this many characters so a stray
# huge bundle doesn't bloat the DB. Audit value of the bundle decays
# fast anyway — for forensic replay we keep the headline fields.
CONTEXT_JSON_MAX_CHARS = 32_000


def _ensure_schema(con: sqlite3.Connection) -> None:
    """Create ai_quant_decisions if missing (idempotent). Migrates the table
    by adding any columns introduced after the initial schema — currently
    ``defer_until_utc``, which carries the unix-ts that a deferred decision
    is scheduled to re-fire at (NULL for normal decisions)."""
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


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else (s[:n - 20] + "…[truncated]")


def save_decision(
    *,
    variant_id: str,
    asset: str,
    decision_result: Any,  # strategies.sleeves.ai_quant.decision.DecisionResult
    context_bundle: dict | None = None,
    trade_action: str = "noop",
    defer_until_utc: int | None = None,
) -> int:
    """Persist one decision call. Returns the new row's autoincrement id.

    `decision_result` is a DecisionResult dataclass; we read .decision /
    .deferred / .error / .turns / .usage / .cost_usd / .model_id /
    .tool_calls. The row's ``decided`` field is one of:
      - "LONG" / "SHORT" / "FLAT" — model called submit_decision
      - "DEFER"                   — model called defer_decision; the
        ``defer_until_utc`` argument carries the absolute unix-ts the
        runtime will re-fire at. ``waiting_for`` and ``reasoning`` are
        stuffed into ``exit_conditions`` and ``rationale_md`` so they
        appear in the markdown archive too.
      - "ERROR"                   — model errored or refused to submit.
    """
    now_utc = clock.now_utc()
    decision_ts = int(now_utc.timestamp())
    decision_date = now_utc.date().isoformat()

    deferred = getattr(decision_result, "deferred", None)
    if deferred is not None:
        decided = "DEFER"
        conviction = None
        horizon = None
        key_drivers = [
            f"waiting_for: {deferred.get('waiting_for', '')}",
            f"retry_in_hours: {deferred.get('retry_in_hours')}",
        ]
        exit_conditions = (f"Re-fire scheduled at unix-ts "
                            f"{defer_until_utc} UTC.")
        caveats = deferred.get("waiting_for")
        rationale = deferred.get("reasoning")
    else:
        payload = decision_result.decision or {}
        decided = payload.get("direction") or "ERROR"
        conviction = payload.get("conviction_0_100")
        horizon = payload.get("time_horizon_days")
        key_drivers = payload.get("key_drivers") or []
        exit_conditions = payload.get("exit_conditions")
        caveats = payload.get("confidence_caveats")
        rationale = payload.get("rationale_md")

    usage = decision_result.usage or {}
    ctx_json = (json.dumps(context_bundle, default=str)
                if context_bundle is not None else None)
    tool_calls_json = json.dumps(decision_result.tool_calls, default=str)

    con = sqlite3.connect(str(db.DASH_DB))
    try:
        _ensure_schema(con)
        cur = con.execute(
            """
            INSERT INTO ai_quant_decisions (
                decision_utc, decision_date, variant_id, asset,
                decided, conviction, time_horizon_days,
                key_drivers_json, exit_conditions, confidence_caveats,
                rationale_md, context_json, tool_calls_json,
                model_id, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens,
                cost_usd, turns, trade_action, error, defer_until_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_ts, decision_date, variant_id, asset.upper(),
                decided, conviction, horizon,
                json.dumps(key_drivers, default=str),
                exit_conditions, caveats, rationale,
                _truncate(ctx_json, CONTEXT_JSON_MAX_CHARS),
                tool_calls_json,
                decision_result.model_id,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                usage.get("cache_read_input_tokens"),
                usage.get("cache_creation_input_tokens"),
                decision_result.cost_usd,
                decision_result.turns,
                trade_action,
                decision_result.error,
                defer_until_utc,
            ),
        )
        con.commit()
        row_id = cur.lastrowid
    finally:
        con.close()

    # Best-effort markdown archive — the DB row is the source of truth;
    # this file is a human-browsable mirror for monitoring decision
    # quality. A failure here must never poison the durable DB row.
    try:
        from . import archive
        archive.write_archive_md(row_id=row_id, row={
            "id": row_id,
            "decision_utc": decision_ts,
            "decision_date": decision_date,
            "variant_id": variant_id,
            "asset": asset.upper(),
            "decided": decided,
            "conviction": conviction,
            "time_horizon_days": horizon,
            "key_drivers_json": json.dumps(key_drivers, default=str),
            "exit_conditions": exit_conditions,
            "confidence_caveats": caveats,
            "rationale_md": rationale,
            "tool_calls_json": tool_calls_json,
            "model_id": decision_result.model_id,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "cache_write_tokens": usage.get("cache_creation_input_tokens"),
            "cost_usd": decision_result.cost_usd,
            "turns": decision_result.turns,
            "trade_action": trade_action,
            "error": decision_result.error,
            "defer_until_utc": defer_until_utc,
        })
    except Exception:
        log.exception("AI_QUANT archive write failed for row %s", row_id)

    return row_id


def count_today_defers(variant_id: str) -> int:
    """Number of DEFER rows for ``variant_id`` on today's UTC date. Used by
    the chain-cap gate (3 defers/day max before the defer tool is stripped
    from the next API call)."""
    today = clock.now_utc().date().isoformat()
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT COUNT(*) FROM ai_quant_decisions "
            "WHERE variant_id = ? AND decision_date = ? AND decided = 'DEFER'",
            (variant_id, today),
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def get_today_decision(variant_id: str) -> dict | None:
    """Return the most recent decision row for `variant_id` on today's
    UTC date, or None if the sleeve hasn't fired yet today.

    Used by the sleeve's idempotency gate. We return ERROR rows too —
    a failed call still counts as "fired today" so we don't burn API
    cost retrying every minute on a transient outage. The orchestrator
    can decide separately whether to allow a same-day retry.
    """
    today = clock.now_utc().date().isoformat()
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT * FROM ai_quant_decisions "
            "WHERE variant_id = ? AND decision_date = ? "
            "ORDER BY decision_utc DESC LIMIT 1",
            (variant_id, today),
        ).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def get_today_cost_usd(variant_id: str) -> float:
    """Sum of cost_usd for all decision rows on today's UTC date.
    Used by the cost-cap gate before the sleeve calls the API."""
    today = clock.now_utc().date().isoformat()
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_quant_decisions "
            "WHERE variant_id = ? AND decision_date = ?",
            (variant_id, today),
        ).fetchone()
    finally:
        con.close()
    return float(row[0]) if row else 0.0


def get_recent_decisions(variant_id: str, days: int = 14) -> list[dict]:
    """Most-recent-first list of the last `days` of decisions for the
    variant. Future use: feeding decision history into the context
    bundle so the LLM can see what it decided yesterday."""
    cutoff = int((clock.now_utc().timestamp())) - days * 86400
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT id, decision_utc, decision_date, decided, conviction, "
            "       time_horizon_days, trade_action, cost_usd "
            "FROM ai_quant_decisions WHERE variant_id = ? AND decision_utc >= ? "
            "ORDER BY decision_utc DESC",
            (variant_id, cutoff),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]
