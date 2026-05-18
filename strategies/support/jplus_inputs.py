"""J+ live decision inputs — regime, vol-target leverage, R4 gate, EMA
position, and per-regime sub-sleeve weights for "today".

Public entrypoint: ``today_inputs()``. Called by the live entry handlers
in ``strategies/sleeves/{r4,ema,eth_daily}/signal.py`` to size positions
at trade-open time, using only data available through yesterday's close.

The heavy lifting is in ``_run_decision_loop()``, which walks the full
historical series (BTC daily/hourly, ETH daily/hourly, LS ratio) and
produces both today's inputs AND the per-day series consumed by
``studies/jplus_analytic/simulate.py`` (the analytic backtest that
re-uses this engine for offline research).

Look-ahead safety: every input for date T is derived strictly from data
available at yesterday's UTC close (regime, EMA cross, R4 gate, vol-
target all use T-1 windows by construction).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from strategies.support import clock
from data import loaders as data
from strategies.support import gate, voltarget
from strategies.support import regime_jplus as regime
from strategies.sleeves.timing_anomalies.internal.r4 import math as r4
from strategies.sleeves.ema import math as ema_sleeve


R4_EXTRA_LEV_UNGATED = 2.5
R4_EXTRA_LEV_GATED = 1.0

# Full per-regime sub-sleeve weights — single source of truth used by both
# the simulator loop and ``today_inputs()``. Mirrors the inline allocation
# at the per-day decision step.
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

CORE_ALLOC_CAP = 0.5
# Maximum combined Core sub-sleeve pre-leverage allocation as a fraction of
# variant capital. Raw REGIME_WEIGHTS_FULL sums per regime are 1.10 / 1.15 /
# 1.35 / 0.30 (strong_bull / mild_bull / uncertain / bear); the cap enforces
# the documented `core_pct: 50.0` intent that was previously decorative in
# register_p300.py:allocator_notes. Added 2026-05-12 after the live bot was
# discovered emitting R4_ETH trades at 200% of variant capital (uncertain
# regime, raw weight 0.40 × 5x stacked leverage = $100k gross on $10k cap).


def _cap_core_weights(raw: dict[str, float]) -> dict[str, float]:
    """Return ``raw`` rescaled so its values sum to at most
    ``CORE_ALLOC_CAP``. If the input already sums to ≤ cap (e.g. bear
    regime), it is returned unchanged. Otherwise each weight is scaled by
    ``CORE_ALLOC_CAP / sum(raw)`` — preserves relative weighting between
    sub-sleeves while bounding total Core gross."""
    total = sum(raw.values())
    if total <= CORE_ALLOC_CAP or total <= 0:
        return dict(raw)
    scale = CORE_ALLOC_CAP / total
    return {k: v * scale for k, v in raw.items()}


def _gate_for_today(bc: list[float]) -> bool:
    """Compute today's R4 gate value from BTC closes through yesterday.

    Mirrors gate.compute_gate_map's per-date logic at index ``len(bc)``
    where ``prev_i = len(bc) - 1`` is yesterday. Returns True if the bot
    should de-lever R4 today (high-vol regime by the same trailing-30d-vs-
    365d-75th-percentile rule)."""
    import math
    from strategies.support.gate import (VOL_WINDOW, VOL_RANK_WINDOW, VOL_RANK_THRESHOLD,
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
        # Wed+Fri at 04→14 — see strategies/sleeves/timing_anomalies/internal/r4/math.py and studies/notebooks/r4_study/.
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

        # Per-regime allocation, capped at CORE_ALLOC_CAP (50% of capital).
        # Raw sums per regime: strong_bull 1.10, mild_bull 1.15, uncertain
        # 1.35, bear 0.30. _cap_core_weights rescales the first three down
        # to 0.50 and leaves bear unchanged.
        weights = _cap_core_weights(REGIME_WEIGHTS_FULL.get(mode, {
            "ema_btc": 0.0, "eth_daily": 0.0,
            "r4_btc": 0.0, "r4_eth": 0.0,
            "r4_btc_v2": 0.0, "r4_eth_v2": 0.0,
        }))
        # R4_ETH live entry is Tue 20:00 UTC, BEFORE Tue daily close
        # (00:00 UTC). At Tue 20:00, today_inputs()'s latest complete
        # daily close is Monday — so the live bot weights R4_ETH by
        # Tuesday's regime, which the simulator computed at iteration
        # i-1 using det_i = i-2 = Monday's close. Using `weights` here
        # (Wed regime, based on Tue close) would inject ~4h of
        # post-entry data into the analytic series. See AUDIT_2026_05_13
        # "High — methodology / look-ahead" item 1. Other sub-sleeves
        # at Wed key (R4_BTC_V2 / R4_ETH_V2 at 04:00 UTC) DO have Tue
        # close available at entry, so they keep the Wed weights.
        prev_rec = out.get(prev_d) if is_r4_e else None
        if prev_rec is not None:
            r4_eth_weights = _cap_core_weights(REGIME_WEIGHTS_FULL.get(
                prev_rec.get("mode"), {
                    "ema_btc": 0.0, "eth_daily": 0.0,
                    "r4_btc": 0.0, "r4_eth": 0.0,
                    "r4_btc_v2": 0.0, "r4_eth_v2": 0.0,
                }))
        else:
            r4_eth_weights = weights

        c_ema = weights["ema_btc"] * ema_p * br
        c_eth = weights["eth_daily"] * er
        c_r4e = r4_eth_weights["r4_eth"] * r4e_r
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


# Per-UTC-date cache for today_inputs(). The result depends only on the
# current UTC date — the underlying _run_decision_loop walks ~2000+ days
# of history each call (~1-2s in live, much more in sim where it's the
# inner loop of the 6 J+ sleeves x 17500 ticks of a 2-year backtest).
# Cache hits make sim backtests tractable (152s/tick → sub-second);
# live ticks save ~95% CPU since the same date repeats for 1440 min.
# Invalidates automatically on UTC date rollover.
_TODAY_INPUTS_CACHE: tuple[str, dict | None] | None = None


def _invalidate_today_inputs_cache() -> None:
    """Reset the today_inputs() cache. Useful for tests that monkeypatch
    the clock and want a clean read."""
    global _TODAY_INPUTS_CACHE
    _TODAY_INPUTS_CACHE = None


def today_inputs() -> dict | None:
    """Decision inputs (regime mode, vol-target leverage, R4 gate, EMA
    position, sub-sleeve weights) for the CURRENT UTC date, derived from
    data through yesterday's close. The live entry handlers in
    ``strategies/sleeves/{r4,ema,eth_daily}/signal.py`` call this at trade-open time to size their
    positions without waiting for today's daily close.

    Returns ``None`` if there isn't enough warmup data to classify regime
    or compute vol-target — defensive guard for a bot booting on a cold DB.

    The ``ema_p_prev`` / ``weights_prev`` / ``mode_prev`` fields carry
    yesterday's signal so callers can detect *fresh transitions* (EMA
    crosses, regime entries) and avoid cold-start fills mid-signal.

    Look-ahead safety: every input here is derived strictly from data
    available at yesterday's UTC close (regime/EMA cross/gate/vol-target
    all use T-1 windows by construction). Calling at any time today
    returns the same answer until midnight UTC tomorrow.

    Performance: the result is cached by UTC date — the first call of a
    new UTC day pays the full ``_run_decision_loop`` walk (~1-2s), every
    subsequent call until midnight returns the cached dict in O(1).
    Sim mode (fast-advancing fake clock) and live mode (1440 calls/day)
    both benefit. ``_invalidate_today_inputs_cache()`` flushes for tests."""
    global _TODAY_INPUTS_CACHE
    today_iso = clock.now_utc().date().isoformat()
    if _TODAY_INPUTS_CACHE is not None and _TODAY_INPUTS_CACHE[0] == today_iso:
        return _TODAY_INPUTS_CACHE[1]

    out, state = _run_decision_loop()
    dates = state["dates"]
    if len(dates) < 60:  # need warmup for regime + vol-target
        _TODAY_INPUTS_CACHE = (today_iso, None)
        return None

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
    weights = _cap_core_weights(REGIME_WEIGHTS_FULL.get(
        mode, {"ema_btc": 0.0, "eth_daily": 0.0, "r4_btc": 0.0, "r4_eth": 0.0,
                "r4_btc_v2": 0.0, "r4_eth_v2": 0.0}))
    yest_rec = out.get(yesterday_iso) or {}
    mode_prev = yest_rec.get("mode")
    weights_prev = _cap_core_weights(REGIME_WEIGHTS_FULL.get(
        mode_prev, {"ema_btc": 0.0, "eth_daily": 0.0, "r4_btc": 0.0, "r4_eth": 0.0,
                     "r4_btc_v2": 0.0, "r4_eth_v2": 0.0}))

    result = {
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
    _TODAY_INPUTS_CACHE = (today_iso, result)
    return result
