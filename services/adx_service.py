"""
S-003 ADX Regime Flip — live shadow service.

Fires LONG/SHORT signals on BTC 1D when ADX crosses from compression (<20)
into trend (>=25). Direction is close-vs-EMA(50). Exit on ADX < 20 (trend
dies), direction-flip, or per-variant stop loss.

This is a SHADOW-ONLY service. It creates phantom trades tagged with the
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

log = logging.getLogger("dashboard.adx_service")

TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"
DASH_DB = Path(__file__).resolve().parent.parent / "data" / "dashboard.db"

# S-003 canonical parameters (match backtest_adx_regime.py defaults)
ADX_PERIOD = 14
ADX_LOW_THRESH = 20.0
ADX_HIGH_THRESH = 25.0
EMA_LEN = 50
WARMUP_BARS = max(ADX_PERIOD * 3, EMA_LEN + 1)


# ─── Data loading + indicator computation ────────────────────────────────────

def _load_btc_daily_candles(limit_days: int = 200) -> list[dict]:
    """Return last N days of BTC 1D candles as [{ts, dt, open, high, low, close}].
    Uses cd_futures_ohlcv, aggregated to daily. Newest last."""
    from collections import defaultdict
    days_back = int(limit_days + WARMUP_BARS + 10)
    since_ts = int(datetime.now(timezone.utc).timestamp()) - days_back * 86400
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, open, high, low, close FROM cd_futures_ohlcv "
        "WHERE timestamp >= ? ORDER BY timestamp",
        (since_ts,),
    ).fetchall()
    con.close()
    days = defaultdict(list)
    for ts, o, h, l, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days[dt].append((ts, o, h, l, c))
    out = []
    for d in sorted(days.keys()):
        bars = days[d]
        out.append({
            "ts": bars[0][0], "dt": d,
            "open": bars[0][1],
            "high": max(b[2] for b in bars),
            "low": min(b[3] for b in bars),
            "close": bars[-1][4],
        })
    return out


def _calc_ema(prices: list[float], period: int) -> list[float]:
    """Standard EMA. Returns NaN for indices before period-1."""
    out = [float("nan")] * len(prices)
    if len(prices) < period:
        return out
    seed = sum(prices[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    for i in range(period, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def _calc_adx(candles: list[dict], period: int) -> list[float]:
    """ADX via Wilder smoothing. Matches backtest_adx_breakout.calc_adx output.
    Returns NaN until warmup complete."""
    n = len(candles)
    if n < period * 2 + 1:
        return [float("nan")] * n
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h, l = candles[i]["high"], candles[i]["low"]
        ph, pl, pc = candles[i - 1]["high"], candles[i - 1]["low"], candles[i - 1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        down = pl - l
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
    atr = [float("nan")] * n
    pdi = [float("nan")] * n
    mdi = [float("nan")] * n
    dx = [float("nan")] * n
    atr[period] = sum(tr[1: period + 1])
    pdm_sum = sum(plus_dm[1: period + 1])
    mdm_sum = sum(minus_dm[1: period + 1])
    for i in range(period + 1, n):
        atr[i] = atr[i - 1] - atr[i - 1] / period + tr[i]
        pdm_sum = pdm_sum - pdm_sum / period + plus_dm[i]
        mdm_sum = mdm_sum - mdm_sum / period + minus_dm[i]
        if atr[i] > 0:
            pdi[i] = 100 * pdm_sum / atr[i]
            mdi[i] = 100 * mdm_sum / atr[i]
            denom = pdi[i] + mdi[i]
            dx[i] = 100 * abs(pdi[i] - mdi[i]) / denom if denom > 0 else 0.0
    adx = [float("nan")] * n
    # Seed ADX as average of first `period` valid DX values
    first = period * 2
    if first < n:
        window = [dx[i] for i in range(period + 1, first + 1) if not math.isnan(dx[i])]
        if window:
            adx[first] = sum(window) / len(window)
            for i in range(first + 1, n):
                if not math.isnan(dx[i]):
                    adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


# ─── Signal evaluation ──────────────────────────────────────────────────────

def _current_signal(candles: list[dict]) -> dict | None:
    """Evaluate today's S-003 signal state.

    Returns a dict with:
      date        — latest candle's UTC date
      adx         — latest ADX reading
      close       — latest close
      ema         — latest EMA(50)
      was_low     — did ADX sink below LOW_THRESH in the lookback window?
      entry_sig   — 'long' | 'short' | None (signal to open a new position)
      exit_sig    — True if ADX dropped below LOW_THRESH (close any open pos)
    None if insufficient history.
    """
    if len(candles) < WARMUP_BARS + 2:
        return None
    closes = [c["close"] for c in candles]
    adx = _calc_adx(candles, ADX_PERIOD)
    ema = _calc_ema(closes, EMA_LEN)
    i = len(candles) - 1
    if math.isnan(adx[i]) or math.isnan(ema[i]):
        return None

    # Look back across recent history for "was_low" evidence (last 20 bars).
    was_low = any(
        not math.isnan(adx[j]) and adx[j] < ADX_LOW_THRESH
        for j in range(max(0, i - 20), i + 1)
    )

    entry_sig = None
    exit_sig = adx[i] < ADX_LOW_THRESH
    if was_low and adx[i] >= ADX_HIGH_THRESH:
        entry_sig = "long" if closes[i] > ema[i] else "short"

    return {
        "date": candles[i]["dt"],
        "adx": round(adx[i], 2),
        "close": closes[i],
        "ema": round(ema[i], 2),
        "was_low": was_low,
        "entry_sig": entry_sig,
        "exit_sig": exit_sig,
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


def _get_open_adx_trade(variant_id: str) -> dict | None:
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM trades WHERE strategy_variant = ? "
        "AND strategy = 'ADX' AND status = 'open' "
        "ORDER BY actual_entry_time DESC LIMIT 1",
        (variant_id,),
    ).fetchone()
    con.close()
    return dict(row) if row else None


def _adx_trade_exists_today(variant_id: str, today_utc: str) -> bool:
    """Has an ADX trade already been created/closed for this variant today?"""
    con = sqlite3.connect(str(DASH_DB))
    row = con.execute(
        "SELECT 1 FROM trades WHERE strategy_variant = ? AND strategy = 'ADX' "
        "AND (actual_entry_time LIKE ? OR actual_exit_time LIKE ?) LIMIT 1",
        (variant_id, f"{today_utc}%", f"{today_utc}%"),
    ).fetchone()
    con.close()
    return row is not None


def _open_adx_shadow(variant: dict, direction: str, entry_price: float,
                     asset: str, allocation_pct: float, reason: dict,
                     leverage: float = 1.0) -> str:
    """Create an open S-003 shadow trade for this variant.

    `leverage` is the per-sleeve leverage multiplier applied to size_usdt
    (and stored on the trade row). Defaults to 1.0x for un-levered variants;
    set by variant_engine.tick via composition spec."""
    from services import trade_db
    capital = float(variant.get("capital_usdt") or
                    trade_db.get_config("paper_account_usdt") or 10000)
    size_usdt = capital * (allocation_pct / 100.0) * leverage
    qty = size_usdt / entry_price if entry_price > 0 else 0
    now_iso = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(DASH_DB))
    try:
        tid = _next_sj_id(con)
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy, regime,
                allocation_pct, leverage, entry_time, exit_time, status,
                execution_mode, strategy_variant, actual_entry_time,
                entry_price, size_usdt, qty, order_ids, notes)
            VALUES (?, 'SJ', ?, ?, 'ADX', ?, ?, ?, ?, '2099-12-31T00:00:00+00:00', 'open',
                    'SHADOW', ?, ?, ?, ?, ?, ?, ?)
        """, (tid, asset, direction.upper(), reason.get("regime", "unknown"),
              allocation_pct, leverage, now_iso, variant["id"], now_iso, entry_price,
              size_usdt, qty, json.dumps([f"SHADOW-{tid}"]),
              json.dumps(reason, default=str)))
        con.commit()
    finally:
        con.close()
    return tid


def _close_adx_shadow(trade_id: str, exit_price: float, reason: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(str(DASH_DB))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT entry_price, qty, size_usdt, direction, notes FROM trades WHERE id=?",
        (trade_id,),
    ).fetchone()
    if row is None:
        con.close()
        return
    if row["direction"] == "LONG":
        pnl_usdt = (exit_price - row["entry_price"]) * row["qty"]
    else:
        pnl_usdt = (row["entry_price"] - exit_price) * row["qty"]
    pnl_pct = (pnl_usdt / row["size_usdt"] * 100) if row["size_usdt"] > 0 else 0
    notes_suffix = f"\nADX_EXIT: {reason}"
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
    """Evaluate today's S-003 signal and open/close shadow trades for this
    variant. Returns a status dict (for logging).

    variant:    registry row (dict with id, capital_usdt, ...)
    sleeve_cfg: composition entry for this sleeve. Reads:
                  weight_pct     — allocation weight within the portfolio
                  params.stop_loss_pct — hard stop (positive number, e.g. 10.0)

    Idempotent: caller may invoke every minute; this function will only act
    on the first tick of a new day when a signal changes.
    """
    from services.price_feed import _get_current_price

    alloc_pct = float(sleeve_cfg.get("weight_pct", 0.0))
    params = sleeve_cfg.get("params") or {}
    stop_loss_pct = float(params.get("stop_loss_pct", 10.0))
    # Per-sleeve leverage injected by variant_engine._tick_composition.
    # Defaults to 1.0 when called outside the composition tick (tests, etc.).
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    candles = _load_btc_daily_candles()
    sig = _current_signal(candles)
    if sig is None:
        return {"status": "warmup", "reason": "insufficient history"}

    today = sig["date"]
    open_trade = _get_open_adx_trade(variant["id"])

    # Step 1: stop-loss check on any open position (independent of daily signal)
    if open_trade:
        current_price = _get_current_price("BTC") or sig["close"]
        entry_price = float(open_trade["entry_price"])
        if open_trade["direction"] == "LONG":
            live_pnl_pct = (current_price - entry_price) / entry_price * 100
        else:
            live_pnl_pct = (entry_price - current_price) / entry_price * 100
        if live_pnl_pct <= -stop_loss_pct:
            _close_adx_shadow(open_trade["id"], current_price,
                              f"stop_loss {live_pnl_pct:.2f}%")
            log.info(f"[adx {variant['id']}] SL hit: closed {open_trade['id']} "
                     f"{open_trade['direction']} at {current_price:.2f} "
                     f"({live_pnl_pct:.2f}%)")
            open_trade = None

    # Step 2: once-per-day signal check (daily cadence)
    if _adx_trade_exists_today(variant["id"], today):
        return {"status": "already_fired_today", "date": today}

    # Exit signal: ADX back below LOW_THRESH — close at current price
    if open_trade and sig["exit_sig"]:
        current_price = _get_current_price("BTC") or sig["close"]
        _close_adx_shadow(open_trade["id"], current_price, "ADX < 20")
        log.info(f"[adx {variant['id']}] ADX exit: closed {open_trade['id']} "
                 f"at {current_price:.2f}")
        open_trade = None

    # Entry signal: ADX crossed into trend
    if sig["entry_sig"]:
        new_dir = sig["entry_sig"].upper()
        # Reversal: close existing opposite-direction trade first
        if open_trade and open_trade["direction"] != new_dir:
            current_price = _get_current_price("BTC") or sig["close"]
            _close_adx_shadow(open_trade["id"], current_price, "direction flip")
            log.info(f"[adx {variant['id']}] reversal: closed {open_trade['id']} "
                     f"{open_trade['direction']} -> new {new_dir}")
            open_trade = None
        # Open new position at current price (live shadow doesn't wait for next bar)
        if open_trade is None:
            entry_price = _get_current_price("BTC") or sig["close"]
            reason = {
                "trigger": "S-003_ADX_entry",
                "variant_id": variant["id"],
                "sleeve": "ADX",
                "adx": sig["adx"],
                "ema50": sig["ema"],
                "close": sig["close"],
                "direction_rule": f"close {'>' if new_dir == 'LONG' else '<'} EMA(50)",
                "regime": "unknown",
                "stop_loss_pct": stop_loss_pct,
            }
            tid = _open_adx_shadow(variant, new_dir, entry_price, "BTC",
                                   alloc_pct, reason, leverage=leverage)
            log.info(f"[adx {variant['id']}] opened {tid} BTC {new_dir} @ "
                     f"{entry_price:.2f} (ADX={sig['adx']}, EMA={sig['ema']}, "
                     f"alloc={alloc_pct}%, k={leverage}x)")
            return {"status": "opened", "trade_id": tid, "direction": new_dir}

    return {"status": "no_action", "date": today, "adx": sig["adx"],
            "has_open_position": open_trade is not None}
