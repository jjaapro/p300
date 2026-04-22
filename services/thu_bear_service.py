"""
S-096 Thu Bear V3 enhanced — live shadow service.

Shorts BTC + ETH at Thursday 00:00 UTC and closes at Thursday 23:59 UTC,
conditional on Wednesday's (previous-day) regime being one of
{bear_trend, sell_off, chop} as classified by `regime_classifier`.

V3 "enhanced" semantics vs V1/V2:
  V1 unconditional (all regimes) — too noisy
  V2 bear_trend + sell_off only  — too restrictive
  V3 bear_trend + sell_off + chop (used by P-200)
  V4 bear only + Wk1+Wk2 only    — alternative, not selected

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

log = logging.getLogger("dashboard.thu_bear_service")

DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"
TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"

# V3 enhanced regime filter (matches backtest_thu_bear.py P-200 usage)
V3_REGIMES_ALLOWED = {"bear_trend", "sell_off", "chop"}

# V4 event-conditioned filter: Thursdays within +/-1 day of CPI or NFP,
# excluding +/-1 day of any OPEX event. Motivated by E4 event-purged CPCV
# (2026-04-19) which showed V3's Sharpe is driven by CPI/NFP-adjacent
# Thursdays and hurt by OPEX-adjacent Thursdays.
V4_INCLUDE_EVENT_TYPES = ("CPI", "NFP")
V4_EXCLUDE_EVENT_TYPES = ("OPEX_MONTHLY", "OPEX_QUARTERLY")
V4_WINDOW_DAYS = 1

# UTC hours at which we act. At exactly HH we fire; idempotency keeps us safe
# across the minute-level ticks within that hour.
ENTRY_HOUR = 0   # Thursday 00:00 UTC
EXIT_HOUR = 23   # Thursday 23:00 UTC (close before Friday roll-over)


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
        con = sqlite3.connect(str(TRADER_DB))
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
    """Return (pass, reason) for V4 event filter on this ISO date."""
    evw = _load_event_windows()
    if not evw["include"]:
        return True, "v4_event_calendar_unavailable"
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
            from regime_classifier import regime_map
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

def _next_sj_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "SJ-0001"
    num = int(row[0].split("-")[1]) + 1
    return f"SJ-{num:04d}"


def _thu_bear_trade_today(variant_id: str, today_utc: str, asset: str) -> dict | None:
    """Return today's THU_BEAR trade for this variant + asset, if any (pending/open/closed)."""
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy_variant = ? AND strategy = 'THU_BEAR' "
        "AND asset = ? AND actual_entry_time LIKE ? LIMIT 1",
        (variant_id, asset, f"{today_utc}%"),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _open_thu_bear_shadow(variant: dict, asset: str, entry_price: float,
                         allocation_pct: float, reason: dict,
                         leverage: float = 1.0) -> str:
    """Per-sleeve `leverage` multiplier applied to size_usdt and recorded on
    the trade row. Defaults to 1.0x for un-levered variants."""
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now_iso = datetime.now(timezone.utc).isoformat()
    # Exit time: Thursday 23:59 UTC — scheduled exit so the engine's close loop
    # picks it up even if Thu_Bear-specific exit tick misses.
    now = datetime.now(timezone.utc)
    exit_dt = now.replace(hour=23, minute=59, second=0, microsecond=0)
    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, 'SHORT', 'THU_BEAR', ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, reason.get("regime", "unknown"), allocation_pct,
              leverage, now_iso, exit_dt.isoformat(), variant["id"], now_iso,
              entry_price, size_usdt, qty, json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_thu_bear_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT entry_price, qty, size_usdt, direction FROM trades WHERE id=?",
        (trade_id,),
    ).fetchone()
    if row is None:
        con.close()
        return
    # SHORT: pnl = (entry - exit) * qty
    pnl_usdt = (row["entry_price"] - exit_price) * row["qty"]
    pnl_pct = (pnl_usdt / row["size_usdt"] * 100) if row["size_usdt"] > 0 else 0
    notes_suffix = f"\nTHU_BEAR_EXIT: {reason}"
    con.execute("""
        UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
            pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
            notes = COALESCE(notes,'') || ?
        WHERE id=?
    """, (now_iso, exit_price, pnl_usdt, pnl_pct, notes_suffix, trade_id))
    con.commit()
    con.close()


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Evaluate S-096 signals for this variant. Called per-minute by variant_engine.

    Parameters via sleeve_cfg.params:
      stop_loss_pct   — hard floor on per-trade loss (positive %, default 5.0)
      assets          — list, defaults to ['BTC', 'ETH']

    Opens SHORTs at the first tick of Thursday 00:xx UTC. Closes at the first
    tick of Thursday 23:xx UTC, or immediately on stop-loss hit.
    """
    from services.price_feed import _get_current_price

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    is_thursday = now.weekday() == 3

    params = sleeve_cfg.get("params") or {}
    stop_loss_pct = float(params.get("stop_loss_pct", 5.0))
    assets = params.get("assets") or ["BTC", "ETH"]
    version = (params.get("version") or "V3_enhanced").upper()
    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))
    # Split allocation across both legs (half per asset so total risk per
    # Thursday equals sleeve weight, not 2x it)
    per_asset_alloc = alloc_pct / max(len(assets), 1)

    actions: list[dict] = []

    # Stop-loss pass — runs every tick for any open THU_BEAR position
    for asset in assets:
        existing = _thu_bear_trade_today(variant["id"], today, asset)
        if existing and existing["status"] == "open":
            current = _get_current_price(asset)
            if current is None:
                continue
            entry = float(existing["entry_price"])
            # SHORT: adverse when price goes up
            live_pnl = (entry - current) / entry * 100
            if live_pnl <= -stop_loss_pct:
                _close_thu_bear_shadow(existing["id"], current,
                                        f"stop_loss {live_pnl:.2f}%")
                actions.append({"status": "sl_closed", "asset": asset,
                                 "trade_id": existing["id"], "pnl_pct": live_pnl})

    if not is_thursday:
        return {"status": "not_thursday", "actions": actions}

    # Entry window: fire at Thursday 00:xx UTC (any minute in that hour)
    if now.hour == ENTRY_HOUR:
        regime = _get_regime_for_prev_day(now)
        if regime not in V3_REGIMES_ALLOWED:
            return {"status": "regime_block", "wed_regime": regime, "actions": actions}
        # V4 additional event filter: today must be within +/-1 of CPI/NFP
        # AND NOT within +/-1 of OPEX. Graceful if calendar unavailable.
        if version.startswith("V4"):
            ok, event_reason = _v4_passes(today)
            if not ok:
                return {"status": "v4_event_block", "reason": event_reason,
                        "wed_regime": regime, "actions": actions}
        for asset in assets:
            existing = _thu_bear_trade_today(variant["id"], today, asset)
            if existing is not None:
                continue   # already fired this Thursday
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
                "regime": regime,
            }
            tid = _open_thu_bear_shadow(variant, asset, price, per_asset_alloc,
                                         reason, leverage=leverage)
            actions.append({"status": "opened", "asset": asset, "trade_id": tid,
                             "entry_price": price, "regime_prev_day": regime})
            log.info(f"[thu_bear {variant['id']}] opened {tid} {asset} SHORT @ "
                     f"{price:.2f} (prev-day regime={regime}, "
                     f"alloc={per_asset_alloc}%, k={leverage}x)")

    # Exit window: close at Thursday 23:xx UTC
    if now.hour == EXIT_HOUR:
        for asset in assets:
            existing = _thu_bear_trade_today(variant["id"], today, asset)
            if existing is None or existing["status"] != "open":
                continue
            price = _get_current_price(asset)
            if price is None:
                continue
            _close_thu_bear_shadow(existing["id"], price, "end_of_thursday")
            actions.append({"status": "eod_closed", "asset": asset,
                             "trade_id": existing["id"], "exit_price": price})
            log.info(f"[thu_bear {variant['id']}] EOD closed {existing['id']} "
                     f"{asset} @ {price:.2f}")

    return {"status": "ok", "actions": actions}
