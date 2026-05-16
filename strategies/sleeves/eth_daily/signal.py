"""ETH_DAILY live entry handler — passive ETH LONG, regime-gated.

Per-tick reconciliation: open / hold / scale / close based on the
regime weight for `eth_daily` (positive in bull modes, zero otherwise).
See README.md for the state-machine table.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from strategies import trades
from strategies.support import clock, db, price_feed

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
    return trades.open_paper_trade(
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
    """Variant-engine dispatch entry point. Returns a status dict.

    Backward-compatible wrapper: runs decide (side-effect close on
    regime exit + scale/leverage rebalance on existing position), then
    executes the OPEN Intent on fresh bull-regime entry.
    """
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    return execute_for_variant(variant, sleeve_cfg, intents[0])


def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Phase-1 of the two-phase dispatch (P2.4e/f Stage 2).

    Side-effects (always run, not subject to reconcile):
      - CASE 3 (regime exit): close on regime turning non-bull.
      - CASE 4 (daily rebalance): scale + leverage adjust on existing
        position; scale-up guarded by inline margin-headroom check
        (reconcile only sees fresh opens, not size-adjustments).

    Returns ``(list[Intent], status_dict)``. Intent is emitted only on
    CASE 2 — fresh bull-regime entry from a flat state (`prev_open ==
    False`). The position-existence question has a triggered entry
    (regime change) and a triggered exit (regime change), with daily
    rebalances as sizing maintenance in between.
    """
    from strategies.support.dispatch import Intent

    now = clock.now_utc()
    today_iso = now.date().isoformat()

    from strategies.support import jplus_inputs as core_sim
    ti = core_sim.today_inputs()
    if ti is None:
        return [], {"status": "no_inputs"}

    eff_w = sleeve_cfg.get("_effective_weight_pct")
    desired_weight = ((eff_w / 100.0) if eff_w is not None
                       else float(ti["weights"]["eth_daily"]))
    prev_weight = float(ti.get("weights_prev", {}).get("eth_daily", 0.0))
    eff_vol = sleeve_cfg.get("_effective_vol_scalar")
    desired_lev = float(eff_vol) if eff_vol is not None else float(ti["lev"])
    desired_open = desired_weight > 0.0
    prev_open = prev_weight > 0.0

    open_trades = trades.get_open_trades(variant["id"], STRATEGY_ETH_DAILY)
    open_pos = open_trades[0] if open_trades else None

    price = price_feed.get_current_price("ETH")
    if price is None or price <= 0:
        return [], {"status": "no_price"}

    capital = float(variant.get("capital_usdt") or 10000)

    # CASE 1: nothing open + don't want anything open.
    if open_pos is None and not desired_open:
        return [], {"status": "no_position_needed", "mode": ti["mode"]}

    # CASE 2: nothing open + want open — emit Intent on fresh bull entry.
    if open_pos is None and desired_open:
        if prev_open:
            return [], {"status": "awaiting_fresh_bull_entry",
                         "mode": ti["mode"], "mode_prev": ti.get("mode_prev")}
        intent = Intent(
            asset="ETH", direction="LONG",
            allocation_pct=desired_weight * 100.0,
            leverage=desired_lev,
            conviction=100,
            priority=float(sleeve_cfg.get("priority", 100)),
            reason={
                "sleeve": STRATEGY_ETH_DAILY, "mode": ti["mode"],
                "ema_p": 0, "vol_lev": desired_lev,
                "trigger": "live_open",
                "_entry_price": price,
                "_mode": ti["mode"],
                "_now_iso": now.isoformat(),
                "_mode_prev": ti.get("mode_prev"),
            },
            scheduled_exit_dt=None,
        )
        return [intent], {"status": "decided", "mode": ti["mode"]}

    # CASE 3: open + regime exited — side-effect close.
    if not desired_open:
        trades.close_perp_trade(
            open_pos["id"], exit_price=price,
            reason=f"regime_exit_{ti['mode']}_{today_iso}",
            sleeve_name=STRATEGY_ETH_DAILY,
        )
        log.info(f"[jplus_live ETH_DAILY {variant['id']}] CLOSED {open_pos['id']} "
                 f"@ ${price:,.2f} reason=regime_exit_{ti['mode']}")
        return [], {"status": "closed", "trade_id": open_pos["id"]}

    # CASE 4: open and want open — daily rebalance side-effects only.
    desired_qty = (capital * desired_weight * desired_lev) / price
    cur_qty = float(open_pos["current_qty"] if open_pos.get("current_qty")
                     is not None else open_pos["qty"] or 0.0)
    cur_lev = float(open_pos["current_leverage"] if open_pos.get("current_leverage")
                     is not None else open_pos["leverage"] or 1.0)

    actions: list[str] = []
    if abs(desired_qty - cur_qty) > max(1e-9, 1e-9 * abs(cur_qty)):
        if not _has_adjustment_today(open_pos["id"], today_iso,
                                      ("SCALE_UP", "SCALE_DOWN")):
            qty_delta = desired_qty - cur_qty
            if qty_delta > 0:
                # Scale-up: reconcile only sees fresh opens; the size
                # adjustment on an existing position keeps its inline
                # margin guard.
                from strategies.support import margin_headroom
                delta_notional = qty_delta * price
                ok, mh_reason = margin_headroom.can_open(variant, delta_notional)
                if not ok:
                    log.info(f"[jplus_live ETH_DAILY {variant['id']}] scale-up "
                             f"margin-constrained: {mh_reason} "
                             f"(delta_qty={qty_delta:.6f}, "
                             f"delta_notional={delta_notional:,.2f})")
                    actions.append("scale_up_margin_constrained")
                    qty_delta = 0.0
            if qty_delta != 0.0 or desired_qty < cur_qty:
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
        return [], {"status": "in_sync", "trade_id": open_pos["id"]}
    return [], {"status": "rebalanced", "actions": actions,
                 "trade_id": open_pos["id"]}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent) -> dict:
    """Phase-2 of the two-phase dispatch — open the fresh ETH LONG
    described by ``intent`` (post-reconcile)."""
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    mode = reason.pop("_mode")
    now_iso = reason.pop("_now_iso")
    mode_prev = reason.pop("_mode_prev")
    now = datetime.fromisoformat(now_iso)
    desired_weight = intent.allocation_pct / 100.0
    tid = _open_continuous(
        variant, desired_weight, intent.leverage, entry_price, mode, now,
    )
    log.info(f"[jplus_live ETH_DAILY {variant['id']}] OPENED {tid} "
             f"ETH LONG @ ${entry_price:,.2f}  k={intent.leverage:.2f}x  "
             f"weight={desired_weight}  mode={mode} "
             f"(fresh entry from {mode_prev})")
    return {"status": "opened", "trade_id": tid}
