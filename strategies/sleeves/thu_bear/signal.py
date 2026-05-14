"""
S-096 Thu Bear V3 enhanced — live shadow service.

Shorts BTC + ETH at Thursday 00:00 UTC and closes at Friday 01:00 UTC,
conditional on Wednesday's (previous-day) regime being one of
{bear_trend, sell_off, chop} as classified by `regime_classifier`.

Exit timing matches the Pine reference (`process_orders_on_close=true`
fills the Fri-00:00 bar at its close ≈ Fri 01:00). The earlier Thu 23:00
exit was sacrificing the post-23:00 Thursday move on big-down weeks
(e.g. 2025-03-06: Pine +5.0% vs Live +0.4%, almost entirely the missed
last-hour move) for no documented benefit beyond UTC day-bucketing.

V3 "enhanced" semantics vs V1/V2:
  V1 unconditional (all regimes) — too noisy
  V2 bear_trend + sell_off only  — too restrictive
  V3 bear_trend + sell_off + chop (used by P-200)
  V4 bear only + Wk1+Wk2 only    — alternative, not selected

WARNING — V4 event-conditioned filter is IN-SAMPLE: it was derived from E4
event-purged CPCV attribution of V3's Thursdays (2026-04-19). Any V4 backtest
that reuses the same CPI/NFP/OPEX series it was filtered on will outperform
V3 by construction. Paper live is the first genuine out-of-sample record;
until then, treat V4 backtest Sharpe as informative about curve-fit risk,
not expected live performance.

Per-variant params:
  stop_loss_pct — hard floor on per-trade loss (e.g. 5.0 for -5%)
  assets        — default ['BTC', 'ETH']

Idempotent: only opens once per Thursday, closes at the end-of-Thursday tick
or on stop loss. Positions are scoped to the variant_id — multiple variants
composing S-096 each get their own independent shadow trades.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from strategies.support import clock
from strategies.support import db

log = logging.getLogger("dashboard.thu_bear_service")

from .config import (
    COST_BP_RT,
    V3_REGIMES_ALLOWED,
    V4_INCLUDE_EVENT_TYPES, V4_EXCLUDE_EVENT_TYPES, V4_WINDOW_DAYS,
    ENTRY_HOUR, EXIT_HOUR, EXIT_WEEKDAY,
)


# ─── Event-calendar lookup (for V4) ──────────────────────────────────────────

_event_cache: dict[str, set[str]] | None = None


def _load_event_windows() -> dict[str, set[str]]:
    """Return {'include': set of ISO dates, 'exclude': set of ISO dates} for V4.

    Cached per-process (event calendar is static per session). Requires the
    `scheduled_events` table in trader.db.
    """
    global _event_cache
    if _event_cache is not None:
        return _event_cache
    include: set[str] = set()
    exclude: set[str] = set()
    try:
        con = sqlite3.connect(str(db.TRADER_DB))
        for kind, bucket in [(V4_INCLUDE_EVENT_TYPES, include),
                              (V4_EXCLUDE_EVENT_TYPES, exclude)]:
            qmarks = ",".join("?" for _ in kind)
            rows = con.execute(
                f"SELECT DISTINCT date FROM scheduled_events "
                f"WHERE event_type IN ({qmarks})", list(kind)
            ).fetchall()
            for (d,) in rows:
                dt = datetime.strptime(d, "%Y-%m-%d")
                for k in range(-V4_WINDOW_DAYS, V4_WINDOW_DAYS + 1):
                    bucket.add((dt + timedelta(days=k)).strftime("%Y-%m-%d"))
        con.close()
    except Exception as e:
        log.warning(f"[thu_bear] V4 event-window load failed: {e} — V4 will allow-all")
        include = set()
        exclude = set()
    _event_cache = {"include": include, "exclude": exclude}
    return _event_cache


def _v4_passes(today_utc_date: str) -> tuple[bool, str]:
    """Return (pass, reason) for V4 event filter on this ISO date.

    Fails closed: if the event calendar is missing/empty, V4 skips the
    Thursday (rather than silently degrading to V1 unconditional shorts).
    Repopulate `scheduled_events` via `fetch_events.py` to re-enable firing.
    """
    evw = _load_event_windows()
    if not evw["include"]:
        return False, "v4_event_calendar_unavailable_fail_closed"
    if today_utc_date in evw["exclude"]:
        return False, "v4_opex_adjacent"
    if today_utc_date not in evw["include"]:
        return False, "v4_no_cpi_nfp_adjacency"
    return True, "v4_event_adjacent"


# ─── Regime lookup ───────────────────────────────────────────────────────────

_regime_map_cache: dict[str, dict] = {}
_regime_map_cache_day: str = ""


def _get_regime_for_prev_day(today_utc: datetime) -> str | None:
    """Return the regime_classifier label for the day BEFORE today_utc (Wed for
    a Thu call). Cached per UTC day — regime_classifier is stable intraday."""
    global _regime_map_cache, _regime_map_cache_day
    today_key = today_utc.strftime("%Y-%m-%d")
    if _regime_map_cache_day != today_key:
        try:
            from strategies.support.regime_tactical import regime_map
            _regime_map_cache = {
                "BTC": regime_map("BTC"),
            }
            _regime_map_cache_day = today_key
        except Exception as e:
            log.warning(f"[thu_bear] regime_map load failed: {e}")
            return None
    prev = today_utc - timedelta(days=1)
    prev_key = prev.strftime("%Y-%m-%d")
    return _regime_map_cache.get("BTC", {}).get(prev_key)


# ─── DB helpers (variant-scoped) ─────────────────────────────────────────────

def _thu_bear_trade_today(variant_id: str, today_utc: str, asset: str) -> dict | None:
    """Return today's THU_BEAR trade for this variant + asset, if any (pending/open/closed)."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy_variant = ? AND strategy = 'THU_BEAR' "
        "AND asset = ? AND actual_entry_time LIKE ? LIMIT 1",
        (variant_id, asset, f"{today_utc}%"),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _get_open_thu_bear_trades(variant_id: str, asset: str) -> list[dict]:
    """THU_BEAR is multi-asset; delegates to strategies.trades.get_open_trades."""
    from strategies.trades import get_open_trades
    return get_open_trades(variant_id, "THU_BEAR", asset=asset)


def _open_thu_bear_shadow(variant: dict, asset: str, entry_price: float,
                         allocation_pct: float, reason: dict,
                         leverage: float = 1.0) -> str:
    """Open a THU_BEAR shadow trade — delegates to strategies.trades.open_shadow_trade.
    Scheduled exit: Friday 01:00 UTC (matches Pine reference). The engine's
    close-due loop picks this up as a fallback if our own EXIT_HOUR tick misses."""
    from strategies.trades import open_shadow_trade
    now = clock.now_utc()
    exit_dt = (now + timedelta(days=1)).replace(
        hour=EXIT_HOUR, minute=0, second=0, microsecond=0)
    return open_shadow_trade(
        variant=variant, sleeve_name="THU_BEAR",
        asset=asset, direction="SHORT",
        entry_price=entry_price, allocation_pct=allocation_pct, leverage=leverage,
        reason=reason, scheduled_exit_dt=exit_dt,
    )


def _close_thu_bear_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    """Sleeve close — delegates to strategies.trades.close_perp_trade."""
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="THU_BEAR",
                     cost_bp_rt=COST_BP_RT, apply_funding=True)


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Evaluate S-096 signals for this variant. Called per-minute by orchestrator.

    Parameters via sleeve_cfg.params:
      stop_loss_pct   — hard floor on per-trade loss (positive %, default 5.0)
      assets          — list, defaults to ['BTC', 'ETH']

    Opens SHORTs at the first tick of Thursday 00:xx UTC. Closes at the first
    tick of Friday EXIT_HOUR:xx UTC (= 01:xx, matching Pine), or immediately
    on stop-loss hit.
    """
    from strategies.support.price_feed import _get_current_price
    from strategies.support.risk_config import effective_price_move_sl_pct

    now = clock.now_utc()
    today = now.strftime("%Y-%m-%d")
    is_thursday = now.weekday() == 3
    is_friday = now.weekday() == EXIT_WEEKDAY

    params = sleeve_cfg.get("params") or {}
    stop_loss_pct = float(params.get("stop_loss_pct", 5.0))
    assets = params.get("assets") or ["BTC", "ETH"]
    version = (params.get("version") or "V3_enhanced").upper()
    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))
    # SL semantic: price_move leaves the configured pct as-is; margin divides
    # by leverage so a 10% margin-loss trigger = 2% price-move at k=5x.
    sl_price_thresh = effective_price_move_sl_pct(stop_loss_pct, leverage)
    # Split allocation across both legs (half per asset so total risk per
    # Thursday equals sleeve weight, not 2x it)
    per_asset_alloc = alloc_pct / max(len(assets), 1)

    actions: list[dict] = []

    # Step 1: SL + stray-open sweep for every open THU_BEAR trade in this
    # (variant, asset). Iterate the FULL list so trades left open by previous
    # Thursdays (the old bug) get stopped out instead of accumulating.
    open_by_asset: dict[str, list[dict]] = {
        asset: _get_open_thu_bear_trades(variant["id"], asset) for asset in assets
    }
    from strategies.support.sleeves import is_sl_hit
    for asset, opens in open_by_asset.items():
        if not opens:
            continue
        current = _get_current_price(asset)
        if current is None:
            continue
        still_open: list[dict] = []
        for tr in opens:
            hit, pnl = is_sl_hit(tr["direction"], float(tr["entry_price"]),
                                 current, sl_price_thresh)
            if hit:
                _close_thu_bear_shadow(tr["id"], current,
                                        f"stop_loss {pnl:.2f}%")
                actions.append({"status": "sl_closed", "asset": asset,
                                 "trade_id": tr["id"], "pnl_pct": pnl})
            else:
                still_open.append(tr)
        open_by_asset[asset] = still_open

    # Outside the entry/exit windows we still want SL coverage (handled above)
    # but no entry/exit action.
    if not (is_thursday or is_friday):
        return {"status": "not_thursday_or_friday", "actions": actions}

    # Step 2: Entry window — fire at Thursday 00:xx UTC. Single-open invariant:
    # only open if NO open THU_BEAR trade for (variant, asset) exists.
    if is_thursday and now.hour == ENTRY_HOUR:
        regime = _get_regime_for_prev_day(now)
        if regime not in V3_REGIMES_ALLOWED:
            return {"status": "regime_block", "wed_regime": regime, "actions": actions}
        if version.startswith("V4"):
            ok, event_reason = _v4_passes(today)
            if not ok:
                return {"status": "v4_event_block", "reason": event_reason,
                        "wed_regime": regime, "actions": actions}
        for asset in assets:
            # Block if any trade already open for this asset — invariant.
            if open_by_asset.get(asset):
                continue
            # Also respect per-Thursday idempotency: no second open if a
            # THU_BEAR trade was entered today on this asset (even if closed).
            if _thu_bear_trade_today(variant["id"], today, asset) is not None:
                continue
            price = _get_current_price(asset)
            if price is None:
                log.warning(f"[thu_bear {variant['id']}] no {asset} price — skip entry")
                continue
            reason = {
                "trigger": f"S-096_thu_bear_{version.lower()}",
                "variant_id": variant["id"],
                "sleeve": "THU_BEAR",
                "version": version,
                "regime_prev_day": regime,
                "entry_hour_utc": ENTRY_HOUR,
                "exit_hour_utc": EXIT_HOUR,
                "stop_loss_pct": stop_loss_pct,
                "sl_semantic_price_thresh_pct": sl_price_thresh,
                "regime": regime,
            }
            tid = _open_thu_bear_shadow(variant, asset, price, per_asset_alloc,
                                         reason, leverage=leverage)
            # Track in memory so a subsequent within-tick exit pass sees it.
            open_by_asset.setdefault(asset, []).append({
                "id": tid, "entry_price": price, "direction": "SHORT",
                "asset": asset, "status": "open",
            })
            actions.append({"status": "opened", "asset": asset, "trade_id": tid,
                             "entry_price": price, "regime_prev_day": regime})
            log.info(f"[thu_bear {variant['id']}] opened {tid} {asset} SHORT @ "
                     f"{price:.2f} (prev-day regime={regime}, "
                     f"alloc={per_asset_alloc}%, k={leverage}x)")

    # Step 3: Exit window — close EVERY open THU_BEAR trade for (variant, asset)
    # at Friday EXIT_HOUR:xx UTC. Sweeping all open trades (not just this
    # Thursday's) ensures any trade left open by a prior Thursday's missed
    # close gets picked up here.
    if is_friday and now.hour == EXIT_HOUR:
        for asset in assets:
            opens = open_by_asset.get(asset) or []
            if not opens:
                continue
            price = _get_current_price(asset)
            if price is None:
                continue
            for tr in opens:
                _close_thu_bear_shadow(tr["id"], price, "scheduled_close_friday")
                actions.append({"status": "scheduled_closed", "asset": asset,
                                 "trade_id": tr["id"], "exit_price": price})
                log.info(f"[thu_bear {variant['id']}] Friday-close {tr['id']} "
                         f"{asset} @ {price:.2f}")
            open_by_asset[asset] = []

    return {"status": "ok", "actions": actions}
