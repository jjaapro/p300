"""S-105 SHORT_SQUEEZE — sleeve dispatcher.

Long BTC perp at a swept low + perp/spot CVD divergence + Asia-grind macro.
See module __init__ docstring and README for the trader-described pattern;
see studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb for
the calibration.

Tick model:
  - Orchestrator ticks every minute (same cadence as other sleeves).
  - On every tick: sweep open positions for stop / target / time-stop hit.
  - On the 15m boundary minute: evaluate the latest closed bar for a new
    trigger. At most one trigger per cooldown window (4h).

Caches (module-level, refreshed at first call after restart):
  - Rolling percentile distribution: 90 days of London/NY perp_cvd and
    divergence values. Refreshed once per UTC day on first 15m tick.
  - Asia macro context: per UTC date, computed at first call after
    asia ends (07:00 UTC).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np

from strategies.support import clock, db
from strategies.support.dispatch import Intent

from . import math as ssq_math
from .config import (
    PERP_CVD_PCT_MAX, DIVERGENCE_PCT_MIN, CLOSE_IN_RANGE_MIN,
    LOOKBACK_BARS, COOLDOWN_BARS, WINDOW_DAYS,
    TP_R, STOP_BUFFER, TIME_STOP_HOURS,
    COST_BP_RT, SLIPPAGE_BP_RT,
)

log = logging.getLogger("p300.short_squeeze")

# ─── Module-level caches ─────────────────────────────────────────────────────

# Distribution caches keyed by ISO-date — refreshed on the first 15m tick of
# each new UTC day so percentile ranks adapt to drift over time.
_dist_cache_date: str | None = None
_perp_cvd_dist: np.ndarray | None = None
_divergence_dist: np.ndarray | None = None

# Asia macro context cache keyed by ISO-date.
_macro_cache: dict[str, dict] = {}

# Per-variant last-trigger timestamp (UTC) for cooldown enforcement.
_last_trigger_ts: dict[str, datetime] = {}


# ─── Data loaders ────────────────────────────────────────────────────────────

def _load_recent_15m_bars(now: datetime, window_days: int) -> list[dict]:
    """Load the last `window_days` of 15m bars (London/NY hours only).
    Each row: ``{'ts', 'session', 'perp_cvd', 'divergence', 'open', 'high',
    'low', 'close'}``. Used for percentile distribution refresh.
    """
    cutoff = now - timedelta(days=window_days)
    cutoff_s = int(cutoff.timestamp())
    now_s = int(now.timestamp())
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute("""
            SELECT
                p.timestamp,
                p.open, p.high, p.low, p.close,
                COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0)
                  AS perp_cvd,
                (COALESCE(s.volume_buy, 0) - COALESCE(s.volume_sell, 0))
                  - (COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0))
                  AS divergence
            FROM cd_futures_15m p
            LEFT JOIN cd_spot_15m s ON s.timestamp = p.timestamp
            WHERE p.timestamp >= ? AND p.timestamp <= ?
            ORDER BY p.timestamp
        """, (cutoff_s, now_s)).fetchall()
    finally:
        con.close()
    out = []
    for ts_s, o, h, l, c, perp_cvd, div in rows:
        hour = datetime.fromtimestamp(int(ts_s), tz=timezone.utc).hour
        if ssq_math.session_of_hour(hour) not in ("london", "ny"):
            continue
        out.append({
            "ts": int(ts_s), "open": float(o), "high": float(h),
            "low": float(l), "close": float(c),
            "perp_cvd": float(perp_cvd or 0.0),
            "divergence": float(div or 0.0),
        })
    return out


def _refresh_percentile_distributions(now: datetime) -> None:
    """Rebuild the 90-day rolling distribution caches. Cheap (~50ms for
    5000 rows) — called at most once per UTC day."""
    global _dist_cache_date, _perp_cvd_dist, _divergence_dist
    bars = _load_recent_15m_bars(now, WINDOW_DAYS)
    if not bars:
        _dist_cache_date = ssq_math.utc_date_of(now)
        _perp_cvd_dist = np.array([])
        _divergence_dist = np.array([])
        return
    _perp_cvd_dist = np.array([b["perp_cvd"] for b in bars], dtype=float)
    _divergence_dist = np.array([b["divergence"] for b in bars], dtype=float)
    _dist_cache_date = ssq_math.utc_date_of(now)
    log.info(f"[short_squeeze] refreshed distributions: {len(_perp_cvd_dist)} bars "
             f"covering {WINDOW_DAYS}d, date={_dist_cache_date}")


def _load_latest_15m_bar(now: datetime) -> dict | None:
    """Return the most recently-closed 15m bar at or before `now`. Pulls
    perp + spot OHLCV and CVDs in one query.
    """
    # Round `now` down to the prior 15m boundary; the bar with timestamp
    # equal to (latest_15m - 15min) is the most recent closed bar.
    floored = now.replace(second=0, microsecond=0)
    floored = floored - timedelta(minutes=floored.minute % 15)
    latest_bar_open = floored - timedelta(minutes=15)
    ts_s = int(latest_bar_open.timestamp())

    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        row = con.execute("""
            SELECT
                p.timestamp,
                p.open, p.high, p.low, p.close,
                COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0)
                  AS perp_cvd,
                (COALESCE(s.volume_buy, 0) - COALESCE(s.volume_sell, 0))
                  - (COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0))
                  AS divergence
            FROM cd_futures_15m p
            LEFT JOIN cd_spot_15m s ON s.timestamp = p.timestamp
            WHERE p.timestamp = ?
        """, (ts_s,)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    ts_s, o, h, l, c, perp_cvd, div = row
    return {"ts": int(ts_s), "open": float(o), "high": float(h),
            "low": float(l), "close": float(c),
            "perp_cvd": float(perp_cvd or 0.0),
            "divergence": float(div or 0.0)}


def _load_prior_lookback_low(now: datetime, bars: int) -> float | None:
    """Lowest perp.low over the `bars` 15m bars ending at the bar PRIOR
    to `now`'s current 15m bar."""
    floored = now.replace(second=0, microsecond=0)
    floored = floored - timedelta(minutes=floored.minute % 15)
    # Look back `bars` bars STRICTLY BEFORE the current closing bar.
    earliest_open = floored - timedelta(minutes=15 * (bars + 1))
    latest_open  = floored - timedelta(minutes=15 * 2)  # exclude the closing bar itself
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        row = con.execute("""
            SELECT MIN(low) FROM cd_futures_15m
            WHERE timestamp >= ? AND timestamp <= ?
        """, (int(earliest_open.timestamp()), int(latest_open.timestamp()))).fetchone()
    finally:
        con.close()
    return float(row[0]) if row and row[0] is not None else None


def _load_asia_bars(date_iso: str) -> list[dict]:
    """Load the asia-session hourly bars for `date_iso` with OI + funding
    merged in. Used to compute the asia macro context."""
    asia_start = datetime.fromisoformat(date_iso).replace(tzinfo=timezone.utc)
    asia_end = asia_start + timedelta(hours=7)
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute("""
            SELECT
                f.timestamp, f.open, f.high, f.low, f.close,
                oi.oi_close, fr.fr_close
            FROM cd_futures_ohlcv f
            LEFT JOIN cd_open_interest oi ON oi.timestamp = f.timestamp
            LEFT JOIN cd_funding_rate  fr ON fr.timestamp = f.timestamp
            WHERE f.timestamp >= ? AND f.timestamp < ?
            ORDER BY f.timestamp
        """, (int(asia_start.timestamp()), int(asia_end.timestamp()))).fetchall()
    finally:
        con.close()
    return [{"timestamp": int(ts), "open": float(o), "high": float(h),
             "low": float(l), "close": float(c),
             "oi_close": float(oi_c) if oi_c is not None else None,
             "funding":  float(fr_c) if fr_c is not None else None}
            for ts, o, h, l, c, oi_c, fr_c in rows]


def _get_macro_context(now: datetime) -> dict | None:
    """Macro context for today. Cached by ISO-date; computed once asia ends."""
    date_iso = ssq_math.utc_date_of(now)
    cached = _macro_cache.get(date_iso)
    if cached is not None:
        return cached
    # Asia ends at 07:00 UTC; if we're before that, no macro yet.
    if now.hour < 7:
        return None
    asia_bars_raw = _load_asia_bars(date_iso)
    # Forward-fill OI within the asia window (binance feed cadence ≤ 1h).
    last_oi = None
    last_fund = None
    asia_bars_ff = []
    for b in asia_bars_raw:
        if b["oi_close"] is not None:
            last_oi = b["oi_close"]
        if b["funding"] is not None:
            last_fund = b["funding"]
        asia_bars_ff.append({**b, "oi_close": last_oi, "funding": last_fund})
    # Drop any leading rows with no OI fill (need at least the open bar's OI).
    asia_bars_ff = [b for b in asia_bars_ff if b["oi_close"] is not None]
    summary = ssq_math.asia_session_summary(asia_bars_ff)
    if summary is None:
        return None
    _macro_cache[date_iso] = summary
    log.info(f"[short_squeeze] macro for {date_iso}: short={summary['is_short_macro']}, "
             f"long={summary['is_long_macro']}, oi_pct={summary['oi_pct']*100:+.2f}%, "
             f"fund={summary['fund_mean']*1e4:+.2f}bp")
    return summary


# ─── Trade close paths ───────────────────────────────────────────────────────

def _close_paper(trade_id: str, exit_price: float, reason: str) -> None:
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="SHORT_SQUEEZE",
                     cost_bp_rt=COST_BP_RT, slippage_bp_rt=SLIPPAGE_BP_RT,
                     apply_funding=True)


def _get_open_short_squeeze_trades(variant_id: str) -> list[dict]:
    """Return the open trades for this variant + sleeve. Each row carries
    enough to evaluate stop/target/time-stop. The reason payload stores
    `_stop_price`, `_target_price`, `_time_stop_iso`."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT id, asset, direction, entry_price, entry_time, notes
            FROM trades
            WHERE strategy_variant = ? AND strategy = 'SHORT_SQUEEZE'
              AND status = 'open' AND execution_mode = 'paper'
        """, (variant_id,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _sweep_open_positions(variant_id: str) -> int:
    """Walk open positions and close any whose stop / target / time-stop has
    triggered against the current 1m price. Always runs (every tick, not
    just at 15m boundaries) so dynamic exits don't wait for the next 15m.
    Returns count closed."""
    from strategies.support.price_feed import get_current_price
    trades = _get_open_short_squeeze_trades(variant_id)
    if not trades:
        return 0
    price = get_current_price("BTC")
    if price is None:
        return 0
    now = clock.now_utc()
    n_closed = 0
    for tr in trades:
        try:
            reason_blob = json.loads(tr.get("notes") or "{}")
        except (json.JSONDecodeError, TypeError):
            reason_blob = {}
        stop  = reason_blob.get("_stop_price")
        target = reason_blob.get("_target_price")
        ts_iso = reason_blob.get("_time_stop_iso")
        direction = tr["direction"].upper()

        # We trade LONG only; SHORT side has no edge per the backtest.
        if direction != "LONG":
            log.warning(f"[short_squeeze {variant_id}] unexpected non-LONG trade "
                        f"{tr['id']}; skipping sweep")
            continue

        if stop is not None and price <= float(stop):
            _close_paper(tr["id"], price, "stop_loss")
            log.info(f"[short_squeeze {variant_id}] stop hit on {tr['id']} "
                     f"@ {price:.2f} (stop={stop:.2f})")
            n_closed += 1
            continue
        if target is not None and price >= float(target):
            _close_paper(tr["id"], price, "take_profit")
            log.info(f"[short_squeeze {variant_id}] target hit on {tr['id']} "
                     f"@ {price:.2f} (target={target:.2f})")
            n_closed += 1
            continue
        if ts_iso is not None:
            try:
                ts_dt = datetime.fromisoformat(ts_iso)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if now >= ts_dt:
                    _close_paper(tr["id"], price, "time_stop")
                    log.info(f"[short_squeeze {variant_id}] time stop on {tr['id']} "
                             f"@ {price:.2f} (after {TIME_STOP_HOURS}h)")
                    n_closed += 1
                    continue
            except (TypeError, ValueError):
                pass
    return n_closed


# ─── Trigger evaluation ──────────────────────────────────────────────────────

def _evaluate_trigger(variant_id: str, now: datetime) -> tuple[bool, dict]:
    """Return ``(should_fire, diagnostics)`` for the current 15m bar.

    Pulls the latest closed 15m bar from prod.db, computes its percentile
    ranks against the cached 90-day distributions, applies all gates
    (session, macro, sweep, percentiles, close-in-range, cooldown).
    """
    diag: dict = {"now_utc": now.isoformat()}

    # Cooldown
    last = _last_trigger_ts.get(variant_id)
    if last is not None and (now - last) < timedelta(minutes=15 * COOLDOWN_BARS):
        return False, {**diag, "status": "cooldown",
                        "minutes_since_last": (now - last).total_seconds() / 60}

    # Distribution refresh (once per UTC day)
    today = ssq_math.utc_date_of(now)
    if _dist_cache_date != today:
        _refresh_percentile_distributions(now)
    if _perp_cvd_dist is None or len(_perp_cvd_dist) == 0:
        return False, {**diag, "status": "no_distribution"}

    # Macro context
    macro = _get_macro_context(now)
    if macro is None:
        return False, {**diag, "status": "macro_not_ready"}
    if not macro["is_short_macro"]:
        return False, {**diag, "status": "macro_not_short", "macro": macro}

    # Latest closed 15m bar
    bar = _load_latest_15m_bar(now)
    if bar is None:
        return False, {**diag, "status": "no_latest_bar"}

    # Session gate
    bar_hour = datetime.fromtimestamp(bar["ts"], tz=timezone.utc).hour
    bar_session = ssq_math.session_of_hour(bar_hour)
    if bar_session not in ("london", "ny"):
        return False, {**diag, "status": "out_of_session", "bar_session": bar_session}

    # Sweep gate: this bar's low must pierce the prior 6h low
    prior_low = _load_prior_lookback_low(now, LOOKBACK_BARS)
    if prior_low is None:
        return False, {**diag, "status": "no_prior_low"}
    if bar["low"] >= prior_low:
        return False, {**diag, "status": "no_sweep",
                        "bar_low": bar["low"], "prior_low": prior_low}

    # Percentile gates
    perp_pct = ssq_math.percentile_rank(bar["perp_cvd"], _perp_cvd_dist)
    div_pct  = ssq_math.percentile_rank(bar["divergence"], _divergence_dist)
    cir      = ssq_math.close_in_range(bar["open"], bar["high"], bar["low"], bar["close"])

    diag.update({
        "bar_ts": bar["ts"], "bar_session": bar_session,
        "bar_low": bar["low"], "bar_close": bar["close"],
        "perp_cvd": bar["perp_cvd"], "perp_cvd_pct": perp_pct,
        "divergence": bar["divergence"], "divergence_pct": div_pct,
        "close_in_range": cir, "prior_low": prior_low,
    })

    if perp_pct >= PERP_CVD_PCT_MAX:
        return False, {**diag, "status": "perp_pct_too_high"}
    if div_pct <= DIVERGENCE_PCT_MIN:
        return False, {**diag, "status": "divergence_pct_too_low"}
    if cir < CLOSE_IN_RANGE_MIN:
        return False, {**diag, "status": "close_in_range_too_low"}

    return True, {**diag, "status": "FIRE", "bar": bar}


# ─── Orchestrator interface ──────────────────────────────────────────────────

def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Two-phase dispatch entry point. See strategies/support/dispatch.py.

    Side effects (always run):
      - Sweep open positions for stop / target / time-stop hits.

    Returns ``(list[Intent], status_dict)``. Emits at most one Intent
    per call, and only on a 15m boundary minute when all gates pass.
    """
    variant_id = variant["id"]
    swept = _sweep_open_positions(variant_id)

    now = clock.now_utc()
    if not ssq_math.is_15m_boundary(now):
        return [], {"status": "not_15m_boundary", "swept": swept}

    # Already-open guard: don't stack triggers on the same variant.
    if _get_open_short_squeeze_trades(variant_id):
        return [], {"status": "position_open", "swept": swept}

    fires, diag = _evaluate_trigger(variant_id, now)
    if not fires:
        return [], {**diag, "swept": swept}

    bar = diag["bar"]
    entry_price = bar["close"]
    stop_price = bar["low"] * (1 - STOP_BUFFER)
    risk = entry_price - stop_price
    if risk <= 0:
        return [], {"status": "invalid_risk", "swept": swept}
    target_price = entry_price + TP_R * risk
    time_stop_dt = now + timedelta(hours=TIME_STOP_HOURS)

    alloc_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                       sleeve_cfg.get("weight_pct", 0.0)))
    leverage  = float(sleeve_cfg.get("_effective_leverage", 1.0))

    reason = {
        "trigger": "short_squeeze_long",
        "variant_id": variant_id,
        "sleeve": "SHORT_SQUEEZE",
        "bar_ts_utc": datetime.fromtimestamp(bar["ts"], tz=timezone.utc).isoformat(),
        "bar_low": bar["low"], "bar_close": bar["close"],
        "perp_cvd": bar["perp_cvd"], "perp_cvd_pct": diag["perp_cvd_pct"],
        "divergence": bar["divergence"], "divergence_pct": diag["divergence_pct"],
        "close_in_range": diag["close_in_range"],
        "prior_low_24x15m": diag["prior_low"],
        # Sweep needs these to evaluate exit conditions.
        "_stop_price": stop_price,
        "_target_price": target_price,
        "_time_stop_iso": time_stop_dt.isoformat(),
        "_entry_price": entry_price,
    }
    intent = Intent(
        asset="BTC", direction="LONG",
        allocation_pct=alloc_pct, leverage=leverage,
        conviction=100,
        priority=float(sleeve_cfg.get("priority", 100)),
        reason=reason, scheduled_exit_dt=time_stop_dt,
    )
    return [intent], {"status": "decided", "swept": swept,
                       "perp_pct": diag["perp_cvd_pct"],
                       "div_pct": diag["divergence_pct"]}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent: Intent) -> dict:
    """Phase 2: open the LONG described by ``intent``."""
    from strategies.trades import open_paper_trade
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    # Keep _stop_price, _target_price, _time_stop_iso in `notes` so the
    # sweep loop can read them.
    tid = open_paper_trade(
        variant=variant, sleeve_name="SHORT_SQUEEZE",
        asset=intent.asset, direction="LONG",
        entry_price=entry_price,
        allocation_pct=intent.allocation_pct, leverage=intent.leverage,
        reason=reason,
        scheduled_exit_dt=intent.scheduled_exit_dt,
        regime_value="short_squeeze",
    )
    _last_trigger_ts[variant["id"]] = clock.now_utc()
    log.info(f"[short_squeeze {variant['id']}] opened {tid} BTC LONG @ "
             f"{entry_price:.2f}  stop={reason['_stop_price']:.2f}  "
             f"target={reason['_target_price']:.2f}  "
             f"alloc={intent.allocation_pct}%  k={intent.leverage}x")
    return {"status": "opened", "trade_id": tid, "entry_price": entry_price,
            "stop_price": reason["_stop_price"],
            "target_price": reason["_target_price"]}


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Single-call entry point for the legacy orchestrator path. Wraps
    decide + execute."""
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    return execute_for_variant(variant, sleeve_cfg, intents[0])
