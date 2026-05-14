"""Adjustment ledger for live positions.

Each position in the ``trades`` table can accumulate a sequence of events:

  OPEN              position opened (seq=0)
  SCALE_UP          notional grew (e.g. regime weight increased)
  SCALE_DOWN        notional shrank; books realized P&L on the closed slice
  LEVERAGE_ADJUST   margin added/removed; notional unchanged
  FLIP              atomic close-and-reopen-opposite-direction (EMA cross)
  CLOSE             position fully closed; books final realized P&L

Every event lands in ``trade_adjustments`` with a monotonic ``seq`` per
trade_id. Idempotency is enforced by ``UNIQUE(trade_id, event_date,
event_type)`` — re-running the J+ emitter for the same UTC day never
duplicates events.

This module is the single write surface for the adjustment ledger. The
``strategies.trades`` lifecycle helpers (open / scale / close) delegate
their event-recording side to functions here so the trades-table mutation
and the adjustment-row insert always happen in the same transaction.

Read API (``get_adjustments``, ``replay_position_state``) is provided so
the J+ trade-emitter can reconstruct in-memory position state from the DB
without any in-process caching.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from strategies.support import clock
from strategies.support import db

log = logging.getLogger("dashboard.trade_adjustments")

# Event-type constants. Use these instead of bare strings at call sites so
# typos surface as ImportError rather than silent UNIQUE-collisions.
EV_OPEN = "OPEN"
EV_SCALE_UP = "SCALE_UP"
EV_SCALE_DOWN = "SCALE_DOWN"
EV_LEVERAGE_ADJUST = "LEVERAGE_ADJUST"
EV_FLIP = "FLIP"
EV_CLOSE = "CLOSE"
ALL_EVENT_TYPES = (
    EV_OPEN, EV_SCALE_UP, EV_SCALE_DOWN, EV_LEVERAGE_ADJUST, EV_FLIP, EV_CLOSE,
)


@dataclass(frozen=True)
class AdjustmentEvent:
    """Read-side view of one row in trade_adjustments."""
    seq: int
    event_type: str
    event_time: str
    event_date: str
    qty_delta: float
    qty_after: float | None
    leverage_before: float | None
    leverage_after: float | None
    margin_delta_usdt: float
    size_usdt_after: float | None
    price: float | None
    fee_usdt: float
    realized_pnl_delta_usdt: float
    notes_json: str | None


def _next_seq(con: sqlite3.Connection, trade_id: str) -> int:
    row = con.execute(
        "SELECT MAX(seq) FROM trade_adjustments WHERE trade_id=?", (trade_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return 0
    return int(row[0]) + 1


def record_adjustment(
    *,
    trade_id: str,
    event_type: str,
    event_time: str | None = None,
    event_date: str | None = None,
    qty_delta: float = 0.0,
    qty_after: float | None = None,
    leverage_before: float | None = None,
    leverage_after: float | None = None,
    margin_delta_usdt: float = 0.0,
    size_usdt_after: float | None = None,
    price: float | None = None,
    fee_usdt: float = 0.0,
    realized_pnl_delta_usdt: float = 0.0,
    notes: dict | None = None,
    con: sqlite3.Connection | None = None,
) -> bool:
    """Insert one adjustment row. Returns True if inserted, False if a row
    already exists for the same (trade_id, event_date, event_type) — i.e.
    idempotent retry.

    If ``con`` is supplied the caller controls the transaction (used by the
    lifecycle helpers in ``strategies.trades`` so the adjustment lands atomic
    with the trades-table mutation). Otherwise opens / commits / closes its
    own connection.
    """
    if event_type not in ALL_EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}; "
                         f"expected one of {ALL_EVENT_TYPES}")

    if event_time is None:
        event_time = clock.now_iso()
    if event_date is None:
        # Normalize to UTC date — the idempotency key.
        try:
            event_date = datetime.fromisoformat(event_time).astimezone(
                timezone.utc).date().isoformat()
        except ValueError:
            event_date = clock.now_utc().date().isoformat()

    notes_json = json.dumps(notes, default=str) if notes else None

    own_con = con is None
    if own_con:
        con = sqlite3.connect(str(db.DASH_DB))
    try:
        seq = _next_seq(con, trade_id)
        try:
            con.execute(
                "INSERT INTO trade_adjustments "
                "(trade_id, seq, event_type, event_time, event_date, "
                " qty_delta, qty_after, leverage_before, leverage_after, "
                " margin_delta_usdt, size_usdt_after, price, fee_usdt, "
                " realized_pnl_delta_usdt, notes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trade_id, seq, event_type, event_time, event_date,
                 qty_delta, qty_after, leverage_before, leverage_after,
                 margin_delta_usdt, size_usdt_after, price, fee_usdt,
                 realized_pnl_delta_usdt, notes_json),
            )
            if own_con:
                con.commit()
            return True
        except sqlite3.IntegrityError as e:
            # UNIQUE(trade_id, event_date, event_type) hit — idempotent no-op.
            if "UNIQUE" in str(e):
                return False
            raise
    finally:
        if own_con:
            con.close()


def get_adjustments(trade_id: str) -> list[AdjustmentEvent]:
    """Return all adjustments for a trade in seq order (oldest first)."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT seq, event_type, event_time, event_date, qty_delta, "
            "qty_after, leverage_before, leverage_after, margin_delta_usdt, "
            "size_usdt_after, price, fee_usdt, realized_pnl_delta_usdt, "
            "notes_json "
            "FROM trade_adjustments WHERE trade_id=? ORDER BY seq",
            (trade_id,),
        ).fetchall()
    finally:
        con.close()
    return [AdjustmentEvent(**dict(r)) for r in rows]


def adjustments_for_date(event_date: str,
                         strategy_prefix: str | None = None,
                         variant_id: str | None = None
                         ) -> list[tuple[str, AdjustmentEvent, dict]]:
    """All adjustments whose event_date equals the given UTC date, optionally
    filtered to a strategy-name prefix (e.g. ``"JPLUS_"``) and a variant.

    Returns ``[(trade_id, AdjustmentEvent, trade_row_dict)]`` — the trade row
    is joined in so callers can compute notional / direction without an extra
    query per event. Used by the parity test and the daily-return derivation.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT a.trade_id, a.seq, a.event_type, a.event_time, "
            "  a.event_date, a.qty_delta, a.qty_after, a.leverage_before, "
            "  a.leverage_after, a.margin_delta_usdt, a.size_usdt_after, "
            "  a.price, a.fee_usdt, a.realized_pnl_delta_usdt, "
            "  a.notes_json, t.strategy, t.asset, t.direction, "
            "  t.strategy_variant, t.entry_price, t.qty, t.size_usdt, "
            "  t.actual_entry_time "
            "FROM trade_adjustments a JOIN trades t ON a.trade_id = t.id "
            "WHERE a.event_date = ?"
        )
        params: list = [event_date]
        if strategy_prefix is not None:
            sql += " AND t.strategy LIKE ?"
            params.append(f"{strategy_prefix}%")
        if variant_id is not None:
            sql += " AND t.strategy_variant = ?"
            params.append(variant_id)
        sql += " ORDER BY a.event_time, a.seq"
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()

    out: list[tuple[str, AdjustmentEvent, dict]] = []
    for r in rows:
        ev = AdjustmentEvent(
            seq=r["seq"], event_type=r["event_type"],
            event_time=r["event_time"], event_date=r["event_date"],
            qty_delta=r["qty_delta"], qty_after=r["qty_after"],
            leverage_before=r["leverage_before"],
            leverage_after=r["leverage_after"],
            margin_delta_usdt=r["margin_delta_usdt"],
            size_usdt_after=r["size_usdt_after"], price=r["price"],
            fee_usdt=r["fee_usdt"],
            realized_pnl_delta_usdt=r["realized_pnl_delta_usdt"],
            notes_json=r["notes_json"],
        )
        trade_meta = {
            "strategy": r["strategy"], "asset": r["asset"],
            "direction": r["direction"], "strategy_variant": r["strategy_variant"],
            "entry_price": r["entry_price"], "qty": r["qty"],
            "size_usdt": r["size_usdt"],
            "actual_entry_time": r["actual_entry_time"],
        }
        out.append((r["trade_id"], ev, trade_meta))
    return out


def daily_pnl_from_adjustments(event_date: str, variant_id: str,
                               strategy_prefix: str | None = None) -> dict:
    """Aggregate trade-event P&L for one UTC day. Returns:

      {
        "realized_pnl_usdt": float,   sum of realized_pnl_delta on closed/scaled-down events
        "fees_usdt":         float,   sum of fee_usdt across all events on this date
        "by_strategy":       {strategy: {"realized": x, "fees": y}}
      }

    Caller adds open-position MTM separately if needed (open positions
    contribute via current_qty × today's mark move; that's the simulator's
    domain, not the adjustment ledger's).
    """
    rows = adjustments_for_date(event_date, strategy_prefix, variant_id)
    realized = 0.0
    fees = 0.0
    by_strat: dict[str, dict[str, float]] = {}
    for _tid, ev, meta in rows:
        realized += float(ev.realized_pnl_delta_usdt or 0.0)
        fees += float(ev.fee_usdt or 0.0)
        s = meta["strategy"]
        if s not in by_strat:
            by_strat[s] = {"realized": 0.0, "fees": 0.0}
        by_strat[s]["realized"] += float(ev.realized_pnl_delta_usdt or 0.0)
        by_strat[s]["fees"] += float(ev.fee_usdt or 0.0)
    return {
        "realized_pnl_usdt": realized,
        "fees_usdt": fees,
        "by_strategy": by_strat,
    }
