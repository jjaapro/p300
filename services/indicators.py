"""services.indicators — pure technical-indicator math (no I/O, no DB).

Single source of truth for the bar-level indicators used across sleeves,
validators, and backtests. Replaces four byte-identical EMA copies and two
byte-identical ADX copies that previously lived in:

  strategies/sleeves/adx/signal.py   (_calc_ema, _calc_adx)
  bitstamp_adx_backtest.py           (calc_ema, calc_adx)
  bitstamp_thu_bear_backtest.py  (calc_ema)
  jplus/regime.py           (ema_calc)

Functions are pure (no DB / network / clock dependencies). All inputs and
outputs are plain Python lists/dicts so the same code runs in the live
service, the historical backtest, and unit tests without adapter layers.

Conventions:
  - NaN sentinel for "warmup not complete" — mirrors the prior implementations
    so tests + downstream consumers don't see behavior shifts from this refactor.
  - Indices align: out[i] is the indicator value as observed AT THE CLOSE of
    candles[i] (or values[i]). Callers reference out[-1] for "today's value
    based on the most recent closed bar".
"""
from __future__ import annotations

import math


def ema(values: list[float], period: int) -> list[float]:
    """Standard EMA seeded with SMA of the first ``period`` values.

    Returns a list of the same length as ``values``, with NaN for indices
    < ``period - 1``. The seed at index ``period - 1`` is the simple mean
    of values[0:period]; from index ``period`` onward the recursion
    ``out[i] = values[i] * k + out[i-1] * (1-k)`` with ``k = 2/(period+1)``.

    Used by ADX (direction filter), THU_BEAR (regime filter), JPLUS regime
    classifier, voltarget warmup. Identical to TradingView's ``ta.ema``.
    """
    out: list[float] = [float("nan")] * len(values)
    if len(values) < period:
        return out
    out[period - 1] = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def adx(candles: list[dict], period: int) -> list[float]:
    """ADX via Wilder smoothing.

    Each candle must be a dict with ``high``, ``low``, ``close`` keys.
    Returns NaN for indices before warmup completes (warmup = 2*period bars).

    Math:
      TR = max(high-low, |high-prev_close|, |low-prev_close|)
      +DM = up if up > down and up > 0 else 0       (up   = high - prev_high)
      -DM = down if down > up and down > 0 else 0   (down = prev_low - low)
      ATR / +DM_smooth / -DM_smooth use Wilder smoothing:
        sum at index ``period`` (of the previous ``period`` raw values),
        then ``smooth[i] = smooth[i-1] - smooth[i-1]/period + raw[i]``.
      DX = 100 * |+DI - -DI| / (+DI + -DI)
      ADX seeded as the mean of the first ``period`` valid DX values, then
      ``adx[i] = (adx[i-1] * (period-1) + dx[i]) / period``.

    Matches TradingView's ``ta.adx`` to within the same boundary-precision
    cases the live ADX validator already documents (≈1 trade in 40 may shift
    by 1 day at the threshold crossing). Used by S-003.
    """
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
    out = [float("nan")] * n
    first = period * 2
    if first < n:
        window = [dx[i] for i in range(period + 1, first + 1)
                  if not math.isnan(dx[i])]
        if window:
            out[first] = sum(window) / len(window)
            for i in range(first + 1, n):
                if not math.isnan(dx[i]):
                    out[i] = (out[i - 1] * (period - 1) + dx[i]) / period
    return out
