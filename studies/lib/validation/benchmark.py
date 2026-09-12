"""Benchmark and robustness helpers (added 2026-09-12 from the brainstorm follow-up studies).

Each one exists because a specific mistake produced a false positive:

* ``exposure_matched_buy_and_hold`` -- a strategy that runs 1.56x gross exposure "beat" a 1x buy-and-hold
  (brainstorm study 26); the benchmark must carry the same exposure.
* ``start_date_sensitivity`` -- the same comparison inverted when the benchmark started four months earlier
  (nine days before the 2017 top vs. the data start); report the comparison for several starts.
* ``paired_block_boot_diff`` -- Sharpe/MAR differences between two curves on the same days need a paired
  circular-block bootstrap, not two independent intervals.
* ``bar_phase_sweep`` -- any daily-bar rule must be shown across the 24 day boundaries; the ADX machine
  moves 0.70-1.08 Sharpe with the boundary alone.
* ``compounded_max_drawdown`` -- the mark-to-market (compounded, peak-relative) drawdown alongside
  ``metrics.max_drawdown`` (additive, trade-close); the ADX sleeve is -15 % on one and -38 % on the other.

numpy + pandas only, no DB access; see ``studies/notebooks/brainstorm_validation_2026_09/bv_lib.py`` for the
study-side originals.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from . import bootstrap as _boot


# --- exposure- and start-matched benchmarks ----------------------------------------

def exposure_matched_buy_and_hold(asset_returns: Sequence[float], exposure: float | Sequence[float]) -> np.ndarray:
    """Buy-and-hold return series scaled to the strategy's gross exposure.

    ``exposure`` is either a scalar (the strategy's mean gross notional / equity) or a per-period array
    (its gross exposure on each period).  The result carries the same market risk as the strategy, so a
    CAGR / drawdown comparison is about timing and selection, not about leverage.
    """
    r = np.asarray(asset_returns, dtype=float)
    e = np.broadcast_to(np.asarray(exposure, dtype=float), r.shape)
    return r * e


def cagr(returns: Sequence[float], periods_per_year: float = 365.0) -> float:
    """Compounded annual growth of a per-period fractional return series (NaN dropped)."""
    r = np.asarray(returns, dtype=float).ravel()
    r = r[np.isfinite(r)]
    if r.size == 0:
        return float("nan")
    growth = float(np.prod(1.0 + r))
    years = r.size / periods_per_year
    if growth <= 0:
        return -1.0
    return growth ** (1.0 / years) - 1.0


def start_date_sensitivity(strategy_returns: Sequence[float], benchmark_returns: Sequence[float],
                           dates: Sequence, start_dates: Iterable, periods_per_year: float = 365.0) -> dict:
    """CAGR of strategy and benchmark from each start date onward, and their difference.

    ``dates`` are the per-period labels (anything ``>=``-comparable with the ``start_dates``, e.g. ISO
    strings or Timestamps).  A benchmark that starts nine days before a cycle top looks very different from
    one that starts four months earlier; this makes that visible instead of choosing one.
    """
    s = np.asarray(strategy_returns, dtype=float)
    b = np.asarray(benchmark_returns, dtype=float)
    d = np.asarray(dates)
    out = {}
    for start in start_dates:
        m = d >= start
        if m.sum() < 2:
            out[str(start)] = dict(n=int(m.sum()), cagr_strategy=float("nan"), cagr_benchmark=float("nan"), diff=float("nan"))
            continue
        cs, cb = cagr(s[m], periods_per_year), cagr(b[m], periods_per_year)
        out[str(start)] = dict(n=int(m.sum()), cagr_strategy=cs, cagr_benchmark=cb, diff=cs - cb)
    return out


# --- paired bootstrap of a difference ------------------------------------------------

def paired_block_boot_diff(a: Sequence[float], b: Sequence[float], stat: Callable[[np.ndarray], float],
                           block: int = 30, n_iter: int = 2000, seed: int = 42,
                           qs: Sequence[float] = (0.05, 0.5, 0.95)) -> dict:
    """Circular-block bootstrap of ``stat(a) - stat(b)`` with the SAME block indices for both series.

    Use for two curves measured on the same periods (a strategy and its benchmark, two variants of one
    rule).  Returns the point estimate, the requested quantiles of the bootstrap differences and the
    share of draws above zero.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        raise ValueError("a and b must be aligned series of the same length")
    T = len(a)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_iter)
    for i in range(n_iter):
        idx = _boot.circular_block_indices(T, T, block, rng)
        diffs[i] = stat(a[idx]) - stat(b[idx])
    return dict(point=float(stat(a) - stat(b)), ci=[float(v) for v in np.quantile(diffs, qs)],
                p_gt_0=float((diffs > 0).mean()), block=block, n_iter=n_iter)


# --- bar-phase sweep ----------------------------------------------------------------------

def daily_bars_at_phase(ts: Sequence[int], open_: Sequence[float], high: Sequence[float], low: Sequence[float],
                        close: Sequence[float], phase_hour: int = 0, step_s: int = 3600, *,
                        require_complete: bool = True, drop_after_ts: int | None = None) -> pd.DataFrame:
    """Aggregate open-stamped intraday bars into daily bars whose day starts at ``phase_hour`` UTC.

    With ``require_complete`` a day is kept only if its first bar opens exactly at the day start and its last
    bar closes the day (so the daily open/close are the true ones).  ``drop_after_ts`` drops the day
    containing that instant and later (the forming day).  Columns: ts (day start), open, high, low, close,
    n_rows.
    """
    t = np.asarray(ts, dtype=np.int64)
    day_start = ((t - phase_hour * 3600) // 86400) * 86400 + phase_hour * 3600
    df = pd.DataFrame(dict(day_start=day_start, ts=t, open=np.asarray(open_, float), high=np.asarray(high, float),
                           low=np.asarray(low, float), close=np.asarray(close, float)))
    g = df.groupby("day_start", sort=True)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                n_rows=("ts", "size"), first_ts=("ts", "min"), last_ts=("ts", "max")).reset_index().rename(columns={"day_start": "ts"})
    keep = np.ones(len(out), dtype=bool)
    if require_complete:
        keep &= (out["first_ts"] == out["ts"]).to_numpy() & (out["last_ts"] + step_s >= out["ts"] + 86400).to_numpy()
    if drop_after_ts is not None:
        keep &= (out["ts"] + 86400 <= int(drop_after_ts)).to_numpy()
    return out[keep].drop(columns=["first_ts", "last_ts"]).reset_index(drop=True)


def bar_phase_sweep(ts: Sequence[int], open_: Sequence[float], high: Sequence[float], low: Sequence[float],
                    close: Sequence[float], evaluate: Callable[[pd.DataFrame], dict], phases: Iterable[int] = range(24),
                    step_s: int = 3600, **kw) -> pd.DataFrame:
    """Run ``evaluate(daily_bars)`` at every day boundary in ``phases`` and tabulate the results.

    ``evaluate`` receives the daily bars for one phase and returns a flat dict of numbers (Sharpe, maxDD, n,
    ...).  The returned frame has one row per phase; use ``phase_summary`` to read the noise floor.
    """
    rows = []
    for ph in phases:
        bars = daily_bars_at_phase(ts, open_, high, low, close, ph, step_s, **kw)
        rows.append(dict(phase_hour=int(ph), **evaluate(bars)))
    return pd.DataFrame(rows)


def phase_summary(sweep: pd.DataFrame, cols: Sequence[str] | None = None) -> dict:
    """[min, median, max] of each metric across phases -- the noise floor a single-phase delta must clear."""
    cols = [c for c in (cols or sweep.columns) if c != "phase_hour"]
    return {c: [float(sweep[c].min()), float(sweep[c].median()), float(sweep[c].max())] for c in cols}


# --- mark-to-market drawdown ------------------------------------------------------------

def compounded_max_drawdown(returns: Sequence[float]) -> float:
    """Peak-relative maximum drawdown of the compounded equity ``cumprod(1 + returns)``, as a positive
    fraction (0.38 = -38 %).  This is the mark-to-market convention; ``metrics.max_drawdown`` is the
    additive trade-close one.  Report both."""
    r = np.asarray(returns, dtype=float).ravel()
    r = r[np.isfinite(r)]
    if r.size == 0:
        return 0.0
    eq = np.concatenate(([1.0], np.cumprod(1.0 + r)))
    peak = np.maximum.accumulate(eq)
    return float(((peak - eq) / peak).max())


def mar(returns: Sequence[float], periods_per_year: float = 365.0) -> float:
    """CAGR divided by the compounded maximum drawdown (inf when there is no drawdown)."""
    dd = compounded_max_drawdown(returns)
    c = cagr(returns, periods_per_year)
    return float(c / dd) if dd > 0 else (math.inf if c > 0 else 0.0)
