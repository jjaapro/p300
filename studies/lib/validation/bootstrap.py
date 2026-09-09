"""Bootstrap confidence intervals for the Sharpe ratio.

Ported from the trader repo root modules ``bootstrap_sharpe.py``
(``bootstrap_sharpe`` -- iid percentile bootstrap of the per-trade Sharpe;
``dsr_required_sr``) and ``reverse_stress.py`` (``circular_block_indices``).
New here: ``block_bootstrap_sharpe`` -- circular-block (Politis & Romano
1992) percentile bootstrap for autocorrelated daily series.

Different question than DSR:
  - DSR asks: "after the multiple-testing penalty, did this Sharpe beat what
               could plausibly arise from chance across N trials?"
  - Bootstrap asks: "given THIS exact set of N observations, how stable is
                     the point estimate of the Sharpe ratio under resampling
                     with replacement?"

Percentile intervals only (no BCa), as in the original.  ``dsr_required_sr``
reuses the Bailey & Lopez de Prado (2014) expected-maximum formula from
``dsr_pbo`` with a Gaussian-tail approximation (skew 0, kurtosis 3).
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Sequence

import numpy as np

from .dsr_pbo import EULER, normal_ppf
from .metrics import daily_sharpe


def _per_trade_sharpe(rs: Sequence[float]) -> float:
    n = len(rs)
    if n < 2:
        return 0.0
    m = sum(rs) / n
    var = sum((r - m) ** 2 for r in rs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    return m / sd


def bootstrap_sharpe(returns: Sequence[float], n_iter: int = 10000,
                     threshold: float | None = None, seed: int = 42,
                     n_per_year: float = 12) -> dict | None:
    """Bootstrap the per-trade Sharpe ratio of a returns series.

    Args:
        returns:    list/array of per-trade returns (decimals).
        n_iter:     number of bootstrap resamples.
        threshold:  per-trade Sharpe threshold to test against. If supplied,
                    the result includes P(boot_sr >= threshold).
        seed:       RNG seed for reproducibility.
        n_per_year: trades per year, used only for reporting an annualized
                    Sharpe alongside the per-trade one.

    Returns:
        dict with:
          n              -- sample size
          sr_point       -- per-trade Sharpe (point estimate)
          sr_point_ann   -- annualized at sqrt(n_per_year)
          sr_p05/p50/p95 -- percentile CI of bootstrap per-trade Sharpe
          sr_mean        -- mean of bootstrap distribution
          sr_sd          -- sd of bootstrap distribution
          threshold      -- echoed back (if supplied)
          p_at_or_above  -- fraction of bootstraps with sr >= threshold (if threshold)
          n_iter         -- echoed back
        or None when fewer than two returns were given.
    """
    n = len(returns)
    if n < 2:
        return None

    rng = random.Random(seed)
    point = _per_trade_sharpe(returns)

    boot = []
    indices = range(n)
    for _ in range(n_iter):
        sample = [returns[rng.randrange(n)] for _ in indices]
        boot.append(_per_trade_sharpe(sample))

    boot.sort()
    p05 = boot[int(0.05 * n_iter)]
    p50 = boot[int(0.50 * n_iter)]
    p95 = boot[int(0.95 * n_iter)]
    boot_mean = sum(boot) / n_iter
    boot_sd = statistics.pstdev(boot)

    out = dict(
        n=n,
        sr_point=point,
        sr_point_ann=point * math.sqrt(n_per_year),
        sr_p05=p05,
        sr_p50=p50,
        sr_p95=p95,
        sr_mean=boot_mean,
        sr_sd=boot_sd,
        n_iter=n_iter,
        threshold=threshold,
    )
    if threshold is not None:
        n_above = sum(1 for s in boot if s >= threshold)
        out["p_at_or_above"] = n_above / n_iter
    return out


# --- circular block bootstrap ------------------------------------------------

def circular_block_indices(T: int, length: int, block: int,
                           rng: np.random.Generator) -> np.ndarray:
    """One circular-block sample of `length` indices drawn from 0..T-1.

    Politis & Romano (1992) circular block bootstrap: ``ceil(length/block)``
    block starts are drawn uniformly from 0..T-1 and each block is the
    ``block`` consecutive indices modulo T.  Same RNG consumption and output
    as the trader double loop (``out[k*block+j] = (starts[k]+j) % T``),
    written with broadcasting.
    """
    n_blocks = (length + block - 1) // block
    starts = rng.integers(0, T, size=n_blocks)
    out = (starts[:, None] + np.arange(block, dtype=np.int64)[None, :]) % T
    return out.reshape(-1)[:length].astype(np.int64, copy=False)


def block_bootstrap_sharpe(daily_returns: Any, block: int = 20, n_iter: int = 5000,
                           seed: int = 42, periods_per_year: float = 365) -> dict | None:
    """Circular-block bootstrap of the annualised Sharpe of a daily series.

    Resamples whole blocks of ``block`` consecutive days (wrapping around), so
    short-range autocorrelation and volatility clustering survive into the
    resamples -- the iid bootstrap in ``bootstrap_sharpe`` destroys them and
    understates the Sharpe's sampling variance on daily data.

    Returns a dict with the point estimate (``sr_point``), the percentile
    interval ``sr_p05`` / ``sr_p50`` / ``sr_p95``, ``sr_mean`` / ``sr_sd`` of
    the bootstrap distribution, ``p_sr_gt_0`` = P(bootstrap SR > 0), and the
    settings; None when fewer than two finite observations.
    """
    r = np.asarray(daily_returns, dtype=np.float64).ravel()
    r = r[np.isfinite(r)]
    T = int(r.size)
    if T < 2:
        return None
    if block < 1:
        raise ValueError(f"block must be >= 1, got {block}")
    rng = np.random.default_rng(seed)
    point = daily_sharpe(r, periods_per_year)
    boot = np.empty(n_iter, dtype=np.float64)
    for i in range(n_iter):
        idx = circular_block_indices(T, T, block, rng)
        boot[i] = daily_sharpe(r[idx], periods_per_year)
    p05, p50, p95 = (float(q) for q in np.quantile(boot, (0.05, 0.50, 0.95)))
    return dict(
        n=T,
        block=block,
        n_iter=n_iter,
        periods_per_year=periods_per_year,
        sr_point=point,
        sr_p05=p05,
        sr_p50=p50,
        sr_p95=p95,
        sr_mean=float(boot.mean()),
        sr_sd=float(boot.std()),
        p_sr_gt_0=float((boot > 0).mean()),
    )


# --- DSR-required threshold helper -------------------------------------------

def dsr_required_sr(n: int, n_trials: int, target_p: float = 0.95) -> float | None:
    """Per-trade Sharpe required for DSR p > target_p at given n and n_trials.

    Uses the same Bailey & Lopez de Prado expected-max-by-chance formula as
    ``dsr_pbo.dsr_from_returns``, with a Gaussian-tail approximation
    (skew=0, kurt=3) so the result is comparable across strategies without
    needing the moments of any specific sample.  Returns None for n < 2.
    """
    if n < 2:
        return None
    var_sr_one = 1.0 / (n - 1)
    if n_trials <= 1:
        sr_expected = 0.0
    else:
        z1 = normal_ppf(1 - 1.0 / n_trials)
        z2 = normal_ppf(1 - 1.0 / (n_trials * math.e))
        sr_expected = math.sqrt(var_sr_one) * ((1 - EULER) * z1 + EULER * z2)
    z_target = normal_ppf(target_p)
    return sr_expected + z_target * math.sqrt(var_sr_one)
