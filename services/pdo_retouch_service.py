"""
PDO Retouch Long (regime-filtered) service — live dispatcher for P-300 shadow
variants.

Signal:
  1. Day opens >= gap_pct (default 2%) above previous day's OPEN (PDO)
  2. Regime filter: BTC 30d trailing return >= -10% (else skip)
  3. During the day, intraday price touches PDO level (±0.1% tolerance)

Execution per fire:
  Entry: market on the first hourly bar that contains the PDO level
  Exit:  min(hold_bars, end-of-UTC-day)   — hold_bars = 24 for BTC, 4 for ETH

Trade tags: SHADOW mode, strategy='PDO_RETOUCH', direction='LONG',
series='SJ-' prefix.

Source probes:
  - diagnostic_pdo_retouch_filter_corr.py  (regime filter + correlation)
  - diagnostic_pdo_retouch_sl_sweep.py     (SL rejected — no per-trade stop)
  - diagnostic_pdo_retouch_regime_threshold.py  (-10% filter pre-commit)
Backtest filtered: BTC n=89 Sh 0.40, ETH n=122 Sh 1.23 per-trade (hi-fidelity).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"
TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"

GAP_THRESHOLD_PCT = 2.0
REGIME_THRESHOLD_PCT = -10.0
TOUCH_TOL_PCT = 0.10  # 0.1% tolerance around PDO
HOLD_BARS_BY_ASSET = {"BTC": 24, "ETH": 4}


# ─── Data loaders ─────────────────────────────────────────────────────────────

def _load_today_open_and_pdo(asset: str) -> dict | None:
    """Return {date, pdo, today_open, gap_pct} or None if data insufficient."""
    table = f"{asset.lower()}_1m"
    con = sqlite3.connect(str(TRADER_DB))
    # Last two distinct UTC days — need prev day open (PDO) and today's open
    row = con.execute(
        f"SELECT MIN(open_time), MAX(open_time) FROM {table}"
    ).fetchone()
    con.close()
    if not row or row[0] is None:
        return None
    now = datetime.now(timezone.utc)
    today_utc_str = now.strftime("%Y-%m-%d")
    yesterday_utc = (now - timedelta(days=1))
    yesterday_utc_str = yesterday_utc.strftime("%Y-%m-%d")

    con = sqlite3.connect(str(TRADER_DB))
    # PDO: first minute of prev UTC day
    pdo_row = con.execute(
        f"SELECT open_time, open FROM {table} "
        f"WHERE open_time >= ? AND open_time < ? ORDER BY open_time LIMIT 1",
        (int(yesterday_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000),
         int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)),
    ).fetchone()
    # Today's open
    today_row = con.execute(
        f"SELECT open_time, open FROM {table} "
        f"WHERE open_time >= ? ORDER BY open_time LIMIT 1",
        (int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000),),
    ).fetchone()
    con.close()
    if pdo_row is None or today_row is None:
        return None
    pdo = float(pdo_row[1])
    today_open = float(today_row[1])
    gap_pct = (today_open - pdo) / pdo * 100 if pdo > 0 else 0
    return {"date": today_utc_str, "pdo": pdo, "today_open": today_open,
            "gap_pct": gap_pct, "asset": asset}


def _btc_30d_return_pct() -> float | None:
    """BTC 30d trailing return in %. Used as portfolio regime filter
    (shared across assets — per PDO spec)."""
    now = datetime.now(timezone.utc)
    con = sqlite3.connect(str(TRADER_DB))
    # Latest close
    now_row = con.execute(
        "SELECT open_time, close FROM btc_1m ORDER BY open_time DESC LIMIT 1"
    ).fetchone()
    # Close ~30 days ago
    thirty_ago_ms = int((now - timedelta(days=30)).timestamp() * 1000)
    old_row = con.execute(
        "SELECT open_time, close FROM btc_1m WHERE open_time >= ? ORDER BY open_time LIMIT 1",
        (thirty_ago_ms,),
    ).fetchone()
    con.close()
    if now_row is None or old_row is None:
        return None
    return (float(now_row[1]) - float(old_row[1])) / float(old_row[1]) * 100


def _get_hourly_bar_for_today(asset: str) -> dict | None:
    """Return latest hourly bar with high/low for touch detection.
    Touch is detected across the current hour — the latest-observed (not
    yet closed) hour is examined."""
    table = f"{asset.lower()}_1m"
    now = datetime.now(timezone.utc)
    hour_start_ms = int(now.replace(minute=0, second=0, microsecond=0).timestamp() * 1000)
    con = sqlite3.connect(str(TRADER_DB))
    row = con.execute(
        f"SELECT MIN(low), MAX(high), close FROM {table} WHERE open_time >= ?",
        (hour_start_ms,),
    ).fetchone()
    con.close()
    if row is None or row[0] is None:
        return None
    return {"low": float(row[0]), "high": float(row[1]), "close": float(row[2])}


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _next_sj_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "SJ-0001"
    num = int(row[0].split("-")[1]) + 1
    return f"SJ-{num:04d}"


def _get_open_pdo_trade(variant_id: str, asset: str) -> dict | None:
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy_variant=? AND strategy='PDO_RETOUCH' "
        "AND asset=? AND status='open' LIMIT 1",
        (variant_id, asset),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _pdo_action_today(variant_id: str, asset: str, today_utc: str) -> bool:
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant=? AND strategy='PDO_RETOUCH' "
        "AND asset=? AND actual_entry_time LIKE ? LIMIT 1",
        (variant_id, asset, f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_pdo_shadow(variant: dict, asset: str, entry_price: float,
                     allocation_pct: float, leverage: float,
                     hold_hours: int, reason: dict) -> str:
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    # Exit time: min(now + hold_hours, end of UTC day)
    end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    by_hold = now + timedelta(hours=hold_hours)
    exit_dt = min(end_of_day, by_hold)

    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, 'LONG', 'PDO_RETOUCH', ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, reason.get("regime", "unknown"), allocation_pct,
              leverage, now_iso, exit_dt.isoformat(), variant["id"], now_iso,
              entry_price, size_usdt, qty, json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_pdo_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    cost = 10.0 / 10000.0  # 10bp RT
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT entry_price, qty, size_usdt FROM trades WHERE id=?", (trade_id,)
    ).fetchone()
    if row is None:
        con.close(); return
    pnl_usdt = (exit_price - row["entry_price"]) * row["qty"] - row["size_usdt"] * cost
    pnl_pct = (pnl_usdt / row["size_usdt"] * 100) if row["size_usdt"] > 0 else 0
    notes_suffix = f"\nPDO_EXIT: {reason}"
    con.execute("""
        UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
            pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
            notes = COALESCE(notes,'') || ?
        WHERE id=?
    """, (now_iso, exit_price, pnl_usdt, pnl_pct, notes_suffix, trade_id))
    con.commit(); con.close()


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Per-tick dispatch: check for PDO retouch opportunity on BTC+ETH.

    sleeve_cfg.params:
      assets: list, default ['BTC','ETH']
      leverage: per-sleeve multiplier (via _effective_leverage)

    Idempotent per (variant, asset, day) — one trade per day max.
    Hourly-granular touch detection (service expected to tick at <=1h cadence).
    """
    from services.price_feed import _get_current_price

    params = sleeve_cfg.get("params") or {}
    assets = params.get("assets", ["BTC", "ETH"])
    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))
    per_asset_alloc = alloc_pct / max(1, len(assets))

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []

    # Regime check once
    btc_30d = _btc_30d_return_pct()
    regime_ok = btc_30d is not None and btc_30d >= REGIME_THRESHOLD_PCT

    for asset in assets:
        hold_hours = HOLD_BARS_BY_ASSET.get(asset, 24)

        # Manage existing open position first (scheduled exit fires via engine's
        # close-due loop; here we only check immediate hold-bar hit)
        open_trade = _get_open_pdo_trade(variant["id"], asset)
        if open_trade:
            # Check if hold_bars have passed since entry
            entry_time = datetime.fromisoformat(open_trade["actual_entry_time"])
            now = datetime.now(timezone.utc)
            if (now - entry_time).total_seconds() >= hold_hours * 3600:
                try:
                    exit_price = _get_current_price(asset)
                    _close_pdo_shadow(open_trade["id"], exit_price, f"hold_{hold_hours}h")
                    log.info(f"[pdo {variant['id']} {asset}] closed {open_trade['id']} "
                             f"@ {exit_price:.2f} (hold {hold_hours}h)")
                    results.append({"asset": asset, "status": "closed_hold",
                                    "trade_id": open_trade["id"]})
                    continue
                except Exception as e:
                    log.exception(f"[pdo {variant['id']} {asset}] close error: {e}")
                    results.append({"asset": asset, "status": "error", "error": str(e)})
                    continue
            results.append({"asset": asset, "status": "open_waiting",
                            "trade_id": open_trade["id"]})
            continue

        # Already fired today (one-trade-per-day rule)
        if _pdo_action_today(variant["id"], asset, today_utc):
            results.append({"asset": asset, "status": "already_fired_today"})
            continue

        # Regime filter
        if not regime_ok:
            results.append({"asset": asset, "status": "regime_block",
                            "btc_30d_pct": btc_30d})
            continue

        # Load today's open + PDO
        sig = _load_today_open_and_pdo(asset)
        if sig is None:
            results.append({"asset": asset, "status": "data_missing"})
            continue

        # Gap filter
        if sig["gap_pct"] < GAP_THRESHOLD_PCT:
            results.append({"asset": asset, "status": "no_gap",
                            "gap_pct": round(sig["gap_pct"], 2)})
            continue

        # Touch detection: current hour's bar range contains PDO?
        hr = _get_hourly_bar_for_today(asset)
        if hr is None:
            results.append({"asset": asset, "status": "no_hour_bar"})
            continue
        pdo_hi = sig["pdo"] * (1 + TOUCH_TOL_PCT / 100)
        pdo_lo = sig["pdo"] * (1 - TOUCH_TOL_PCT / 100)
        touched = hr["low"] <= pdo_hi and hr["high"] >= pdo_lo
        if not touched:
            results.append({"asset": asset, "status": "no_touch",
                            "pdo": round(sig["pdo"], 2),
                            "hour_low": round(hr["low"], 2),
                            "hour_high": round(hr["high"], 2)})
            continue

        # Fire
        try:
            entry_price = _get_current_price(asset)
        except Exception as e:
            log.exception(f"[pdo {variant['id']} {asset}] price fetch error: {e}")
            continue

        reason = {
            "trigger": "PDO_retouch",
            "variant_id": variant["id"],
            "sleeve": "PDO-L-RF",
            "asset": asset,
            "pdo": sig["pdo"], "today_open": sig["today_open"],
            "gap_pct": round(sig["gap_pct"], 2),
            "btc_30d_pct": round(btc_30d, 2) if btc_30d is not None else None,
            "hold_hours": hold_hours,
            "regime": "gap_up_retrace",
        }
        tid = _open_pdo_shadow(variant, asset, entry_price, per_asset_alloc,
                               leverage, hold_hours, reason)
        log.info(f"[pdo {variant['id']} {asset}] opened {tid} @ {entry_price:.2f} "
                 f"(PDO={sig['pdo']:.2f}, gap={sig['gap_pct']:.2f}%, "
                 f"alloc={per_asset_alloc}%, lev={leverage:.1f}x)")
        results.append({"asset": asset, "status": "opened", "trade_id": tid,
                        "entry_price": entry_price, "pdo": sig["pdo"]})

    non_neutral = [r for r in results if r["status"] not in
                   ("no_gap", "no_touch", "regime_block",
                    "already_fired_today", "open_waiting")]
    return {
        "status": "dispatched" if non_neutral else "no_action",
        "date": today_utc,
        "btc_30d_pct": btc_30d,
        "regime_ok": regime_ok,
        "assets": results,
    }
