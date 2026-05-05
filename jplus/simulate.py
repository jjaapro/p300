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

from datetime import datetime, timezone

from services import clock
from jplus import data, ema_sleeve, gate, r4, regime, voltarget


R4_EXTRA_LEV_UNGATED = 2.5
R4_EXTRA_LEV_GATED = 1.0


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
    btc_d = data.load_btc_daily()
    eth_d = data.load_eth_daily()
    ls_d = data.load_ls_ratio_btc()
    hourly = data.load_btc_hourly()
    btc_h = data.btc_hourly_by_bucket()
    eth_h = data.load_eth_hourly()

    r4b_map = r4.r4_btc_returns(btc_h)
    r4e_map = r4.r4_eth_returns(eth_h)
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
        dt = btc_d[d]["dt"]
        is_r4_b = dt.weekday() in (0, 2) and dt.day <= 14
        is_r4_e = dt.weekday() == 2 and dt.day <= 14
        r4b_r = r4b_map.get(d, 0.0) if is_r4_b else 0.0
        r4e_r = r4e_map.get(d, 0.0) if is_r4_e else 0.0

        # Rule-based R4 gate (T-1 vol-percentile rule).
        gate_fired = gate_map.get(d, False)
        r4_lev = R4_EXTRA_LEV_GATED if gate_fired else R4_EXTRA_LEV_UNGATED
        r4b_r *= r4_lev
        r4e_r *= r4_lev

        # Per-regime allocation (same weights as upstream simulate_full_jplus,
        # but with GOLD dropped — the crypto side now stands alone at 1.0).
        # Also track per-sub-sleeve contribution to the daily 1x return for
        # downstream attribution logging. Each `_c_*` term is the contribution
        # of that sleeve to `rl` at this regime's weights.
        c_ema = c_eth = c_r4b = c_r4e = 0.0
        if mode == "strong_bull":
            c_ema = 0.50 * ema_p * br
            c_eth = 0.20 * er
            c_r4e = 0.15 * r4e_r
            c_r4b = 0.15 * r4b_r
        elif mode == "mild_bull":
            c_ema = 0.30 * ema_p * br
            c_eth = 0.10 * er
            c_r4e = 0.30 * r4e_r
            c_r4b = 0.20 * r4b_r
        elif mode == "uncertain":
            c_r4e = 0.40 * r4e_r
            c_r4b = 0.30 * r4b_r
            c_ema = 0.30 * ema_p * br
        elif mode == "bear":
            c_ema = 0.30 * ema_p * br
        # (undefined mode → all zero; defensive)
        rl = c_ema + c_eth + c_r4e + c_r4b

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
            # Whether each calendar-driven sleeve actually fired today.
            "r4_btc_fired": is_r4_b,
            "r4_eth_fired": is_r4_e,
            # Underlying 1x daily returns of the asset legs (for sanity-check).
            "btc_daily_pct": br * 100,
            "eth_daily_pct": er * 100,
            "r4_btc_pct": r4b_r * 100,
            "r4_eth_pct": r4e_r * 100,
        }

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
