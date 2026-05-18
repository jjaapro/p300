"""S-099 R4 live entry handlers.

Four handlers, one per dispatch key, all calendar-driven LONG entries
sized off ``strategies.support.jplus_inputs.today_inputs()``:

  r4_btc_try_fire    -> JPLUS_R4_BTC      (Mon wk1-2, 06:00 → 18:00 UTC)
  r4_eth_try_fire    -> JPLUS_R4_ETH      (Tue → Wed wk1-2, 20:00 → 20:00 UTC)
  r4_btc_v2_try_fire -> JPLUS_R4_BTC_V2   (Wed+Fri wk1-2, 04:00 → 14:00 UTC)
  r4_eth_v2_try_fire -> JPLUS_R4_ETH_V2   (Wed+Fri wk1-2, 04:00 → 14:00 UTC, ETH)

All handlers:
  - use ``strategies.support.price_feed.get_current_price`` for live execution price
    (latest closed 1m bar; ~30s lag from instant);
  - open trades via ``strategies.trades.open_paper_trade`` with
    ``scheduled_exit_dt`` set so ``orchestrator._close_due_paper_trades``
    closes them at the right time;
  - are idempotent per UTC day via the ``trades`` table existence check.

See README.md for variant calendar / window summary and edge thesis.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta

from strategies import trades
from strategies.support import clock, db, price_feed

from .config import (
    STRATEGY_R4_BTC, STRATEGY_R4_ETH,
    STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH_V2,
    R4_INNER_LEV_GATED, R4_INNER_LEV_UNGATED,
    R4_BTC_ENTRY_HOUR, R4_BTC_EXIT_HOUR,
    R4_ETH_ENTRY_HOUR, R4_ETH_EXIT_HOUR,
    R4_V2_ENTRY_HOUR, R4_V2_EXIT_HOUR,
)

log = logging.getLogger("dashboard.jplus_live")


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


# ─── Shared decide / execute helpers ────────────────────────────────────────


def _r4_decide(variant: dict, sleeve_cfg: dict, *, asset: str, strategy: str,
                weight_key: str, exit_dt) -> tuple[list, dict]:
    """Phase-1 of the two-phase dispatch for R4 sleeves.

    Pure entry — no maintenance side-effects. Reads the regime weight /
    gate / vol-scalar fallbacks (P2.4a/b/c), then emits an Intent if
    weight > 0 and price is available. Window / weekday / idempotency
    gates are the caller's responsibility (different per R4 variant).

    Returns ``(list[Intent], status_dict)``. Empty list ⇒ no entry
    (zero-weight regime / no inputs / no price). Inline
    margin_headroom.can_open REMOVED — reconcile owns it.
    """
    from strategies.support import jplus_inputs as core_sim
    from strategies.support.dispatch import Intent

    ti = core_sim.today_inputs()
    if ti is None:
        return [], {"status": "no_inputs"}

    eff_w = sleeve_cfg.get("_effective_weight_pct")
    weight = ((eff_w / 100.0) if eff_w is not None
              else ti["weights"].get(weight_key, 0.0))
    if weight <= 0:
        return [], {"status": "regime_zero_weight", "mode": ti["mode"]}

    eff_gate = sleeve_cfg.get("_effective_gate")
    if eff_gate is not None:
        inner_lev = R4_INNER_LEV_UNGATED * eff_gate.leverage_mult
    else:
        inner_lev = R4_INNER_LEV_GATED if ti["gated"] else R4_INNER_LEV_UNGATED
    eff_vol = sleeve_cfg.get("_effective_vol_scalar")
    vol_lev = float(eff_vol) if eff_vol is not None else float(ti["lev"])
    stacked_lev = inner_lev * vol_lev

    price = price_feed.get_current_price(asset)
    if price is None or price <= 0:
        return [], {"status": "no_price"}

    now = clock.now_utc()
    reason = {
        "sleeve": strategy, "mode": ti["mode"],
        "vol_lev": vol_lev, "inner_lev": inner_lev,
        "gated": ti["gated"], "trigger": "calendar_open",
        "_entry_price": price,
        "_strategy": strategy,
        "_exit_dt_iso": exit_dt.isoformat(),
        "_mode": ti["mode"],
        "_now_iso": now.isoformat(),
    }
    intent = Intent(
        asset=asset, direction="LONG",
        allocation_pct=weight * 100.0,
        leverage=stacked_lev,
        conviction=100,
        priority=float(sleeve_cfg.get("priority", 100)),
        reason=reason, scheduled_exit_dt=exit_dt,
    )
    return [intent], {"status": "decided", "weight": weight,
                       "stacked_lev": stacked_lev}


def _r4_execute(variant: dict, sleeve_cfg: dict, intent) -> dict:
    """Phase-2 of the two-phase dispatch for R4 sleeves — open the
    calendar-bounded LONG described by ``intent`` (post-reconcile)."""
    from datetime import datetime
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    strategy = reason.pop("_strategy")
    exit_dt = datetime.fromisoformat(reason.pop("_exit_dt_iso"))
    mode = reason.pop("_mode")
    now = datetime.fromisoformat(reason.pop("_now_iso"))
    weight = intent.allocation_pct / 100.0
    capital = float(variant.get("capital_usdt") or 10000)
    tid = trades.open_paper_trade(
        variant=variant, sleeve_name=strategy,
        asset=intent.asset, direction="LONG",
        entry_price=entry_price,
        allocation_pct=intent.allocation_pct,
        leverage=intent.leverage,
        reason=reason,
        scheduled_exit_dt=exit_dt,
        regime_value=mode,
        entry_dt=now,
    )
    log.info(f"[jplus_live {strategy} {variant['id']}] OPENED {tid} "
             f"{intent.asset} LONG @ ${entry_price:,.2f}  "
             f"notional=${capital * weight * intent.leverage:,.2f}  "
             f"k={intent.leverage:.2f}x  exit_due={exit_dt.isoformat()}")
    return {"status": "opened", "trade_id": tid, "entry_price": entry_price,
             "stacked_lev": intent.leverage, "weight": weight}


# ─── R4 BTC V1 (Mon wk1-2, 06:00 → 18:00 UTC) ───────────────────────────────


def r4_btc_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Variant-engine dispatch entry. Backward-compatible wrapper."""
    intents, status = r4_btc_decide(variant, sleeve_cfg)
    if not intents:
        return status
    return _r4_execute(variant, sleeve_cfg, intents[0])


def r4_btc_decide(variant: dict, sleeve_cfg: dict):
    """Open R4_BTC LONG at 06:00 UTC on Mon wk1-2, scheduled to close at
    18:00 UTC same day. Mon-only since 2026-05-08; Wednesdays moved to
    r4_btc_v2 at the era-stable 04:00→14:00 window."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() != 0:
        return [], {"status": "not_calendar_day"}
    if now.day > 14:
        return [], {"status": "not_wk_1_2"}
    if now.hour < R4_BTC_ENTRY_HOUR:
        return [], {"status": "before_open_window"}
    if now.hour >= R4_BTC_EXIT_HOUR:
        return [], {"status": "after_close_window"}
    if _has_trade_for_day(variant["id"], STRATEGY_R4_BTC, today_iso):
        return [], {"status": "already_open"}

    exit_dt = now.replace(hour=R4_BTC_EXIT_HOUR, minute=0,
                           second=0, microsecond=0)
    return _r4_decide(variant, sleeve_cfg, asset="BTC",
                       strategy=STRATEGY_R4_BTC, weight_key="r4_btc",
                       exit_dt=exit_dt)


# ─── R4 ETH V1 (Tue 20:00 → Wed 20:00 UTC) ──────────────────────────────────


def r4_eth_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Variant-engine dispatch entry. Backward-compatible wrapper."""
    intents, status = r4_eth_decide(variant, sleeve_cfg)
    if not intents:
        return status
    return _r4_execute(variant, sleeve_cfg, intents[0])


def r4_eth_decide(variant: dict, sleeve_cfg: dict):
    """Open R4_ETH LONG at Tue 20:00 UTC if next-day Wed has day ≤ 14,
    scheduled to close at Wed 20:00 UTC. Live-realism convention: sizes
    the position using TODAY's inputs (Tue's regime/lev), not tomorrow's."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() != 1:
        return [], {"status": "not_tuesday"}
    tomorrow = now + timedelta(days=1)
    if tomorrow.day > 14:
        return [], {"status": "next_day_not_wk_1_2"}
    if now.hour < R4_ETH_ENTRY_HOUR:
        return [], {"status": "before_open_window"}
    if _has_trade_for_day(variant["id"], STRATEGY_R4_ETH, today_iso):
        return [], {"status": "already_open"}

    exit_dt = tomorrow.replace(hour=R4_ETH_EXIT_HOUR, minute=0,
                                 second=0, microsecond=0)
    intents, status = _r4_decide(
        variant, sleeve_cfg, asset="ETH",
        strategy=STRATEGY_R4_ETH, weight_key="r4_eth", exit_dt=exit_dt,
    )
    if intents:
        # ETH V1 records trade_day = Wed in the reason (vs Tue today).
        intents[0].reason["trade_day"] = tomorrow.date().isoformat()
    return intents, status


# ─── R4 V2 (Wed/Fri wk1-2, 04:00 → 14:00 UTC) ───────────────────────────────


def _r4_v2_try_fire(variant: dict, asset: str, strategy: str,
                    weight_key: str, sleeve_cfg: dict | None = None) -> dict:
    """Backward-compatible legacy entry for the V2 pair. Used by direct
    callers (tests) and the dispatch wrappers below."""
    sleeve_cfg = sleeve_cfg or {}
    intents, status = _r4_v2_decide(variant, sleeve_cfg, asset=asset,
                                      strategy=strategy, weight_key=weight_key)
    if not intents:
        return status
    return _r4_execute(variant, sleeve_cfg, intents[0])


def _r4_v2_decide(variant: dict, sleeve_cfg: dict, *,
                   asset: str, strategy: str, weight_key: str):
    """Shared decide for R4_BTC_V2 / R4_ETH_V2. Wed+Fri wk1-2 04:00→14:00
    UTC entry window with scheduled 14:00 close."""
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() not in (2, 4):  # Wed or Fri
        return [], {"status": "not_calendar_day"}
    if now.day > 14:
        return [], {"status": "not_wk_1_2"}
    if now.hour < R4_V2_ENTRY_HOUR:
        return [], {"status": "before_open_window"}
    if now.hour >= R4_V2_EXIT_HOUR:
        return [], {"status": "after_close_window"}
    if _has_trade_for_day(variant["id"], strategy, today_iso):
        return [], {"status": "already_open"}

    exit_dt = now.replace(hour=R4_V2_EXIT_HOUR, minute=0,
                           second=0, microsecond=0)
    intents, status = _r4_decide(
        variant, sleeve_cfg, asset=asset, strategy=strategy,
        weight_key=weight_key, exit_dt=exit_dt,
    )
    if intents:
        intents[0].reason["window"] = "wed_fri_wk1-2_04-14_v2"
    return intents, status


def r4_btc_v2_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """R4_BTC_V2 — Wed+Fri wk1-2 04:00→14:00 UTC. Backward-compatible
    wrapper."""
    return _r4_v2_try_fire(variant, asset="BTC",
                            strategy=STRATEGY_R4_BTC_V2,
                            weight_key="r4_btc_v2",
                            sleeve_cfg=sleeve_cfg)


def r4_btc_v2_decide(variant: dict, sleeve_cfg: dict):
    return _r4_v2_decide(variant, sleeve_cfg, asset="BTC",
                          strategy=STRATEGY_R4_BTC_V2,
                          weight_key="r4_btc_v2")


def r4_eth_v2_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """R4_ETH_V2 — Wed+Fri wk1-2 04:00→14:00 UTC on ETH. Wrapper."""
    return _r4_v2_try_fire(variant, asset="ETH",
                            strategy=STRATEGY_R4_ETH_V2,
                            weight_key="r4_eth_v2",
                            sleeve_cfg=sleeve_cfg)


def r4_eth_v2_decide(variant: dict, sleeve_cfg: dict):
    return _r4_v2_decide(variant, sleeve_cfg, asset="ETH",
                          strategy=STRATEGY_R4_ETH_V2,
                          weight_key="r4_eth_v2")
