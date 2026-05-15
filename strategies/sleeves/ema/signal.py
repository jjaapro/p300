"""EMA(BTC) live entry handler — continuous position with weekly-cross flips.

Per-tick reconciliation: open / hold / scale / flip / close based on
``strategies.support.jplus_inputs.today_inputs().ema_p`` and the regime-weighted sizing
target. See README.md for the state-machine table.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from strategies import trades
from strategies.support import clock, db, price_feed

from .config import STRATEGY_EMA_BTC

log = logging.getLogger("dashboard.jplus_live")


# ─── Idempotency helper for SCALE/LEV/FLIP events ───────────────────────────


def _has_adjustment_today(trade_id: str, today_iso: str,
                           event_types: tuple[str, ...]) -> bool:
    """True if any of the given event_types already exist for this trade
    on ``today_iso``. Lets the live handler re-tick safely — once a
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


def _open_continuous(variant: dict, asset: str, direction: str,
                      weight: float, lev: float, price: float,
                      mode: str, ema_p: int, now: datetime) -> str:
    """Open a continuous EMA_BTC trade (no scheduled exit; closed on
    FLIP / weekly-cross-to-zero / regime exit)."""
    return trades.open_paper_trade(
        variant=variant, sleeve_name=STRATEGY_EMA_BTC,
        asset=asset, direction=direction,
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=lev,
        reason={"sleeve": STRATEGY_EMA_BTC, "mode": mode,
                "ema_p": ema_p, "vol_lev": lev,
                "trigger": "live_open"},
        scheduled_exit_dt=None,
        regime_value=mode,
        entry_dt=now,
    )


# ─── Live handler ───────────────────────────────────────────────────────────


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

    from strategies.support import jplus_inputs as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return {"status": "no_inputs"}

    desired_ema_p = int(ti["ema_p"])
    prev_ema_p = int(ti.get("ema_p_prev", 0))
    # P2.4a: orchestrator-injected allocation, ti["weights"] fallback for tests.
    eff_w = sleeve_cfg.get("_effective_weight_pct")
    desired_weight = ((eff_w / 100.0) if eff_w is not None
                       else float(ti["weights"]["ema_btc"]))
    # P2.4c: orchestrator-injected vol scalar, ti["lev"] fallback for tests.
    eff_vol = sleeve_cfg.get("_effective_vol_scalar")
    desired_lev = float(eff_vol) if eff_vol is not None else float(ti["lev"])

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
        tid = _open_continuous(
            variant, "BTC", direction,
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
