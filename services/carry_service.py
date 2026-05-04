"""
S-078 Filtered Carry — live shadow service (delta-neutral funding harvest).

Strategy (from backtest_tail_harvester.py, mode='filtered'):
  Entry: 7-day rolling avg of daily BTC funding rate > 0
  Exit:  3 consecutive days of negative daily funding
  Structure: long BTC spot + short BTC perp (delta-neutral)
  P&L: short perp collects funding; basis ~0 over open period; fees on
       entry + exit

Modeled as a single shadow trade per variant with strategy='CARRY' and
direction='LONG' (the long-spot leg is the defining side for accounting).
The short-perp hedge is implicit — P&L is computed from funding accrual
only (delta-neutral construction zeroes out spot-vs-perp mark-to-market).

This is SHADOW-ONLY. No exchange calls. Daily idempotent via a trade-exists-
per-day check.

Daily funding is the sum of 3 settlements (00:00 / 08:00 / 16:00 UTC), each
pulled from cd_funding_rate (Binance BTC perp).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from services import clock

log = logging.getLogger("dashboard.carry_service")

TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"
DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"

# S-078 V2 filtered canonical params (match backtest_tail_harvester)
FR_WINDOW_DAYS = 7
FR_ENTRY_THRESHOLD = 0.0
EXIT_NEG_DAYS = 3
# Round-trip costs: 5bp spot + 5bp perp per leg, both sides = 20bp total
ENTRY_EXIT_COST_PCT = 0.20   # 20bp as percent


# ─── Funding rate loading ────────────────────────────────────────────────────

def _load_recent_daily_funding(days: int = 30) -> list[dict]:
    """Aggregate cd_funding_rate 8h-settlement rows into daily sums. Returns
    list of {date, daily_funding_pct, spot_close, perp_close} oldest-first.

    Daily funding is the sum of the 3 settlement rates (00/08/16 UTC) as a
    percentage of notional; only complete days (>=3 settlements) are returned.

    NOTE: cd_funding_rate stores HOURLY OHLC (24 rows/day), but funding only
    pays at the 8h settlement boundaries. We filter to timestamps where
    ts % 28800 == 0 so each settlement is counted exactly once. Without this
    filter the daily funding was 8x overstated — the same bug funding_util
    had until 2026-05-04.
    """
    upper_ts = clock.now_ts()
    since_ts = upper_ts - (days + 2) * 86400
    con = sqlite3.connect(str(TRADER_DB))
    fr_rows = con.execute(
        "SELECT timestamp, fr_close FROM cd_funding_rate "
        "WHERE timestamp >= ? AND timestamp <= ? "
        "AND timestamp % 28800 = 0 ORDER BY timestamp",
        (since_ts, upper_ts),
    ).fetchall()
    spot_rows = con.execute(
        "SELECT timestamp, close FROM cd_spot_binance "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (since_ts, upper_ts),
    ).fetchall()
    perp_rows = con.execute(
        "SELECT timestamp, close FROM cd_futures_ohlcv "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (since_ts, upper_ts),
    ).fetchall()
    con.close()

    from collections import defaultdict
    fr_by_day: dict[str, list[float]] = defaultdict(list)
    for ts, fr in fr_rows:
        if fr is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        fr_by_day[d].append(fr)
    # Daily funding = sum of the 3 settlement rates per day (00/08/16 UTC).
    # Binance /fapi/v1/fundingRate returns one row per settlement, so
    # fr_by_day[d] has 3 rows in a complete day. Drop incomplete days from the
    # head/tail to avoid partial-day bias.
    today = clock.now_utc().strftime("%Y-%m-%d")
    daily_funding_pct: dict[str, float] = {
        d: sum(rates) * 100 for d, rates in fr_by_day.items()
        if len(rates) >= 3 and d != today
    }

    spot_by_day: dict[str, float] = {}
    for ts, c in spot_rows:
        if c:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            spot_by_day[d] = c
    perp_by_day: dict[str, float] = {}
    for ts, c in perp_rows:
        if c:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            perp_by_day[d] = c

    all_days = sorted(set(daily_funding_pct) & set(spot_by_day) & set(perp_by_day))
    out = []
    for d in all_days:
        out.append({
            "date": d,
            "daily_funding_pct": daily_funding_pct[d],
            "spot_close": spot_by_day[d],
            "perp_close": perp_by_day[d],
        })
    return out


def _rolling_avg(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(window - 1, len(values)):
        out[i] = sum(values[i - window + 1: i + 1]) / window
    return out


# ─── Signal evaluation ──────────────────────────────────────────────────────

def _evaluate_today(records: list[dict]) -> dict | None:
    """Given a sorted daily-funding series, return today's signal state.

    Returns {
      date: latest date,
      daily_funding_pct: today's daily funding (as % of notional),
      fr_7d_avg_pct: 7-day rolling avg,
      entry_ok: True if avg > threshold,
      neg_streak_days: consecutive negative-funding days ending today,
      exit_trigger: True if neg streak >= EXIT_NEG_DAYS,
      spot_close, perp_close
    }
    None if insufficient history.
    """
    if len(records) < FR_WINDOW_DAYS + 1:
        return None
    funding = [r["daily_funding_pct"] for r in records]
    avg = _rolling_avg(funding, FR_WINDOW_DAYS)
    today = records[-1]
    # Negative streak ending today
    streak = 0
    for r in reversed(records):
        if r["daily_funding_pct"] < 0:
            streak += 1
        else:
            break
    return {
        "date": today["date"],
        "daily_funding_pct": today["daily_funding_pct"],
        "fr_7d_avg_pct": avg[-1],
        "entry_ok": (avg[-1] is not None and avg[-1] > FR_ENTRY_THRESHOLD),
        "neg_streak_days": streak,
        "exit_trigger": streak >= EXIT_NEG_DAYS,
        "spot_close": today["spot_close"],
        "perp_close": today["perp_close"],
    }


# ─── DB helpers (variant-scoped) ─────────────────────────────────────────────

def _next_sj_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT id FROM trades WHERE series='SJ' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "SJ-0001"
    num = int(row[0].split("-")[1]) + 1
    return f"SJ-{num:04d}"


def _get_open_carry_trades(variant_id: str) -> list[dict]:
    """Return ALL open CARRY trades for this variant (newest first). Strategy
    invariant is single-open; sweep the full list on close paths so any stray
    legacy trades get cleaned up instead of ignored."""
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM trades WHERE strategy_variant = ? AND strategy = 'CARRY' "
        "AND status = 'open' ORDER BY actual_entry_time DESC",
        (variant_id,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def _carry_action_today(variant_id: str, today_utc: str) -> bool:
    """Has a CARRY open/close already happened for this variant today?"""
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant = ? AND strategy = 'CARRY' "
        "AND (actual_entry_time LIKE ? OR actual_exit_time LIKE ?) LIMIT 1",
        (variant_id, f"{today_utc}%", f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_carry_shadow(variant: dict, entry_price: float, allocation_pct: float,
                       reason: dict, leverage: float = 1.0) -> str:
    """Per-sleeve `leverage` applied to size_usdt and recorded on the trade
    row. Defaults to 1.0x for un-levered variants."""
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now_iso = clock.now_utc().isoformat()
    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', 'BTC', 'LONG', 'CARRY', ?, ?, ?, ?,
                    '2099-12-31T00:00:00+00:00', 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, reason.get("regime", "unknown"), allocation_pct, leverage,
              now_iso, variant["id"], now_iso, entry_price, size_usdt, qty,
              json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_carry_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    """Close a CARRY trade. P&L = collected funding (per-settlement, summed
    over [entry_ts, exit_ts]) minus round-trip fees. Basis P&L is assumed
    zero (delta-neutral long-spot + short-perp).

    Short-perp leg earns when funding rate > 0 — identical math to a SHORT
    perp position, which is what funding_util.accrued_funding_pct returns
    for direction='SHORT'.
    """
    now = clock.now_utc()
    now_iso = now.isoformat()
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT entry_price, qty, size_usdt, actual_entry_time "
        "FROM trades WHERE id=?",
        (trade_id,),
    ).fetchone()
    if row is None:
        con.close()
        return
    from services.funding_util import accrued_funding_pct
    try:
        entry_dt = datetime.fromisoformat(row["actual_entry_time"])
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        funding_pct = accrued_funding_pct("BTC", entry_dt, now, "SHORT")
    except (TypeError, ValueError):
        funding_pct = 0.0
    net_pct = funding_pct - ENTRY_EXIT_COST_PCT
    pnl_usdt = row["size_usdt"] * net_pct / 100.0
    notes_suffix = (f"\nCARRY_EXIT: {reason}; "
                    f"funding={funding_pct:.3f}% (per-settlement), "
                    f"fees={ENTRY_EXIT_COST_PCT:.2f}%, net={net_pct:.3f}%")
    con.execute("""
        UPDATE trades SET status='closed', actual_exit_time=?, exit_price=?,
            pnl_usdt=?, pnl_pct=?, resolution='filled_closed',
            notes = COALESCE(notes,'') || ?
        WHERE id=?
    """, (now_iso, exit_price, pnl_usdt, net_pct, notes_suffix, trade_id))
    con.commit()
    con.close()
    from services.trade_db import format_close_summary
    log.info("[carry] " + format_close_summary(
        trade_id=trade_id, asset="BTC", direction="DELTA_NEUTRAL",
        entry_price=row["entry_price"], exit_price=exit_price,
        pnl_pct=net_pct, pnl_usdt=pnl_usdt,
        entry_time_iso=row["actual_entry_time"], exit_time_iso=now_iso,
        reason=reason))


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Evaluate S-078 carry signal for this variant. Daily idempotent.

    Opens / closes a paired CARRY shadow trade based on funding rate regime.
    P&L is computed at close from accumulated funding minus fees.
    """
    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    records = _load_recent_daily_funding(days=FR_WINDOW_DAYS + EXIT_NEG_DAYS + 7)
    sig = _evaluate_today(records)
    if sig is None:
        return {"status": "warmup", "reason": "insufficient funding history"}

    # Wall-clock UTC today for idempotency (NOT sig["date"] which is the
    # last completed funding day — typically yesterday).
    today = clock.now_utc().strftime("%Y-%m-%d")
    already_acted_today = _carry_action_today(variant["id"], today)
    open_trades = _get_open_carry_trades(variant["id"])

    # Exit sweep: close every open carry trade if the 3-day negative-streak
    # exit signal fires. Funding collected is computed per-settlement inside
    # _close_carry_shadow.
    if open_trades and sig["exit_trigger"]:
        exit_price = sig["spot_close"]
        closed_ids = []
        for tr in open_trades:
            _close_carry_shadow(tr["id"], exit_price,
                                f"neg_streak={sig['neg_streak_days']}d")
            closed_ids.append(tr["id"])
            log.info(f"[carry {variant['id']}] closed {tr['id']} @ "
                     f"{exit_price:.2f} (neg_streak={sig['neg_streak_days']}d)")
        return {"status": "closed", "trade_ids": closed_ids}

    # Entry: only open if ZERO open carry trades exist (single-open invariant)
    # AND no action happened yet today AND the 7d avg funding is above threshold.
    if not open_trades and sig["entry_ok"] and not already_acted_today:
        entry_price = sig["spot_close"]
        reason = {
            "trigger": "S-078_carry_entry",
            "variant_id": variant["id"],
            "sleeve": "CARRY",
            "fr_7d_avg_pct": round(sig["fr_7d_avg_pct"], 4) if sig["fr_7d_avg_pct"] else None,
            "threshold": FR_ENTRY_THRESHOLD,
            "fr_window_days": FR_WINDOW_DAYS,
            "structure": "long_spot_short_perp_delta_neutral",
            "regime": "unknown",
        }
        tid = _open_carry_shadow(variant, entry_price, alloc_pct, reason,
                                  leverage=leverage)
        log.info(f"[carry {variant['id']}] opened {tid} @ {entry_price:.2f} "
                 f"(7d avg FR = {sig['fr_7d_avg_pct']:.4f}%, alloc={alloc_pct}%)")
        return {"status": "opened", "trade_id": tid,
                "fr_7d_avg_pct": sig["fr_7d_avg_pct"]}

    return {"status": "no_action",
            "date": today,
            "open_count": len(open_trades),
            "fr_7d_avg_pct": sig["fr_7d_avg_pct"],
            "neg_streak": sig["neg_streak_days"]}
