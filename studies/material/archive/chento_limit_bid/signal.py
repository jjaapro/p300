"""S-106 CHENTO_LIMIT_BID — sleeve dispatcher.

Long BTC perp on approach to a confirmed swing-base when MTF bias is in
the +1/+2 net range OR matches the --+++ capitulation signature, with
confluence score ≥ 3 across {basis, funding, OI flush, spot CVD}.

See README.md for the strategy, and the math module for the pure helpers
shared with the backtest notebook.

Tick model: same pattern as SHORT_SQUEEZE — every-minute sweep loop +
on-15m-boundary trigger evaluation. Cooldown 24h between fires.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from strategies.support import clock, db
from strategies.support.dispatch import Intent

from . import math as cli_math
from .config import (
    BASE_WINDOW_HOURS, BASE_APPROACH_BAND_PCT,
    CONF_SCORE_MIN, MTF_DEFS,
    COOLDOWN_MIN, STOP_OFFSET_PCT, TIF_DAYS,
    T1_R, T1_CLOSE_PCT, T2_R, T2_CLOSE_PCT, TRAIL_PCT,
    COST_BP_RT, SLIPPAGE_BP_RT,
)

log = logging.getLogger("p300.chento_limit_bid")

# ─── Module caches ────────────────────────────────────────────────────────

# Per-UTC-day cache for the MTF bias maps (built once per day, ~500ms cost).
_mtf_cache_date: str | None = None
_mtf_bias_map: dict[str, pd.Series] = {}

# Per-variant last-trigger timestamp for cooldown enforcement.
_last_trigger_ts: dict[str, datetime] = {}


# ─── Data loaders ────────────────────────────────────────────────────────

def _load_15m_enriched(now: datetime, hours_back: int) -> pd.DataFrame:
    """Load the last `hours_back` of 15m bars with all the columns the
    detector + scorer need:

      'spot_o', 'spot_h', 'spot_l', 'spot_c'  — spot OHLC
      'fut_c'                                  — perp close (for basis)
      'basis_bp'                               — (perp_close - spot_close) / spot_close * 10000
      'spot_cvd'                               — spot taker buy minus sell
      'oi'                                     — open interest (ff from hourly)
      'funding'                                — funding rate (ff from 8h)

    Tz-aware index (UTC).
    """
    cutoff_s = int((now - timedelta(hours=hours_back)).timestamp())
    now_s = int(now.timestamp())
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        df = pd.read_sql("""
            SELECT
              s.timestamp,
              s.open  AS spot_o, s.high AS spot_h,
              s.low   AS spot_l, s.close AS spot_c,
              COALESCE(s.volume_buy, 0) - COALESCE(s.volume_sell, 0) AS spot_cvd,
              f.close AS fut_c
            FROM cd_spot_15m s
            LEFT JOIN cd_futures_15m f ON f.timestamp = s.timestamp
            WHERE s.timestamp >= ? AND s.timestamp <= ?
            ORDER BY s.timestamp
        """, con, params=(cutoff_s, now_s))
        # OI is hourly; ffill within bound
        oi_df = pd.read_sql("""
            SELECT timestamp, oi_close FROM cd_open_interest
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        """, con, params=(cutoff_s - 3600 * 24, now_s))
        # Funding is 8h; need wider lookback to ffill
        fund_df = pd.read_sql("""
            SELECT timestamp, fr_close FROM cd_funding_rate
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp
        """, con, params=(cutoff_s - 3600 * 8 * 3, now_s))
    finally:
        con.close()

    if df.empty:
        return pd.DataFrame()

    df['ts'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
    df = df.drop(columns='timestamp').set_index('ts')

    oi_df['ts'] = pd.to_datetime(oi_df['timestamp'], unit='s', utc=True)
    oi_series = oi_df.set_index('ts')['oi_close']
    df['oi'] = oi_series.reindex(df.index, method='ffill', limit=4)

    fund_df['ts'] = pd.to_datetime(fund_df['timestamp'], unit='s', utc=True)
    fund_series = fund_df.set_index('ts')['fr_close']
    df['funding'] = fund_series.reindex(df.index, method='ffill', limit=32)

    df['basis'] = df['fut_c'] - df['spot_c']
    df['basis_bp'] = df['basis'] / df['spot_c'] * 10000.0

    df = df.dropna(subset=['spot_c', 'fut_c', 'oi', 'funding'])
    return df


def _load_minute_for_mtf(now: datetime, days_back: int = 400) -> pd.DataFrame:
    """Load btc_1m back days_back days for resampling to higher TFs.
    Returns a DataFrame with tz-aware index and columns o/h/l/c/v.
    Caller resamples; we cache the resamples per day."""
    start_ms = int((now - timedelta(days=days_back)).timestamp() * 1000)
    now_ms = int(now.timestamp() * 1000)
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        df = pd.read_sql("""
            SELECT open_time, open, high, low, close, volume
            FROM btc_1m
            WHERE open_time >= ? AND open_time <= ?
            ORDER BY open_time
        """, con, params=(start_ms, now_ms))
    finally:
        con.close()
    df['ts'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
    df = df.drop(columns='open_time').set_index('ts')
    df.columns = ['o', 'h', 'l', 'c', 'v']
    return df


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 1m OHLCV → higher TF. Empty bars dropped."""
    return df.resample(rule).agg(
        o=('o', 'first'), h=('h', 'max'),
        l=('l', 'min'),   c=('c', 'last'),
        v=('v', 'sum')).dropna()


def _refresh_mtf_bias_cache(now: datetime) -> None:
    """Rebuild the MTF bias cache. Expensive (~500ms) — called once per UTC day."""
    global _mtf_cache_date, _mtf_bias_map
    m1 = _load_minute_for_mtf(now)
    if m1.empty:
        _mtf_cache_date = cli_math.utc_date_of(now)
        _mtf_bias_map = {}
        return

    rules = {'M': '1ME', 'W': '1W', 'D': '1D', 'H4': '4h', 'H1': '1h'}
    bias_map: dict[str, pd.Series] = {}
    for label, rule in rules.items():
        tf_df = _resample_ohlcv(m1, rule)
        cfg = MTF_DEFS[label]
        bias_map[label] = cli_math.compute_tf_bias_series(
            tf_df, period=cfg['period'], slope=cfg['slope'])
    _mtf_bias_map = bias_map
    _mtf_cache_date = cli_math.utc_date_of(now)
    log.info(f"[chento_limit_bid] refreshed MTF bias cache; "
             f"date={_mtf_cache_date}, TFs={list(rules.keys())}")


# ─── Trade close paths ───────────────────────────────────────────────────

def _close_paper(trade_id: str, exit_price: float, reason: str) -> None:
    from strategies.trades import close_perp_trade
    close_perp_trade(trade_id, exit_price, reason, sleeve_name="CHENTO_LIMIT_BID",
                     cost_bp_rt=COST_BP_RT, slippage_bp_rt=SLIPPAGE_BP_RT,
                     apply_funding=True)


def _partial_close_paper(trade_id: str, exit_price: float,
                         close_pct_of_current: float, reason: str) -> bool:
    """Close `close_pct_of_current` of the trade's CURRENT qty via apply_scale.
    Returns True if a SCALE_DOWN was recorded, False otherwise.

    close_pct_of_current is a fraction of the CURRENT (remaining) qty —
    NOT of the original. e.g. 0.50 at T2 closes 50% of the 67% remaining
    after T1, leaving ~33% as the runner.
    """
    from strategies.trades import apply_scale
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT COALESCE(current_qty, qty) AS cur_qty FROM trades "
            "WHERE id=? AND status='open'", (trade_id,)).fetchone()
    finally:
        con.close()
    if row is None or float(row['cur_qty']) <= 0:
        return False
    cur_qty = float(row['cur_qty'])
    slice_qty = cur_qty * close_pct_of_current
    new_qty = cur_qty - slice_qty
    # Per-slice cost on the closed slice notional.
    fee_usdt = slice_qty * exit_price * (COST_BP_RT + SLIPPAGE_BP_RT) / 10000.0
    return apply_scale(trade_id, new_qty=new_qty, price=exit_price,
                       fee_usdt=fee_usdt,
                       notes={"sleeve": "CHENTO_LIMIT_BID", "reason": reason})


def _read_trade_state(notes_blob: str | None) -> dict:
    """Extract the tier state machine sub-dict from the trade's notes JSON."""
    if not notes_blob:
        return {}
    try:
        blob = json.loads(notes_blob)
    except (json.JSONDecodeError, TypeError):
        return {}
    return blob.get('_tier_state', {})


def _write_trade_state(trade_id: str, state: dict) -> None:
    """Persist the tier state machine into the trade's notes JSON."""
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
        blob['_tier_state'] = state
        con.execute("UPDATE trades SET notes=? WHERE id=?",
                    (json.dumps(blob, default=str), trade_id))
        con.commit()
    finally:
        con.close()


def _get_open_trades(variant_id: str) -> list[dict]:
    """Open paper trades for this variant + sleeve."""
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("""
            SELECT id, asset, direction, entry_price, entry_time, notes
            FROM trades
            WHERE strategy_variant = ? AND strategy = 'CHENTO_LIMIT_BID'
              AND status = 'open' AND execution_mode = 'paper'
        """, (variant_id,)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _sweep_open_positions(variant_id: str) -> int:
    """Walk open positions and apply the v2 tier state machine:

      - Tier 1 (1R): partial close 33% of original qty
      - Tier 2 (3R): partial close 50% of remaining (= 33% of original)
      - Runner: trail 5% under high-water mark (armed after T1)
      - Original stop_loss still active throughout
      - Time stop (TIF) closes the runner

    Returns count of FULL-CLOSE events (partials are not counted).
    """
    from strategies.support.price_feed import get_current_price
    trades = _get_open_trades(variant_id)
    if not trades:
        return 0
    price = get_current_price("BTC")
    if price is None:
        return 0
    now = clock.now_utc()
    n_closed = 0
    for tr in trades:
        tid = tr["id"]
        try:
            blob = json.loads(tr.get("notes") or "{}")
        except (json.JSONDecodeError, TypeError):
            blob = {}
        entry = float(blob.get("_entry_price", tr.get("entry_price") or 0.0))
        stop_initial = blob.get("_stop_price")
        ts_iso = blob.get("_time_stop_iso")

        if tr["direction"].upper() != "LONG":
            log.warning(f"[chento_limit_bid {variant_id}] unexpected non-LONG "
                        f"trade {tid}; skipping sweep")
            continue
        if entry <= 0 or stop_initial is None:
            continue
        stop_initial = float(stop_initial)

        # Pull tier state (or initialize)
        state = blob.get("_tier_state") or {
            "t1_done": False, "t2_done": False, "trail_armed": False,
            "high_water": entry, "active_stop": stop_initial,
        }

        # We sweep on 1m ticks — use price as both high and low (point sample).
        # Bar-resolution H/L will be visited on the trigger 15m boundary tick
        # by the same logic.
        result = cli_math.evaluate_tier_transitions(
            state, bar_high=price, bar_low=price,
            entry=entry, stop_initial=stop_initial,
            t1_r=T1_R, t2_r=T2_R, trail_pct=TRAIL_PCT,
        )
        new_state = result["new_state"]
        actions = result["actions"]

        # Apply each action in order
        for act in actions:
            if act["kind"] == "stop_exit":
                reason = ("trail_stop" if new_state.get("trail_armed")
                          and new_state["active_stop"] > stop_initial else "stop_loss")
                _close_paper(tid, act["price"], reason)
                log.info(f"[chento_limit_bid {variant_id}] {reason} @ "
                         f"{act['price']:.2f} (state={new_state})")
                n_closed += 1
                break  # trade is closed, no further actions
            elif act["kind"] == "t1":
                if _partial_close_paper(tid, act["price"], T1_CLOSE_PCT, "t1_partial"):
                    log.info(f"[chento_limit_bid {variant_id}] T1 partial "
                             f"({T1_CLOSE_PCT*100:.0f}% of orig) @ {act['price']:.2f}")
            elif act["kind"] == "t2":
                if _partial_close_paper(tid, act["price"], T2_CLOSE_PCT, "t2_partial"):
                    log.info(f"[chento_limit_bid {variant_id}] T2 partial "
                             f"({T2_CLOSE_PCT*100:.0f}% of remaining) @ {act['price']:.2f}")

        # Persist state if any change occurred (action OR high_water/trail update)
        if (actions or
                new_state.get("high_water") != state.get("high_water") or
                new_state.get("active_stop") != state.get("active_stop")):
            _write_trade_state(tid, new_state)

        # Time-stop the runner if no other action closed the trade
        any_close = any(a["kind"] == "stop_exit" for a in actions)
        if not any_close and ts_iso is not None:
            try:
                ts_dt = datetime.fromisoformat(ts_iso)
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if now >= ts_dt:
                    _close_paper(tid, price, "time_stop")
                    log.info(f"[chento_limit_bid {variant_id}] TIF close "
                             f"@ {price:.2f} (after {TIF_DAYS}d)")
                    n_closed += 1
            except (TypeError, ValueError):
                pass
    return n_closed


# ─── Trigger evaluation ───────────────────────────────────────────────────

def _evaluate_trigger(variant_id: str, now: datetime
                       ) -> tuple[bool, dict]:
    """Return (should_fire, diagnostics). Pulls 15m data, runs all gates."""
    diag: dict = {"now_utc": now.isoformat()}

    # Cooldown
    last = _last_trigger_ts.get(variant_id)
    if last is not None and (now - last) < timedelta(minutes=COOLDOWN_MIN):
        return False, {**diag, "status": "cooldown",
                        "minutes_since_last": (now - last).total_seconds() / 60}

    # Time / day-of-week gate
    if not cli_math.passes_time_gate(now):
        return False, {**diag, "status": "time_gate",
                        "hour": now.hour, "weekday": now.weekday()}

    # MTF bias cache refresh (once per UTC day)
    today_iso = cli_math.utc_date_of(now)
    global _mtf_cache_date
    if _mtf_cache_date != today_iso:
        _refresh_mtf_bias_cache(now)
    if not _mtf_bias_map:
        return False, {**diag, "status": "no_mtf_cache"}

    # Load enriched 15m frame (last ~10 days to cover base + expansion + buffer)
    lookback_hours = BASE_WINDOW_HOURS + 24 * 5 + 12
    f15 = _load_15m_enriched(now, lookback_hours)
    if len(f15) < BASE_WINDOW_HOURS * 4:
        return False, {**diag, "status": "insufficient_15m_data",
                        "n_bars": len(f15)}

    # Detect active base
    now_ts = pd.Timestamp(now).tz_convert('UTC') if now.tzinfo else pd.Timestamp(now, tz='UTC')
    base = cli_math.detect_active_base(f15, now_ts)
    if base is None:
        return False, {**diag, "status": "no_active_base"}

    # Current price (= most recent 15m close as proxy)
    current_price = float(f15['spot_c'].iloc[-1])
    if not cli_math.is_approaching_base(current_price, base['base_low'],
                                         BASE_APPROACH_BAND_PCT):
        return False, {**diag, "status": "not_approaching_base",
                        "current_price": current_price,
                        "base_low": base['base_low']}

    # Score confluence on the base window
    window = f15.loc[base['base_start_ts']: base['base_end_ts']]
    score = cli_math.score_base_window(window)
    if score['conf_score'] < CONF_SCORE_MIN:
        return False, {**diag, "status": "conf_score_too_low",
                        "conf_score": score['conf_score'],
                        "base_low": base['base_low']}

    # MTF bias
    sig, net = cli_math.mtf_signature_at(now_ts, _mtf_bias_map)
    if not cli_math.passes_mtf_gate(sig, net):
        return False, {**diag, "status": "mtf_gate_failed",
                        "mtf_sig": sig, "mtf_net": net}

    return True, {**diag, "status": "FIRE",
                   "base_low": base['base_low'],
                   "base_start_ts": base['base_start_ts'].isoformat(),
                   "base_end_ts": base['base_end_ts'].isoformat(),
                   "confirm_ts": base['confirm_ts'].isoformat(),
                   "current_price": current_price,
                   "conf_score": score['conf_score'],
                   "basis_bp_mean": score['basis_bp_mean'],
                   "funding_mean": score['funding_mean'],
                   "oi_drawdown_pct": score['oi_drawdown_pct'],
                   "spot_cvd_sum": score['spot_cvd_sum'],
                   "mtf_sig": sig, "mtf_net": net}


# ─── Orchestrator interface ──────────────────────────────────────────────

def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Two-phase dispatch entry point.

    Side effects (always run):
      - Sweep open positions for stop / target / time-stop hits.

    Returns ``(list[Intent], status_dict)``. Emits at most one Intent
    per call.
    """
    variant_id = variant["id"]
    swept = _sweep_open_positions(variant_id)

    now = clock.now_utc()
    if not cli_math.is_15m_boundary(now):
        return [], {"status": "not_15m_boundary", "swept": swept}

    # Don't stack a new long on top of an existing one for this variant.
    if _get_open_trades(variant_id):
        return [], {"status": "position_open", "swept": swept}

    fires, diag = _evaluate_trigger(variant_id, now)
    if not fires:
        return [], {**diag, "swept": swept}

    base_low = float(diag["base_low"])
    entry_price = float(diag["current_price"])
    stop_price = base_low * (1 - STOP_OFFSET_PCT)
    risk = entry_price - stop_price
    if risk <= 0:
        return [], {"status": "invalid_risk", "swept": swept,
                    "entry_price": entry_price, "stop_price": stop_price}
    # v2: targets computed dynamically by the sweep state machine from
    # entry + stop_initial. Recorded here for diagnostics only.
    t1_price = entry_price + T1_R * risk
    t2_price = entry_price + T2_R * risk
    time_stop_dt = now + timedelta(days=TIF_DAYS)

    alloc_pct = float(sleeve_cfg.get("_effective_weight_pct",
                                       sleeve_cfg.get("weight_pct", 0.0)))
    leverage = float(sleeve_cfg.get("_effective_leverage", 1.0))

    reason = {
        "trigger": "chento_limit_bid",
        "variant_id": variant_id,
        "sleeve": "CHENTO_LIMIT_BID",
        "base_low": base_low,
        "base_start_ts_utc": diag["base_start_ts"],
        "base_end_ts_utc": diag["base_end_ts"],
        "confirm_ts_utc": diag["confirm_ts"],
        "conf_score": diag["conf_score"],
        "basis_bp_mean": diag["basis_bp_mean"],
        "funding_mean": diag["funding_mean"],
        "oi_drawdown_pct": diag["oi_drawdown_pct"],
        "spot_cvd_sum": diag["spot_cvd_sum"],
        "mtf_sig": diag["mtf_sig"],
        "mtf_net": diag["mtf_net"],
        "_stop_price": stop_price,
        "_t1_price": t1_price,    # diagnostic — actual exit logic in sweep
        "_t2_price": t2_price,    # diagnostic
        "_time_stop_iso": time_stop_dt.isoformat(),
        "_entry_price": entry_price,
        "_tier_state": {           # initialize tier state machine
            "t1_done": False, "t2_done": False, "trail_armed": False,
            "high_water": entry_price, "active_stop": stop_price,
        },
    }
    intent = Intent(
        asset="BTC", direction="LONG",
        allocation_pct=alloc_pct, leverage=leverage,
        conviction=100,
        priority=float(sleeve_cfg.get("priority", 100)),
        reason=reason, scheduled_exit_dt=time_stop_dt,
    )
    return [intent], {"status": "decided", "swept": swept,
                       "base_low": base_low,
                       "conf_score": diag["conf_score"],
                       "mtf_sig": diag["mtf_sig"],
                       "mtf_net": diag["mtf_net"]}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent: Intent) -> dict:
    """Phase 2: open the LONG described by `intent`."""
    from strategies.trades import open_paper_trade
    reason = dict(intent.reason or {})
    entry_price = float(reason.pop("_entry_price"))
    tid = open_paper_trade(
        variant=variant, sleeve_name="CHENTO_LIMIT_BID",
        asset=intent.asset, direction="LONG",
        entry_price=entry_price,
        allocation_pct=intent.allocation_pct, leverage=intent.leverage,
        reason=reason,
        scheduled_exit_dt=intent.scheduled_exit_dt,
        regime_value="chento_limit_bid",
    )
    _last_trigger_ts[variant["id"]] = clock.now_utc()
    log.info(f"[chento_limit_bid {variant['id']}] opened {tid} BTC LONG @ "
             f"{entry_price:.2f}  stop={reason['_stop_price']:.2f}  "
             f"t1={reason['_t1_price']:.2f}  t2={reason['_t2_price']:.2f}  "
             f"sig={reason['mtf_sig']} net={reason['mtf_net']:+d} "
             f"conf={reason['conf_score']}  "
             f"alloc={intent.allocation_pct}%  k={intent.leverage}x")
    return {"status": "opened", "trade_id": tid, "entry_price": entry_price,
            "stop_price": reason["_stop_price"],
            "t1_price": reason["_t1_price"],
            "t2_price": reason["_t2_price"]}


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Single-call entry point for legacy orchestrator paths."""
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    return execute_for_variant(variant, sleeve_cfg, intents[0])
