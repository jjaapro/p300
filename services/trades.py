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
from services import db

log = logging.getLogger("dashboard.trades")

# Standard round-trip taker fee on a single perp leg, in basis points.
# Covers exchange fees only; slippage + bid-ask spread is modeled
# separately via DEFAULT_SLIPPAGE_BP_RT below so the two cost sources
# can be tuned independently per sleeve.
DEFAULT_COST_BP_RT = 10.0
# CARRY pays fees on BOTH the spot leg and the perp leg, both sides — 4 taker
# fills × 5bp = 20bp per round-trip on the synthetic position.
CARRY_COST_PCT = 0.20

# Bid-ask spread + market-impact on a round-trip directional perp position,
# in basis points. AUDIT_2026_05_13 calibration: Binance BTC/USDT retail
# market-order fills cost ~3–5bp spread + 1–2bp impact at $1k–$20k notional;
# 5bp is the conservative mid of the 5–10bp/RT range. FOMC overrides this
# to 10bp (10× leverage at the announcement bar = elevated cost). JPLUS
# perp sleeves pass 0.0 to preserve pre-2026-05-13 behavior pending audit
# item #8 (EMA_BTC / ETH_DAILY zero-fee + zero-funding cleanup).
DEFAULT_SLIPPAGE_BP_RT = 5.0
# CARRY round-trip slippage on the synthetic spot+perp position. 4 fills ×
# ~1bp = 4bp; lower than directional because CARRY entries are limit-style
# at the spot/perp basis, not market orders.
CARRY_SLIPPAGE_PCT = 0.04

# Distant-future sentinel for "open-ended" trades (CARRY, ADX) — written to
# the exit_time column so the engine's close_due loop never matches them.
_NO_SCHEDULED_EXIT_ISO = "2099-12-31T00:00:00+00:00"


def _next_sj_id(con: sqlite3.Connection) -> str:
    """Mint the next sequential SJ-NNNN trade ID. Uses numeric MAX to avoid
    text-ordering overflow at SJ-10000+. Race-safe under SQLite's
    single-writer semantics; caller holds the connection through INSERT."""
    row = con.execute(
        "SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) FROM trades WHERE series='SJ'"
    ).fetchone()
    if row is None or row[0] is None:
        return "SJ-0001"
    return f"SJ-{row[0] + 1:04d}"


def get_open_trades(variant_id: str, strategy: str,
                    asset: str | None = None) -> list[dict]:
    """Return all open shadow trades for (variant_id, strategy[, asset]),
    newest first.

    The single-open invariant most sleeves observe doesn't change the contract
    — callers always sweep the full list on close paths so stray legacy opens
    (from prior-version code paths) get cleaned up instead of leaking.

    Used by ADX, CARRY (asset=None), CPR, PDO, THU_BEAR (asset filter).
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        if asset is None:
            rows = con.execute(
                "SELECT * FROM trades WHERE strategy_variant=? AND strategy=? "
                "AND status='open' ORDER BY actual_entry_time DESC",
                (variant_id, strategy),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM trades WHERE strategy_variant=? AND strategy=? "
                "AND asset=? AND status='open' ORDER BY actual_entry_time DESC",
                (variant_id, strategy, asset),
            ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def open_shadow_trade(*, variant: dict, sleeve_name: str,
                      asset: str, direction: str,
                      entry_price: float, allocation_pct: float,
                      leverage: float = 1.0,
                      reason: dict,
                      scheduled_exit_dt: datetime | None = None,
                      regime_value: str | None = None,
                      entry_dt: datetime | None = None) -> str:
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
      entry_dt           optional override for the open timestamp. Defaults
                         to ``clock.now_utc()``. Used by the J+ trade-emitter
                         to backdate an OPEN event to the historical entry
                         moment (e.g. R4 BTC opens at 06:00 UTC, not "now").

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

    now_iso = entry_dt.isoformat() if entry_dt is not None else clock.now_iso()
    exit_iso = (scheduled_exit_dt.isoformat() if scheduled_exit_dt is not None
                else _NO_SCHEDULED_EXIT_ISO)
    if regime_value is None:
        regime_value = reason.get("regime", "unknown")

    con = sqlite3.connect(str(db.DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes,
                current_qty, current_leverage, current_size_usdt,
                realized_pnl_usdt, avg_entry_price)
            VALUES (?, 'SJ', ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """, (tid, asset, direction.upper(), sleeve_name.upper(),
              regime_value, allocation_pct, leverage,
              now_iso, exit_iso, variant["id"], now_iso,
              entry_price, size_usdt, qty,
              json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str),
              qty, leverage, size_usdt, entry_price))
        # Implicit OPEN event in the adjustment ledger. Idempotency-safe:
        # a duplicate INSERT (e.g. retry after partial commit) becomes a no-op.
        from services.trade_adjustments import record_adjustment, EV_OPEN
        record_adjustment(
            trade_id=tid, event_type=EV_OPEN,
            event_time=now_iso,
            qty_delta=qty if direction.upper() == "LONG" else -qty,
            qty_after=qty,
            leverage_after=leverage,
            margin_delta_usdt=size_usdt,
            size_usdt_after=size_usdt,
            price=entry_price,
            fee_usdt=0.0,
            notes={"sleeve": sleeve_name.upper(), "reason": "open"},
            con=con,
        )
        con.commit()
    finally:
        con.close()
    return tid


@dataclass(frozen=True)
class CloseComponents:
    """All numeric pieces of a trade's close, before persistence."""
    price_pnl_usdt: float
    cost_usdt: float          # total execution cost = fee + slippage
    cost_pct: float           # total execution cost as % of notional
    fee_pct: float            # exchange-fee component only (cost_bp_rt / 100)
    slippage_pct: float       # spread+impact component only (slippage_bp_rt / 100)
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
                       slippage_bp_rt: float = DEFAULT_SLIPPAGE_BP_RT,
                       apply_funding: bool = True) -> CloseComponents:
    """Pure compute: price PnL (direction-aware), fees, slippage, optional funding.

    Direction is ``LONG`` or ``SHORT`` — the price-PnL sign convention is
    enforced here. Sleeves that hold delta-neutral positions (CARRY) use
    ``close_carry_trade`` instead, which has no price-PnL component.

    ``cost_bp_rt`` is the exchange-fee round-trip cost (taker fees only).
    ``slippage_bp_rt`` is the bid-ask spread + market-impact round-trip cost.
    Both are charged against ``size_usdt`` at close; total execution cost
    is (cost_bp_rt + slippage_bp_rt) bp of notional. The two are kept
    separate so the slippage assumption can be tuned per sleeve without
    perturbing the fee accounting.

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

    total_bp = cost_bp_rt + slippage_bp_rt
    cost_usdt = size_usdt * total_bp / 10000.0
    cost_pct = total_bp / 100.0  # bp -> percent
    fee_pct = cost_bp_rt / 100.0
    slippage_pct = slippage_bp_rt / 100.0

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
        fee_pct=fee_pct, slippage_pct=slippage_pct,
        funding_pct=funding_pct, funding_usdt=funding_usdt,
        pnl_usdt=pnl_usdt, pnl_pct=pnl_pct,
    )


def persist_close(trade_id: str, exit_price: float, exit_time_iso: str,
                  pnl_usdt: float, pnl_pct: float,
                  notes_suffix: str,
                  *,
                  fee_usdt: float = 0.0) -> sqlite3.Row | None:
    """UPDATE the trade row with close fields. Returns the row as it was
    BEFORE the close (so callers can log entry-side details), or None if
    no row matched the trade_id.

    Semantics:
      ``pnl_usdt``  — P&L realized on the CLOSE event itself (= price_pnl
                      on remaining qty against the avg basis, minus the
                      close-side fee). Computed by ``compute_perp_close``
                      using the trade's current_qty / avg_entry_price.
      Trade row's ``pnl_usdt``  is set to ``prior_realized + pnl_usdt`` —
                      the cumulative realized across all SCALE_DOWN events
                      plus this final close.
      Trade row's ``pnl_pct``   uses the supplied pct as-is (caller's
                      responsibility — typically pnl_usdt / size_usdt × 100).
      ``realized_pnl_delta_usdt`` on the CLOSE adjustment row records
                      just this event's realization (= pnl_usdt arg).
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT asset, direction, entry_price, qty, size_usdt, "
            "       actual_entry_time, current_qty, realized_pnl_usdt "
            "FROM trades WHERE id=? AND status='open'",
            (trade_id,),
        ).fetchone()
        if row is None:
            return None
        prior_realized = float(row["realized_pnl_usdt"] or 0.0)
        remaining_qty = float(row["current_qty"] if row["current_qty"] is not None
                              else row["qty"] or 0.0)
        close_realized_delta = pnl_usdt
        cumulative_pnl = prior_realized + pnl_usdt
        con.execute("""
            UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
                pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
                notes = COALESCE(notes,'') || ?,
                current_qty=0, realized_pnl_usdt=?
            WHERE id=? AND status='open'
        """, (exit_time_iso, exit_price, cumulative_pnl, pnl_pct, notes_suffix,
              cumulative_pnl, trade_id))
        from services.trade_adjustments import record_adjustment, EV_CLOSE
        # Sign of qty_delta is the closing-trade direction: closing a LONG
        # sells (-qty), closing a SHORT buys (+qty).
        direction = (row["direction"] or "").upper()
        qty_delta = -remaining_qty if direction == "LONG" else remaining_qty
        record_adjustment(
            trade_id=trade_id, event_type=EV_CLOSE,
            event_time=exit_time_iso,
            qty_delta=qty_delta, qty_after=0.0,
            margin_delta_usdt=-float(row["size_usdt"] or 0.0),
            size_usdt_after=0.0,
            price=exit_price,
            fee_usdt=fee_usdt,
            realized_pnl_delta_usdt=close_realized_delta,
            notes={"reason": (notes_suffix or "").strip()[:200]},
            con=con,
        )
        con.commit()
        return row
    finally:
        con.close()


def _format_perp_notes(sleeve_name: str, reason: str,
                       cost_bp_rt: float, slippage_bp_rt: float,
                       funding_pct: float | None) -> str:
    """Build the notes_suffix appended to the trade row on close."""
    suffix = (f"\n{sleeve_name.upper()}_EXIT: {reason}; "
              f"fees={cost_bp_rt:.0f}bp RT, slip={slippage_bp_rt:.0f}bp RT")
    if funding_pct is not None:
        suffix += f", funding={funding_pct:+.3f}%"
    return suffix


def close_perp_trade(trade_id: str, exit_price: float, reason: str,
                     sleeve_name: str, *,
                     cost_bp_rt: float = DEFAULT_COST_BP_RT,
                     slippage_bp_rt: float = DEFAULT_SLIPPAGE_BP_RT,
                     apply_funding: bool = True) -> None:
    """End-to-end close for ADX / THU_BEAR / FOMC / CPR / PDO.

    Reads the trade row, computes PnL via ``compute_perp_close``, persists,
    and logs a one-line close summary tagged with the sleeve_name.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT asset, direction, entry_price, "
            "       COALESCE(avg_entry_price, entry_price) AS basis_price, "
            "       COALESCE(current_qty, qty) AS qty, "
            "       COALESCE(current_size_usdt, size_usdt) AS size_usdt, "
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

    # basis_price is the running weighted-average for trades that have
    # scaled up; falls back to the immutable entry_price for the legacy
    # tactical-sleeve path.
    components = compute_perp_close(
        direction=row["direction"],
        entry_price=float(row["basis_price"]),
        exit_price=float(exit_price),
        qty=float(row["qty"]),
        size_usdt=float(row["size_usdt"]),
        asset=row["asset"],
        entry_dt=entry_dt,
        exit_dt=now,
        cost_bp_rt=cost_bp_rt,
        slippage_bp_rt=slippage_bp_rt,
        apply_funding=apply_funding,
    )

    notes = _format_perp_notes(
        sleeve_name, reason, cost_bp_rt, slippage_bp_rt,
        components.funding_pct if apply_funding else None,
    )
    persist_close(trade_id, exit_price, now.isoformat(),
                  components.pnl_usdt, components.pnl_pct, notes,
                  fee_usdt=components.cost_usdt)

    from services.trade_db import format_close_summary
    log.info(f"[{sleeve_name.lower()}] " + format_close_summary(
        trade_id=trade_id, asset=row["asset"], direction=row["direction"],
        entry_price=float(row["entry_price"]), exit_price=float(exit_price),
        pnl_pct=components.pnl_pct, pnl_usdt=components.pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now.isoformat(),
        reason=reason))


def close_carry_trade(trade_id: str, exit_price: float, reason: str,
                      *, cost_pct: float = CARRY_COST_PCT,
                      slippage_pct: float = CARRY_SLIPPAGE_PCT) -> None:
    """End-to-end close for CARRY (delta-neutral long-spot + short-perp).

    P&L is collected funding minus (round-trip fees + slippage) on both legs.
    Price PnL is assumed zero (delta-neutral). The short-perp leg's funding
    accrual is computed as ``services.funding.accrued_pct(BTC, entry, now, "SHORT")``.

    ``cost_pct`` covers exchange fees on the synthetic position (4 fills);
    ``slippage_pct`` covers bid-ask spread + impact. The two are tracked
    separately so the spread assumption can be tuned without touching fees.
    """
    con = sqlite3.connect(str(db.DASH_DB))
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

    total_cost_pct = cost_pct + slippage_pct
    net_pct = funding_pct - total_cost_pct
    pnl_usdt = float(row["size_usdt"]) * net_pct / 100.0

    notes = (f"\nCARRY_EXIT: {reason}; funding={funding_pct:.3f}% "
             f"(per-settlement), fees={cost_pct:.2f}%, slip={slippage_pct:.2f}%, "
             f"net={net_pct:.3f}%")
    fee_carry = float(row["size_usdt"] or 0.0) * total_cost_pct / 100.0
    persist_close(trade_id, exit_price, now.isoformat(),
                  pnl_usdt, net_pct, notes,
                  fee_usdt=fee_carry)

    from services.trade_db import format_close_summary
    log.info("[carry] " + format_close_summary(
        trade_id=trade_id, asset="BTC", direction="DELTA_NEUTRAL",
        entry_price=float(row["entry_price"]), exit_price=float(exit_price),
        pnl_pct=net_pct, pnl_usdt=pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now.isoformat(),
        reason=reason))


# ─── Position-adjustment helpers (Core J+ + future tactical use) ────────────


def apply_scale(trade_id: str, *, new_qty: float, price: float,
                fee_usdt: float = 0.0,
                event_time: str | None = None,
                event_date: str | None = None,
                notes: dict | None = None) -> bool:
    """Resize an open position to ``new_qty``. Books realized P&L on the
    closed slice if scaling down (price PnL on the qty delta). Returns True
    if the event was recorded; False on idempotent retry.

    Maintains the running weighted-average ``avg_entry_price`` on SCALE_UP:
    ``new_avg = (prev_qty × prev_avg + delta_qty × price) / new_qty``.
    SCALE_DOWN keeps avg_entry_price unchanged — the remaining qty keeps
    its prior basis; the closed slice realizes against that basis.

    Vol-target leverage is unchanged by this event — use
    ``apply_leverage_adjust`` for that.
    """
    from services.trade_adjustments import (record_adjustment,
                                              EV_SCALE_UP, EV_SCALE_DOWN)
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT direction, current_qty, current_size_usdt, "
            "       current_leverage, entry_price, qty, size_usdt, leverage, "
            "       avg_entry_price "
            "FROM trades WHERE id=? AND status='open'", (trade_id,),
        ).fetchone()
        if row is None:
            return False
        direction = (row["direction"] or "").upper()
        prev_qty = float(row["current_qty"] if row["current_qty"] is not None
                         else row["qty"] or 0.0)
        if abs(new_qty - prev_qty) < 1e-12:
            return False  # no-op
        qty_delta = new_qty - prev_qty
        # Basis: running avg if set, otherwise immutable entry_price.
        prev_avg = float(row["avg_entry_price"] if row["avg_entry_price"]
                         is not None else row["entry_price"] or 0.0)
        # On scale-down, book realized P&L on the closed slice against the
        # current avg-entry. Direction-adjusted.
        realized_delta = 0.0
        new_avg = prev_avg
        if qty_delta < 0 and prev_avg > 0:
            slice_qty = -qty_delta  # positive
            move = price - prev_avg
            if direction == "SHORT":
                move = -move
            realized_delta = slice_qty * move - fee_usdt
            # avg_entry stays the same on scale-down.
        elif qty_delta > 0 and new_qty > 0:
            # Weighted-average update on scale-up.
            new_avg = (prev_qty * prev_avg + qty_delta * price) / new_qty
        new_size = new_qty * price
        prev_size = float(row["current_size_usdt"] if row["current_size_usdt"]
                          is not None else row["size_usdt"] or 0.0)
        margin_delta = new_size - prev_size
        # Update mutable position state.
        con.execute(
            "UPDATE trades SET current_qty=?, current_size_usdt=?, "
            "    realized_pnl_usdt = COALESCE(realized_pnl_usdt, 0) + ?, "
            "    avg_entry_price = ? "
            "WHERE id=? AND status='open'",
            (new_qty, new_size, realized_delta if qty_delta < 0 else 0.0,
             new_avg, trade_id),
        )
        ev_type = EV_SCALE_UP if qty_delta > 0 else EV_SCALE_DOWN
        # qty_delta sign for the ledger reflects long/short conventions: a
        # LONG scale-up buys (+); a LONG scale-down sells (−); a SHORT
        # scale-up sells more (−); a SHORT scale-down buys back (+).
        ledger_qty_delta = qty_delta if direction == "LONG" else -qty_delta
        recorded = record_adjustment(
            trade_id=trade_id, event_type=ev_type,
            event_time=event_time, event_date=event_date,
            qty_delta=ledger_qty_delta, qty_after=new_qty,
            leverage_before=float(row["current_leverage"] or row["leverage"] or 1.0),
            leverage_after=float(row["current_leverage"] or row["leverage"] or 1.0),
            margin_delta_usdt=margin_delta, size_usdt_after=new_size,
            price=price, fee_usdt=fee_usdt,
            realized_pnl_delta_usdt=realized_delta,
            notes=notes, con=con,
        )
        if recorded:
            con.commit()
        return recorded
    finally:
        con.close()


def apply_leverage_adjust(trade_id: str, *, new_leverage: float,
                           price: float, fee_usdt: float = 0.0,
                           event_time: str | None = None,
                           event_date: str | None = None,
                           notes: dict | None = None) -> bool:
    """Change effective leverage on an open position without touching qty.
    Margin moves; notional stays. Returns True if recorded; False on
    idempotent retry or when leverage is unchanged.

    Mechanically: ``current_size_usdt`` represents the notional, which is
    ``current_qty × price``. Leverage doesn't change notional — but it does
    change the collateral required. We track the implied margin movement as
    ``margin_delta_usdt = notional × (1/old_lev − 1/new_lev)``.
    """
    from services.trade_adjustments import record_adjustment, EV_LEVERAGE_ADJUST
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT current_qty, current_size_usdt, current_leverage, "
            "       qty, size_usdt, leverage "
            "FROM trades WHERE id=? AND status='open'", (trade_id,),
        ).fetchone()
        if row is None:
            return False
        prev_lev = float(row["current_leverage"] if row["current_leverage"]
                         is not None else row["leverage"] or 1.0)
        if abs(new_leverage - prev_lev) < 1e-12:
            return False
        cur_qty = float(row["current_qty"] if row["current_qty"] is not None
                        else row["qty"] or 0.0)
        notional = cur_qty * price
        margin_old = notional / prev_lev if prev_lev > 0 else notional
        margin_new = notional / new_leverage if new_leverage > 0 else notional
        margin_delta = margin_new - margin_old  # +ve = adding collateral
        con.execute(
            "UPDATE trades SET current_leverage=?, current_size_usdt=? "
            "WHERE id=? AND status='open'",
            (new_leverage, notional, trade_id),
        )
        recorded = record_adjustment(
            trade_id=trade_id, event_type=EV_LEVERAGE_ADJUST,
            event_time=event_time, event_date=event_date,
            qty_delta=0.0, qty_after=cur_qty,
            leverage_before=prev_lev, leverage_after=new_leverage,
            margin_delta_usdt=margin_delta, size_usdt_after=notional,
            price=price, fee_usdt=fee_usdt,
            notes=notes, con=con,
        )
        if recorded:
            con.commit()
        return recorded
    finally:
        con.close()


def apply_flip(trade_id: str, *, new_direction: str, price: float,
               fee_usdt: float = 0.0,
               event_time: str | None = None,
               event_date: str | None = None,
               notes: dict | None = None) -> str | None:
    """Atomic close-and-reopen-opposite: marks the current trade closed and
    opens a new trade in the opposite direction at the same notional /
    leverage. Used by EMA_BTC on weekly cross.

    Writes a FLIP event on the OLD trade then an OPEN event on the NEW
    trade. ``parent_position_id`` on the new trade references the closed
    one. Returns the new trade_id, or None if the original was not open.
    """
    from services.trade_adjustments import record_adjustment, EV_FLIP, EV_OPEN
    new_direction = new_direction.upper()
    if new_direction not in ("LONG", "SHORT"):
        raise ValueError(f"new_direction must be LONG or SHORT, got {new_direction!r}")
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT asset, direction, strategy, regime, allocation_pct, "
            "  leverage, current_qty, current_size_usdt, current_leverage, "
            "  qty, size_usdt, entry_price, "
            "  COALESCE(avg_entry_price, entry_price) AS basis_price, "
            "  realized_pnl_usdt, "
            "  strategy_variant, exit_time, actual_entry_time "
            "FROM trades WHERE id=? AND status='open'", (trade_id,),
        ).fetchone()
        if row is None:
            return None
        old_direction = (row["direction"] or "").upper()
        if old_direction == new_direction:
            return None  # no-op flip
        cur_qty = float(row["current_qty"] if row["current_qty"] is not None
                        else row["qty"] or 0.0)
        cur_lev = float(row["current_leverage"] if row["current_leverage"]
                        is not None else row["leverage"] or 1.0)
        # Realized P&L on the closing leg, against the running weighted-avg
        # basis (so daily-rebalanced trades book the correct slice realized
        # on flip).
        basis_price = float(row["basis_price"] or 0.0)
        move = price - basis_price
        if old_direction == "SHORT":
            move = -move
        realized_close = cur_qty * move - fee_usdt / 2.0
        # Cumulative P&L = prior SCALE_DOWN realizations + this flip's close.
        prior_realized = float(row["realized_pnl_usdt"] or 0.0)
        cumulative_pnl = prior_realized + realized_close
        # Mark old trade closed.
        now_iso = event_time or clock.now_iso()
        ref_size = float(row["current_size_usdt"]
                         if row["current_size_usdt"] is not None
                         else row["size_usdt"] or 0.0)
        con.execute(
            "UPDATE trades SET status='closed', actual_exit_time=?, "
            "  exit_price=?, pnl_usdt=?, pnl_pct=?, "
            "  resolution='filled_flipped', current_qty=0, "
            "  realized_pnl_usdt=? "
            "WHERE id=? AND status='open'",
            (now_iso, price, cumulative_pnl,
             (cumulative_pnl / ref_size * 100.0) if ref_size else 0.0,
             cumulative_pnl, trade_id),
        )
        record_adjustment(
            trade_id=trade_id, event_type=EV_FLIP,
            event_time=now_iso, event_date=event_date,
            qty_delta=-cur_qty if old_direction == "LONG" else cur_qty,
            qty_after=0.0,
            leverage_before=cur_lev, leverage_after=cur_lev,
            margin_delta_usdt=-float(row["current_size_usdt"] or row["size_usdt"] or 0.0),
            size_usdt_after=0.0,
            price=price, fee_usdt=fee_usdt / 2.0,
            realized_pnl_delta_usdt=realized_close,
            notes=(notes or {}) | {"flip": "close_leg"},
            con=con,
        )
        # Mint new trade row in opposite direction with same notional/lev.
        new_size = cur_qty * price
        new_qty = new_size / price if price > 0 else 0.0
        new_tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy,
                regime, allocation_pct, leverage, entry_time, exit_time,
                status, execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes,
                current_qty, current_leverage, current_size_usdt,
                realized_pnl_usdt, parent_position_id, avg_entry_price)
            VALUES (?, 'SJ', ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (new_tid, row["asset"], new_direction, row["strategy"],
              row["regime"], row["allocation_pct"], cur_lev,
              now_iso, row["exit_time"] or _NO_SCHEDULED_EXIT_ISO,
              row["strategy_variant"], now_iso, price, new_size, new_qty,
              json.dumps([f"SHADOW-{new_tid}"]),
              json.dumps((notes or {}) | {"opened_via": "flip",
                                            "parent": trade_id}, default=str),
              new_qty, cur_lev, new_size, trade_id, price))
        record_adjustment(
            trade_id=new_tid, event_type=EV_OPEN,
            event_time=now_iso, event_date=event_date,
            qty_delta=new_qty if new_direction == "LONG" else -new_qty,
            qty_after=new_qty, leverage_after=cur_lev,
            margin_delta_usdt=new_size, size_usdt_after=new_size,
            price=price, fee_usdt=fee_usdt / 2.0,
            notes=(notes or {}) | {"opened_via": "flip", "parent": trade_id},
            con=con,
        )
        con.commit()
        return new_tid
    finally:
        con.close()
