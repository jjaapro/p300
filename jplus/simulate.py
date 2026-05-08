"""Daily simulator — composes regime, R4, EMA sleeve, vol-target, and the
rule-based R4 gate into a daily return series for the Core J+ portfolio.

Ported from upstream `validate_s100_vt50_jplus.simulate_full_jplus` with:
  - ML gate replaced by rule-based gate (`jplus.gate`) using T-1 data only.
  - GOLD overlay dropped; crypto-side return is taken at full weight.
  - Data loaders all clock-bounded via `services.clock`.

Produces: {date_iso: daily_return_pct} — where return is NET (includes the
regime-allocation weighting, EMA sleeve contribution, R4 intraday windows
gated by the volatility rule, per-day leverage from the vol target).

Pre-leverage (1x) returns are tracked for the vol-target feedback loop.

Look-ahead safety: all decision inputs for date T (regime, leverage, gate,
EMA position) are derived from data available strictly through T-1.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services import clock
from jplus import data, ema_sleeve, gate, r4, regime, voltarget


R4_EXTRA_LEV_UNGATED = 2.5
R4_EXTRA_LEV_GATED = 1.0

# Full per-regime sub-sleeve weights — single source of truth used by both
# the simulator loop and ``today_inputs()``. Mirrors the inline allocation
# at the per-day decision step. Values match
# services.jplus_trade_emitter.REGIME_WEIGHTS by construction; the parity
# tests in tests/test_jplus_trade_emitter.py catch any drift.
REGIME_WEIGHTS_FULL = {
    "strong_bull": {"ema_btc": 0.50, "eth_daily": 0.20,
                     "r4_btc": 0.15, "r4_eth": 0.15,
                     "r4_btc_v2": 0.075, "r4_eth_v2": 0.075},
    "mild_bull":   {"ema_btc": 0.30, "eth_daily": 0.10,
                     "r4_btc": 0.20, "r4_eth": 0.30,
                     "r4_btc_v2": 0.10, "r4_eth_v2": 0.15},
    "uncertain":   {"ema_btc": 0.30, "eth_daily": 0.00,
                     "r4_btc": 0.30, "r4_eth": 0.40,
                     "r4_btc_v2": 0.15, "r4_eth_v2": 0.20},
    "bear":        {"ema_btc": 0.30, "eth_daily": 0.00,
                     "r4_btc": 0.00, "r4_eth": 0.00,
                     "r4_btc_v2": 0.00, "r4_eth_v2": 0.00},
}


def _gate_for_today(bc: list[float]) -> bool:
    """Compute today's R4 gate value from BTC closes through yesterday.

    Mirrors gate.compute_gate_map's per-date logic at index ``len(bc)``
    where ``prev_i = len(bc) - 1`` is yesterday. Returns True if the bot
    should de-lever R4 today (high-vol regime by the same trailing-30d-vs-
    365d-75th-percentile rule)."""
    import math
    from jplus.gate import (VOL_WINDOW, VOL_RANK_WINDOW, VOL_RANK_THRESHOLD,
                              _rolling_vol_annualized)
    if len(bc) < VOL_WINDOW + 1:
        return False
    rets = [0.0]
    for i in range(1, len(bc)):
        if bc[i - 1] > 0 and bc[i] > 0:
            rets.append(math.log(bc[i] / bc[i - 1]))
        else:
            rets.append(0.0)
    vol_series = _rolling_vol_annualized(rets, VOL_WINDOW)
    prev_i = len(bc) - 1
    if prev_i < VOL_WINDOW - 1:
        return False
    cur_vol = vol_series[prev_i]
    if math.isnan(cur_vol):
        return False
    rank_start = max(VOL_WINDOW - 1, prev_i - VOL_RANK_WINDOW + 1)
    hist = [v for v in vol_series[rank_start:prev_i + 1]
            if not math.isnan(v)]
    if len(hist) < 30:
        return False
    hist_sorted = sorted(hist)
    threshold = hist_sorted[int(len(hist_sorted) * VOL_RANK_THRESHOLD)]
    return cur_vol >= threshold


def _run_decision_loop() -> tuple[dict[str, dict], dict]:
    """Walk every available historical date once, computing the same per-day
    decisions ``simulate()`` did. Returns ``(out, final_state)`` where
    ``out`` is the daily-return map (keyed by ISO date) and ``final_state``
    captures the running state at end-of-loop for ``today_inputs()`` to
    project one more step into "today".

    final_state keys:
      - ``recent_1x``: list of all 1x daily returns (in percent), oldest-first
      - ``spot_peak``, ``cb_until``: regime classifier scratch
      - ``dates``, ``bc``, ``e20``, ``e50``, ``ls_d``, ``ema_pos``: shared
        inputs reused for the today-projection step
    """
    btc_d = data.load_btc_daily()
    eth_d = data.load_eth_daily()
    ls_d = data.load_ls_ratio_btc()
    hourly = data.load_btc_hourly()
    btc_h = data.btc_hourly_by_bucket()
    eth_h = data.load_eth_hourly()

    r4b_map = r4.r4_btc_returns(btc_h)
    r4e_map = r4.r4_eth_returns(eth_h)
    r4b_v2_map = r4.r4_btc_v2_returns(btc_h)
    r4e_v2_map = r4.r4_eth_v2_returns(eth_h)
    ema_pos = ema_sleeve.compute_ema_position_map(hourly)

    dates = sorted(set(btc_d.keys()))  # BTC-daily is the primary calendar
    bc = [btc_d[d]["c"] for d in dates]
    e20 = regime.ema_calc(bc, 20)
    e50 = regime.ema_calc(bc, 50)

    gate_map = gate.compute_gate_map(dates, bc)

    out: dict[str, dict] = {}
    recent_1x: list[float] = []
    spot_peak = max(bc[:51]) if len(bc) > 50 else (max(bc) if bc else 0.0)
    cb_until = ""

    for i in range(1, len(dates)):
        d = dates[i]
        prev_d = dates[i - 1]
        if prev_d not in btc_d or d not in btc_d:
            continue

        # Close-to-close daily returns (used by the EMA_BTC sleeve and the
        # implicit ETH continuous sleeve 'er').
        br = (btc_d[d]["c"] - btc_d[prev_d]["c"]) / btc_d[prev_d]["c"]
        er = 0.0
        if prev_d in eth_d and d in eth_d:
            er = (eth_d[d]["c"] - eth_d[prev_d]["c"]) / eth_d[prev_d]["c"]

        # Regime for today, using strictly T-1 data.
        det_i = max(1, i - 1)
        mode, spot_peak, cb_until = regime.classify_day(
            det_i, bc, e20, e50, ls_d, dates, spot_peak, cb_until, d,
        )

        # EMA position (+1/-1/0).
        ema_p = ema_pos.get(d, 0)

        # R4 flags and per-trade returns (intraday windows).
        # V1 R4_BTC: Mon-only since 2026-05-08 (was Mon+Wed). V2 captures
        # Wed+Fri at 04→14 — see jplus/r4.py and tools/r4_study/.
        dt = btc_d[d]["dt"]
        is_r4_b = dt.weekday() == 0 and dt.day <= 14
        is_r4_e = dt.weekday() == 2 and dt.day <= 14
        is_r4_b_v2 = dt.weekday() in (2, 4) and dt.day <= 14
        is_r4_e_v2 = dt.weekday() in (2, 4) and dt.day <= 14
        r4b_r = r4b_map.get(d, 0.0) if is_r4_b else 0.0
        r4e_r = r4e_map.get(d, 0.0) if is_r4_e else 0.0
        r4b_v2_r = r4b_v2_map.get(d, 0.0) if is_r4_b_v2 else 0.0
        r4e_v2_r = r4e_v2_map.get(d, 0.0) if is_r4_e_v2 else 0.0

        # Rule-based R4 gate (T-1 vol-percentile rule).
        # NOTE: R4 leverage stacks with vol-target: max effective on R4 sub-sleeve
        # is R4_EXTRA_LEV_UNGATED × H_CAPS["strong_bull"] = 2.5 × 3.0 = 7.5x.
        # V2 sleeves use the same gate / inner-leverage as V1 — the gate
        # rule is volatility-regime-based, not sleeve-specific.
        gate_fired = gate_map.get(d, False)
        r4_lev = R4_EXTRA_LEV_GATED if gate_fired else R4_EXTRA_LEV_UNGATED
        r4b_r *= r4_lev
        r4e_r *= r4_lev
        r4b_v2_r *= r4_lev
        r4e_v2_r *= r4_lev

        # Per-regime allocation. Weights intentionally sum < 1.0 in risk-off
        # regimes (remainder is idle cash):
        #   strong_bull: 1.00 (fully invested)
        #   mild_bull:   0.90 (10% cash buffer)
        #   uncertain:   1.00 (fully invested in mean-reversion sleeves)
        #   bear:        0.30 (70% cash — defensive posture)
        weights = REGIME_WEIGHTS_FULL.get(mode, {
            "ema_btc": 0.0, "eth_daily": 0.0,
            "r4_btc": 0.0, "r4_eth": 0.0,
            "r4_btc_v2": 0.0, "r4_eth_v2": 0.0,
        })
        c_ema = weights["ema_btc"] * ema_p * br
        c_eth = weights["eth_daily"] * er
        c_r4e = weights["r4_eth"] * r4e_r
        c_r4b = weights["r4_btc"] * r4b_r
        c_r4b_v2 = weights.get("r4_btc_v2", 0.0) * r4b_v2_r
        c_r4e_v2 = weights.get("r4_eth_v2", 0.0) * r4e_v2_r
        rl = c_ema + c_eth + c_r4e + c_r4b + c_r4b_v2 + c_r4e_v2

        # Vol-target cap using prior-day recent_1x history.
        lev = voltarget.leverage_for_day(recent_1x, mode)

        r_strat = rl * 100 * lev  # percent
        recent_1x.append(rl * 100)

        # No GOLD overlay. r_total = r_strat.
        r_total = r_strat

        out[d] = {
            "return_pct": r_total,
            "mode": mode,
            "lev": lev,
            "r1x_pct": rl * 100,
            "gated": gate_fired,
            "ema_p": ema_p,
            # Per-sub-sleeve attribution (1x contributions, percent — pre-vol-target).
            # Sum equals r1x_pct. Multiply by `lev` to get final daily-return
            # contribution for that sub-sleeve.
            "ema_contrib_1x_pct": c_ema * 100,
            "eth_daily_contrib_1x_pct": c_eth * 100,
            "r4_btc_contrib_1x_pct": c_r4b * 100,
            "r4_eth_contrib_1x_pct": c_r4e * 100,
            "r4_btc_v2_contrib_1x_pct": c_r4b_v2 * 100,
            "r4_eth_v2_contrib_1x_pct": c_r4e_v2 * 100,
            # Whether each calendar-driven sleeve actually fired today.
            "r4_btc_fired": is_r4_b,
            "r4_eth_fired": is_r4_e,
            "r4_btc_v2_fired": is_r4_b_v2,
            "r4_eth_v2_fired": is_r4_e_v2,
            # Underlying 1x daily returns of the asset legs (for sanity-check).
            "btc_daily_pct": br * 100,
            "eth_daily_pct": er * 100,
            "r4_btc_pct": r4b_r * 100,
            "r4_eth_pct": r4e_r * 100,
            "r4_btc_v2_pct": r4b_v2_r * 100,
            "r4_eth_v2_pct": r4e_v2_r * 100,
        }

    final_state = {
        "recent_1x": recent_1x,
        "spot_peak": spot_peak,
        "cb_until": cb_until,
        "dates": dates,
        "bc": bc,
        "e20": e20,
        "e50": e50,
        "ls_d": ls_d,
        "ema_pos": ema_pos,
    }
    return out, final_state


def simulate(start_date: str | None = None, end_date: str | None = None) -> dict[str, dict]:
    """Run the J+ regime-gate simulator over the available history.

    Args:
      start_date: ISO string; only dates ≥ this are emitted. Default: no floor.
      end_date:   ISO string; only dates ≤ this are emitted. Default: no ceiling
                  (uses data up to the current clock).

    Returns:
      {date_iso: {
          "return_pct": float,   net daily return in percent
          "mode": str,           regime classification
          "lev": float,          vol-target leverage applied today
          "r1x_pct": float,      pre-leverage 1x return in percent
          "gated": bool,         whether the rule-based gate fired today
          "ema_p": int,          EMA sleeve position (+1/-1/0)
      }}
    """
    out, _state = _run_decision_loop()

    # Cap at strictly BEFORE the clock's UTC date. A daily close on the clock
    # date itself would mix fully-formed bars from before the clock with the
    # single hourly bar that sits AT the clock, giving a non-reproducible
    # "partial daily close." Excluding the clock date keeps the return series
    # look-ahead-safe AND deterministic across clock positions.
    clock_date = clock.now_utc().date().isoformat()
    out = {k: v for k, v in out.items() if k < clock_date}

    # Apply optional date filters.
    if start_date or end_date:
        keys = sorted(out.keys())
        if start_date:
            keys = [k for k in keys if k >= start_date]
        if end_date:
            keys = [k for k in keys if k <= end_date]
        out = {k: out[k] for k in keys}
    return out


def today_inputs() -> dict | None:
    """Decision inputs (regime mode, vol-target leverage, R4 gate, EMA
    position, sub-sleeve weights) for the CURRENT UTC date, derived from
    data through yesterday's close. The live entry handlers in
    ``services/jplus_live.py`` call this at trade-open time to size their
    positions without waiting for today's daily close.

    Returns ``None`` if there isn't enough warmup data to classify regime
    or compute vol-target — defensive guard for a bot booting on a cold DB.

    The ``ema_p_prev`` / ``weights_prev`` / ``mode_prev`` fields carry
    yesterday's signal so callers can detect *fresh transitions* (EMA
    crosses, regime entries) and avoid cold-start fills mid-signal — see
    the cold-start guards in ``services/jplus_trade_emitter.py``.

    Look-ahead safety: every input here is derived strictly from data
    available at yesterday's UTC close (regime/EMA cross/gate/vol-target
    all use T-1 windows by construction). Calling at any time today
    returns the same answer until midnight UTC tomorrow."""
    out, state = _run_decision_loop()
    dates = state["dates"]
    if len(dates) < 60:  # need warmup for regime + vol-target
        return None

    today_iso = clock.now_utc().date().isoformat()
    yesterday_iso = (clock.now_utc().date() - timedelta(days=1)).isoformat()

    # det_i = index of yesterday in `dates` = len(dates) - 1. The simulator
    # uses ``max(1, i - 1)`` for in-loop iterations; for "today" we project
    # one more step, so det_i is the last historical index.
    det_i = len(dates) - 1
    mode, _sp, _cb = regime.classify_day(
        det_i, state["bc"], state["e20"], state["e50"], state["ls_d"],
        state["dates"], state["spot_peak"], state["cb_until"], today_iso,
    )

    # EMA position for today: ema_pos is keyed by date and the position-map
    # builder spreads through the last hourly bar's calendar date, which
    # includes today if any 1m bar has been observed today.
    ema_p = int(state["ema_pos"].get(today_iso, 0))
    ema_p_prev = int(state["ema_pos"].get(yesterday_iso, 0))

    # R4 gate: today's gate uses bc through yesterday (= state["bc"]).
    gated = _gate_for_today(state["bc"])

    # Vol-target leverage: recent_1x is "all 1x returns through yesterday"
    # which is exactly what voltarget.leverage_for_day expects.
    lev = voltarget.leverage_for_day(state["recent_1x"], mode)

    # Sub-sleeve weights from regime, both today's and yesterday's. Yesterday's
    # mode is read from the per-day decision loop output ``out``; if yesterday
    # is before warmup or otherwise missing, mode_prev is None and weights_prev
    # is all-zero (treated by callers as "no signal yet" — same as no entry).
    weights = dict(REGIME_WEIGHTS_FULL.get(
        mode, {"ema_btc": 0.0, "eth_daily": 0.0, "r4_btc": 0.0, "r4_eth": 0.0,
                "r4_btc_v2": 0.0, "r4_eth_v2": 0.0}))
    yest_rec = out.get(yesterday_iso) or {}
    mode_prev = yest_rec.get("mode")
    weights_prev = dict(REGIME_WEIGHTS_FULL.get(
        mode_prev, {"ema_btc": 0.0, "eth_daily": 0.0, "r4_btc": 0.0, "r4_eth": 0.0,
                     "r4_btc_v2": 0.0, "r4_eth_v2": 0.0}))

    return {
        "date": today_iso,
        "mode": mode,
        "mode_prev": mode_prev,
        "lev": lev,
        "gated": gated,
        "ema_p": ema_p,
        "ema_p_prev": ema_p_prev,
        "weights": weights,
        "weights_prev": weights_prev,
    }


# Per-regime sub-sleeve weights — kept here for the fee helper. Mirrors the
# inline allocation in the loop above (lines 108-131); single source of truth
# would be nicer but the loop's hot-path doesn't want a dict lookup. The
# parity test in tests/test_jplus_trade_emitter.py catches drift if these
# get out of sync with the actual loop logic.
_REGIME_R4_WEIGHTS = {
    # (r4_btc, r4_eth, r4_btc_v2, r4_eth_v2)
    "strong_bull": (0.15, 0.15, 0.075, 0.075),
    "mild_bull":   (0.20, 0.30, 0.10,  0.15),
    "uncertain":   (0.30, 0.40, 0.15,  0.20),
    "bear":        (0.00, 0.00, 0.00,  0.00),
}


def apply_r4_fees(series: dict[str, dict], fee_bp_rt: float = 10.0) -> None:
    """Subtract R4 round-trip fees from each day's ``return_pct``, in place.

    Pre-Step-5 of the trade-emitter migration, ``jplus/r4.py`` deducted a
    10bp round-trip fee from R4 window returns BEFORE the simulator
    weighted them into the daily contribution. Step 5 zeroed that
    deduction so live trades own fee accounting via trade-adjustment
    events. Backtest-replay paths that consume ``return_pct`` directly
    (notably ``tools/combine_replay.py``) need to re-apply the same fee
    model to remain comparable to historical backtests.

    The fee in % of capital terms for one R4 fire =
        regime_weight × r4_inner_lev × vol_target_lev × (fee_bp/10000) × 100

    where ``r4_inner_lev`` is 1.0 if the gate fired today, else 2.5.

    V2 sleeves are charged the same fee model with their own per-regime
    weights — see ``_REGIME_R4_WEIGHTS`` above.

    The live path (``services/jplus_service.py``) does NOT call this —
    it derives fees from the trade-event ledger, which is the canonical
    P&L source under Path B.
    """
    fee_frac = fee_bp_rt / 10000.0
    for rec in series.values():
        weights = _REGIME_R4_WEIGHTS.get(rec.get("mode", ""),
                                          (0.0, 0.0, 0.0, 0.0))
        gated = bool(rec.get("gated", False))
        r4_lev = 1.0 if gated else 2.5
        lev = float(rec.get("lev", 1.0))
        fee_pct = 0.0
        if rec.get("r4_btc_fired"):
            fee_pct += weights[0] * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_eth_fired"):
            fee_pct += weights[1] * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_btc_v2_fired"):
            fee_pct += weights[2] * r4_lev * lev * fee_frac * 100.0
        if rec.get("r4_eth_v2_fired"):
            fee_pct += weights[3] * r4_lev * lev * fee_frac * 100.0
        rec["return_pct"] = float(rec["return_pct"]) - fee_pct
        rec["r4_fees_pct"] = fee_pct
