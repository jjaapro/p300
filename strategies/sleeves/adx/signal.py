"""
S-003 ADX Regime Flip — live paper service.

Fires LONG/SHORT signals on BTC 1D when ADX crosses from compression (<20)
into trend (>=25). Direction is close-vs-EMA(50). Exit on ADX < 20 (trend
dies), direction-flip, or per-variant stop loss.

This is a paper-ONLY service. It creates phantom trades tagged with the
caller's variant_id. No exchange orders are ever placed.

Ticks idempotently — if called multiple times per day, only fires once per
UTC date per variant. Positions persist across ticks via the DB (no in-process
state).

Source of truth for logic: backtest_adx_regime.py. This service reproduces
its signal semantics exactly, then layers per-variant params (stop loss,
allocation weight) on top.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from strategies.support import clock

log = logging.getLogger("dashboard.adx_service")

from .config import (
    ADX_PERIOD, ADX_LOW_THRESH, ADX_HIGH_THRESH,
    EMA_LEN, TREND_EMA_LEN, WARMUP_BARS, COST_BP_RT,
)

# Dedup the trend-filter-block log message — the daily signal is stable for
# the whole UTC day, so without this dedup we'd log the same block 1440
# times/day in live (one per minute tick). Keyed by variant_id, value is the
# (signal_date, close, trend_ema) tuple last logged.
_trend_block_logged: dict[str, tuple] = {}


# ─── Data loading + indicator computation ────────────────────────────────────

def _load_btc_daily_candles(limit_days: int = 200) -> list[dict]:
    """Return last N days of BTC 1D candles as [{ts, dt, open, high, low, close}].
    Uses cd_spot_binance (BTC spot 1h), aggregated to daily. Newest last.

    Switched 2026-05-01 from cd_futures_ohlcv (perp) to cd_spot_binance (spot)
    to match TradingView's default "BTCUSDT 1D" feed. The perp/spot ADX delta
    can be 1-2 points on calm tape (e.g. 2026-04-27 spot crossed 25 while perp
    only hit 24.2), enough to flip entry decisions. All execution prices are
    already spot (price_feed reads btc_1m); this aligns the signal source."""
    from collections import defaultdict
    days_back = int(limit_days + WARMUP_BARS + 10)
    upper_ts = clock.now_ts()
    since_ts = upper_ts - days_back * 86400
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, open, high, low, close FROM cd_spot_binance "
        "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
        (since_ts, upper_ts),
    ).fetchall()
    con.close()
    days = defaultdict(list)
    for ts, o, h, l, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days[dt].append((ts, o, h, l, c))
    # Drop today's (still-forming) daily bar so the signal only uses
    # closed daily candles — matches the backtest convention and prevents
    # intraday flapping on partial ADX/EMA reads.
    today = clock.now_utc().strftime("%Y-%m-%d")
    out = []
    for d in sorted(days.keys()):
        if d == today:
            continue
        bars = days[d]
        out.append({
            "ts": bars[0][0], "dt": d,
            "open": bars[0][1],
            "high": max(b[2] for b in bars),
            "low": min(b[3] for b in bars),
            "close": bars[-1][4],
        })
    return out


# EMA + ADX math lives in strategies.support.indicators (single source of truth across
# the live service, bitstamp validators, and the JPLUS regime classifier).
from strategies.support.indicators import ema, adx
from strategies.support import db


# ─── Signal evaluation ──────────────────────────────────────────────────────

def _current_signal(candles: list[dict]) -> dict | None:
    """Evaluate today's S-003 signal state via the stateful was_low machine.

    State rules (exact port of the Pine reference):
      1. was_low starts False at the first warmed-up bar.
      2. was_low := True on every bar where ADX < ADX_LOW_THRESH.
      3. Entry event fires when was_low AND ADX >= ADX_HIGH_THRESH; on that
         bar was_low is consumed (set False). Direction = close vs EMA(50)
         at the moment of consumption.
      4. TREND FILTER (when TREND_EMA_LEN > 0): LONG entries additionally
         require close > EMA(TREND_EMA_LEN). SHORT entries have no trend
         filter — applied asymmetrically since 2026-05-04 because counter-
         trend SHORTs in bull markets earn perp funding and often pay off
         on price too (the symmetric variant lost ~$1.1k vs no-filter on
         the funding-aware 2023-09 → 2026-05 replay). If the filter
         rejects a LONG, was_low is still consumed (one attempt per cycle).
      5. Exit fires when in-position AND ADX < ADX_LOW_THRESH (also re-arms
         was_low for the next entry).

    `entry_sig` is set ONLY when the latest candle (i = n-1, the most recent
    closed daily bar) IS the consumption bar AND the trend filter agrees.
    This means the live tick fires exactly once per regime change, mirroring
    Pine's strategy.entry().

    Returns a dict with:
      date            — latest candle's UTC date
      adx             — latest ADX reading
      close           — latest close
      ema             — latest EMA(50)
      trend_ema       — latest EMA(TREND_EMA_LEN), or None if disabled
      was_low_pending — True if a fresh ADX<low has been observed but not
                        yet consumed by an entry (informational only)
      entry_sig       — 'long'|'short'|None — fires only on the consumption
                        bar AND trend filter agreement
      entry_blocked_by_trend — True when an entry would have fired but was
                        rejected by the trend filter (log-only, useful for
                        dashboard observation)
      exit_sig        — True if latest ADX < LOW_THRESH (caller closes pos)
    None if insufficient history.
    """
    if len(candles) < WARMUP_BARS + 2:
        return None
    closes = [c["close"] for c in candles]
    adx_series = adx(candles, ADX_PERIOD)
    ema_series = ema(closes, EMA_LEN)
    trend_ema = ema(closes, TREND_EMA_LEN) if TREND_EMA_LEN > 0 else None
    i = len(candles) - 1
    if math.isnan(adx_series[i]) or math.isnan(ema_series[i]):
        return None
    if trend_ema is not None and math.isnan(trend_ema[i]):
        # Not enough history for the trend filter yet — treat the same as a
        # filter-disabled run so we don't silently block every entry.
        trend_ema = None

    # Walk forward from the start of warmup, tracking was_low and the last
    # entry-event bar. State machine is deterministic, so this is equivalent
    # to maintaining was_low across ticks — just recomputed each call, which
    # keeps the service stateless w.r.t. the DB.
    was_low = False
    last_entry_idx = -1
    last_entry_dir: str | None = None
    last_entry_blocked = False
    for j in range(len(candles)):
        if math.isnan(adx_series[j]) or math.isnan(ema_series[j]):
            continue
        if adx_series[j] < ADX_LOW_THRESH:
            was_low = True
        if was_low and adx_series[j] >= ADX_HIGH_THRESH:
            new_dir = "long" if closes[j] > ema_series[j] else "short"
            blocked = False
            # ASYMMETRIC trend filter: applies to LONGs only.
            # Rationale: counter-trend SHORTs in bull markets earn perp funding
            # (longs pay shorts when funding > 0) AND many of them work on price
            # too — the 2026-05-04 funding-aware backtest showed the symmetric
            # filter dropped ~$700 of winning post-funding SHORT P&L over the
            # 2023-09 → 2026-05 window (e.g. 2025-02-22 +15.93%, 2025-10-11
            # +29.63%, 2026-01-21 +20.04%). Bull-market LONGs that fight the
            # daily EMA(150) trend are still filtered out, which was the
            # original whipsaw-protection motivation.
            if trend_ema is not None and not math.isnan(trend_ema[j]):
                if new_dir == "long" and closes[j] <= trend_ema[j]:
                    blocked = True
            last_entry_idx = j
            last_entry_dir = None if blocked else new_dir
            last_entry_blocked = blocked
            was_low = False  # consume regardless of block — one attempt/cycle

    entry_sig = last_entry_dir if last_entry_idx == i else None
    blocked_now = last_entry_blocked if last_entry_idx == i else False
    exit_sig = adx_series[i] < ADX_LOW_THRESH

    return {
        "date": candles[i]["dt"],
        "adx": round(adx_series[i], 2),
        "close": closes[i],
        "ema": round(ema_series[i], 2),
        "trend_ema": (round(trend_ema[i], 2) if trend_ema is not None else None),
        "was_low_pending": was_low,
        "entry_sig": entry_sig,
        "entry_blocked_by_trend": blocked_now,
        "exit_sig": exit_sig,
    }


# ─── DB helpers (variant-scoped) ─────────────────────────────────────────────

def _get_open_adx_trades(variant_id: str) -> list[dict]:
    """ADX is BTC-only; delegates to strategies.trades.get_open_trades."""
    from strategies.trades import get_open_trades
    return get_open_trades(variant_id, "ADX")


def _adx_trade_exists_today(variant_id: str, today_utc: str) -> bool:
    """Has an ADX trade already been created/closed for this variant today?"""
    con = sqlite3.connect(str(db.DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant = ? AND strategy = 'ADX' "
        "AND (actual_entry_time LIKE ? OR actual_exit_time LIKE ?) LIMIT 1",
        (variant_id, f"{today_utc}%", f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_adx_paper(variant: dict, direction: str, entry_price: float,
                     asset: str, allocation_pct: float, reason: dict,
                     leverage: float = 1.0) -> str:
    """Open an S-003 paper trade — delegates to strategies.trades.open_paper_trade.
    ADX exits on signal (ADX < 20) so no scheduled exit_time is set."""
    from strategies.trades import open_paper_trade
    return open_paper_trade(
        variant=variant, sleeve_name="ADX",
        asset=asset, direction=direction,
        entry_price=entry_price, allocation_pct=allocation_pct, leverage=leverage,
        reason=reason, scheduled_exit_dt=None,
    )


def _close_adx_paper(trade_id: str, exit_price: float, reason: str) -> None:
    """Sleeve close — delegates to strategies.trades.close_perp_trade. Kept as a
    thin wrapper so ``strategies.support.margin_check._load_close_fn`` can
    resolve it by sleeve."""
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="ADX",
                     cost_bp_rt=COST_BP_RT, apply_funding=True)


# ─── Public tick ─────────────────────────────────────────────────────────────

def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Variant-engine dispatch entry point. Returns a status dict.

    Backward-compatible wrapper: calls the two-phase entry points
    (:func:`try_decide_for_variant` then :func:`execute_for_variant`)
    so callers that don't know about the new protocol — including every
    legacy unit test and the backtest runner — keep working unchanged.
    """
    from strategies.support.dispatch import Intent

    intent, status = try_decide_for_variant(variant, sleeve_cfg)
    if intent is None:
        return status
    return execute_for_variant(variant, sleeve_cfg, intent)


def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Phase-1 of the two-phase dispatch (P2.4e/f Stage 2).

    Side-effects (always run, not subject to reconcile):
      - Stop-loss sweep on every open ADX trade.
      - Exit-signal close (ADX < 20) on remaining open trades.
      - Direction-flip close when today's signal disagrees with open dir.

    Returns ``(Intent | None, status_dict)``. None means "no entry this
    tick" (warmup / already-fired-today / no entry signal / trend filter
    block / flip-only-no-new-entry). When the signal fires fresh and
    the open-trade count is zero post-flip, returns an ``Intent`` for
    the orchestrator's reconcile pass — directional-conflict and
    margin-headroom checks now live there.

    Idempotent within a UTC day: once an ADX trade has entered or
    exited today, subsequent ticks short-circuit.
    """
    from strategies.support.price_feed import _get_current_price
    from strategies.support.risk_config import effective_price_move_sl_pct
    from strategies.support.dispatch import Intent

    alloc_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                       sleeve_cfg.get("weight_pct", 0.0)))
    params = sleeve_cfg.get("params") or {}
    stop_loss_pct = float(params.get("stop_loss_pct", 10.0))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))
    sl_price_thresh = effective_price_move_sl_pct(stop_loss_pct, leverage)

    candles = _load_btc_daily_candles()
    sig = _current_signal(candles)
    if sig is None:
        return None, {"status": "warmup", "reason": "insufficient history"}

    today = clock.now_utc().strftime("%Y-%m-%d")
    open_trades = _get_open_adx_trades(variant["id"])

    # Step 1: stop-loss sweep.
    from strategies.support.sleeves import is_sl_hit
    current_price = _get_current_price("BTC") or sig["close"]
    still_open: list[dict] = []
    for tr in open_trades:
        hit, pnl_pct = is_sl_hit(tr["direction"], float(tr["entry_price"]),
                                 current_price, sl_price_thresh)
        if hit:
            _close_adx_paper(tr["id"], current_price,
                              f"stop_loss {pnl_pct:.2f}%")
            log.info(f"[adx {variant['id']}] SL hit: closed {tr['id']} "
                     f"{tr['direction']} at {current_price:.2f} "
                     f"({pnl_pct:.2f}% px, threshold={sl_price_thresh:.2f}%)")
        else:
            still_open.append(tr)
    open_trades = still_open

    # Step 2: once-per-day idempotency.
    if _adx_trade_exists_today(variant["id"], today):
        return None, {"status": "already_fired_today", "date": today}

    # Step 3: Exit signal — close remaining opens if ADX < 20.
    if open_trades and sig["exit_sig"]:
        for tr in open_trades:
            _close_adx_paper(tr["id"], current_price, "ADX < 20")
            log.info(f"[adx {variant['id']}] ADX exit: closed {tr['id']} "
                     f"at {current_price:.2f}")
        open_trades = []

    # Step 4a: trend-filter block diagnostic (no Intent emitted).
    if sig.get("entry_blocked_by_trend"):
        sig_key = (sig["date"], round(sig["close"], 2), sig["trend_ema"])
        if _trend_block_logged.get(variant["id"]) != sig_key:
            _trend_block_logged[variant["id"]] = sig_key
            log.info(f"[adx {variant['id']}] trend-filter BLOCKED entry: "
                     f"close={sig['close']:.2f} vs EMA({TREND_EMA_LEN})="
                     f"{sig['trend_ema']} (ADX={sig['adx']}, EMA50={sig['ema']})")
        return None, {"status": "trend_filter_block",
                       "date": today, "adx": sig["adx"],
                       "close": sig["close"], "trend_ema": sig["trend_ema"]}

    if not sig["entry_sig"]:
        return None, {"status": "no_action", "date": today, "adx": sig["adx"],
                       "open_count": len(open_trades)}

    new_dir = sig["entry_sig"].upper()
    # Step 4b: direction-flip close on still-open opposite-direction trades.
    for tr in list(open_trades):
        if tr["direction"] != new_dir:
            _close_adx_paper(tr["id"], current_price, "direction flip")
            log.info(f"[adx {variant['id']}] reversal: closed {tr['id']} "
                     f"{tr['direction']} -> new {new_dir}")
            open_trades.remove(tr)

    # Single-open invariant: only emit an Intent when no trades remain.
    if open_trades:
        return None, {"status": "no_action", "date": today, "adx": sig["adx"],
                       "open_count": len(open_trades)}

    reason = {
        "trigger": "S-003_ADX_entry",
        "variant_id": variant["id"],
        "sleeve": "ADX",
        "adx": sig["adx"],
        "ema50": sig["ema"],
        "trend_ema": sig.get("trend_ema"),
        "trend_ema_len": TREND_EMA_LEN,
        "close": sig["close"],
        "direction_rule": (
            f"close > EMA(50) AND close > EMA({TREND_EMA_LEN})"
            if new_dir == "LONG"
            else f"close < EMA(50)  [SHORT: trend filter not applied]"
        ),
        "regime": "unknown",
        "stop_loss_pct": stop_loss_pct,
        "sl_semantic_price_thresh_pct": sl_price_thresh,
        # Private payload the execute phase consumes to write the trade
        # without redoing the candle/indicator work.
        "_entry_price": current_price,
    }
    intent = Intent(
        asset="BTC",
        direction=new_dir,
        allocation_pct=alloc_pct,
        leverage=leverage,
        conviction=100,  # ADX has no graded conviction; fixed-signal sleeve.
        priority=float(sleeve_cfg.get("priority", 100)),
        reason=reason,
        scheduled_exit_dt=None,
    )
    return intent, {"status": "decided", "direction": new_dir,
                     "adx": sig["adx"], "ema50": sig["ema"]}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent) -> dict:
    """Phase-2 of the two-phase dispatch — open the trade described by
    ``intent`` (post-reconcile, so allocation_pct / leverage already
    account for cross-sleeve margin / conflict / pooling).
    """
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    tid = _open_adx_paper(
        variant, intent.direction, entry_price, intent.asset,
        intent.allocation_pct, reason, leverage=intent.leverage,
    )
    log.info(f"[adx {variant['id']}] opened {tid} {intent.asset} "
             f"{intent.direction} @ {entry_price:.2f} "
             f"(alloc={intent.allocation_pct}%, k={intent.leverage}x)")
    return {"status": "opened", "trade_id": tid, "direction": intent.direction}
