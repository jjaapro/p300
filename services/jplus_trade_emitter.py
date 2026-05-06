"""Retrospective Core J+ trade-emitter — offline-period gap-filler.

ROLE POST LIVE-EXECUTION REFACTOR: this module is no longer the primary
trade-emit path. The four live handlers in ``services/jplus_live.py``
own real-time entry / exit / scale / leverage-adjust / flip emission for
the Core sub-sleeves at the actual calendar/signal moment. This module
remains as the offline-period BACKFILL: ``emit_catchup`` runs at bot
startup (via ``run.py:_catchup_core_trade_emit``) to fill any historical
dates whose live handlers were never called because the bot was offline.

Both paths land in the same ``trades`` and ``trade_adjustments`` tables;
idempotency via the ``UNIQUE(trade_id, event_date, event_type)``
constraint on the adjustment ledger makes the two paths safe to coexist
— if a live handler already wrote today's events, ``emit_for_date(today)``
is a no-op when called by the catchup.

Public API:
  emit_for_date(variant, date_iso, sim_record, prev_state=None,
                sim_record_yesterday=None) -> PositionState
  get_position_state(variant_id) -> PositionState
  emit_catchup(variant, end_date_iso) -> dict

Per-sub-sleeve coverage:
  - R4_BTC: OPEN at d 06:00 UTC + CLOSE at d 18:00 UTC on Mon/Wed wk1-2.
  - R4_ETH: OPEN at d-1 (Tue) 20:00 UTC + CLOSE at d (Wed) 20:00 UTC on
    Tue→Wed wk1-2. The OPEN event is written when emit is called for the
    Wed (its event_date = the Tue), so the trade is keyed in the DB as
    soon as Wed's emit runs.
  - EMA_BTC, ETH_DAILY: continuous positions with daily SCALE /
    LEVERAGE_ADJUST / FLIP events.

Idempotency: every event lands via ``record_adjustment`` whose
UNIQUE(trade_id, event_date, event_type) constraint makes
``emit_for_date(d)`` safe to call any number of times for the same ``d``.

Look-ahead safety: the emitter only reads bars / sim values for date ≤ d.
All hourly-price queries are clock-bounded via the same data loaders the
simulator uses (``jplus.data``).
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import clock, db, trades

log = logging.getLogger("dashboard.jplus_trade_emitter")

# Strategy-name constants written to trades.strategy. The JPLUS_ prefix
# distinguishes Core sub-sleeves from tactical sleeves at a glance and keeps
# the strategy column values queryable as a single SQL pattern.
STRATEGY_R4_BTC = "JPLUS_R4_BTC"
STRATEGY_R4_ETH = "JPLUS_R4_ETH"
STRATEGY_EMA_BTC = "JPLUS_EMA_BTC"
STRATEGY_ETH_DAILY = "JPLUS_ETH_DAILY"
ALL_CORE_STRATEGIES = (
    STRATEGY_R4_BTC, STRATEGY_R4_ETH, STRATEGY_EMA_BTC, STRATEGY_ETH_DAILY,
)

# Per-regime allocation weights — copied verbatim from jplus/simulate.py:108-131.
# DUPLICATION: kept here to avoid the emitter importing the simulator's full
# numpy stack just to look up four constants. If simulator.py changes these,
# the parity test will catch the drift.
REGIME_WEIGHTS = {
    "strong_bull": {"r4_btc": 0.15, "r4_eth": 0.15,
                     "ema_btc": 0.50, "eth_daily": 0.20},
    "mild_bull":   {"r4_btc": 0.20, "r4_eth": 0.30,
                     "ema_btc": 0.30, "eth_daily": 0.10},
    "uncertain":   {"r4_btc": 0.30, "r4_eth": 0.40,
                     "ema_btc": 0.30, "eth_daily": 0.00},
    "bear":        {"r4_btc": 0.00, "r4_eth": 0.00,
                     "ema_btc": 0.30, "eth_daily": 0.00},
}

R4_INNER_LEV_GATED = 1.0
R4_INNER_LEV_UNGATED = 2.5
R4_FEE_BP_RT = 10.0  # round-trip 10bp; split 5/5 across OPEN and CLOSE.

R4_BTC_ENTRY_HOUR = 6
R4_BTC_EXIT_HOUR = 18
R4_ETH_ENTRY_HOUR = 20  # Tue 20:00 UTC
R4_ETH_EXIT_HOUR = 20   # Wed 20:00 UTC


@dataclass
class PositionState:
    """Open Core J+ positions for one variant (one trade-row dict per
    sub-sleeve, or None if no open position). Read from the DB via
    ``get_position_state``; never carried across emit calls in memory."""
    ema_btc: dict | None = None
    eth_daily: dict | None = None
    r4_btc: dict | None = None
    r4_eth: dict | None = None


def _first_or_none(rows: list[dict]) -> dict | None:
    return rows[0] if rows else None


def get_position_state(variant_id: str) -> PositionState:
    """Reconstruct open Core J+ positions for a variant from the DB."""
    return PositionState(
        ema_btc=_first_or_none(trades.get_open_trades(variant_id, STRATEGY_EMA_BTC)),
        eth_daily=_first_or_none(trades.get_open_trades(variant_id, STRATEGY_ETH_DAILY)),
        r4_btc=_first_or_none(trades.get_open_trades(variant_id, STRATEGY_R4_BTC)),
        r4_eth=_first_or_none(trades.get_open_trades(variant_id, STRATEGY_R4_ETH)),
    )


# ─── Price lookups ──────────────────────────────────────────────────────────

def _hourly_open(asset: str, date_iso: str, hour: int) -> float | None:
    """Return the opening price of the (date_iso, hour) hourly bar for the
    given asset, or None if missing.

    BTC reads cd_spot_binance (timestamps in seconds). ETH aggregates from
    eth_1m on the fly (open_time in ms). Both are clock-bounded so a query
    at simulated clock T can never see future bars."""
    if asset.upper() == "BTC":
        ts_start = int(datetime.fromisoformat(date_iso).replace(
            tzinfo=timezone.utc, hour=hour).timestamp())
        upper = clock.now_ts()
        if ts_start > upper:
            return None
        con = sqlite3.connect(str(db.TRADER_DB))
        try:
            row = con.execute(
                "SELECT open FROM cd_spot_binance WHERE timestamp = ?",
                (ts_start,),
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None:
            return None
        return float(row[0])
    if asset.upper() == "ETH":
        # The first eth_1m bar of the (date, hour) window is the hour's open.
        hour_start_ms = int(datetime.fromisoformat(date_iso).replace(
            tzinfo=timezone.utc, hour=hour).timestamp() * 1000)
        next_hour_ms = hour_start_ms + 3600_000
        upper = clock.now_ts_ms()
        if hour_start_ms > upper:
            return None
        con = sqlite3.connect(str(db.TRADER_DB))
        try:
            row = con.execute(
                "SELECT open FROM eth_1m "
                "WHERE open_time >= ? AND open_time < ? "
                "ORDER BY open_time LIMIT 1",
                (hour_start_ms, next_hour_ms),
            ).fetchone()
        finally:
            con.close()
        if row is None or row[0] is None:
            return None
        return float(row[0])
    raise ValueError(f"unknown asset {asset!r}")


# ─── R4 BTC emit ────────────────────────────────────────────────────────────

def _emit_r4_btc(variant: dict, date_iso: str, sim_record: dict,
                 capital: float) -> None:
    """Emit OPEN+CLOSE for an R4_BTC fire on `date_iso`. No-op if the
    sleeve doesn't fire today, regime weight is zero, or events for this
    date already exist (idempotency)."""
    if not sim_record.get("r4_btc_fired"):
        return
    mode = sim_record.get("mode", "uncertain")
    weight = REGIME_WEIGHTS.get(mode, {}).get("r4_btc", 0.0)
    if weight <= 0.0:
        return  # bear regime: R4 idle

    gated = bool(sim_record.get("gated", False))
    inner_lev = R4_INNER_LEV_GATED if gated else R4_INNER_LEV_UNGATED
    vol_lev = float(sim_record.get("lev", 1.0))
    stacked_lev = inner_lev * vol_lev

    entry_price = _hourly_open("BTC", date_iso, R4_BTC_ENTRY_HOUR)
    exit_price = _hourly_open("BTC", date_iso, R4_BTC_EXIT_HOUR)
    if entry_price is None or exit_price is None:
        log.warning(f"[r4_btc] no hourly bars for {date_iso}; skipping emit")
        return

    entry_dt = datetime.fromisoformat(date_iso).replace(
        tzinfo=timezone.utc, hour=R4_BTC_ENTRY_HOUR)
    exit_dt = datetime.fromisoformat(date_iso).replace(
        tzinfo=timezone.utc, hour=R4_BTC_EXIT_HOUR)

    if _has_open_event(variant["id"], STRATEGY_R4_BTC, date_iso):
        return  # already emitted this date

    tid = trades.open_shadow_trade(
        variant=variant, sleeve_name=STRATEGY_R4_BTC,
        asset="BTC", direction="LONG",
        entry_price=entry_price,
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        reason={"sleeve": STRATEGY_R4_BTC, "mode": mode,
                "stacked_lev": stacked_lev, "gated": gated,
                "fired": True, "trigger_date": date_iso},
        scheduled_exit_dt=exit_dt,
        regime_value=mode,
        entry_dt=entry_dt,
    )
    # Synthesize the CLOSE via close_perp_trade with the full round-trip
    # cost — matches the simulator's accounting (r4.py deducts 10bp RT from
    # the window return). apply_funding=False — R4 windows are intraday,
    # funding negligible.
    clock.set_simulated_now(exit_dt)
    try:
        trades.close_perp_trade(
            tid, exit_price=exit_price,
            reason=f"r4_btc_window_close_{date_iso}",
            sleeve_name=STRATEGY_R4_BTC,
            cost_bp_rt=R4_FEE_BP_RT,
            apply_funding=False,
        )
    finally:
        clock.set_simulated_now(None)


# ─── R4 ETH emit ────────────────────────────────────────────────────────────

def _emit_r4_eth(variant: dict, date_iso: str, sim_record: dict,
                 capital: float) -> None:
    """For a Wed-attributed R4_ETH fire, write the OPEN at Tue 20:00 UTC
    and the CLOSE at Wed 20:00 UTC atomically. The simulator uses Wed's
    stacked leverage and regime weight for the entire trade — we mirror
    that here for parity (a future refinement could split into Tue's-lev
    OPEN + midnight LEVERAGE_ADJUST for live realism).
    """
    if not sim_record.get("r4_eth_fired"):
        return
    mode = sim_record.get("mode", "uncertain")
    weight = REGIME_WEIGHTS.get(mode, {}).get("r4_eth", 0.0)
    if weight <= 0.0:
        return

    gated = bool(sim_record.get("gated", False))
    inner_lev = R4_INNER_LEV_GATED if gated else R4_INNER_LEV_UNGATED
    vol_lev = float(sim_record.get("lev", 1.0))
    stacked_lev = inner_lev * vol_lev

    wed_dt = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    tue_dt = wed_dt - timedelta(days=1)
    tue_iso = tue_dt.date().isoformat()

    entry_price = _hourly_open("ETH", tue_iso, R4_ETH_ENTRY_HOUR)
    exit_price = _hourly_open("ETH", date_iso, R4_ETH_EXIT_HOUR)
    if entry_price is None or exit_price is None:
        log.warning(f"[r4_eth] no hourly bars for Tue={tue_iso} or Wed={date_iso}; "
                    f"skipping emit")
        return

    entry_dt = tue_dt.replace(hour=R4_ETH_ENTRY_HOUR)
    exit_dt = wed_dt.replace(hour=R4_ETH_EXIT_HOUR)

    # Idempotency check on the Tue OPEN event.
    if _has_open_event(variant["id"], STRATEGY_R4_ETH, tue_iso):
        return

    tid = trades.open_shadow_trade(
        variant=variant, sleeve_name=STRATEGY_R4_ETH,
        asset="ETH", direction="LONG",
        entry_price=entry_price,
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        reason={"sleeve": STRATEGY_R4_ETH, "mode": mode,
                "stacked_lev": stacked_lev, "gated": gated,
                "trade_day": date_iso, "open_calendar_day": tue_iso},
        scheduled_exit_dt=exit_dt,
        regime_value=mode,
        entry_dt=entry_dt,
    )

    clock.set_simulated_now(exit_dt)
    try:
        trades.close_perp_trade(
            tid, exit_price=exit_price,
            reason=f"r4_eth_window_close_{date_iso}",
            sleeve_name=STRATEGY_R4_ETH,
            cost_bp_rt=R4_FEE_BP_RT,
            apply_funding=False,
        )
    finally:
        clock.set_simulated_now(None)


# ─── ETH_DAILY emit ─────────────────────────────────────────────────────────


def _eth_daily_close(date_iso: str) -> float | None:
    """ETH spot close at end of date_iso UTC (= last eth_1m bar of the day's
    close, equivalently the first bar's open of date+1 — small jitter
    possible). Used both as the previous-day-close basis when sizing today's
    notional and as the close price for daily MTM."""
    day_start_ms = int(datetime.fromisoformat(date_iso).replace(
        tzinfo=timezone.utc).timestamp() * 1000)
    day_end_ms = day_start_ms + 86_400_000
    upper = clock.now_ts_ms()
    if day_start_ms > upper:
        return None
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        row = con.execute(
            "SELECT close FROM eth_1m "
            "WHERE open_time >= ? AND open_time < ? AND open_time <= ? "
            "ORDER BY open_time DESC LIMIT 1",
            (day_start_ms, day_end_ms, upper),
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _emit_eth_daily(variant: dict, date_iso: str, sim_record: dict,
                    capital: float, prev_state: PositionState) -> None:
    """ETH_DAILY: continuous long ETH while regime is in {strong_bull,
    mild_bull}; idle otherwise.

    Each day in bull regime, the position is rebalanced so that current
    notional equals ``capital × eth_daily_weight × vol_target_lev``. The
    rebalance is timestamped at date_iso 00:00 UTC, priced at the prior
    UTC day's ETH close (= today's open in continuous markets). Two events
    can fire per day: a SCALE (qty change to match new notional) and a
    LEVERAGE_ADJUST (margin change for vol-target update).

    On the first day the regime exits bull, the position is closed at the
    same price (prior-day close).
    """
    mode = sim_record.get("mode", "uncertain")
    weight = REGIME_WEIGHTS.get(mode, {}).get("eth_daily", 0.0)
    vol_lev = float(sim_record.get("lev", 1.0))
    desired_open = weight > 0.0
    open_pos = prev_state.eth_daily

    cur_dt = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    prev_day_iso = (cur_dt - timedelta(days=1)).date().isoformat()
    rebalance_price = _eth_daily_close(prev_day_iso)
    if rebalance_price is None or rebalance_price <= 0:
        log.warning(f"[eth_daily] no ETH close at {prev_day_iso}; skipping {date_iso}")
        return

    rebalance_dt = cur_dt  # 00:00 UTC of date_iso

    if not desired_open:
        # If we have an open ETH_DAILY trade, close it at prior-day close.
        if open_pos is not None:
            if _has_event(open_pos["id"], date_iso, "CLOSE"):
                return
            clock.set_simulated_now(rebalance_dt)
            try:
                trades.close_perp_trade(
                    open_pos["id"], exit_price=rebalance_price,
                    reason=f"eth_daily_regime_exit_{mode}_{date_iso}",
                    sleeve_name=STRATEGY_ETH_DAILY,
                    cost_bp_rt=0.0, apply_funding=False,
                )
            finally:
                clock.set_simulated_now(None)
        return

    # desired_open == True
    desired_notional = capital * weight * vol_lev
    desired_qty = desired_notional / rebalance_price

    if open_pos is None:
        if _has_open_event(variant["id"], STRATEGY_ETH_DAILY, date_iso):
            return
        trades.open_shadow_trade(
            variant=variant, sleeve_name=STRATEGY_ETH_DAILY,
            asset="ETH", direction="LONG",
            entry_price=rebalance_price,
            allocation_pct=weight * 100.0,
            leverage=vol_lev,
            reason={"sleeve": STRATEGY_ETH_DAILY, "mode": mode,
                    "vol_lev": vol_lev, "weight": weight},
            scheduled_exit_dt=None,
            regime_value=mode,
            entry_dt=rebalance_dt,
        )
        return

    # Already open: rebalance qty and leverage to today's targets.
    cur_qty = float(open_pos["current_qty"] if open_pos.get("current_qty")
                    is not None else open_pos["qty"] or 0.0)
    cur_lev = float(open_pos["current_leverage"] if open_pos.get("current_leverage")
                    is not None else open_pos["leverage"] or 1.0)
    if abs(desired_qty - cur_qty) > max(1e-9, 1e-9 * abs(cur_qty)):
        if not _has_event(open_pos["id"], date_iso, "SCALE_UP") and \
           not _has_event(open_pos["id"], date_iso, "SCALE_DOWN"):
            trades.apply_scale(
                open_pos["id"], new_qty=desired_qty,
                price=rebalance_price, fee_usdt=0.0,
                event_time=rebalance_dt.isoformat(),
                event_date=date_iso,
                notes={"reason": "daily_rebalance", "mode": mode,
                       "weight": weight, "vol_lev": vol_lev},
            )
    if abs(vol_lev - cur_lev) > 1e-9:
        if not _has_event(open_pos["id"], date_iso, "LEVERAGE_ADJUST"):
            trades.apply_leverage_adjust(
                open_pos["id"], new_leverage=vol_lev,
                price=rebalance_price, fee_usdt=0.0,
                event_time=rebalance_dt.isoformat(),
                event_date=date_iso,
                notes={"reason": "vol_target_update", "mode": mode},
            )


# ─── EMA_BTC emit ───────────────────────────────────────────────────────────


def _btc_day_open_price(date_iso: str) -> float | None:
    """BTC price at 00:00 UTC of date_iso (= prior-day close in continuous
    markets). Used as the daily rebalance / FLIP price for EMA_BTC, mirroring
    the simulator's close-to-close ``br`` accounting."""
    return _hourly_open("BTC", date_iso, 0)


def _emit_ema_btc(variant: dict, date_iso: str, sim_record: dict,
                  capital: float, prev_state: PositionState) -> None:
    """EMA_BTC: continuous BTC position whose direction is governed by the
    weekly EMA(5/21) cross signal (``sim_record['ema_p']``). Active in all
    four regimes (every regime gives EMA_BTC a non-zero weight).

    Per-day events for an open EMA_BTC trade:
      - SCALE if regime weight × vol-target lev changes the desired notional.
      - LEVERAGE_ADJUST if vol-target lev changes day-to-day.
      - FLIP if ``ema_p`` sign flips — closes the current trade and opens
        an opposite-direction trade at the same rebalance price.

    Initial OPEN happens on the first day where ``ema_p != 0``. CLOSE
    only fires defensively if ``ema_p`` returns to 0 (rare — only at the
    extreme edge of warmup).
    """
    ema_p = int(sim_record.get("ema_p", 0) or 0)
    mode = sim_record.get("mode", "uncertain")
    weight = REGIME_WEIGHTS.get(mode, {}).get("ema_btc", 0.0)
    vol_lev = float(sim_record.get("lev", 1.0))
    open_pos = prev_state.ema_btc

    rebalance_price = _btc_day_open_price(date_iso)
    if rebalance_price is None or rebalance_price <= 0:
        log.warning(f"[ema_btc] no BTC bar at {date_iso} 00:00 UTC; skipping")
        return
    rebalance_dt = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)

    desired_active = ema_p != 0 and weight > 0.0
    desired_direction = "LONG" if ema_p > 0 else ("SHORT" if ema_p < 0 else None)
    desired_notional = (capital * weight * vol_lev) if desired_active else 0.0
    desired_qty = (desired_notional / rebalance_price) if rebalance_price > 0 else 0.0

    if not desired_active:
        # Defensive close — only triggers if regime weight goes to 0 (never
        # under current allocations) or ema_p returns to 0.
        if open_pos is not None:
            if _has_event(open_pos["id"], date_iso, "CLOSE"):
                return
            clock.set_simulated_now(rebalance_dt)
            try:
                trades.close_perp_trade(
                    open_pos["id"], exit_price=rebalance_price,
                    reason=f"ema_btc_inactive_{mode}_{date_iso}",
                    sleeve_name=STRATEGY_EMA_BTC,
                    cost_bp_rt=0.0, apply_funding=False,
                )
            finally:
                clock.set_simulated_now(None)
        return

    # desired_active == True
    if open_pos is None:
        if _has_open_event(variant["id"], STRATEGY_EMA_BTC, date_iso):
            return
        trades.open_shadow_trade(
            variant=variant, sleeve_name=STRATEGY_EMA_BTC,
            asset="BTC", direction=desired_direction,
            entry_price=rebalance_price,
            allocation_pct=weight * 100.0,
            leverage=vol_lev,
            reason={"sleeve": STRATEGY_EMA_BTC, "mode": mode,
                    "ema_p": ema_p, "vol_lev": vol_lev, "weight": weight},
            scheduled_exit_dt=None,
            regime_value=mode,
            entry_dt=rebalance_dt,
        )
        return

    cur_direction = (open_pos["direction"] or "").upper()
    cur_qty = float(open_pos["current_qty"] if open_pos.get("current_qty")
                    is not None else open_pos["qty"] or 0.0)
    cur_lev = float(open_pos["current_leverage"] if open_pos.get("current_leverage")
                    is not None else open_pos["leverage"] or 1.0)

    # FLIP path: ema_p sign disagrees with current direction.
    if cur_direction != desired_direction:
        if _has_event(open_pos["id"], date_iso, "FLIP"):
            return
        trades.apply_flip(
            open_pos["id"], new_direction=desired_direction,
            price=rebalance_price, fee_usdt=0.0,
            event_time=rebalance_dt.isoformat(),
            event_date=date_iso,
            notes={"reason": "ema_weekly_cross", "ema_p": ema_p,
                   "mode": mode},
        )
        # The new trade was just minted by apply_flip with same notional/lev
        # as the old one. If today's desired notional/lev differ, follow up
        # with a SCALE / LEVERAGE_ADJUST below by re-fetching state.
        new_pos = _first_or_none(trades.get_open_trades(
            variant["id"], STRATEGY_EMA_BTC))
        if new_pos is None:
            return
        cur_qty = float(new_pos["current_qty"] if new_pos.get("current_qty")
                        is not None else new_pos["qty"] or 0.0)
        cur_lev = float(new_pos["current_leverage"] if new_pos.get("current_leverage")
                        is not None else new_pos["leverage"] or 1.0)
        open_pos = new_pos

    # Daily rebalance on same direction.
    if abs(desired_qty - cur_qty) > max(1e-9, 1e-9 * abs(cur_qty)):
        if not _has_event(open_pos["id"], date_iso, "SCALE_UP") and \
           not _has_event(open_pos["id"], date_iso, "SCALE_DOWN"):
            trades.apply_scale(
                open_pos["id"], new_qty=desired_qty,
                price=rebalance_price, fee_usdt=0.0,
                event_time=rebalance_dt.isoformat(),
                event_date=date_iso,
                notes={"reason": "daily_rebalance", "mode": mode,
                       "weight": weight, "vol_lev": vol_lev,
                       "ema_p": ema_p},
            )
    if abs(vol_lev - cur_lev) > 1e-9:
        if not _has_event(open_pos["id"], date_iso, "LEVERAGE_ADJUST"):
            trades.apply_leverage_adjust(
                open_pos["id"], new_leverage=vol_lev,
                price=rebalance_price, fee_usdt=0.0,
                event_time=rebalance_dt.isoformat(),
                event_date=date_iso,
                notes={"reason": "vol_target_update", "mode": mode},
            )


# ─── helpers ────────────────────────────────────────────────────────────────

def _has_open_event(variant_id: str, strategy: str, event_date: str) -> bool:
    """True if any trade for (variant_id, strategy) already has an OPEN
    adjustment row whose event_date matches. Cheaper than recomputing the
    full position state for an idempotency check."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT 1 FROM trade_adjustments a JOIN trades t ON a.trade_id=t.id "
            "WHERE t.strategy=? AND t.strategy_variant=? "
            "  AND a.event_type='OPEN' AND a.event_date=? LIMIT 1",
            (strategy, variant_id, event_date),
        ).fetchone()
    finally:
        con.close()
    return row is not None


def _has_event(trade_id: str, event_date: str, event_type: str) -> bool:
    """True if this trade already has an event of (event_date, event_type).
    Used to short-circuit before calling apply_scale / apply_leverage_adjust /
    close_perp_trade on the same day."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT 1 FROM trade_adjustments "
            "WHERE trade_id=? AND event_date=? AND event_type=? LIMIT 1",
            (trade_id, event_date, event_type),
        ).fetchone()
    finally:
        con.close()
    return row is not None


# ─── Public emit ────────────────────────────────────────────────────────────

def emit_for_date(variant: dict, date_iso: str, sim_record: dict,
                  prev_state: PositionState | None = None,
                  sim_record_yesterday: dict | None = None
                  ) -> PositionState:
    """Emit Core J+ trade events for the simulator-attributed UTC date
    ``date_iso``. Idempotent: re-running for the same date never duplicates
    events. ``prev_state`` is reconstructed from the DB if not supplied.

    Returns the post-emit PositionState (re-read from DB).
    """
    if prev_state is None:
        prev_state = get_position_state(variant["id"])
    capital = float(variant.get("capital_usdt") or 10000)

    _emit_r4_btc(variant, date_iso, sim_record, capital)
    _emit_r4_eth(variant, date_iso, sim_record, capital)
    _emit_eth_daily(variant, date_iso, sim_record, capital, prev_state)
    _emit_ema_btc(variant, date_iso, sim_record, capital, prev_state)

    return get_position_state(variant["id"])


def emit_catchup(variant: dict, end_date_iso: str,
                 start_date_iso: str | None = None) -> dict:
    """Walk the simulator series from ``start_date_iso`` (or the day after
    the most recent emit, if None) through ``end_date_iso`` and emit each
    day. Useful after a restart or schema migration. Returns a status dict
    with the count of dates processed and any dates that returned no
    sim_record (insufficient warm-up)."""
    from jplus import simulate as core_sim

    if start_date_iso is None:
        # Default: the day after the latest event_date in the ledger for
        # this variant.
        con = sqlite3.connect(str(db.DASH_DB))
        try:
            row = con.execute(
                "SELECT MAX(a.event_date) FROM trade_adjustments a "
                "JOIN trades t ON a.trade_id = t.id "
                "WHERE t.strategy_variant = ? AND t.strategy LIKE 'JPLUS_%'",
                (variant["id"],),
            ).fetchone()
        finally:
            con.close()
        if row and row[0]:
            start_date_iso = (datetime.fromisoformat(row[0])
                              + timedelta(days=1)).date().isoformat()
        else:
            # Cold start — go back ~30 days
            start_date_iso = (datetime.fromisoformat(end_date_iso)
                              - timedelta(days=30)).date().isoformat()

    series = core_sim.simulate(start_date=start_date_iso,
                                 end_date=end_date_iso)
    processed: list[str] = []
    skipped: list[str] = []
    cur = datetime.fromisoformat(start_date_iso).date()
    end = datetime.fromisoformat(end_date_iso).date()
    while cur <= end:
        d = cur.isoformat()
        rec = series.get(d)
        if rec is None:
            skipped.append(d)
        else:
            emit_for_date(variant, d, rec)
            processed.append(d)
        cur = cur + timedelta(days=1)
    return {"processed": len(processed), "skipped": len(skipped),
            "first": processed[0] if processed else None,
            "last": processed[-1] if processed else None,
            "skipped_dates": skipped[:10]}
