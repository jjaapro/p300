"""
CPR (Contrarian Positioning Reversal) service — live dispatcher for P-300
shadow variants.

Signal (all must fire on same day):
  1. funding 3d avg <= rolling 20th percentile of trailing 180d
  2. L/S ratio <= rolling 20th percentile of trailing 180d
  3. close > EMA 20 daily (momentum up)
  4. EMA 20 > EMA 50 daily (short-term trend flip)

Execution per fire:
  Entry:  next UTC-day open, LONG at market
  Target: daily BB upper (20, 2σ) at signal day — fixed level
  Stop:   entry * 0.95 (5% hard stop)
  Time:   15 calendar days

Assets: BTC, ETH (each evaluated independently; each opens its own shadow
trade with prefix `SJ-`).

Source probe: probes/diagnostic_contrarian_positioning.py (2026-04-22)
Backtest (n=12 BTC, n=9 ETH): per-trade +129bp BTC, +69bp ETH, WR 83/67%.
Statistical power limited — live data accumulation is the validation.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from services import clock

log = logging.getLogger(__name__)

DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"
TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"

COST_BP_RT = 10.0
STOP_PCT = 0.05
TIME_STOP_DAYS = 15
PCTILE_WINDOW = 180
PCTILE_THRESHOLD = 0.20


# ─── Data loaders ─────────────────────────────────────────────────────────────

# CPR's signal is a DAILY signal computed on the latest fully-closed day. Within
# the same UTC day the result is stationary — it does not change with intraday
# ticks. We cache loaded series per (day_key, asset) so hourly ticks within a
# day reuse the heavy per-minute aggregation. This benefits BOTH live (small)
# and backtest (large — full 1m history is millions of rows).
_DAILY_LOOKBACK_DAYS = PCTILE_WINDOW + 60  # 180 + 60 = 240d, matches _evaluate_today
_daily_closes_cache: dict[tuple[str, str], tuple] = {}
_funding_map_cache: tuple[str, dict] | None = None
_ls_map_cache: dict[tuple[str, str], dict] = {}


def _load_daily_closes(asset: str) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Daily OHLC from trader.db for given asset. Returns (dates_str, open, high, low, close).

    Drops today's (still-forming) day so the caller sees only closed daily
    bars — matches a daily-close backtest convention.

    Cached per (asset, UTC-day). Only loads the last _DAILY_LOOKBACK_DAYS of
    1m data — enough for CPR's PCTILE_WINDOW (180d) + a warmup buffer.
    """
    day_key = clock.now_utc().strftime("%Y-%m-%d")
    cached = _daily_closes_cache.get((asset, day_key))
    if cached is not None:
        return cached

    table = f"{asset.lower()}_1m"
    upper_ms = clock.now_ts_ms()
    since_ms = upper_ms - _DAILY_LOOKBACK_DAYS * 86400_000
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        f"SELECT open_time, open, high, low, close FROM {table} "
        f"WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
        (since_ms, upper_ms),
    ).fetchall()
    con.close()
    if not rows:
        out = ([], np.empty(0), np.empty(0), np.empty(0), np.empty(0))
        _daily_closes_cache[(asset, day_key)] = out
        return out
    arr = np.asarray(rows, dtype=np.float64)
    ts_ms = arr[:, 0].astype(np.int64)
    day = ts_ms // 86400000
    starts = np.concatenate([[0], np.where(np.diff(day) != 0)[0] + 1, [len(day)]])
    n = len(starts) - 1
    today_day = int(clock.now_ts() // 86400)
    dates: list[str] = []
    o_l, h_l, l_l, c_l = [], [], [], []
    for i in range(n):
        s, e = starts[i], starts[i + 1]
        if int(day[s]) == today_day:
            continue  # drop today's partial bar
        dt = datetime.fromtimestamp(day[s] * 86400, tz=timezone.utc)
        dates.append(dt.strftime("%Y-%m-%d"))
        o_l.append(arr[s, 1]); h_l.append(arr[s:e, 2].max())
        l_l.append(arr[s:e, 3].min()); c_l.append(arr[e - 1, 4])
    out = (dates, np.asarray(o_l), np.asarray(h_l), np.asarray(l_l), np.asarray(c_l))
    # Prune cache to avoid unbounded growth during long replays.
    if len(_daily_closes_cache) > 8:
        _daily_closes_cache.clear()
    _daily_closes_cache[(asset, day_key)] = out
    return out


def _load_funding_daily() -> dict[str, float]:
    """Daily mean funding rate (fr_close) from cd_funding_rate, bounded by clock.
    Cached per UTC day."""
    global _funding_map_cache
    day_key = clock.now_utc().strftime("%Y-%m-%d")
    if _funding_map_cache and _funding_map_cache[0] == day_key:
        return _funding_map_cache[1]
    upper_ts = clock.now_ts()
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT date(timestamp,'unixepoch'), AVG(fr_close) FROM cd_funding_rate "
        "WHERE timestamp <= ? GROUP BY 1 ORDER BY 1",
        (upper_ts,),
    ).fetchall()
    con.close()
    out = {r[0]: r[1] for r in rows}
    _funding_map_cache = (day_key, out)
    return out


def _load_ls_ratio_daily(asset: str) -> dict[str, float]:
    """Cached per (asset, UTC-day)."""
    day_key = clock.now_utc().strftime("%Y-%m-%d")
    cached = _ls_map_cache.get((asset, day_key))
    if cached is not None:
        return cached
    upper_ts = clock.now_ts()
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT date(timestamp,'unixepoch'), ratio FROM ca_long_short_ratio "
        "WHERE asset=? AND timestamp <= ? ORDER BY timestamp",
        (asset.upper(), upper_ts),
    ).fetchall()
    con.close()
    out = {r[0]: r[1] for r in rows}
    if len(_ls_map_cache) > 8:
        _ls_map_cache.clear()
    _ls_map_cache[(asset, day_key)] = out
    return out


# ─── Signal evaluator ────────────────────────────────────────────────────────

def _ema(arr: np.ndarray, L: int) -> np.ndarray:
    a = 2.0 / (L + 1)
    out = np.zeros_like(arr)
    out[0] = arr[0]
    for k in range(1, len(arr)):
        out[k] = a * arr[k] + (1 - a) * out[k - 1]
    return out


def _evaluate_today(asset: str) -> dict | None:
    """Return signal dict for today (latest day in panel) or None if no fire."""
    dates, o, h, l, c = _load_daily_closes(asset)
    if len(dates) < PCTILE_WINDOW + 5:
        return {"fire": False, "reason": "warmup"}

    funding_map = _load_funding_daily()
    ls_map = _load_ls_ratio_daily(asset)

    # Build aligned series to panel dates
    n = len(dates)
    fund_aligned = np.full(n, np.nan)
    ls_aligned = np.full(n, np.nan)
    sorted_fund_keys = sorted(funding_map.keys())
    fk_idx = {d: i for i, d in enumerate(sorted_fund_keys)}
    fund_vals_sorted = np.array([funding_map[d] for d in sorted_fund_keys])
    for i, d in enumerate(dates):
        if d in fk_idx:
            fi = fk_idx[d]
            if fi >= 2:
                fund_aligned[i] = fund_vals_sorted[fi - 2:fi + 1].mean()
        if d in ls_map:
            ls_aligned[i] = ls_map[d]

    # Compute EMAs and BB upper
    ema20 = _ema(c, 20); ema50 = _ema(c, 50)
    bb_upper = np.zeros(n)
    for i in range(20, n):
        m = c[i - 19:i + 1].mean()
        s = np.std(c[i - 19:i + 1], ddof=0)
        bb_upper[i] = m + 2 * s

    # Today's index (last)
    today_i = n - 1
    today_date = dates[today_i]
    if np.isnan(fund_aligned[today_i]) or np.isnan(ls_aligned[today_i]):
        return {"fire": False, "reason": "missing_data", "date": today_date}

    # Rolling percentiles
    f_win = fund_aligned[today_i - PCTILE_WINDOW:today_i]
    ls_win = ls_aligned[today_i - PCTILE_WINDOW:today_i]
    f_win = f_win[~np.isnan(f_win)]
    ls_win = ls_win[~np.isnan(ls_win)]
    if len(f_win) < 30 or len(ls_win) < 30:
        return {"fire": False, "reason": "pctile_window_too_thin", "date": today_date}

    f_thresh = float(np.percentile(f_win, PCTILE_THRESHOLD * 100))
    ls_thresh = float(np.percentile(ls_win, PCTILE_THRESHOLD * 100))

    cond_fund = fund_aligned[today_i] <= f_thresh
    cond_ls = ls_aligned[today_i] <= ls_thresh
    cond_close = c[today_i] > ema20[today_i]
    cond_align = ema20[today_i] > ema50[today_i]
    fire = cond_fund and cond_ls and cond_close and cond_align

    return {
        "fire": fire,
        "date": today_date,
        "asset": asset,
        "price": float(c[today_i]),
        "bb_upper": float(bb_upper[today_i]),
        "ema20": float(ema20[today_i]),
        "ema50": float(ema50[today_i]),
        "fund_3d": float(fund_aligned[today_i]),
        "fund_20pctile": f_thresh,
        "ls_ratio": float(ls_aligned[today_i]),
        "ls_20pctile": ls_thresh,
        "conditions": {
            "fund_below_pctile": bool(cond_fund),
            "ls_below_pctile": bool(cond_ls),
            "close_above_ema20": bool(cond_close),
            "ema20_above_ema50": bool(cond_align),
        },
    }


# ─── DB helpers ──────────────────────────────────────────────────────────────

def _next_sj_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "SJ-0001"
    num = int(row[0].split("-")[1]) + 1
    return f"SJ-{num:04d}"


def _get_open_cpr_trades(variant_id: str, asset: str) -> list[dict]:
    """All open CPR trades for (variant, asset), newest first. Invariant is
    single-open; sweep the full list on close paths."""
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM trades WHERE strategy_variant=? AND strategy='CPR' "
        "AND asset=? AND status='open' ORDER BY actual_entry_time DESC",
        (variant_id, asset),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _cpr_action_today(variant_id: str, asset: str, today_utc: str) -> bool:
    """Any CPR action for this variant+asset today (open or close)?"""
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant=? AND strategy='CPR' AND asset=? "
        "AND (actual_entry_time LIKE ? OR actual_exit_time LIKE ?) LIMIT 1",
        (variant_id, asset, f"{today_utc}%", f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_cpr_shadow(variant: dict, asset: str, entry_price: float,
                     target: float, stop: float, allocation_pct: float,
                     leverage: float, reason: dict) -> str:
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now = clock.now_utc()
    now_iso = now.isoformat()
    exit_dt = now + timedelta(days=TIME_STOP_DAYS)
    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, 'LONG', 'CPR', ?, ?, ?, ?, ?, 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, reason.get("regime", "unknown"), allocation_pct,
              leverage, now_iso, exit_dt.isoformat(), variant["id"], now_iso,
              entry_price, size_usdt, qty, json.dumps([f"SHADOW-{tid}"]),
              json.dumps({**reason, "target": target, "stop": stop},
                         default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_cpr_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    now_iso = clock.now_utc().isoformat()
    cost = COST_BP_RT / 10000.0
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT asset, direction, entry_price, qty, size_usdt, "
        "       actual_entry_time FROM trades WHERE id=?", (trade_id,)
    ).fetchone()
    if row is None:
        con.close(); return
    # LONG: pnl = (exit - entry) * qty, cost applied as roundtrip
    pnl_usdt = (exit_price - row["entry_price"]) * row["qty"] - row["size_usdt"] * cost
    pnl_pct = (pnl_usdt / row["size_usdt"] * 100) if row["size_usdt"] > 0 else 0
    notes_suffix = f"\nCPR_EXIT: {reason}"
    con.execute("""
        UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
            pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
            notes = COALESCE(notes,'') || ?
        WHERE id=?
    """, (now_iso, exit_price, pnl_usdt, pnl_pct, notes_suffix, trade_id))
    con.commit(); con.close()
    from services.trade_db import format_close_summary
    log.info("[cpr] " + format_close_summary(
        trade_id=trade_id, asset=row["asset"], direction=row["direction"],
        entry_price=row["entry_price"], exit_price=exit_price,
        pnl_pct=pnl_pct, pnl_usdt=pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now_iso,
        reason=reason))


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Daily-idempotent CPR dispatch for this variant.

    sleeve_cfg.params:
      assets   — list, default ['BTC','ETH']
      leverage — per-sleeve leverage (provided as _effective_leverage)
    """
    from services.price_feed import _get_current_price

    params = sleeve_cfg.get("params") or {}
    assets = params.get("assets", ["BTC", "ETH"])
    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    # Split allocation across assets (default: even split)
    per_asset_alloc = alloc_pct / max(1, len(assets))
    today_utc = clock.now_utc().strftime("%Y-%m-%d")

    results = []
    for asset in assets:
        try:
            sig = _evaluate_today(asset)
        except Exception as e:
            log.exception(f"[cpr {variant['id']} {asset}] eval error: {e}")
            continue
        if sig is None or sig.get("reason") in ("warmup", "missing_data", "pctile_window_too_thin"):
            results.append({"asset": asset, "status": "no_signal", "reason": sig.get("reason") if sig else "none"})
            continue

        # Manage ALL open CPR positions for this (variant, asset). For each,
        # check stop / target / time-stop; close any that trigger. Invariant
        # is single-open; sweeping the full list guards against stray legacy
        # opens.
        open_trades = _get_open_cpr_trades(variant["id"], asset)
        still_open: list[dict] = []
        price = _get_current_price(asset) if open_trades else None
        for tr in open_trades:
            if price is None:
                still_open.append(tr)
                results.append({"asset": asset, "status": "stale_price_skip",
                                "trade_id": tr["id"]})
                continue
            try:
                entry_price = float(tr["entry_price"])
                stop_px = entry_price * (1 - STOP_PCT)
                notes = tr.get("notes") or "{}"
                try:
                    notes_data = json.loads(notes.split("\n")[0])
                    target_px = float(notes_data.get("target", 0))
                except (json.JSONDecodeError, ValueError):
                    target_px = 0.0
                if price <= stop_px:
                    _close_cpr_shadow(tr["id"], price, f"stop_hit@{price:.2f}")
                    log.info(f"[cpr {variant['id']} {asset}] closed {tr['id']} "
                             f"stop (fill={price:.2f}, level={stop_px:.2f})")
                    results.append({"asset": asset, "status": "closed_stop", "trade_id": tr["id"]})
                    continue
                if target_px > 0 and price >= target_px:
                    _close_cpr_shadow(tr["id"], price, f"target_hit@{price:.2f}")
                    log.info(f"[cpr {variant['id']} {asset}] closed {tr['id']} "
                             f"target (fill={price:.2f}, level={target_px:.2f})")
                    results.append({"asset": asset, "status": "closed_target", "trade_id": tr["id"]})
                    continue
                entry_time = datetime.fromisoformat(tr["actual_entry_time"])
                age_days = (clock.now_utc() - entry_time).days
                if age_days >= TIME_STOP_DAYS:
                    _close_cpr_shadow(tr["id"], price, f"time_stop_{age_days}d")
                    log.info(f"[cpr {variant['id']} {asset}] closed {tr['id']} time_stop")
                    results.append({"asset": asset, "status": "closed_time", "trade_id": tr["id"]})
                    continue
                still_open.append(tr)
            except Exception as e:
                log.exception(f"[cpr {variant['id']} {asset}] exit-check error: {e}")
                still_open.append(tr)
                results.append({"asset": asset, "status": "error", "error": str(e)})

        if still_open:
            # Single-open invariant: if any trade remains open, skip new entry.
            results.append({"asset": asset, "status": "open_waiting",
                            "open_count": len(still_open)})
            continue

        # No open position — check if signal fired today
        if not sig.get("fire"):
            results.append({"asset": asset, "status": "no_signal",
                            "conditions": sig.get("conditions")})
            continue
        if _cpr_action_today(variant["id"], asset, today_utc):
            results.append({"asset": asset, "status": "already_fired_today"})
            continue

        # Cross-sleeve BTC-long cap — shared with PDO_RETOUCH.
        if asset == "BTC":
            from services.risk_caps import btc_long_cap_allows
            if not btc_long_cap_allows(variant, per_asset_alloc):
                results.append({"asset": asset, "status": "btc_cap_block"})
                continue

        # Fire entry
        entry_price = _get_current_price(asset)
        if entry_price is None:
            results.append({"asset": asset, "status": "stale_price_skip"})
            continue

        target = sig["bb_upper"]
        stop = entry_price * (1 - STOP_PCT)
        if target <= entry_price:
            results.append({"asset": asset, "status": "degenerate_target",
                            "entry": entry_price, "target": target})
            continue

        reason = {
            "trigger": "CPR_entry",
            "variant_id": variant["id"],
            "sleeve": "CPR",
            "asset": asset,
            "fund_3d": sig["fund_3d"], "fund_20pctile": sig["fund_20pctile"],
            "ls_ratio": sig["ls_ratio"], "ls_20pctile": sig["ls_20pctile"],
            "conditions": sig["conditions"],
            "regime": "contrarian_squeeze_long",
        }
        tid = _open_cpr_shadow(variant, asset, entry_price, target, stop,
                               per_asset_alloc, leverage, reason)
        log.info(f"[cpr {variant['id']} {asset}] opened {tid} @ {entry_price:.2f} "
                 f"target={target:.2f} alloc={per_asset_alloc}% lev={leverage:.1f}x")
        results.append({"asset": asset, "status": "opened", "trade_id": tid,
                        "entry_price": entry_price, "target": target})

    # Summarize
    non_neutral = [r for r in results if r["status"] not in ("no_signal", "open_waiting", "already_fired_today")]
    return {
        "status": "dispatched" if non_neutral else "no_action",
        "date": today_utc,
        "assets": results,
    }
