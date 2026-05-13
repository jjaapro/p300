"""Real-time live entry handlers for Core J+ sub-sleeves.

Each handler matches the ``try_fire_for_variant(variant, sleeve_cfg) -> dict``
contract used by the tactical stack (services/adx_service.py,
services/fomc_service.py, etc.) so ``services.variant_engine`` can
dispatch the same way it dispatches FOMC/ADX/CPR/etc.

This is the LIVE entry path — handlers fire on the actual calendar /
signal moment, open trades at current market price, and the trades are
visible in ``trades`` table immediately. The retrospective
``services/jplus_trade_emitter`` (offline-period catchup) was removed
2026-05-10; if the bot is offline during a sub-sleeve's window, the
trade is missed permanently — same semantics as tactical sleeves.

Handlers:
  - r4_btc_try_fire: Mon wk1-2 06:00 → 18:00 UTC discrete trade.
  - r4_eth_try_fire: Tue 20:00 → Wed 20:00 UTC where Wed.day ≤ 14.
  - r4_btc_v2_try_fire: Wed/Fri wk1-2 04:00 → 14:00 UTC.
  - r4_eth_v2_try_fire: Wed/Fri wk1-2 04:00 → 14:00 UTC on ETH.
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

# Strategy names written to trades.strategy. The JPLUS_ prefix
# distinguishes Core sub-sleeves from tactical sleeves at a glance.
STRATEGY_R4_BTC = "JPLUS_R4_BTC"
STRATEGY_R4_ETH = "JPLUS_R4_ETH"
STRATEGY_R4_BTC_V2 = "JPLUS_R4_BTC_V2"
STRATEGY_R4_ETH_V2 = "JPLUS_R4_ETH_V2"
STRATEGY_EMA_BTC = "JPLUS_EMA_BTC"
STRATEGY_ETH_DAILY = "JPLUS_ETH_DAILY"

R4_INNER_LEV_GATED = 1.0
R4_INNER_LEV_UNGATED = 2.5

R4_BTC_ENTRY_HOUR = 6
R4_BTC_EXIT_HOUR = 18
R4_ETH_ENTRY_HOUR = 20
R4_ETH_EXIT_HOUR = 20
R4_V2_ENTRY_HOUR = 4
R4_V2_EXIT_HOUR = 14


# ─── Idempotency helper ─────────────────────────────────────────────────────


def _has_trade_for_day(variant_id: str, strategy: str, day_iso: str) -> bool:
    """True if (variant_id, strategy) already has a trade entered on
    ``day_iso`` (any status: pending / open / closed). Stops re-opening
    within the same UTC day if the bot ticks multiple times during the
    entry window."""
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
    """Open R4_BTC LONG at 06:00 UTC on Mon wk1-2, scheduled to close at
    18:00 UTC same day. ``variant_engine._close_due_shadows`` handles the
    close via ``scheduled_exit_dt``.

    Mon-only since 2026-05-08 — Wednesdays moved to ``r4_btc_v2_try_fire``
    at the era-stable 04:00→14:00 window. See tools/r4_study/findings.md.

    Sizing: ``capital × weights['r4_btc'] × inner_lev × vol_lev`` where
    inner_lev is 2.5× (or 1.0× if the gate fired) and vol_lev is the
    vol-target leverage. All from ``jplus.simulate.today_inputs()``.

    Returns a status dict — see in-code branches for status keys.
    """
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    # Calendar gate: Mon (weekday=0), day-of-month ≤ 14.
    if now.weekday() != 0:
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


# ─── R4 V2 (Wed/Fri wk1-2 04:00 → 14:00 UTC, BTC + ETH) ────────────────────


def _r4_v2_try_fire(variant: dict, asset: str, strategy: str,
                    weight_key: str) -> dict:
    """Shared live entry path for R4_BTC_V2 / R4_ETH_V2. Wed+Fri wk1-2,
    04:00 UTC entry, 14:00 UTC scheduled exit. See tools/r4_study/."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() not in (2, 4):  # Wed or Fri
        return {"status": "not_calendar_day"}
    if now.day > 14:
        return {"status": "not_wk_1_2"}

    if now.hour < R4_V2_ENTRY_HOUR:
        return {"status": "before_open_window"}
    if now.hour >= R4_V2_EXIT_HOUR:
        return {"status": "after_close_window"}

    if _has_trade_for_day(variant["id"], strategy, today_iso):
        return {"status": "already_open"}

    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    weight = ti["weights"].get(weight_key, 0.0)
    if weight <= 0:
        return {"status": "regime_zero_weight", "mode": ti["mode"]}

    inner_lev = R4_INNER_LEV_GATED if ti["gated"] else R4_INNER_LEV_UNGATED
    stacked_lev = inner_lev * float(ti["lev"])

    price = price_feed.get_current_price(asset)
    if price is None or price <= 0:
        return {"status": "no_price"}

    exit_dt = now.replace(hour=R4_V2_EXIT_HOUR, minute=0,
                           second=0, microsecond=0)
    capital = float(variant.get("capital_usdt") or 10000)
    tid = trades.open_shadow_trade(
        variant=variant, sleeve_name=strategy,
        asset=asset, direction="LONG",
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        reason={"sleeve": strategy, "mode": ti["mode"],
                "vol_lev": ti["lev"], "inner_lev": inner_lev,
                "gated": ti["gated"], "trigger": "calendar_open",
                "window": "wed_fri_wk1-2_04-14_v2"},
        scheduled_exit_dt=exit_dt,
        regime_value=ti["mode"],
        entry_dt=now,
    )
    log.info(f"[jplus_live {strategy} {variant['id']}] OPENED {tid} "
             f"{asset} LONG @ ${price:,.2f}  "
             f"notional=${capital * weight * stacked_lev:,.2f}  "
             f"k={stacked_lev:.2f}x  exit_due={exit_dt.isoformat()}")
    return {"status": "opened", "trade_id": tid, "entry_price": price,
            "stacked_lev": stacked_lev, "weight": weight}


def r4_btc_v2_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """R4_BTC_V2 — Wed+Fri wk1-2 04:00→14:00 UTC."""
    return _r4_v2_try_fire(variant, asset="BTC",
                            strategy=STRATEGY_R4_BTC_V2,
                            weight_key="r4_btc_v2")


def r4_eth_v2_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """R4_ETH_V2 — Wed+Fri wk1-2 04:00→14:00 UTC on ETH."""
    return _r4_v2_try_fire(variant, asset="ETH",
                            strategy=STRATEGY_R4_ETH_V2,
                            weight_key="r4_eth_v2")


# ─── Continuous-position helpers (EMA_BTC, ETH_DAILY) ───────────────────────


def _has_adjustment_today(trade_id: str, today_iso: str,
                           event_types: tuple[str, ...]) -> bool:
    """True if any of the given event_types already exist for this trade
    on ``today_iso``. Lets the live handlers re-tick safely — once a
    SCALE / LEVERAGE_ADJUST event has been emitted today, subsequent
    ticks no-op."""
    placeholders = ",".join("?" for _ in event_types)
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            f"SELECT 1 FROM trade_adjustments WHERE trade_id=? "
            f"AND event_date=? AND event_type IN ({placeholders}) LIMIT 1",
            (trade_id, today_iso, *event_types),
        ).fetchone()
    finally:
        con.close()
    return row is not None


def _open_continuous_btc_or_eth(variant: dict, sleeve_name: str,
                                 asset: str, direction: str,
                                 weight: float, lev: float,
                                 price: float, mode: str, ema_p: int,
                                 now: datetime) -> str:
    """Open a continuous Core sub-sleeve trade (no scheduled exit; closed
    on regime exit / FLIP / signal)."""
    return trades.open_shadow_trade(
        variant=variant, sleeve_name=sleeve_name,
        asset=asset, direction=direction,
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=lev,
        reason={"sleeve": sleeve_name, "mode": mode,
                "ema_p": ema_p, "vol_lev": lev,
                "trigger": "live_open"},
        scheduled_exit_dt=None,
        regime_value=mode,
        entry_dt=now,
    )


# ─── EMA_BTC (BTC perp, continuous, ema_p direction) ────────────────────────


def ema_btc_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Continuous BTC position whose direction follows ``today_inputs.ema_p``.

    Per-tick state machine:
      - No open trade + ema_p != 0:        OPEN at current price.
      - Open trade, direction = ema_p sign: SCALE/LEVERAGE_ADJUST if
        regime weight or vol-target lev changed (idempotent per UTC
        day via UNIQUE on adjustment events).
      - Open trade, direction != ema_p:    FLIP — close the current
        trade and open the opposite-direction trade at current price.
      - Open trade, ema_p == 0:            CLOSE (defensive; rare).

    Active in all four regimes (every regime gives EMA_BTC a positive
    weight in REGIME_WEIGHTS_FULL).
    """
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    desired_ema_p = int(ti["ema_p"])
    prev_ema_p = int(ti.get("ema_p_prev", 0))
    desired_weight = float(ti["weights"]["ema_btc"])
    desired_lev = float(ti["lev"])

    open_trades = trades.get_open_trades(variant["id"], STRATEGY_EMA_BTC)
    open_pos = open_trades[0] if open_trades else None

    price = price_feed.get_current_price("BTC")
    if price is None or price <= 0:
        return {"status": "no_price"}

    capital = float(variant.get("capital_usdt") or 10000)

    # CASE 1: nothing open.
    if open_pos is None:
        if desired_ema_p == 0 or desired_weight <= 0:
            return {"status": "no_position_needed"}
        # Cold-start guard: only open at a fresh weekly EMA cross. If
        # yesterday's ema_p already had today's value, the cross fired
        # before this variant was emitting trades — wait for the next
        # cross rather than entering offside at today's price.
        if prev_ema_p == desired_ema_p:
            return {"status": "awaiting_fresh_cross",
                    "ema_p": desired_ema_p, "ema_p_prev": prev_ema_p}
        direction = "LONG" if desired_ema_p > 0 else "SHORT"
        tid = _open_continuous_btc_or_eth(
            variant, STRATEGY_EMA_BTC, "BTC", direction,
            desired_weight, desired_lev, price, ti["mode"],
            desired_ema_p, now,
        )
        log.info(f"[jplus_live EMA_BTC {variant['id']}] OPENED {tid} BTC "
                 f"{direction} @ ${price:,.2f}  k={desired_lev:.2f}x  "
                 f"weight={desired_weight}  ema_p={desired_ema_p} "
                 f"(fresh cross from {prev_ema_p:+d})")
        return {"status": "opened", "trade_id": tid}

    # CASE 2: position open but ema_p went to 0 — defensive close.
    if desired_ema_p == 0:
        trades.close_perp_trade(
            open_pos["id"], exit_price=price,
            reason=f"ema_p_zero_{today_iso}",
            sleeve_name=STRATEGY_EMA_BTC,
        )
        log.info(f"[jplus_live EMA_BTC {variant['id']}] CLOSED {open_pos['id']} "
                 f"@ ${price:,.2f} reason=ema_p_zero")
        return {"status": "closed", "trade_id": open_pos["id"]}

    # CASE 3: direction flipped (weekly EMA cross).
    cur_dir = (open_pos["direction"] or "").upper()
    desired_dir = "LONG" if desired_ema_p > 0 else "SHORT"
    if cur_dir != desired_dir:
        if _has_adjustment_today(open_pos["id"], today_iso, ("FLIP",)):
            return {"status": "flip_already_today"}
        new_tid = trades.apply_flip(
            open_pos["id"], new_direction=desired_dir,
            price=price, fee_usdt=0.0,
            event_time=now.isoformat(), event_date=today_iso,
            notes={"reason": "ema_weekly_cross", "mode": ti["mode"],
                   "ema_p": desired_ema_p},
        )
        log.info(f"[jplus_live EMA_BTC {variant['id']}] FLIPPED {open_pos['id']} "
                 f"-> {new_tid} {desired_dir} @ ${price:,.2f}")
        # After FLIP the new trade has the same notional/lev; the SCALE/
        # LEV_ADJ pass on the next tick (or below) brings it to today's
        # desired sizing if regime weight or vol-target diverged.
        return {"status": "flipped", "old_trade_id": open_pos["id"],
                "new_trade_id": new_tid}

    # CASE 4: same direction — daily rebalance.
    desired_qty = (capital * desired_weight * desired_lev) / price
    cur_qty = float(open_pos["current_qty"] if open_pos.get("current_qty")
                     is not None else open_pos["qty"] or 0.0)
    cur_lev = float(open_pos["current_leverage"] if open_pos.get("current_leverage")
                     is not None else open_pos["leverage"] or 1.0)

    actions: list[str] = []
    if abs(desired_qty - cur_qty) > max(1e-9, 1e-9 * abs(cur_qty)):
        if not _has_adjustment_today(open_pos["id"], today_iso,
                                      ("SCALE_UP", "SCALE_DOWN")):
            trades.apply_scale(
                open_pos["id"], new_qty=desired_qty, price=price,
                fee_usdt=0.0,
                event_time=now.isoformat(), event_date=today_iso,
                notes={"reason": "daily_rebalance", "mode": ti["mode"],
                       "weight": desired_weight, "vol_lev": desired_lev,
                       "ema_p": desired_ema_p},
            )
            actions.append("scaled")
    if abs(desired_lev - cur_lev) > 1e-9:
        if not _has_adjustment_today(open_pos["id"], today_iso,
                                      ("LEVERAGE_ADJUST",)):
            trades.apply_leverage_adjust(
                open_pos["id"], new_leverage=desired_lev, price=price,
                fee_usdt=0.0,
                event_time=now.isoformat(), event_date=today_iso,
                notes={"reason": "vol_target_update", "mode": ti["mode"]},
            )
            actions.append("leverage_adjusted")

    if not actions:
        return {"status": "in_sync", "trade_id": open_pos["id"]}
    return {"status": "rebalanced", "actions": actions,
            "trade_id": open_pos["id"]}


# ─── ETH_DAILY (ETH perp, continuous in bull regimes only) ──────────────────


def eth_daily_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Continuous ETH LONG that exists only while regime ∈ {strong_bull,
    mild_bull}. Same per-tick state machine shape as EMA_BTC, minus the
    direction-flip case (always LONG) and with explicit OPEN-on-bull-
    enter / CLOSE-on-bull-exit transitions."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    from jplus import simulate as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    desired_weight = float(ti["weights"]["eth_daily"])
    prev_weight = float(ti.get("weights_prev", {}).get("eth_daily", 0.0))
    desired_lev = float(ti["lev"])
    desired_open = desired_weight > 0.0
    prev_open = prev_weight > 0.0

    open_trades = trades.get_open_trades(variant["id"], STRATEGY_ETH_DAILY)
    open_pos = open_trades[0] if open_trades else None

    price = price_feed.get_current_price("ETH")
    if price is None or price <= 0:
        return {"status": "no_price"}

    capital = float(variant.get("capital_usdt") or 10000)

    # CASE 1: nothing open + don't want anything open (uncertain or bear).
    if open_pos is None and not desired_open:
        return {"status": "no_position_needed", "mode": ti["mode"]}

    # CASE 2: nothing open + want open (regime entered bull).
    if open_pos is None and desired_open:
        # Cold-start guard: only open on a *fresh* entry to bull. If
        # yesterday was already bull, the regime entry happened before
        # this variant was emitting trades — wait for the next bull
        # entry rather than chasing into mid-trend.
        if prev_open:
            return {"status": "awaiting_fresh_bull_entry",
                    "mode": ti["mode"], "mode_prev": ti.get("mode_prev")}
        tid = _open_continuous_btc_or_eth(
            variant, STRATEGY_ETH_DAILY, "ETH", "LONG",
            desired_weight, desired_lev, price, ti["mode"],
            ema_p=0, now=now,
        )
        log.info(f"[jplus_live ETH_DAILY {variant['id']}] OPENED {tid} "
                 f"ETH LONG @ ${price:,.2f}  k={desired_lev:.2f}x  "
                 f"weight={desired_weight}  mode={ti['mode']} "
                 f"(fresh entry from {ti.get('mode_prev')})")
        return {"status": "opened", "trade_id": tid}

    # CASE 3: open + regime exited bull — close.
    if not desired_open:
        trades.close_perp_trade(
            open_pos["id"], exit_price=price,
            reason=f"regime_exit_{ti['mode']}_{today_iso}",
            sleeve_name=STRATEGY_ETH_DAILY,
        )
        log.info(f"[jplus_live ETH_DAILY {variant['id']}] CLOSED {open_pos['id']} "
                 f"@ ${price:,.2f} reason=regime_exit_{ti['mode']}")
        return {"status": "closed", "trade_id": open_pos["id"]}

    # CASE 4: open and want open — daily rebalance.
    desired_qty = (capital * desired_weight * desired_lev) / price
    cur_qty = float(open_pos["current_qty"] if open_pos.get("current_qty")
                     is not None else open_pos["qty"] or 0.0)
    cur_lev = float(open_pos["current_leverage"] if open_pos.get("current_leverage")
                     is not None else open_pos["leverage"] or 1.0)

    actions: list[str] = []
    if abs(desired_qty - cur_qty) > max(1e-9, 1e-9 * abs(cur_qty)):
        if not _has_adjustment_today(open_pos["id"], today_iso,
                                      ("SCALE_UP", "SCALE_DOWN")):
            trades.apply_scale(
                open_pos["id"], new_qty=desired_qty, price=price,
                fee_usdt=0.0,
                event_time=now.isoformat(), event_date=today_iso,
                notes={"reason": "daily_rebalance", "mode": ti["mode"],
                       "weight": desired_weight, "vol_lev": desired_lev},
            )
            actions.append("scaled")
    if abs(desired_lev - cur_lev) > 1e-9:
        if not _has_adjustment_today(open_pos["id"], today_iso,
                                      ("LEVERAGE_ADJUST",)):
            trades.apply_leverage_adjust(
                open_pos["id"], new_leverage=desired_lev, price=price,
                fee_usdt=0.0,
                event_time=now.isoformat(), event_date=today_iso,
                notes={"reason": "vol_target_update", "mode": ti["mode"]},
            )
            actions.append("leverage_adjusted")

    if not actions:
        return {"status": "in_sync", "trade_id": open_pos["id"]}
    return {"status": "rebalanced", "actions": actions,
            "trade_id": open_pos["id"]}
