"""Real-time live entry handlers for Core J+ sub-sleeves.

Each handler matches the ``try_fire_for_variant(variant, sleeve_cfg) -> dict``
contract used by the tactical stack (services/adx_service.py,
services/fomc_service.py, etc.) so ``services.variant_engine`` can
dispatch the same way it dispatches FOMC/ADX/CPR/etc.

This is the LIVE entry path — handlers fire on the actual calendar /
signal moment, open trades at current market price, and the trades are
visible in ``trades`` table immediately. Replaces the pre-Phase-1
retrospective ``services/jplus_trade_emitter`` flow which only emitted
yesterday's trades at midnight UTC and was a fatal blocker for real-money
execution (no exchange order ever placed at the entry moment).

Handlers (this file, Phase 2):
  - r4_btc_try_fire: Mon/Wed wk1-2 06:00 → 18:00 UTC discrete trade.
  - r4_eth_try_fire: Tue 20:00 → Wed 20:00 UTC where Wed.day ≤ 14.

Handlers (this file, Phase 3 — pending):
  - ema_btc_try_fire: continuous; 00:00 UTC daily SCALE/LEV_ADJ/FLIP.
  - eth_daily_try_fire: continuous in bull regimes only.

All handlers:
  - call ``jplus.simulate.today_inputs()`` for sizing inputs derived from
    data through yesterday's close (regime mode, vol-target lev, R4 gate,
    ema_p, sub-sleeve weights);
  - use ``services.price_feed.get_current_price`` for live execution
    price (latest closed 1m bar; ~30s lag from instant);
  - open trades via ``services.trades.open_shadow_trade`` and rely on
    ``variant_engine._close_due_shadows`` for scheduled-exit closes;
  - are idempotent per UTC day via the ``trades`` table existence check
    (handler-side) and ``UNIQUE(trade_id, event_date, event_type)``
    (adjustment-ledger side).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from services import clock, db, price_feed, trades

log = logging.getLogger("dashboard.jplus_live")

# Strategy names — must match those used by services/jplus_trade_emitter.py
# so the live entries and the catchup-emit path land in the same trade
# rows (idempotency relies on this alignment).
STRATEGY_R4_BTC = "JPLUS_R4_BTC"
STRATEGY_R4_ETH = "JPLUS_R4_ETH"
STRATEGY_EMA_BTC = "JPLUS_EMA_BTC"
STRATEGY_ETH_DAILY = "JPLUS_ETH_DAILY"

R4_INNER_LEV_GATED = 1.0
R4_INNER_LEV_UNGATED = 2.5

R4_BTC_ENTRY_HOUR = 6
R4_BTC_EXIT_HOUR = 18
R4_ETH_ENTRY_HOUR = 20
R4_ETH_EXIT_HOUR = 20


# ─── Idempotency helper ─────────────────────────────────────────────────────


def _has_trade_for_day(variant_id: str, strategy: str, day_iso: str) -> bool:
    """True if (variant_id, strategy) already has a trade entered on
    ``day_iso`` (any status: pending / open / closed). Stops re-opening
    within the same UTC day if the bot ticks multiple times during the
    entry window or if ``services.jplus_trade_emitter.emit_catchup``
    already backfilled the trade at startup."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT 1 FROM trades WHERE strategy_variant=? AND strategy=? "
            "AND date(actual_entry_time)=? LIMIT 1",
            (variant_id, strategy, day_iso),
        ).fetchone()
    finally:
        con.close()
    return row is not None


# ─── R4 BTC (Mon/Wed wk1-2 intraday) ────────────────────────────────────────


def r4_btc_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Open R4_BTC LONG at 06:00 UTC on Mon/Wed wk1-2, scheduled to close
    at 18:00 UTC same day. ``variant_engine._close_due_shadows`` handles
    the close via ``scheduled_exit_dt``.

    Sizing: ``capital × weights['r4_btc'] × inner_lev × vol_lev`` where
    inner_lev is 2.5× (or 1.0× if the gate fired) and vol_lev is the
    vol-target leverage. All from ``jplus.simulate.today_inputs()``.

    Returns a status dict — see in-code branches for status keys.
    """
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    # Calendar gate: Mon (weekday=0) or Wed (weekday=2), day-of-month ≤ 14.
    if now.weekday() not in (0, 2):
        return {"status": "not_calendar_day"}
    if now.day > 14:
        return {"status": "not_wk_1_2"}

    # Time window: open at 06:00, scheduled close at 18:00.
    if now.hour < R4_BTC_ENTRY_HOUR:
        return {"status": "before_open_window"}
    if now.hour >= R4_BTC_EXIT_HOUR:
        return {"status": "after_close_window"}

    # Idempotency.
    if _has_trade_for_day(variant["id"], STRATEGY_R4_BTC, today_iso):
        return {"status": "already_open"}

    # Sizing inputs from data through yesterday's close.
    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    weight = ti["weights"]["r4_btc"]
    if weight <= 0:
        return {"status": "regime_zero_weight", "mode": ti["mode"]}

    inner_lev = R4_INNER_LEV_GATED if ti["gated"] else R4_INNER_LEV_UNGATED
    stacked_lev = inner_lev * float(ti["lev"])

    price = price_feed.get_current_price("BTC")
    if price is None or price <= 0:
        return {"status": "no_price"}

    exit_dt = now.replace(hour=R4_BTC_EXIT_HOUR, minute=0,
                           second=0, microsecond=0)
    capital = float(variant.get("capital_usdt") or 10000)
    tid = trades.open_shadow_trade(
        variant=variant, sleeve_name=STRATEGY_R4_BTC,
        asset="BTC", direction="LONG",
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        reason={"sleeve": STRATEGY_R4_BTC, "mode": ti["mode"],
                "vol_lev": ti["lev"], "inner_lev": inner_lev,
                "gated": ti["gated"], "trigger": "calendar_open"},
        scheduled_exit_dt=exit_dt,
        regime_value=ti["mode"],
        entry_dt=now,
    )
    log.info(f"[jplus_live R4_BTC {variant['id']}] OPENED {tid} BTC LONG @ "
             f"${price:,.2f}  notional=${capital * weight * stacked_lev:,.2f}  "
             f"k={stacked_lev:.2f}x  exit_due={exit_dt.isoformat()}")
    return {"status": "opened", "trade_id": tid, "entry_price": price,
            "stacked_lev": stacked_lev, "weight": weight}


# ─── R4 ETH (Tue 20:00 → Wed 20:00 UTC) ─────────────────────────────────────


def r4_eth_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Open R4_ETH LONG at Tue 20:00 UTC if next-day Wed has day ≤ 14,
    scheduled to close at Wed 20:00 UTC.

    Live-realism convention: sizes the position using TODAY's inputs
    (Tue's regime/lev), not tomorrow's. The retrospective emitter used
    Wed's; that small parity drift if regime/lev flip overnight is the
    cost of the bot actually being able to place an order at Tue 20:00.
    """
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() != 1:
        return {"status": "not_tuesday"}

    # Tomorrow (Wed) must be in days 1-14.
    tomorrow = now + timedelta(days=1)
    if tomorrow.day > 14:
        return {"status": "next_day_not_wk_1_2"}

    # Time window: open at 20:00 Tue (no upper bound — once past 20:00
    # we want the trade open for the rest of the day).
    if now.hour < R4_ETH_ENTRY_HOUR:
        return {"status": "before_open_window"}

    if _has_trade_for_day(variant["id"], STRATEGY_R4_ETH, today_iso):
        return {"status": "already_open"}

    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    weight = ti["weights"]["r4_eth"]
    if weight <= 0:
        return {"status": "regime_zero_weight", "mode": ti["mode"]}

    inner_lev = R4_INNER_LEV_GATED if ti["gated"] else R4_INNER_LEV_UNGATED
    stacked_lev = inner_lev * float(ti["lev"])

    price = price_feed.get_current_price("ETH")
    if price is None or price <= 0:
        return {"status": "no_price"}

    exit_dt = tomorrow.replace(hour=R4_ETH_EXIT_HOUR, minute=0,
                                 second=0, microsecond=0)
    capital = float(variant.get("capital_usdt") or 10000)
    tid = trades.open_shadow_trade(
        variant=variant, sleeve_name=STRATEGY_R4_ETH,
        asset="ETH", direction="LONG",
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        reason={"sleeve": STRATEGY_R4_ETH, "mode": ti["mode"],
                "vol_lev": ti["lev"], "inner_lev": inner_lev,
                "gated": ti["gated"], "trigger": "calendar_open",
                "trade_day": tomorrow.date().isoformat()},
        scheduled_exit_dt=exit_dt,
        regime_value=ti["mode"],
        entry_dt=now,
    )
    log.info(f"[jplus_live R4_ETH {variant['id']}] OPENED {tid} ETH LONG @ "
             f"${price:,.2f}  notional=${capital * weight * stacked_lev:,.2f}  "
             f"k={stacked_lev:.2f}x  exit_due={exit_dt.isoformat()}")
    return {"status": "opened", "trade_id": tid, "entry_price": price,
            "stacked_lev": stacked_lev, "weight": weight}
