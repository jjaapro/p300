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

from services import clock
from services import db

log = logging.getLogger(__name__)

GAP_THRESHOLD_PCT = 2.0
REGIME_THRESHOLD_PCT = -10.0
TOUCH_TOL_PCT = 0.10  # 0.1% tolerance around PDO
HOLD_BARS_BY_ASSET = {"BTC": 24, "ETH": 4}


# ─── Data loaders ─────────────────────────────────────────────────────────────

def _bar_day_start(now: datetime) -> datetime:
    """The UTC midnight of the calendar day the just-closed 1H bar belongs to.

    The just-closed bar at clock T covers [T-1h, T). Its 'day' is the date
    of T-1h. At T = HH:00 with HH > 0 this equals T's date; at T = 00:00
    this is the previous day. Pine's setupDay/PDO/CDO logic follows the
    bar's day, not the clock's day, so all PDO state must be keyed on this.
    """
    return (now - timedelta(hours=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)


def _load_today_open_and_pdo(asset: str) -> dict | None:
    """Return {date, pdo, today_open, gap_pct} for the day of the just-closed
    1H bar, or None if data insufficient. PDO = open of the prior day's
    first 1m bar; CDO = open of bar_day's first 1m bar."""
    table = f"{asset.lower()}_1m"
    now = clock.now_utc()
    bar_day_start = _bar_day_start(now)
    today_utc_str = bar_day_start.strftime("%Y-%m-%d")
    yesterday_start = bar_day_start - timedelta(days=1)
    today_start_ms = int(bar_day_start.timestamp() * 1000)
    yesterday_start_ms = int(yesterday_start.timestamp() * 1000)
    upper_ms = clock.now_ts_ms()

    con = sqlite3.connect(str(db.TRADER_DB))
    # PDO: first minute of prev UTC day
    pdo_row = con.execute(
        f"SELECT open_time, open FROM {table} "
        f"WHERE open_time >= ? AND open_time < ? ORDER BY open_time LIMIT 1",
        (yesterday_start_ms, today_start_ms),
    ).fetchone()
    # Today's open (first minute of today, ≤ now)
    today_row = con.execute(
        f"SELECT open_time, open FROM {table} "
        f"WHERE open_time >= ? AND open_time <= ? ORDER BY open_time LIMIT 1",
        (today_start_ms, upper_ms),
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
    """BTC 30d trailing return matching Pine's request.security("60",
    [close[1], close[721]]) at the touch-evaluation bar.

    At the just-closed 1H bar that ends at clock T, Pine evaluates:
        btcPrev    = close[1]   -> close of bar before the just-closed bar
                                  = price at T-1h
        btc30dAgo  = close[721] -> close of bar 721 1H bars ago
                                  = price at T-721h
    ratio = (btcPrev - btc30dAgo) / btc30dAgo * 100
    The 720-hour gap = exactly 30 days.

    Reads cd_spot_binance (the same BINANCE:BTCUSDT 1H feed Pine subscribes
    to) so the regime number aligns to Pine within rounding."""
    upper_ts = clock.now_ts()
    # cd_spot_binance.timestamp = bar OPEN in seconds; bar covers [ts, ts+3600).
    # Bar that closes at T-1h has open_ts = T-7200.
    prev_bar_open_ts = upper_ts - 7200
    # Bar that closes at T-721h has open_ts = T - 722h.
    old_bar_open_ts = upper_ts - 722 * 3600

    con = sqlite3.connect(str(db.TRADER_DB))
    # Use a ±1h window to tolerate timestamp jitter in cd_spot_binance
    prev_row = con.execute(
        "SELECT close FROM cd_spot_binance "
        "WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
        (prev_bar_open_ts - 3600, prev_bar_open_ts + 3600),
    ).fetchone()
    old_row = con.execute(
        "SELECT close FROM cd_spot_binance "
        "WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
        (old_bar_open_ts - 3600, old_bar_open_ts + 3600),
    ).fetchone()
    con.close()
    if prev_row is None or old_row is None:
        return None
    prev_close = float(prev_row[0])
    old_close = float(old_row[0])
    if old_close <= 0:
        return None
    return (prev_close - old_close) / old_close * 100


def _get_hourly_bar_for_today(asset: str) -> dict | None:
    """Return the JUST-CLOSED 1H bar (low/high/close) for touch detection.

    Pine reference uses `process_orders_on_close=true` — the strategy evaluates
    a touch when a 1H bar CLOSES (its full [low, high] range is final). To
    match: at any tick within hour H, look at the bar that closed at the start
    of H, i.e. covering [H-1:00, H:00). At HH:00:00 sharp this is the bar that
    just finished; later in the hour we still report the same just-closed bar
    so live and replay produce the same once-per-hour decision.

    Returns None until at least one full prior 1H bar exists in the table.
    """
    table = f"{asset.lower()}_1m"
    now = clock.now_utc()
    cur_hour_start = now.replace(minute=0, second=0, microsecond=0)
    prev_hour_start_ms = int((cur_hour_start - timedelta(hours=1)).timestamp() * 1000)
    cur_hour_start_ms = int(cur_hour_start.timestamp() * 1000)
    con = sqlite3.connect(str(db.TRADER_DB))
    row = con.execute(
        f"SELECT MIN(low), MAX(high) FROM {table} "
        f"WHERE open_time >= ? AND open_time < ?",
        (prev_hour_start_ms, cur_hour_start_ms),
    ).fetchone()
    # Close of the just-closed 1H bar = close of its last 1m bar
    close_row = con.execute(
        f"SELECT close FROM {table} "
        f"WHERE open_time >= ? AND open_time < ? "
        f"ORDER BY open_time DESC LIMIT 1",
        (prev_hour_start_ms, cur_hour_start_ms),
    ).fetchone()
    con.close()
    if row is None or row[0] is None or close_row is None:
        return None
    return {"low": float(row[0]), "high": float(row[1]),
            "close": float(close_row[0])}


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _get_open_pdo_trades(variant_id: str, asset: str) -> list[dict]:
    """PDO_RETOUCH is multi-asset; delegates to services.trades.get_open_trades."""
    from services.trades import get_open_trades
    return get_open_trades(variant_id, "PDO_RETOUCH", asset=asset)


def _pdo_action_for_bar_day(variant_id: str, asset: str,
                             bar_day_start: datetime) -> bool:
    """True if a PDO trade was fired for this (variant, asset, bar_day).

    Bar_day's setupDay can fire entries at any clock between bar_day's 01:00
    UTC (close of bar [00:00, 01:00)) and bar_day+1's 00:00 UTC (close of
    bar [23:00, 00:00 next day)). So the actual_entry_time of a bar_day
    trade falls in [bar_day 01:00 UTC, bar_day+1 01:00 UTC).
    """
    lower = (bar_day_start + timedelta(hours=1)).isoformat()
    upper = (bar_day_start + timedelta(days=1, hours=1)).isoformat()
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant=? AND strategy='PDO_RETOUCH' "
        "AND asset=? AND actual_entry_time >= ? AND actual_entry_time < ? LIMIT 1",
        (variant_id, asset, lower, upper),
    ).fetchone()
    con.close()
    return row is not None


def _open_pdo_shadow(variant: dict, asset: str, entry_price: float,
                     allocation_pct: float, leverage: float,
                     hold_hours: int, reason: dict) -> str:
    """Open a PDO_RETOUCH shadow trade — delegates to services.trades.open_shadow_trade.

    Scheduled exit: Pine's ``else if newDay`` closes at the close of the
    first 1H bar of the day AFTER bar_day (= bar_day+1 at 01:00 UTC),
    capped by hold_hours (HoldLimit). Bar_day = day of the just-closed bar,
    derived from ``now`` so trades fired at bar_day+1's 00:00 UTC (against
    bar_day's last-hour bar) get the next-bar exit, not a 25h DayEnd.
    """
    from services.trades import open_shadow_trade
    now = clock.now_utc()
    bar_day_start = _bar_day_start(now)
    day_after_01_utc = bar_day_start + timedelta(days=1, hours=1)
    by_hold = now + timedelta(hours=hold_hours)
    exit_dt = min(day_after_01_utc, by_hold)
    return open_shadow_trade(
        variant=variant, sleeve_name="PDO_RETOUCH",
        asset=asset, direction="LONG",
        entry_price=entry_price, allocation_pct=allocation_pct, leverage=leverage,
        reason=reason, scheduled_exit_dt=exit_dt,
    )


def _close_pdo_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    """Sleeve close — delegates to services.trades.close_perp_trade.

    No funding modeling: PDO BTC holds are scheduled-24h, ETH 4h. A
    24h window crosses 3 funding settlements at ~5bp each (~5-15bp
    impact in either direction); skipping it is a deliberate
    conservative-and-rough simplification, documented in
    AUDIT_2026_05_04 "PDO intraday" rationale. The asymmetry is bounded
    and should be quantified vs the strict-funding alternative once
    enough live PDO trades have accumulated."""
    from services.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="PDO_RETOUCH",
                     cost_bp_rt=10.0, apply_funding=False)


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

    now_utc = clock.now_utc()
    bar_day_start = _bar_day_start(now_utc)
    bar_day_str = bar_day_start.strftime("%Y-%m-%d")
    results = []

    # Regime check once
    btc_30d = _btc_30d_return_pct()
    regime_ok = btc_30d is not None and btc_30d >= REGIME_THRESHOLD_PCT

    for asset in assets:
        hold_hours = HOLD_BARS_BY_ASSET.get(asset, 24)

        # Manage existing open positions first — iterate ALL open PDO trades
        # for this (variant, asset); close any whose hold_hours has elapsed.
        # Invariant is single-open; sweeping the full list cleans up any
        # stray legacy opens.
        open_trades = _get_open_pdo_trades(variant["id"], asset)
        still_open: list[dict] = []
        for tr in open_trades:
            entry_time = datetime.fromisoformat(tr["actual_entry_time"])
            now = clock.now_utc()
            if (now - entry_time).total_seconds() >= hold_hours * 3600:
                exit_price = _get_current_price(asset)
                if exit_price is None:
                    still_open.append(tr)
                    results.append({"asset": asset, "status": "stale_price_skip",
                                    "trade_id": tr["id"]})
                    continue
                _close_pdo_shadow(tr["id"], exit_price, f"hold_{hold_hours}h")
                log.info(f"[pdo {variant['id']} {asset}] closed {tr['id']} "
                         f"@ {exit_price:.2f} (hold {hold_hours}h)")
                results.append({"asset": asset, "status": "closed_hold",
                                "trade_id": tr["id"]})
            else:
                still_open.append(tr)
        if still_open:
            # At least one trade still within its hold window — no new entry.
            results.append({"asset": asset, "status": "open_waiting",
                            "open_count": len(still_open)})
            continue

        # Already fired for this bar_day's setupDay (one-trade-per-day rule)
        if _pdo_action_for_bar_day(variant["id"], asset, bar_day_start):
            results.append({"asset": asset, "status": "already_fired_today"})
            continue

        # Regime filter
        if not regime_ok:
            results.append({"asset": asset, "status": "regime_block",
                            "btc_30d_pct": btc_30d})
            continue

        # Load bar_day's open + PDO (PDO=prev day's open, CDO=bar_day's open)
        sig = _load_today_open_and_pdo(asset)
        if sig is None:
            results.append({"asset": asset, "status": "data_missing"})
            continue

        # Gap filter
        if sig["gap_pct"] < GAP_THRESHOLD_PCT:
            results.append({"asset": asset, "status": "no_gap",
                            "gap_pct": round(sig["gap_pct"], 2)})
            continue

        # Touch detection: just-closed 1H bar's range contains PDO?
        # At clock HH:00..HH:59, _get_hourly_bar_for_today returns the bar
        # that closed at HH:00, i.e. covering [HH-1:00, HH:00). At HH=0 that
        # bar belongs to yesterday — which is correctly bar_day for the
        # PDO/CDO/idempotency above, so we evaluate Pine's last-hour-of-day
        # entry opportunity here too.
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

        # Cross-sleeve BTC-long cap (max_net_btc) — pre-leverage % of capital.
        if asset == "BTC":
            from services.risk_caps import btc_long_cap_allows
            if not btc_long_cap_allows(variant, per_asset_alloc):
                results.append({"asset": asset, "status": "btc_cap_block"})
                continue

        # Fire
        entry_price = _get_current_price(asset)
        if entry_price is None:
            results.append({"asset": asset, "status": "stale_price_skip"})
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
        "date": bar_day_str,
        "btc_30d_pct": btc_30d,
        "regime_ok": regime_ok,
        "assets": results,
    }
