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
  - open trades via ``strategies.trades.open_shadow_trade`` with
    ``scheduled_exit_dt`` set so ``variant_engine._close_due_shadows``
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


# ─── R4 BTC V1 (Mon wk1-2, 06:00 → 18:00 UTC) ───────────────────────────────


def r4_btc_try_fire(variant: dict, sleeve_cfg: dict) -> dict:
    """Open R4_BTC LONG at 06:00 UTC on Mon wk1-2, scheduled to close at
    18:00 UTC same day. ``variant_engine._close_due_shadows`` handles the
    close via ``scheduled_exit_dt``.

    Mon-only since 2026-05-08 — Wednesdays moved to ``r4_btc_v2_try_fire``
    at the era-stable 04:00→14:00 window. See tools/r4_study/findings.md.

    Sizing: ``capital × weights['r4_btc'] × inner_lev × vol_lev`` where
    inner_lev is 2.5× (or 1.0× if the gate fired) and vol_lev is the
    vol-target leverage. All from ``strategies.support.jplus_inputs.today_inputs()``.

    Returns a status dict — see in-code branches for status keys.
    """
    now = clock.now_utc()
    today_iso = now.date().isoformat()

    if now.weekday() != 0:
        return {"status": "not_calendar_day"}
    if now.day > 14:
        return {"status": "not_wk_1_2"}

    if now.hour < R4_BTC_ENTRY_HOUR:
        return {"status": "before_open_window"}
    if now.hour >= R4_BTC_EXIT_HOUR:
        return {"status": "after_close_window"}

    if _has_trade_for_day(variant["id"], STRATEGY_R4_BTC, today_iso):
        return {"status": "already_open"}

    from strategies.support import jplus_inputs as core_sim
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


# ─── R4 ETH V1 (Tue 20:00 → Wed 20:00 UTC) ──────────────────────────────────


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

    tomorrow = now + timedelta(days=1)
    if tomorrow.day > 14:
        return {"status": "next_day_not_wk_1_2"}

    if now.hour < R4_ETH_ENTRY_HOUR:
        return {"status": "before_open_window"}

    if _has_trade_for_day(variant["id"], STRATEGY_R4_ETH, today_iso):
        return {"status": "already_open"}

    from strategies.support import jplus_inputs as core_sim
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


# ─── R4 V2 (Wed/Fri wk1-2, 04:00 → 14:00 UTC) ───────────────────────────────


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

    from strategies.support import jplus_inputs as core_sim
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
