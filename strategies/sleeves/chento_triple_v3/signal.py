"""CHENTO_TRIPLE_V3 — sleeve orchestrator.

Implements the optimized chento Triple composite + filters + adaptive A4
ladder on BTC perp 15m. See README.md for the full provenance and the
performance numbers from validation work.

Tick pattern (same as other sleeves):
  - _sweep_open_positions(): every minute, manage open trades
                              (stop/target/ladder/TIF via math.evaluate_position_step)
  - _evaluate_trigger():     at 15m boundary, check Triple composite + filters

Data dependencies (loaded per tick from prod.db):
  - cd_futures_15m (BTC perp OHLC + taker buy/sell)
  - ca_long_short_ratio asset='BTC' (for B5)
  - okx_perp_1h close (for OKX gate)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.support import clock, db
from strategies.support.dispatch import Intent

from . import math as ctm
from .config import (
    SLEEVE_NAME, ASSET,
    ATR_PERIOD, ATR_STOP_MULT, TARGET_R, TIF_HOURS, COST_BP_RT,
    B1_CVD_WINDOW_BARS, B1_VEL_WINDOW_BARS, B1_CVD_Z_THRESHOLD, B1_VEL_Z_MAX,
    B5_ROLLING_DAYS,
    B7_TIMEFRAMES, B7_Z_THRESHOLD,
    TRIPLE_WINDOW_HOURS,
    FILTER_NO_TILT, FILTER_NO_RESIST_OB, FILTER_OKX_ALIGNED,
    FILTER_SKIP_UP_30D_SHORTS,
    SMC_PIVOT_N, SMC_OB_WITHIN_R,
    OKX_DELTA_WINDOW_HOURS, OKX_ALIGN_Z_MIN,
    UP_30D_THRESHOLD,
    LADDER_ENABLED, LADDER_ADV_TRIGGER_R,
    LADDER_T1_SIZE_FRAC, LADDER_T3_SIZE_FRAC, LADDER_POST_STOP_R,
    VP_WINDOW_DAYS, VP_N_BINS, VP_VALUE_AREA_PCT,
    COOLDOWN_HOURS,
    TRIGGER_HOUR_MIN, TRIGGER_HOUR_MAX, TRIGGER_WEEKDAYS,
)

log = logging.getLogger("p300.chento_triple_v3")


# ─── Module caches (rebuild once per UTC day) ───────────────────────────────
_cache_date: str | None = None
_cached_features: dict = {}      # {ts -> {cvd_z, vel_z, mtf_z dict, lsr_extremes, okx_delta_z, ret_30d}}
_cached_obs: list[dict] = []     # SMC OBs computed once per day
_cached_df_15m_idx: pd.DatetimeIndex | None = None
_last_trigger_ts: dict[str, datetime] = {}    # per-variant cooldown
_last_loss_ts: dict[str, datetime] = {}       # per-variant last loss timestamp (for no_tilt)


# ─── Diagnostics (opt-in via env CHENTO_V3_DIAG=1) ──────────────────────────
# Per-day counters tracking which gate killed each candidate. Flushed to a
# JSONL one line per UTC day on cache rebuild. Use to root-cause sparse
# trade output by quantifying where the signal pipeline truncates.
_DIAG_ENABLED: bool = os.environ.get("CHENTO_V3_DIAG") == "1"
_DIAG_PATH: Path = Path(os.environ.get(
    "CHENTO_V3_DIAG_PATH",
    str(Path(__file__).resolve().parents[3] / "data" / "diagnostics"
        / "chento_v3_diag.jsonl"),
))
_diag_current_day: str | None = None
_diag_counters: dict[str, int] = {}
_diag_near_misses: list[dict] = []


def _diag_inc(key: str, n: int = 1) -> None:
    if not _DIAG_ENABLED:
        return
    _diag_counters[key] = _diag_counters.get(key, 0) + n


def _diag_near_miss(ts: datetime, **kwargs) -> None:
    """Record a bar where Triple fired but a downstream gate killed it.
    Caller decides what context to record."""
    if not _DIAG_ENABLED:
        return
    _diag_near_misses.append({"ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                              **kwargs})


def _diag_flush(new_day_iso: str) -> None:
    """Append previous day's counters to the JSONL and reset for the new day.
    No-op if DIAG is off."""
    global _diag_current_day, _diag_counters, _diag_near_misses
    if not _DIAG_ENABLED:
        _diag_current_day = new_day_iso
        return
    if _diag_current_day is not None and (_diag_counters or _diag_near_misses):
        try:
            _DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _DIAG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "utc_date": _diag_current_day,
                    "counters": dict(_diag_counters),
                    "near_misses": list(_diag_near_misses),
                }) + "\n")
        except Exception:
            log.exception(f"[{SLEEVE_NAME}] diag flush failed (path={_DIAG_PATH})")
    _diag_counters = {}
    _diag_near_misses = []
    _diag_current_day = new_day_iso


# ─── Data loaders ──────────────────────────────────────────────────────────

def _load_15m_btc(now: datetime, days_back: int) -> pd.DataFrame:
    """Load BTC perp 15m OHLCV with taker buy/sell split."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        cutoff = int((now - timedelta(days=days_back)).timestamp())
        df = pd.read_sql("""
            SELECT timestamp, open, high, low, close, volume, quote_volume,
                   volume_buy, quote_volume_buy, volume_sell, quote_volume_sell
            FROM cd_futures_15m
            WHERE timestamp >= ?
            ORDER BY timestamp
        """, con, params=(cutoff,))
    finally:
        con.close()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("ts").drop(columns="timestamp")


def _load_lsr_btc(now: datetime, days_back: int) -> pd.DataFrame:
    """Load BTC long_short_ratio."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        cutoff = int((now - timedelta(days=days_back)).timestamp())
        df = pd.read_sql("""
            SELECT timestamp, ratio, long_pct, short_pct
            FROM ca_long_short_ratio
            WHERE asset='BTC' AND timestamp >= ?
            ORDER BY timestamp
        """, con, params=(cutoff,))
    finally:
        con.close()
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("ts").drop(columns="timestamp")


def _load_okx_1h(now: datetime, days_back: int) -> pd.Series:
    """Load OKX BTC-USDT-SWAP 1h close."""
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        cutoff = int((now - timedelta(days=days_back)).timestamp())
        df = pd.read_sql("""
            SELECT timestamp, close FROM okx_perp_1h
            WHERE timestamp >= ?
            ORDER BY timestamp
        """, con, params=(cutoff,))
    finally:
        con.close()
    if df.empty:
        return pd.Series(dtype=float)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("ts")["close"]


# ─── Daily feature cache rebuild ───────────────────────────────────────────

def _rebuild_daily_cache(now: datetime) -> None:
    """Once per UTC day, recompute all rolling/expensive features and store
    on the latest 15m index. Subsequent ticks within the day just lookup.

    This is the same pattern as v2's MTF bias cache.
    """
    global _cache_date, _cached_features, _cached_obs, _cached_df_15m_idx

    today_iso = now.astimezone(timezone.utc).date().isoformat()
    if _cache_date == today_iso:
        return

    # Flush prior-day diag counters before mutating cache state.
    _diag_flush(today_iso)

    log.info(f"[{SLEEVE_NAME}] rebuilding daily feature cache for {today_iso}")

    # Need 90d of 15m to handle 30d CVD z window + some buffer
    df_15m = _load_15m_btc(now, days_back=90)
    if df_15m.empty:
        log.warning(f"[{SLEEVE_NAME}] no 15m data, skipping cache rebuild")
        return

    # B1: cvd_z + vel_z
    df_15m = ctm.compute_moneyflow_signal(
        df_15m, cvd_window_bars=B1_CVD_WINDOW_BARS,
        velocity_window_bars=B1_VEL_WINDOW_BARS)

    # B7: multi-TF CVD z-scores
    df_15m = ctm.compute_multitf_cvd_z(df_15m, B7_TIMEFRAMES)

    # ATR
    df_15m["atr"] = ctm.compute_atr(df_15m, period=ATR_PERIOD)

    # 30d return (resampled daily)
    df_15m["ret_30d"] = ctm.compute_30d_return(df_15m["close"], days=30)

    # LSR extremes
    lsr_df = _load_lsr_btc(now, days_back=90)
    if not lsr_df.empty:
        lsr_z = ctm.compute_lsr_extremes(lsr_df, rolling_days=B5_ROLLING_DAYS)
        # Align onto 15m index by ffill with limit=4 bars (1h freshness) to
        # match research's validation_B5_lsr_extremes.b5_triggers. Earlier
        # version had no limit, which let stale LSR values keep firing B5 for
        # the full day after each daily update — a port bug fixed 2026-05-31.
        df_15m["long_pct"] = lsr_z["long_pct"].reindex(df_15m.index, method="ffill", limit=4)
        df_15m["lp_p10"] = lsr_z["lp_p10"].reindex(df_15m.index, method="ffill", limit=4)
        df_15m["lp_p90"] = lsr_z["lp_p90"].reindex(df_15m.index, method="ffill", limit=4)
    else:
        df_15m["long_pct"] = np.nan
        df_15m["lp_p10"] = np.nan
        df_15m["lp_p90"] = np.nan

    # OKX delta z
    okx_close = _load_okx_1h(now, days_back=30)
    if not okx_close.empty:
        # Resample 15m close to 1h
        bnb_1h_close = df_15m["close"].resample("1h").last()
        delta_z = ctm.compute_okx_delta_z(bnb_1h_close, okx_close,
                                            window_hours=OKX_DELTA_WINDOW_HOURS)
        df_15m["okx_delta_z"] = delta_z.reindex(df_15m.index, method="ffill")
    else:
        df_15m["okx_delta_z"] = np.nan

    # SMC OBs
    df_smc = ctm.detect_pivots(df_15m, n=SMC_PIVOT_N)
    _cached_obs = ctm.detect_order_blocks(df_smc, n=SMC_PIVOT_N)

    # Windowed triple-intersection columns (replaces same-bar triple_fires).
    # Mirrors research's validation_B_composite.intersect_triggers with a
    # backward-only TRIPLE_WINDOW_HOURS window. Adds columns:
    # triple_long_w / triple_short_w (used at trigger time) and
    # triple_long_same / triple_short_same (kept for diag comparisons).
    triple_window_bars = TRIPLE_WINDOW_HOURS * 4   # 4 bars per hour
    df_15m = ctm.compute_triple_windowed(
        df_15m, window_bars=triple_window_bars,
        b1_cvd_threshold=B1_CVD_Z_THRESHOLD, b1_vel_max=B1_VEL_Z_MAX,
        b7_timeframes=B7_TIMEFRAMES, b7_z_threshold=B7_Z_THRESHOLD,
    )

    # Store the full feature dataframe for tick lookup
    _cached_features = {
        "df": df_15m,
        "smc_pivots": df_smc,
    }
    _cached_df_15m_idx = df_15m.index
    _cache_date = today_iso
    log.info(f"[{SLEEVE_NAME}] daily cache built: {len(df_15m)} 15m bars, "
              f"{len(_cached_obs)} OBs")


# ─── Triple trigger evaluation ─────────────────────────────────────────────

def _check_triple_at_idx(idx: int) -> str | None:
    """Return 'long', 'short', or None using the B1-ANCHORED triple columns
    pre-computed by compute_triple_windowed. Fires when B1 (money-flow
    divergence) fires fresh AT this bar AND B5+B7 have same-direction
    fires in the trailing window. Mirrors research's intersect_triggers
    semantics — research anchors on B1's b1_triggers list and keeps
    only those with B5/B7 within ±24h. Empirically picks LATER bars
    within a confluence than the rising-edge anchor (closer to the
    exhaustion / reversal point, capturing more R per trade)."""
    df = _cached_features.get("df")
    if df is None or idx < 0 or idx >= len(df):
        return None
    row = df.iloc[idx]
    long_a = bool(row.get("triple_long_anchor", False))
    short_a = bool(row.get("triple_short_anchor", False))
    # Contested (B1 fires both directions at this bar — impossible by
    # construction of b1_fires, but defensive).
    if long_a and short_a:
        return None
    if long_a:
        return "long"
    if short_a:
        return "short"
    return None


# ─── Filter gates ──────────────────────────────────────────────────────────

def _filter_passes(direction: str, idx: int, entry_price: float, risk: float,
                    variant_id: str) -> tuple[bool, dict]:
    """Apply all 4 filter gates. Returns (passes, diag_dict)."""
    df = _cached_features.get("df")
    if df is None:
        return False, {"reason": "no_cache"}
    diag = {}

    # Filter 1: no-tilt (no recent losing trade)
    if FILTER_NO_TILT:
        last_loss = _last_loss_ts.get(variant_id)
        if last_loss is not None:
            # Skip if loss happened since the last trigger AND it was the most
            # recent trade for this sleeve. (Simplified: skip if any loss in
            # the last 48h — same effect since cooldown is 4h and TIF is 72h.)
            now = clock.now_utc()
            if (now - last_loss).total_seconds() < 48 * 3600:
                return False, {"reason": "no_tilt_recent_loss",
                               "last_loss_age_hours":
                                   (now - last_loss).total_seconds() / 3600}
        diag["no_tilt"] = "pass"

    # Filter 2: no_resist_OB_within_2R
    if FILTER_NO_RESIST_OB:
        dist_R = ctm.nearest_resist_ob_distance_R(
            entry_price, direction, risk, _cached_obs, idx)
        if dist_R <= SMC_OB_WITHIN_R:
            return False, {"reason": "resist_OB_too_close", "dist_R": dist_R}
        diag["resist_ob_dist_R"] = dist_R if np.isfinite(dist_R) else 99.0

    # Filter 3: OKX delta z aligned with direction
    if FILTER_OKX_ALIGNED:
        okx_z = float(df.iloc[idx]["okx_delta_z"])
        if not ctm.okx_aligned(okx_z, direction, OKX_ALIGN_Z_MIN):
            return False, {"reason": "okx_misaligned", "okx_delta_z": okx_z}
        diag["okx_delta_z"] = okx_z

    # Filter 4: skip shorts in up_30d
    if FILTER_SKIP_UP_30D_SHORTS and direction == "short":
        ret_30d = float(df.iloc[idx]["ret_30d"])
        if ctm.is_up_30d(ret_30d, UP_30D_THRESHOLD):
            return False, {"reason": "up_30d_short_skip", "ret_30d": ret_30d}
        diag["ret_30d"] = ret_30d

    return True, diag


# ─── Adaptive sizing (H_B classifier via VP) ───────────────────────────────

def _ladder_size_for_now(entry_price: float, idx: int) -> tuple[float, bool]:
    """Compute the H_B ladder size based on inside-VA classification.
    Returns (size_frac, inside_va_bool)."""
    df = _cached_features.get("df")
    if df is None:
        return LADDER_T1_SIZE_FRAC, False
    # Compute VP on trailing VP_WINDOW_DAYS
    bars_per_day = 96   # 15m
    window_bars = VP_WINDOW_DAYS * bars_per_day
    lo = max(0, idx - window_bars)
    window_df = df.iloc[lo:idx]
    poc, vah, val = ctm.compute_volume_profile_for_ts(
        window_df, n_bins=VP_N_BINS, value_area_pct=VP_VALUE_AREA_PCT)
    inside = ctm.is_inside_va(entry_price, vah, val)
    return (LADDER_T3_SIZE_FRAC if inside else LADDER_T1_SIZE_FRAC, inside)


# ─── Trade DB helpers (mirror v2 pattern) ──────────────────────────────────

def _get_open_trades(variant_id: str) -> list[dict]:
    """Open paper trades for this variant + sleeve."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT id, asset, direction, entry_price, entry_time, exit_time, notes
            FROM trades
            WHERE strategy_variant = ? AND strategy = ?
              AND status = 'open' AND execution_mode = 'paper'
        """, (variant_id, SLEEVE_NAME)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _close_paper(trade_id: str, exit_price: float, reason: str) -> None:
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name=SLEEVE_NAME,
                      cost_bp_rt=COST_BP_RT, slippage_bp_rt=0.0,
                      apply_funding=False)


def _write_trade_state(trade_id: str, state: dict, extra: dict | None = None) -> None:
    """Persist the position state into the trade's notes JSON."""
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute("SELECT notes FROM trades WHERE id=?",
                           (trade_id,)).fetchone()
        if row is None:
            return
        try:
            blob = json.loads(row[0]) if row[0] else {}
        except (json.JSONDecodeError, TypeError):
            blob = {}
        blob["_state"] = state
        if extra:
            blob.update(extra)
        con.execute("UPDATE trades SET notes=? WHERE id=?",
                     (json.dumps(blob), trade_id))
        con.commit()
    finally:
        con.close()


# ─── Bar-walking helpers (Fix A: intra-bar resolution) ─────────────────────
# Production previously called evaluate_position_step with current_price as
# bar_high / bar_low / bar_close — a point sample that systematically
# misses intra-bar target/stop/ladder fills. These helpers let the sweep
# walk each just-closed 15m bar with its actual high/low/close, matching
# research's validation_liquidation_and_C6.replay_with_mae semantics.

def _just_closed_15m_ts(now: datetime) -> pd.Timestamp:
    """Largest 15m boundary STRICTLY less than `now`. Used to cap the
    walking range so we never read a bar whose open-time >= now."""
    floor = now.replace(second=0, microsecond=0)
    floor -= timedelta(minutes=floor.minute % 15)
    if floor == now:
        floor -= timedelta(minutes=15)
    return pd.Timestamp(floor)


def _bar_ohlc_for(ts: pd.Timestamp) -> tuple[float, float, float] | None:
    """Return (high, low, close) for the 15m bar with open_time `ts`.
    Try the cached features first (cheap dict-hash lookup); fall back to a
    single-row indexed SQLite read; return None on genuine data gap so the
    caller can degrade to point-sample with a WARN log."""
    df = _cached_features.get("df")
    if df is not None and ts in df.index:
        row = df.loc[ts]
        h, l, c = float(row["high"]), float(row["low"]), float(row["close"])
        if not (pd.isna(h) or pd.isna(l) or pd.isna(c)):
            return h, l, c
    con = sqlite3.connect(str(db.PROD_DB))
    try:
        row = con.execute(
            "SELECT high, low, close FROM cd_futures_15m WHERE timestamp = ?",
            (int(ts.timestamp()),),
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    return float(row[0]), float(row[1]), float(row[2])


def _iter_closed_bars(after_ts: pd.Timestamp, through_ts: pd.Timestamp):
    """Yield each 15m bar open-time in (after_ts, through_ts] in order."""
    cur = after_ts + pd.Timedelta(minutes=15)
    while cur <= through_ts:
        yield cur
        cur += pd.Timedelta(minutes=15)


# ─── Open-position sweep (every-tick) ──────────────────────────────────────

def _sweep_open_positions(variant: dict, sleeve_cfg: dict) -> int:
    """Manage open CHENTO_TRIPLE_V3 trades: check stop/target/ladder/TIF.

    Returns number of position-management actions taken.
    """
    from strategies.support.price_feed import get_current_price

    variant_id = variant["id"]
    trades = _get_open_trades(variant_id)
    if not trades:
        return 0

    current_price = float(get_current_price(ASSET))
    if not current_price or current_price <= 0:
        return 0
    now = clock.now_utc()
    n_actions = 0

    for tr in trades:
        trade_id = tr["id"]
        direction_l = tr["direction"].lower()
        try:
            blob = json.loads(tr.get("notes") or "{}")
        except (json.JSONDecodeError, TypeError):
            blob = {}
        state = blob.get("_state", {})
        if not state:
            log.warning(f"[{SLEEVE_NAME}] {trade_id} has no state in notes; skipping")
            continue

        # Parse TIF schedule (used both for the zero-walk fallback and
        # the in-walker TIF check). exit_time column carries the schedule.
        sched_dt = None
        sched_exit = tr.get("exit_time")
        if sched_exit:
            try:
                sched_dt = datetime.fromisoformat(sched_exit)
                if sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)
            except Exception:
                sched_dt = None

        # Walk bars from (last_walked_ts, just_closed_ts] in chronological
        # order. Each bar's actual high/low feeds evaluate_position_step,
        # matching research's replay_with_mae bar-by-bar walking.
        just_closed = _just_closed_15m_ts(now)
        last_walked_iso = state.get("last_walked_ts")
        if last_walked_iso:
            walk_from = pd.Timestamp(last_walked_iso)
        else:
            # First sweep after entry: anchor at the trigger bar so the iter
            # yields entry_bar_ts + 15m as the first walked bar (matches
            # research's `start = idx + 1` convention).
            entry_bar_iso = state.get("entry_bar_ts")
            walk_from = (pd.Timestamp(entry_bar_iso) if entry_bar_iso
                          else just_closed - pd.Timedelta(minutes=15))

        entry_price = float(state["entry_price"])
        cost_R = (COST_BP_RT / 10000.0) * (entry_price / float(state["risk"]))
        closed = False
        walked_any = False
        for bar_ts in _iter_closed_bars(walk_from, just_closed):
            walked_any = True

            # In-walker TIF check: exit at THIS bar's close if TIF falls
            # at or before the bar. Matches research's bar-count TIF cap.
            if sched_dt is not None and bar_ts >= sched_dt:
                ohlc = _bar_ohlc_for(bar_ts)
                tif_close = ohlc[2] if ohlc is not None else current_price
                _close_paper(trade_id, tif_close, "tif_expiry")
                n_actions += 1
                closed = True
                break

            ohlc = _bar_ohlc_for(bar_ts)
            if ohlc is None:
                log.warning(f"[{SLEEVE_NAME}] {trade_id} no OHLC for {bar_ts}; "
                             f"fallback point-sample at {current_price:.2f}")
                bh = bl = bc = current_price
            else:
                bh, bl, bc = ohlc

            result = ctm.evaluate_position_step(
                state,
                bar_high=bh, bar_low=bl, bar_close=bc,
                atr_now=state.get("atr_at_entry", 0.0),
                direction=direction_l,
                ladder_enabled=LADDER_ENABLED,
                ladder_adv_trigger_R=LADDER_ADV_TRIGGER_R,
                ladder_size_frac=state.get("ladder_size_frac", LADDER_T1_SIZE_FRAC),
                ladder_post_stop_R=LADDER_POST_STOP_R,
                cost_R=cost_R,
            )
            action = result.get("action")
            if action == "stop_hit":
                _close_paper(trade_id, result["exit_price"], "stop_hit")
                n_actions += 1
                if result["r_outcome"] < 0:
                    _last_loss_ts[variant_id] = now
                closed = True
                break
            elif action == "target_hit":
                _close_paper(trade_id, result["exit_price"], "target_hit")
                n_actions += 1
                closed = True
                break
            elif action == "ladder_fired":
                # Persist mid-walk so subsequent bars in THIS loop see the
                # mutated state (wider stop, ladder_added=True).
                state["last_walked_ts"] = bar_ts.isoformat()
                _write_trade_state(trade_id, state,
                                    extra={"_ladder_fired_at": bar_ts.isoformat()})
                log.info(f"[{SLEEVE_NAME}] {trade_id} ladder fired @ "
                          f"{result['ladder_entry']:.2f}; new combined stop "
                          f"{result['new_stop']:.2f}")
                n_actions += 1
            # action is None: no event this bar; continue walking.

        # Fallback TIF check for the zero-bar-walk case (live mode between
        # 15m boundaries when last_walked_ts already == just_closed).
        if not closed and not walked_any and sched_dt is not None and now >= sched_dt:
            _close_paper(trade_id, current_price, "tif_expiry")
            n_actions += 1
            closed = True

        # Persist walking progress so the next sweep doesn't re-walk these
        # bars. Only update if we actually advanced.
        if not closed and walked_any:
            state["last_walked_ts"] = just_closed.isoformat()
            _write_trade_state(trade_id, state)
    return n_actions


# ─── 15m trigger evaluation ────────────────────────────────────────────────

def _evaluate_trigger(now: datetime, variant: dict,
                       sleeve_cfg: dict) -> tuple[list[Intent], dict]:
    """Phase 1: check Triple + filters at the current 15m bar."""
    variant_id = variant["id"]
    _diag_inc("eval_calls")

    # Cooldown
    last_trigger = _last_trigger_ts.get(variant_id)
    if last_trigger is not None:
        if (now - last_trigger).total_seconds() < COOLDOWN_HOURS * 3600:
            _diag_inc("cooldown_blocked")
            return [], {"status": "cooldown"}

    # Trigger-window gate (default all-open)
    h = now.hour
    if not (TRIGGER_HOUR_MIN <= h <= TRIGGER_HOUR_MAX):
        _diag_inc("off_hour")
        return [], {"status": "off_hour", "hour": h}
    if now.weekday() not in TRIGGER_WEEKDAYS:
        _diag_inc("off_day")
        return [], {"status": "off_day"}

    # Rebuild daily cache if needed
    _rebuild_daily_cache(now)
    df = _cached_features.get("df")
    if df is None or df.empty:
        _diag_inc("no_data")
        return [], {"status": "no_data"}

    # Find the most recent 15m bar index
    idx = df.index.searchsorted(now, side="right") - 1
    if idx < 0:
        _diag_inc("no_bar")
        return [], {"status": "no_bar"}
    bar_ts = df.index[idx]
    # Only fire on actual 15m boundaries (within 1 minute tolerance)
    if abs((now - bar_ts).total_seconds()) > 60:
        _diag_inc("boundary_skipped")
        # Triple should only be evaluated AT the bar close, not mid-bar
        return [], {"status": "not_at_15m_boundary",
                    "bar_ts": bar_ts.isoformat(),
                    "minutes_into_bar": (now - bar_ts).total_seconds() / 60}

    _diag_inc("bars_at_boundary")

    # Triple check via windowed columns (TRIPLE_WINDOW_HOURS backward).
    direction = _check_triple_at_idx(idx)
    if _DIAG_ENABLED:
        row = df.iloc[idx]
        # Per-leg fires AT this bar (same-bar) — kept for backward comparison
        # with the pre-windowed diag output.
        b1_dir = ctm.b1_fires(row["cvd_z"], row["vel_z"],
                               cvd_threshold=B1_CVD_Z_THRESHOLD,
                               vel_max=B1_VEL_Z_MAX)
        b5_dir = ctm.b5_fires(row["long_pct"], row["lp_p10"], row["lp_p90"])
        cvd_z_values = {tf: row[f"cvd_z_{tf}"] for tf in B7_TIMEFRAMES
                         if f"cvd_z_{tf}" in df.columns}
        b7_dir = ctm.b7_alignment_fires(cvd_z_values, z_threshold=B7_Z_THRESHOLD)
        _diag_inc(f"b1_{b1_dir or 'none'}")
        _diag_inc(f"b5_{b5_dir or 'none'}")
        _diag_inc(f"b7_{b7_dir or 'none'}")
        # Windowed B1/B5/B7 — whether each gate had a same-direction fire in
        # the trailing TRIPLE_WINDOW_HOURS. These drive the triple decision.
        for gate, col_l, col_s in (("b1", "b1_long_w", "b1_short_w"),
                                     ("b5", "b5_long_w", "b5_short_w"),
                                     ("b7", "b7_long_w", "b7_short_w")):
            if bool(row.get(col_l, False)):
                _diag_inc(f"{gate}_window_long")
            if bool(row.get(col_s, False)):
                _diag_inc(f"{gate}_window_short")
        # Also track same-bar triple (would've been the old logic) for compare.
        if bool(row.get("triple_long_same", False)):
            _diag_inc("triple_long_same_bar")
        if bool(row.get("triple_short_same", False)):
            _diag_inc("triple_short_same_bar")
        # Windowed-true (would-fire-every-bar) for cluster diagnosis.
        if bool(row.get("triple_long_w", False)):
            _diag_inc("triple_long_windowed_true")
        if bool(row.get("triple_short_w", False)):
            _diag_inc("triple_short_windowed_true")
        # Rising-edge of windowed-AND (the prior anchor) — kept for compare.
        if bool(row.get("triple_long_edge", False)):
            _diag_inc("triple_long_edge_fires")
        if bool(row.get("triple_short_edge", False)):
            _diag_inc("triple_short_edge_fires")
        # B1-anchored fires (the current production anchor).
        if bool(row.get("triple_long_anchor", False)):
            _diag_inc("triple_long_anchor_fires")
        if bool(row.get("triple_short_anchor", False)):
            _diag_inc("triple_short_anchor_fires")

    if direction is None:
        _diag_inc("no_triple")
        return [], {"status": "no_triple"}

    _diag_inc(f"triple_{direction}_fires")

    # Sizing inputs
    entry_price = float(df.iloc[idx]["close"])
    atr_now = float(df.iloc[idx]["atr"])
    if pd.isna(atr_now) or atr_now <= 0:
        _diag_inc("invalid_atr")
        return [], {"status": "invalid_atr"}
    risk = atr_now * ATR_STOP_MULT

    # Filters
    passes, filter_diag = _filter_passes(direction, idx, entry_price, risk, variant_id)
    if not passes:
        reason = filter_diag.get("reason", "unknown")
        _diag_inc(f"filter_{reason}")
        _diag_near_miss(bar_ts, direction=direction, reason=reason,
                         entry=float(entry_price), **{
                             k: float(v) if isinstance(v, (int, float, np.floating)) else v
                             for k, v in filter_diag.items() if k != "reason"
                         })
        return [], {"status": "filter_blocked", **filter_diag}

    _diag_inc(f"decided_{direction}")

    # Adaptive ladder sizing via inside-VA classification
    ladder_size, inside_va = _ladder_size_for_now(entry_price, idx)

    # Compute stop / target
    if direction == "long":
        stop_price = entry_price - risk
        target_price = entry_price + risk * TARGET_R
    else:
        stop_price = entry_price + risk
        target_price = entry_price - risk * TARGET_R

    time_stop_dt = now + timedelta(hours=TIF_HOURS)

    alloc_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                       sleeve_cfg.get("weight_pct", 0.0)))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    reason = {
        "trigger": "chento_triple_v3",
        "variant_id": variant_id,
        "sleeve": SLEEVE_NAME,
        "bar_ts": bar_ts.isoformat(),
        "_entry_price": entry_price,
        "_stop_price": stop_price,
        "_target_price": target_price,
        "_atr_at_entry": atr_now,
        "_risk": risk,
        "_inside_va": bool(inside_va),
        "_ladder_size_frac": ladder_size,
        "_time_stop_iso": time_stop_dt.isoformat(),
        "_filter_diag": filter_diag,
        "_state": {
            "entry_price": entry_price,
            "risk": risk,
            "stop_price": stop_price,
            "target_price": target_price,
            "ladder_added": False,
            "ladder_entry": None,
            "ladder_size_frac": ladder_size,
            "atr_at_entry": atr_now,
            # Fix A: bar-walker anchor. entry_bar_ts is the open-time of the
            # trigger bar; the sweep walker iterates bars (entry_bar_ts,
            # just_closed_ts] so the first walked bar is entry_bar+15m,
            # matching research's `start = idx + 1` convention.
            "entry_bar_ts": bar_ts.isoformat(),
            "last_walked_ts": bar_ts.isoformat(),
        },
    }
    intent = Intent(
        asset=ASSET, direction=direction.upper(),
        allocation_pct=alloc_pct, leverage=leverage,
        conviction=100,
        priority=float(sleeve_cfg.get("priority", 100)),
        reason=reason, scheduled_exit_dt=time_stop_dt,
    )
    return [intent], {"status": "decided",
                       "direction": direction, "entry": entry_price,
                       "stop": stop_price, "target": target_price,
                       "inside_va": inside_va, "ladder_size_frac": ladder_size}


# ─── Public sleeve interface ───────────────────────────────────────────────

def try_decide_for_variant(variant: dict, sleeve_cfg: dict
                              ) -> tuple[list[Intent], dict]:
    """Phase 1: position management sweep, then 15m trigger evaluation."""
    now = clock.now_utc()
    swept = _sweep_open_positions(variant, sleeve_cfg)
    intents, status = _evaluate_trigger(now, variant, sleeve_cfg)
    return intents, {"swept": swept, **status}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent: Intent) -> dict:
    """Phase 2: open the trade described by `intent`."""
    from strategies.trades import open_paper_trade
    reason = dict(intent.reason or {})
    entry_price = float(reason["_entry_price"])
    tid = open_paper_trade(
        variant=variant, sleeve_name=SLEEVE_NAME,
        asset=intent.asset, direction=intent.direction,
        entry_price=entry_price,
        allocation_pct=intent.allocation_pct, leverage=intent.leverage,
        reason=reason,
        scheduled_exit_dt=intent.scheduled_exit_dt,
        regime_value="chento_triple_v3",
    )
    _last_trigger_ts[variant["id"]] = clock.now_utc()
    log.info(f"[{SLEEVE_NAME} {variant['id']}] opened {tid} BTC {intent.direction} "
              f"@ {entry_price:.2f}  stop={reason['_stop_price']:.2f}  "
              f"target={reason['_target_price']:.2f}  "
              f"inside_va={reason['_inside_va']}  "
              f"ladder_size={reason['_ladder_size_frac']:.2f}x  "
              f"alloc={intent.allocation_pct}%  k={intent.leverage}x")
    return {"status": "opened", "trade_id": tid, "entry_price": entry_price,
            "stop_price": reason["_stop_price"],
            "target_price": reason["_target_price"]}


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Single-call entry point for legacy orchestrator paths."""
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    return execute_for_variant(variant, sleeve_cfg, intents[0])
