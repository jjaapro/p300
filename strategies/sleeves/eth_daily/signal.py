"""ETH_DAILY live entry handler — passive ETH LONG, regime-gated.

Per-tick reconciliation: open / hold / scale / close based on the
regime weight for `eth_daily` (positive in bull modes, zero otherwise).
See README.md for the state-machine table.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from services import clock, db, price_feed, trades

from .config import STRATEGY_ETH_DAILY

log = logging.getLogger("dashboard.jplus_live")


# ─── Idempotency helper for SCALE/LEV events ────────────────────────────────


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


def _open_continuous(variant: dict, weight: float, lev: float,
                      price: float, mode: str, now: datetime) -> str:
    """Open a continuous ETH_DAILY LONG (no scheduled exit; closed on
    regime exit from bull)."""
    return trades.open_shadow_trade(
        variant=variant, sleeve_name=STRATEGY_ETH_DAILY,
        asset="ETH", direction="LONG",
        entry_price=price,
        allocation_pct=weight * 100.0,
        leverage=lev,
        reason={"sleeve": STRATEGY_ETH_DAILY, "mode": mode,
                "ema_p": 0, "vol_lev": lev,
                "trigger": "live_open"},
        scheduled_exit_dt=None,
        regime_value=mode,
        entry_dt=now,
    )


# ─── Live handler ───────────────────────────────────────────────────────────


def eth_daily_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Continuous ETH LONG that exists only while regime ∈ {strong_bull,
    mild_bull}. Same per-tick state machine shape as EMA_BTC, minus the
    direction-flip case (always LONG) and with explicit OPEN-on-bull-
    enter / CLOSE-on-bull-exit transitions."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    from strategies.support import jplus_inputs as core_sim
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
        tid = _open_continuous(
            variant, desired_weight, desired_lev, price, ti["mode"], now,
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
