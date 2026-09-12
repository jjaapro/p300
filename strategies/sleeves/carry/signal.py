"""
S-078 Filtered Carry — live paper service (delta-neutral funding harvest).

Strategy (from backtest_tail_harvester.py, mode='filtered'; exit rule
replaced 2026-09-12 per studies/notebooks/carry_exit_rule_2026_09/):
  Entry: 7-day rolling avg of daily BTC funding rate > 0
  Exit:  trailing 30-day cumulative daily funding < -0.5 % of notional
  Structure: long BTC spot + short BTC perp (delta-neutral)
  P&L: short perp collects funding; basis ~0 over open period; fees on
       entry + exit

Modeled as a single paper trade per variant with strategy='CARRY' and
direction='LONG' (the long-spot leg is the defining side for accounting).
The short-perp hedge is implicit — P&L is computed from funding accrual
only (delta-neutral construction zeroes out spot-vs-perp mark-to-market).

This is paper-ONLY. No exchange calls. Daily idempotent via a trade-exists-
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

from strategies.support import clock
from strategies.support import db

log = logging.getLogger("dashboard.carry_service")

from .config import (
    FR_WINDOW_DAYS, FR_ENTRY_THRESHOLD, EXIT_CUM_DAYS, EXIT_CUM_THRESHOLD_PCT,
    ENTRY_EXIT_COST_PCT,
)


# ─── Funding rate loading ────────────────────────────────────────────────────

def _load_recent_daily_funding(days: int = 30) -> list[dict]:
    """Return ``[{date, daily_funding_pct, spot_close, perp_close}]`` oldest-first.

    Daily funding is the sum of the 3 settlement rates per day (00/08/16 UTC)
    as a percentage of notional; only complete days are returned. Funding
    aggregation is delegated to ``strategies.support.funding.daily_sums_pct`` which is
    the single source of truth for funding access — see commit 2ca7cdc for
    the bug class that motivated the consolidation.

    Spot + perp closes are joined inline because they're carry-specific (not
    a generic funding concern). Today is excluded because the day's funding
    settlements may not all be in yet.
    """
    from strategies.support import funding
    upper_ts = clock.now_ts()
    since_ts = upper_ts - (days + 2) * 86400

    funding_by_day = funding.daily_sums_pct("BTC", since_ts, upper_ts,
                                             complete_only=True)

    con = sqlite3.connect(str(db.TRADER_DB))
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

    today = clock.now_utc().strftime("%Y-%m-%d")
    all_days = sorted((set(funding_by_day) - {today})
                      & set(spot_by_day) & set(perp_by_day))
    return [
        {"date": d, "daily_funding_pct": funding_by_day[d],
         "spot_close": spot_by_day[d], "perp_close": perp_by_day[d]}
        for d in all_days
    ]


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
      cum_funding_pct: sum of the last EXIT_CUM_DAYS complete daily sums
                       (% of notional), or None when fewer days are known,
      exit_trigger: True if cum_funding_pct < EXIT_CUM_THRESHOLD_PCT,
      spot_close, perp_close
    }
    None if insufficient history for the entry average.

    The window is the last EXIT_CUM_DAYS *complete* funding days the loader
    returned, which is EXIT_CUM_DAYS calendar days unless a day is missing
    from the table (the study's pandas rolling(30) over a gap-free series).
    """
    if len(records) < FR_WINDOW_DAYS + 1:
        return None
    funding = [r["daily_funding_pct"] for r in records]
    avg = _rolling_avg(funding, FR_WINDOW_DAYS)
    today = records[-1]
    cum = (sum(funding[-EXIT_CUM_DAYS:]) if len(funding) >= EXIT_CUM_DAYS
           else None)
    return {
        "date": today["date"],
        "daily_funding_pct": today["daily_funding_pct"],
        "fr_7d_avg_pct": avg[-1],
        "entry_ok": (avg[-1] is not None and avg[-1] > FR_ENTRY_THRESHOLD),
        "cum_funding_pct": cum,
        "exit_trigger": cum is not None and cum < EXIT_CUM_THRESHOLD_PCT,
        "spot_close": today["spot_close"],
        "perp_close": today["perp_close"],
    }


# ─── DB helpers (variant-scoped) ─────────────────────────────────────────────

def _get_open_carry_trades(variant_id: str) -> list[dict]:
    """CARRY is BTC-only; delegates to strategies.trades.get_open_trades."""
    from strategies.trades import get_open_trades
    return get_open_trades(variant_id, "CARRY")


def _carry_action_today(variant_id: str, today_utc: str) -> bool:
    """Has a CARRY open/close already happened for this variant today?"""
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant = ? AND strategy = 'CARRY' "
        "AND (actual_entry_time LIKE ? OR actual_exit_time LIKE ?) LIMIT 1",
        (variant_id, f"{today_utc}%", f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_carry_paper(variant: dict, entry_price: float, allocation_pct: float,
                       reason: dict, leverage: float = 1.0) -> str:
    """Open a CARRY paper trade — delegates to strategies.trades.open_paper_trade.
    CARRY is delta-neutral (long-spot + short-perp); the trades.direction
    column stores 'LONG' as the spot-leg notation. Carry exits when the
    trailing ``EXIT_CUM_DAYS``-day cumulative funding falls below
    ``EXIT_CUM_THRESHOLD_PCT``, not on a schedule."""
    from strategies.trades import open_paper_trade
    return open_paper_trade(
        variant=variant, sleeve_name="CARRY",
        asset="BTC", direction="LONG",
        entry_price=entry_price, allocation_pct=allocation_pct, leverage=leverage,
        reason=reason, scheduled_exit_dt=None,
    )


def _close_carry_paper(trade_id: str, exit_price: float, reason: str) -> None:
    """Sleeve close — delegates to strategies.trades.close_carry_trade. CARRY is
    delta-neutral, so its close has no price-PnL component (just funding
    collected − fees on both legs)."""
    from strategies.trades import close_carry_trade
    close_carry_trade(trade_id, exit_price, reason, cost_pct=ENTRY_EXIT_COST_PCT)


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Variant-engine dispatch entry point. Returns a status dict.

    Backward-compatible wrapper: runs decide (side-effect exit sweep +
    entry Intent), then executes any returned Intent.
    """
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    return execute_for_variant(variant, sleeve_cfg, intents[0])


def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Phase-1 of the two-phase dispatch (P2.4e/f Stage 2).

    Side-effect (always run, not subject to reconcile):
      - Exit sweep: close every open carry trade when the trailing 30-day
        cumulative-funding exit signal fires.

    Returns ``(list[Intent], status_dict)``. Single Intent on entry
    conditions (no open trade, entry_ok, no exit_trigger, idempotency
    clear). CARRY is in `_NEUTRAL_STRATEGIES` in dispatch.py — the
    reconcile pass exempts it from directional-conflict checks (its
    perp SHORT is delta-neutral collateral, not a directional bet).
    Reconcile still enforces margin headroom against CARRY's notional
    since the perp leg consumes real gross budget.
    """
    from strategies.support.dispatch import Intent

    alloc_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                       sleeve_cfg.get("weight_pct", 0.0)))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    records = _load_recent_daily_funding(days=EXIT_CUM_DAYS + 7)
    sig = _evaluate_today(records)
    if sig is None:
        return [], {"status": "warmup", "reason": "insufficient funding history"}

    today = clock.now_utc().strftime("%Y-%m-%d")
    already_acted_today = _carry_action_today(variant["id"], today)
    open_trades = _get_open_carry_trades(variant["id"])

    # Side-effect: exit sweep when the trailing cumulative funding has
    # gone below the threshold.
    if open_trades and sig["cum_funding_pct"] is None:
        log.warning(f"[carry {variant['id']}] only {len(records)} complete "
                    f"funding days loaded, fewer than EXIT_CUM_DAYS="
                    f"{EXIT_CUM_DAYS}: the exit cannot be evaluated")
    if open_trades and sig["exit_trigger"]:
        exit_price = sig["spot_close"]
        why = f"cum{EXIT_CUM_DAYS}d={sig['cum_funding_pct']:.3f}%"
        closed_ids = []
        for tr in open_trades:
            _close_carry_paper(tr["id"], exit_price, why)
            closed_ids.append(tr["id"])
            log.info(f"[carry {variant['id']}] closed {tr['id']} @ "
                     f"{exit_price:.2f} ({why})")
        return [], {"status": "closed", "trade_ids": closed_ids}

    if (not open_trades and sig["entry_ok"]
            and not sig["exit_trigger"] and not already_acted_today):
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
            "_entry_price": entry_price,
            "_fr_7d_avg_pct": sig["fr_7d_avg_pct"],
        }
        intent = Intent(
            asset="BTC", direction="LONG",
            allocation_pct=alloc_pct, leverage=leverage,
            conviction=100,
            priority=float(sleeve_cfg.get("priority", 100)),
            reason=reason, scheduled_exit_dt=None,
        )
        return [intent], {"status": "decided",
                            "fr_7d_avg_pct": sig["fr_7d_avg_pct"]}

    return [], {"status": "no_action",
                 "date": today,
                 "open_count": len(open_trades),
                 "fr_7d_avg_pct": sig["fr_7d_avg_pct"],
                 "cum_funding_pct": sig["cum_funding_pct"]}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent) -> dict:
    """Phase-2 of the two-phase dispatch — open the delta-neutral
    carry pair described by ``intent`` (post-reconcile)."""
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    fr_7d = reason.pop("_fr_7d_avg_pct")
    tid = _open_carry_paper(
        variant, entry_price, intent.allocation_pct, reason,
        leverage=intent.leverage,
    )
    log.info(f"[carry {variant['id']}] opened {tid} @ {entry_price:.2f} "
             f"(7d avg FR = {fr_7d:.4f}%, alloc={intent.allocation_pct}%)")
    return {"status": "opened", "trade_id": tid, "fr_7d_avg_pct": fr_7d}
