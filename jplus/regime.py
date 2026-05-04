"""4-state regime classifier — direct port from upstream's
validate_s100_vt50_jplus.simulate_full_jplus regime logic.

Inputs at date T: the classifier uses ONLY data available as of T-1:
  - BC[T-1]: BTC daily close at T-1
  - EMA20[T-1], EMA50[T-1]: computed from daily closes ≤ T-1
  - m30[T-1], m7[T-1]: 30- and 7-day momentum, using closes ≤ T-1
  - LS[T-1], LS[T-8]: long-short ratio snapshots
  - Spot-peak drawdown: max(BC[:T-1]) → BC[T-1]

Look-ahead safety: everything indexed at det_i = i-1 where i is the index
of the "today" T. Nothing touches BC[T] or anything later.

States:
  strong_bull  close > EMA50 AND close > EMA20 AND m30 > 0 AND m7 > 0
  mild_bull    close > EMA50 AND (m30 > 0 OR close > EMA20)
  bear         close < EMA50 AND m30 < 0
  uncertain    otherwise  (OR peak-DD > 5% while bullish, OR LS CB active)

Circuit breaker: if LS[T-1] - LS[T-8] < -15 (long crowd unwinding fast),
force 'uncertain' for the next 7 calendar days. This is a "fade the crowded
exit" trigger inherited from the upstream research.

Peak-DD override: if current close is > 5% off the trailing peak AND the
mode would otherwise be bullish, demote to 'uncertain' (don't open risky
bullish exposure into a nearby drawdown).

This classifier is strictly the same as upstream. The ONLY change in the
port is that the classification for date T uses data through T-1 (not T),
enforced by iterating with det_i = i-1 throughout.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta


REGIMES = ("strong_bull", "mild_bull", "uncertain", "bear")


def ema_calc(values: list[float], period: int) -> list[float]:
    """Standard EMA seeded with SMA of first `period` values. Returns a list
    of the same length as `values`, with NaN for indices < period-1."""
    out: list[float] = [float("nan")] * len(values)
    if len(values) < period:
        return out
    out[period - 1] = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def classify_day(
    det_i: int,
    bc: list[float],
    e20: list[float],
    e50: list[float],
    ls_d: dict[str, float],
    dates: list[str],
    spot_peak: float,
    cb_until: str,
    today_iso: str,
) -> tuple[str, float, str]:
    """Classify the regime for date `dates[det_i + 1]` using data at det_i
    (= T-1). Returns (mode, updated_spot_peak, updated_cb_until).

    The caller iterates: at each new 'i' (today index), passes det_i = i-1
    and the previous spot_peak / cb_until state; this function updates them.
    """
    # Base state
    if det_i < 50 or math.isnan(e50[det_i]) or math.isnan(e20[det_i]):
        mode = "uncertain"
    else:
        a50 = bc[det_i] > e50[det_i]
        a20 = bc[det_i] > e20[det_i]
        m30 = (bc[det_i] / bc[max(0, det_i - 30)] - 1) if det_i >= 30 else 0.0
        m7 = (bc[det_i] / bc[max(0, det_i - 7)] - 1) if det_i >= 7 else 0.0
        if a50 and m30 > 0 and m7 > 0 and a20:
            mode = "strong_bull"
        elif a50 and (m30 > 0 or a20):
            mode = "mild_bull"
        elif not a50 and m30 < 0:
            mode = "bear"
        else:
            mode = "uncertain"

    # Peak-drawdown override — updates spot_peak in-place.
    spot_peak = max(spot_peak, bc[det_i])
    if spot_peak > 0 and (spot_peak - bc[det_i]) / spot_peak > 0.05 \
            and mode in ("strong_bull", "mild_bull"):
        mode = "uncertain"

    # LS circuit breaker: check whether T-1's shift vs T-8 is < -15.
    det_date = dates[det_i]
    d7 = dates[max(0, det_i - 7)]
    if det_date in ls_d and d7 in ls_d:
        shift = ls_d[det_date] - ls_d[d7]
        if shift < -15:
            cb_until = (datetime.strptime(today_iso, "%Y-%m-%d")
                        + timedelta(days=7)).isoformat()[:10]
    if today_iso <= cb_until:
        mode = "uncertain"

    return mode, spot_peak, cb_until


def classify_series(
    dates: list[str],
    bc: list[float],
    ls_d: dict[str, float],
) -> dict[str, str]:
    """Run the classifier forward and return {date_iso: mode} for every
    date from dates[1] onward (first date has no T-1 to look at).

    This is the batch-mode helper; the live service uses classify_day
    directly with precomputed EMA series and maintains the rolling state.
    """
    e20 = ema_calc(bc, 20)
    e50 = ema_calc(bc, 50)
    out: dict[str, str] = {}
    spot_peak = bc[50] if len(bc) > 50 else bc[0] if bc else 0.0
    cb_until = ""
    for i in range(1, len(dates)):
        det_i = max(1, i - 1)
        today_iso = dates[i]
        mode, spot_peak, cb_until = classify_day(
            det_i, bc, e20, e50, ls_d, dates, spot_peak, cb_until, today_iso
        )
        out[today_iso] = mode
    return out
