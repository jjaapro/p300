"""Triple-barrier event labeling per AFML Chapter 3.

Ported verbatim from the trader repo root module ``triple_barrier.py`` (all
functions; the self-test is dropped).

For each decision day t, place three barriers on the future cumulative
return path and label by which is hit FIRST:

  - upper horizontal barrier at +upper_pct (take-profit / rally escape)
  - lower horizontal barrier at -lower_pct (stop-loss / MDD onset)
  - vertical barrier at t + horizon_days (timeout)

For the MDD-gate use case we care about whether a -X% drawdown occurs
BEFORE an offsetting rally, not merely whether it occurs at any point
in a fixed 5-day window. That distinction is what separates fixed-
horizon labels from triple-barrier labels, and is where AFML argues the
machine-learning target should actually live.

Reference: Lopez de Prado, M. (2018). "Advances in Financial Machine
  Learning", Chapter 3 (The Triple-Barrier Method) + Chapter 4
  (Sample Weights -- concurrency-aware weighting).

This module provides:
  - `triple_barrier_labels` -- core labeler (path-dependent first-touch)
  - `mdd_binary_labels` -- convenience wrapper producing {0,1} MDD labels
  - `sample_uniqueness_weights` -- AFML Ch 4 concurrency weights

Design notes:
  - Input is a DAILY return series (decimal, not percent). Cumulative path
    is computed by simple sum (log-return convention); swap to product if
    using decimal simple returns and you need long-horizon accuracy.
  - Barriers are applied from the day AFTER t (we size on close of t,
    outcomes unfold t+1..t+horizon).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Barriers:
    upper_pct: float          # e.g., +0.10 for +10% rally barrier
    lower_pct: float          # e.g.,  0.10 for -10% MDD barrier (positive magnitude)
    horizon_days: int         # vertical-barrier timeout in trading days


def triple_barrier_labels(
    daily_returns: np.ndarray,
    barriers: Barriers,
    use_log: bool = True,
) -> dict:
    """Apply triple-barrier labeling to a daily-return series.

    Parameters
    ----------
    daily_returns : 1-D array of floats
        Daily returns for day 1..T (NOT log of day close; decimal simple
        or log -- controlled by use_log).
    barriers : Barriers
    use_log : bool
        If True, accumulate by sum (approximate for small returns, exact
        for log returns). If False, accumulate by compounding (1+r).

    Returns
    -------
    dict with arrays of length T-horizon:
        labels      : {-1, 0, +1} per AFML convention
                      +1 = upper barrier hit first (safe/rally)
                       0 = vertical barrier hit first (neutral timeout)
                      -1 = lower barrier hit first (MDD event)
        touch_day   : integer offset into [1, horizon] where the barrier hit
        touch_side  : 'upper' | 'lower' | 'timeout'
        path_min    : minimum cumulative return within the horizon
        path_max    : maximum cumulative return within the horizon
    """
    r = np.asarray(daily_returns, dtype=np.float64)
    T = len(r)
    H = barriers.horizon_days
    n = T - H
    if n <= 0:
        raise ValueError(f"need T > horizon_days (got T={T}, H={H})")

    labels = np.zeros(n, dtype=np.int8)
    touch_day = np.zeros(n, dtype=np.int32)
    touch_side = np.empty(n, dtype=object)
    path_min = np.zeros(n)
    path_max = np.zeros(n)

    upper = barriers.upper_pct
    lower = -abs(barriers.lower_pct)  # ensure negative

    for t in range(n):
        cum = 0.0
        pmin = 0.0
        pmax = 0.0
        label = 0
        tday = H
        side = "timeout"
        for k in range(1, H + 1):
            rk = r[t + k]
            if use_log:
                cum += rk
            else:
                cum = (1 + cum) * (1 + rk) - 1
            if cum < pmin:
                pmin = cum
            if cum > pmax:
                pmax = cum
            if cum >= upper:
                label = 1
                tday = k
                side = "upper"
                break
            if cum <= lower:
                label = -1
                tday = k
                side = "lower"
                break
        labels[t] = label
        touch_day[t] = tday
        touch_side[t] = side
        path_min[t] = pmin
        path_max[t] = pmax

    return {
        "labels": labels,
        "touch_day": touch_day,
        "touch_side": touch_side,
        "path_min": path_min,
        "path_max": path_max,
    }


def mdd_binary_labels(
    daily_returns: np.ndarray,
    upper_pct: float,
    lower_pct: float,
    horizon_days: int,
    use_log: bool = True,
) -> np.ndarray:
    """Convenience wrapper for MDD-gate classifier.

    Returns a binary array where 1 = lower barrier hit first (MDD event)
    and 0 = either upper barrier hit first or vertical timeout with no breach.

    This is the triple-barrier analog of the fixed-horizon
    'next 5d min cum return < -X%' label.
    """
    out = triple_barrier_labels(
        daily_returns,
        Barriers(upper_pct=upper_pct, lower_pct=lower_pct,
                 horizon_days=horizon_days),
        use_log=use_log,
    )
    return (out["labels"] == -1).astype(np.int8)


def sample_uniqueness_weights(horizon_days: int, n_samples: int) -> np.ndarray:
    """AFML Ch 4 concurrency-aware weights for overlapping-horizon samples.

    When labeling day t with a horizon of H days, samples t and t+1 share
    H-1 future days of information. Naive IID treatment double-counts
    evidence. AFML weights each sample i by 1 / (number of samples whose
    label horizon overlaps i's horizon).

    For a fixed-horizon labeling with stride-1 sampling, every sample in
    the middle overlaps with 2H-1 neighbors -- so w_i = 1/(2H-1) for
    interior samples. Edges decay linearly. Normalized to mean 1.

    Returns an array of length n_samples.
    """
    if n_samples <= 0:
        return np.array([])
    H = horizon_days
    weights = np.empty(n_samples, dtype=np.float64)
    for i in range(n_samples):
        lo = max(0, i - (H - 1))
        hi = min(n_samples - 1, i + (H - 1))
        concurrency = hi - lo + 1
        weights[i] = 1.0 / concurrency
    mean = weights.mean()
    if mean > 0:
        weights /= mean
    return weights
