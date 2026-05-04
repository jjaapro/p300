"""services.trades — single source of truth for shadow-trade close mechanics.

Replaces 6 near-identical ``_close_X_shadow`` implementations across the
sleeve services. Each implementation read the trade row, computed price PnL
direction-conditionally, applied fees, optionally applied perp funding,
wrote the close, and logged a summary — same 7 steps, six copies.

Architecture:
  compute_perp_close(...)  pure-function math: takes prices/qty/dir/funding
                            inputs, returns a ``CloseComponents`` dataclass.
                            No I/O. Easy to unit-test.
  persist_close(...)       UPDATE trades row with close fields. Returns the
                            row that was closed (for caller logging).
  close_perp_trade(...)    end-to-end close for a directional perp position
                            (LONG / SHORT). Reads -> computes -> persists ->
                            logs. ADX, THU_BEAR, FOMC, CPR, PDO use this.
  close_carry_trade(...)   end-to-end close for a delta-neutral CARRY trade.
                            P&L = funding collected − round-trip fees, no
                            price-PnL component.

Each sleeve's legacy ``_close_X_shadow`` is a thin wrapper that just sets
the sleeve-name parameter (kept so backtest_runner._load_close_fn doesn't
need to change). The bug-fix surface for every shadow close is now this
one module.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services import clock

log = logging.getLogger("dashboard.trades")

DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"

# Standard round-trip taker fee on a single perp leg, in basis points.
DEFAULT_COST_BP_RT = 10.0
# CARRY pays fees on BOTH the spot leg and the perp leg, both sides — 4 taker
# fills × 5bp = 20bp per round-trip on the synthetic position.
CARRY_COST_PCT = 0.20

# Distant-future sentinel for "open-ended" trades (CARRY, ADX) — written to
# the exit_time column so the engine's close_due loop never matches them.
_NO_SCHEDULED_EXIT_ISO = "2099-12-31T00:00:00+00:00"


def _next_sj_id(con: sqlite3.Connection) -> str:
    """Mint the next sequential SJ-NNNN trade ID. Looks at MAX(id)+1 in the
    SJ series. Race-safe under SQLite's single-writer semantics; caller is
    expected to hold the connection through the subsequent INSERT."""
    row = con.execute(
        "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "SJ-0001"
    return f"SJ-{int(row[0].split('-')[1]) + 1:04d}"


def open_shadow_trade(*, variant: dict, sleeve_name: str,
                      asset: str, direction: str,
                      entry_price: float, allocation_pct: float,
                      leverage: float = 1.0,
                      reason: dict,
                      scheduled_exit_dt: datetime | None = None,
                      regime_value: str | None = None) -> str:
    """Insert a new shadow trade row and return its trade ID.

    Centralizes the per-sleeve open path: capital lookup, size_usdt math,
    qty math, ID mint, INSERT — previously duplicated 6× across services.

    Parameters that vary per sleeve:
      sleeve_name        goes into the trades.strategy column (uppercased)
      asset              "BTC" / "ETH" — written to trades.asset
      direction          "LONG" / "SHORT" — written to trades.direction
      scheduled_exit_dt  if provided, written to trades.exit_time so the
                         engine's close_due loop has a fallback close time;
                         None means open-ended (signal-based exit only).
      regime_value       what to write to the trades.regime column. Defaults
                         to ``reason.get("regime", "unknown")``. FOMC uses
                         ``reason.get("phase", ...)`` so passes it explicitly.
      reason             arbitrary dict — serialized into trades.notes for
                         post-hoc inspection.

    Sizing:
      capital = variant.capital_usdt or paper_account_usdt config or $10k
      size_usdt = capital × (allocation_pct / 100) × leverage
      qty = size_usdt / entry_price  (clamped to 0 if entry_price <= 0)
    """
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0.0

    now_iso = clock.now_utc().isoformat()
    exit_iso = (scheduled_exit_dt.isoformat() if scheduled_exit_dt is not None
                else _NO_SCHEDULED_EXIT_ISO)
    if regime_value is None:
        regime_value = reason.get("regime", "unknown")

    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, direction.upper(), sleeve_name.upper(),
              regime_value, allocation_pct, leverage,
              now_iso, exit_iso, variant["id"], now_iso,
              entry_price, size_usdt, qty,
              json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


@dataclass(frozen=True)
class CloseComponents:
    """All numeric pieces of a trade's close, before persistence."""
    price_pnl_usdt: float
    cost_usdt: float
    cost_pct: float           # round-trip fee as % of notional
    funding_pct: float        # accrued funding as % of notional, signed for direction
    funding_usdt: float
    pnl_usdt: float           # net = price_pnl − cost + funding
    pnl_pct: float


def compute_perp_close(*, direction: str,
                       entry_price: float, exit_price: float,
                       qty: float, size_usdt: float,
                       asset: str,
                       entry_dt: datetime, exit_dt: datetime,
                       cost_bp_rt: float = DEFAULT_COST_BP_RT,
                       apply_funding: bool = True) -> CloseComponents:
    """Pure compute: price PnL (direction-aware), fees, optional funding.

    Direction is ``LONG`` or ``SHORT`` — the price-PnL sign convention is
    enforced here. Sleeves that hold delta-neutral positions (CARRY) use
    ``close_carry_trade`` instead, which has no price-PnL component.

    ``apply_funding`` should be False for sleeves whose holds are short
    enough that funding is negligible (CPR, PDO are intraday).
    """
    direction = direction.upper()
    if direction == "LONG":
        price_pnl = (exit_price - entry_price) * qty
    elif direction == "SHORT":
        price_pnl = (entry_price - exit_price) * qty
    else:
        raise ValueError(
            f"compute_perp_close: direction must be LONG or SHORT, got {direction!r}. "
            f"Use close_carry_trade for delta-neutral positions."
        )

    cost_usdt = size_usdt * cost_bp_rt / 10000.0
    cost_pct = cost_bp_rt / 100.0  # bp -> percent

    funding_pct = 0.0
    if apply_funding:
        try:
            from services import funding as _funding
            funding_pct = _funding.accrued_pct(asset, entry_dt, exit_dt, direction)
        except (TypeError, ValueError):
            funding_pct = 0.0
    funding_usdt = size_usdt * funding_pct / 100.0

    pnl_usdt = price_pnl - cost_usdt + funding_usdt
    pnl_pct = (pnl_usdt / size_usdt * 100.0) if size_usdt > 0 else 0.0

    return CloseComponents(
        price_pnl_usdt=price_pnl,
        cost_usdt=cost_usdt, cost_pct=cost_pct,
        funding_pct=funding_pct, funding_usdt=funding_usdt,
        pnl_usdt=pnl_usdt, pnl_pct=pnl_pct,
    )


def persist_close(trade_id: str, exit_price: float, exit_time_iso: str,
                  pnl_usdt: float, pnl_pct: float,
                  notes_suffix: str) -> sqlite3.Row | None:
    """UPDATE the trade row with close fields. Returns the row as it was
    BEFORE the close (so callers can log entry-side details), or None if
    no row matched the trade_id.
    """
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT asset, direction, entry_price, qty, size_usdt, "
            "       actual_entry_time FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        if row is None:
            return None
        con.execute("""
            UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
                pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
                notes = COALESCE(notes,'') || ?
            WHERE id=?
        """, (exit_time_iso, exit_price, pnl_usdt, pnl_pct, notes_suffix, trade_id))
        con.commit()
        return row
    finally:
        con.close()


def _format_perp_notes(sleeve_name: str, reason: str,
                       cost_bp_rt: float, funding_pct: float | None) -> str:
    """Build the notes_suffix appended to the trade row on close."""
    suffix = f"\n{sleeve_name.upper()}_EXIT: {reason}; fees={cost_bp_rt:.0f}bp RT"
    if funding_pct is not None:
        suffix += f", funding={funding_pct:+.3f}%"
    return suffix


def close_perp_trade(trade_id: str, exit_price: float, reason: str,
                     sleeve_name: str, *,
                     cost_bp_rt: float = DEFAULT_COST_BP_RT,
                     apply_funding: bool = True) -> None:
    """End-to-end close for ADX / THU_BEAR / FOMC / CPR / PDO.

    Reads the trade row, computes PnL via ``compute_perp_close``, persists,
    and logs a one-line close summary tagged with the sleeve_name.
    """
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT asset, direction, entry_price, qty, size_usdt, "
            "       actual_entry_time FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return

    now = clock.now_utc()
    entry_dt = datetime.fromisoformat(row["actual_entry_time"])
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)

    components = compute_perp_close(
        direction=row["direction"],
        entry_price=float(row["entry_price"]),
        exit_price=float(exit_price),
        qty=float(row["qty"]),
        size_usdt=float(row["size_usdt"]),
        asset=row["asset"],
        entry_dt=entry_dt,
        exit_dt=now,
        cost_bp_rt=cost_bp_rt,
        apply_funding=apply_funding,
    )

    notes = _format_perp_notes(
        sleeve_name, reason, cost_bp_rt,
        components.funding_pct if apply_funding else None,
    )
    persist_close(trade_id, exit_price, now.isoformat(),
                  components.pnl_usdt, components.pnl_pct, notes)

    from services.trade_db import format_close_summary
    log.info(f"[{sleeve_name.lower()}] " + format_close_summary(
        trade_id=trade_id, asset=row["asset"], direction=row["direction"],
        entry_price=float(row["entry_price"]), exit_price=float(exit_price),
        pnl_pct=components.pnl_pct, pnl_usdt=components.pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now.isoformat(),
        reason=reason))


def close_carry_trade(trade_id: str, exit_price: float, reason: str,
                      *, cost_pct: float = CARRY_COST_PCT) -> None:
    """End-to-end close for CARRY (delta-neutral long-spot + short-perp).

    P&L is collected funding minus round-trip fees on both legs. Price PnL is
    assumed zero (delta-neutral). The short-perp leg's funding accrual is
    computed as ``services.funding.accrued_pct(BTC, entry, now, "SHORT")``.
    """
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT entry_price, qty, size_usdt, actual_entry_time "
            "FROM trades WHERE id=?", (trade_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return

    now = clock.now_utc()
    entry_dt = datetime.fromisoformat(row["actual_entry_time"])
    if entry_dt.tzinfo is None:
        entry_dt = entry_dt.replace(tzinfo=timezone.utc)

    try:
        from services import funding as _funding
        funding_pct = _funding.accrued_pct("BTC", entry_dt, now, "SHORT")
    except (TypeError, ValueError):
        funding_pct = 0.0

    net_pct = funding_pct - cost_pct
    pnl_usdt = float(row["size_usdt"]) * net_pct / 100.0

    notes = (f"\nCARRY_EXIT: {reason}; funding={funding_pct:.3f}% "
             f"(per-settlement), fees={cost_pct:.2f}%, net={net_pct:.3f}%")
    persist_close(trade_id, exit_price, now.isoformat(),
                  pnl_usdt, net_pct, notes)

    from services.trade_db import format_close_summary
    log.info("[carry] " + format_close_summary(
        trade_id=trade_id, asset="BTC", direction="DELTA_NEUTRAL",
        entry_price=float(row["entry_price"]), exit_price=float(exit_price),
        pnl_pct=net_pct, pnl_usdt=pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now.isoformat(),
        reason=reason))
