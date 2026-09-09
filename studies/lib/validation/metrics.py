"""Return-series metrics shared by the validation toolkit.

Ported from the trader repo root modules ``deflate.py`` (``trade_sharpe``,
``moments``, ``annualize_factor_per_trade``, ``trades_to_monthly_returns``,
``month_count``) and ``cpcv.py`` (``sharpe``).  New here: ``max_drawdown``
(additive equity walk), ``daily_sharpe`` (numpy, NaN-tolerant), the era
constants and ``era_split``.

Conventions
-----------
* ``sharpe`` / ``daily_sharpe`` annualise a per-period Sharpe by
  ``sqrt(periods_per_year)``; ``trade_sharpe`` does the same with trades per
  year.  Bailey & Lopez de Prado (2014) treat the per-observation Sharpe as
  the primitive, which is what ``dsr_pbo`` works in -- do not feed an
  annualised Sharpe into the DSR.
* ``moments`` returns *excess* kurtosis (0 for a Gaussian) with population
  (1/n) denominators, exactly as ``deflate.py`` did.  ``dsr_pbo`` carries its
  own sample-moment helper with the full-kurtosis convention of the DSR paper.
* Era constants are ISO dates.  ``era_split`` splits at the BTC spot-ETF
  approval (2024-01-11) by default; "pre" is strictly before the cutoff.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Sequence

import numpy as np

# --- era constants ------------------------------------------------------------

BINANCE_FUT_LAUNCH = "2019-09-08"   # BTCUSDT perpetual listed on Binance Futures
BTC_SPOT_ETF = "2024-01-11"         # first US spot-BTC ETF trading day
ETH_SPOT_ETF = "2024-07-23"         # first US spot-ETH ETF trading day


# --- Sharpe / moments (deflate.py, cpcv.py) -----------------------------------

def annualize_factor_per_trade(trades_per_year: float) -> float:
    """Sharpe annualization factor when SR is computed from per-trade returns."""
    return math.sqrt(trades_per_year)


def trade_sharpe(trade_returns: Sequence[float], trades_per_year: float) -> float:
    """Annualized Sharpe from a list of per-trade % returns. Returns 0 for degenerate cases.

    Numerical guard: if std is effectively zero (e.g., all trades hit SL at the
    same %), float arithmetic can produce a tiny positive variance like 1e-15
    that yields a meaningless Sharpe of 1e15. Treat std < 1e-4 as zero spread.
    """
    n = len(trade_returns)
    if n < 2:
        return 0.0
    mean = sum(trade_returns) / n
    var = sum((r - mean) ** 2 for r in trade_returns) / (n - 1)
    if var <= 0:
        return 0.0
    sd = math.sqrt(var)
    if sd < 1e-4:  # essentially zero spread -- degenerate trial
        return 0.0
    return (mean / sd) * annualize_factor_per_trade(trades_per_year)


def moments(x: Sequence[float]) -> tuple[float, float, float, float]:
    """Return (mean, std, skewness, excess_kurtosis) for sample x.

    Population (1/n) denominators; excess kurtosis is 0 for a Gaussian.
    Returns all zeros for n < 4.
    """
    n = len(x)
    if n < 4:
        return 0.0, 0.0, 0.0, 0.0
    mean = sum(x) / n
    m2 = sum((v - mean) ** 2 for v in x) / n
    if m2 <= 0:
        return mean, 0.0, 0.0, 0.0
    sd = math.sqrt(m2)
    m3 = sum((v - mean) ** 3 for v in x) / n
    m4 = sum((v - mean) ** 4 for v in x) / n
    skew = m3 / (sd ** 3)
    kurt = m4 / (m2 * m2) - 3.0   # excess kurtosis
    return mean, sd, skew, kurt


def sharpe(rets: Sequence[float], periods_per_year: float = 365.0) -> float:
    """Annualized Sharpe from daily percent returns (scale-free)."""
    n = len(rets)
    if n < 2:
        return 0.0
    mu = sum(rets) / n
    var = sum((r - mu) ** 2 for r in rets) / (n - 1)
    sd = math.sqrt(var)
    return mu / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0


def daily_sharpe(returns: Any, periods_per_year: float = 365.0) -> float:
    """Annualised Sharpe of a per-period return array (NaN entries dropped).

    The same estimator as ``sharpe`` (sample std, ddof=1) but accepts any
    array-like and ignores NaN, so it applies directly to stitched OOS series
    with gaps.  Returns 0.0 for fewer than 2 finite points or zero spread.
    """
    r = np.asarray(returns, dtype=np.float64).ravel()
    r = r[np.isfinite(r)]
    if r.size < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    if sd <= 0.0:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def max_drawdown(returns: Any) -> float:
    """Maximum drawdown of the additive equity walk ``cumsum(returns)``.

    Equity starts at 0 and the running peak includes that starting point, so
    a series that only ever loses reports its full cumulative loss.  Returned
    as a positive magnitude in the units of ``returns`` (percent in, percent
    out; R in, R out).  Additive rather than compounded on purpose -- it is
    the convention of ``sizing_risk.drawdown_series`` and of per-fire PnL /
    R-multiple series.  NaN entries are dropped.
    """
    r = np.asarray(returns, dtype=np.float64).ravel()
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    eq = np.concatenate(([0.0], np.cumsum(r)))
    peak = np.maximum.accumulate(eq)
    return float((peak - eq).max())


# --- trade -> time-binned returns (deflate.py) --------------------------------

def trades_to_monthly_returns(trades: Sequence[Any], n_months: int,
                              start_dt: str, end_dt: str) -> list[float]:
    """Bin closed trade PnLs into monthly buckets between start and end (inclusive months).
    Returns a list of length n_months. Each value = sum of trade PnLs that closed in that month.

    ``trades`` are duck-typed: each needs ``.closed`` (bool), ``.exit_time``
    (string starting with YYYY-MM-DD) and ``.pnl_pct``.  ``end_dt`` is kept
    for signature parity with the original; the bin count comes from
    ``n_months`` (see ``month_count``).
    """
    start = datetime.strptime(start_dt, "%Y-%m-%d")
    bins = [0.0] * n_months
    for t in trades:
        if not t.closed:
            continue
        try:
            ed = datetime.strptime(t.exit_time[:10], "%Y-%m-%d")
        except Exception:
            continue
        idx = (ed.year - start.year) * 12 + (ed.month - start.month)
        if 0 <= idx < n_months:
            bins[idx] += t.pnl_pct
    return bins


def month_count(start_dt: str, end_dt: str) -> int:
    """Number of calendar months spanned by [start_dt, end_dt], inclusive."""
    s = datetime.strptime(start_dt, "%Y-%m-%d")
    e = datetime.strptime(end_dt, "%Y-%m-%d")
    return (e.year - s.year) * 12 + (e.month - s.month) + 1


# --- eras ---------------------------------------------------------------------

def era_split(dates: Sequence[Any], values: Sequence[Any],
              cutoff: str = BTC_SPOT_ETF) -> dict[str, list]:
    """Split ``values`` into {"pre_etf": [...], "post_etf": [...]} by date.

    ``dates`` may be ISO strings, ``date``/``datetime`` objects or pandas
    Timestamps -- anything whose ``str()`` starts with YYYY-MM-DD.  A date is
    "post" when it is on or after ``cutoff`` (default BTC spot-ETF day).
    """
    if len(dates) != len(values):
        raise ValueError(f"dates ({len(dates)}) and values ({len(values)}) differ in length")
    pre: list = []
    post: list = []
    for d, v in zip(dates, values):
        key = str(d)[:10]
        (post if key >= cutoff else pre).append(v)
    return {"pre_etf": pre, "post_etf": post}
