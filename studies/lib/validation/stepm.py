"""Romano-Wolf StepM -- per-strategy FWER-controlled superiority test.

Ported from the trader repo root module ``romano_wolf_stepm.py``
(``circular_block_bootstrap_indices``, ``stepm``); the registry loaders,
memo writer and CLI are dropped.

Fills the gap Hansen's SPA leaves: SPA answers the binary "does the best
candidate beat the benchmark?"  StepM answers "WHICH individual strategies
beat the benchmark at FWER alpha?" via a studentized block-bootstrap that
captures joint dependence across strategies.

Reference: Romano, J. P. & Wolf, M. (2005). "Stepwise Multiple Testing as
Formalized Data Snooping." Econometrica 73(4): 1237-1282, Algorithm 4.1.

Algorithm outline:
  1. For each strategy i, compute studentized t_i = mean(r_i - b) / se(r_i - b)
  2. Circular block-bootstrap: resample time blocks jointly for all strategies
     (preserves cross-strategy correlation); recenter under H_0
  3. Iteratively:
     - max_t*_over_survivors across bootstrap replicates
     - critical value = (1-alpha) quantile of max_t*
     - reject any survivor with t_i > c_alpha; remove from survivor set
     - stop when no new rejections

Default block length is ceil(T^(1/3)) (crude but standard heuristic).  For
crypto daily returns with T~2200 this gives ~13 days.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np


def circular_block_bootstrap_indices(T: int, block_length: int,
                                     rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano circular block bootstrap -- returns T indices.

    Same RNG consumption and output as the trader double loop
    (``idx[k*block+j] = (starts[k]+j) % T``), written with broadcasting.
    """
    n_blocks = (T + block_length - 1) // block_length
    starts = rng.integers(0, T, size=n_blocks)
    idx = (starts[:, None] + np.arange(block_length, dtype=np.int64)[None, :]) % T
    return idx.reshape(-1)[:T].astype(np.int64, copy=False)


def stepm(excess_returns: Any, alpha: float = 0.10, n_bootstrap: int = 2000,
          block_length: int | None = None, seed: int = 42,
          verbose: bool = False) -> dict:
    """Romano-Wolf StepM on excess returns (strategy minus benchmark).

    Parameters
    ----------
    excess_returns : array (N, T)
        Rows = strategies, cols = time. excess_returns[i, t] = r_i,t - b_t.
    alpha : float
        FWER level (default 0.10).
    n_bootstrap : int
        Number of bootstrap replicates.
    block_length : int or None
        Block length for circular bootstrap. If None, uses ceil(T**(1/3)).
    seed : int
        RNG seed.
    verbose : bool

    Returns
    -------
    dict with keys: t_stats (N,), rejected (set of strategy indices),
                    iterations (list of step-by-step rejection records),
                    block_length, n_bootstrap, alpha, N, T.
    """
    excess_returns = np.asarray(excess_returns, dtype=np.float64)
    N, T = excess_returns.shape
    if block_length is None:
        block_length = max(1, int(np.ceil(T ** (1.0 / 3.0))))

    # Point estimates and standard errors of each row mean
    means = excess_returns.mean(axis=1)                       # (N,)
    # Use sample SD / sqrt(T) -- studentized bootstrap captures dependence
    # structure; HAC correction would be orthogonal refinement.
    sds = excess_returns.std(axis=1, ddof=1)
    ses = sds / np.sqrt(T)
    ses = np.where(ses <= 0, np.nan, ses)

    # Original one-sided t-stats for H_A: mean > 0
    t_stats = means / ses

    # Bootstrap recentered null distribution
    rng = np.random.default_rng(seed)
    boot_t = np.empty((n_bootstrap, N), dtype=np.float64)
    if verbose:
        t0 = time.time()
    for b in range(n_bootstrap):
        idx = circular_block_bootstrap_indices(T, block_length, rng)
        sample = excess_returns[:, idx]                  # (N, T) resampled cols
        boot_mean = sample.mean(axis=1)
        boot_sd = sample.std(axis=1, ddof=1)
        boot_se = boot_sd / np.sqrt(T)
        boot_se = np.where(boot_se <= 0, np.nan, boot_se)
        # Recenter under H_0 by subtracting the original means
        boot_t[b] = (boot_mean - means) / boot_se
        if verbose and (b + 1) % 500 == 0:
            rate = (b + 1) / (time.time() - t0)
            print(f"  bootstrap {b+1}/{n_bootstrap} ({rate:.0f}/s)")

    # StepM iterations
    survivors = list(range(N))
    rejected: set[int] = set()
    iterations = []
    step_idx = 0
    while survivors:
        step_idx += 1
        sub = boot_t[:, survivors]                        # (B, |S|)
        max_t = np.nanmax(sub, axis=1)                    # (B,)
        c_alpha = float(np.nanquantile(max_t, 1.0 - alpha))

        new_reject = [i for i in survivors if t_stats[i] > c_alpha]
        iterations.append({
            "step": step_idx,
            "critical_value": c_alpha,
            "survivors_before": list(survivors),
            "rejected_this_step": list(new_reject),
        })
        if not new_reject:
            break
        rejected |= set(new_reject)
        survivors = [i for i in survivors if i not in new_reject]

    return {
        "t_stats": t_stats,
        "rejected": rejected,
        "iterations": iterations,
        "block_length": block_length,
        "n_bootstrap": n_bootstrap,
        "alpha": alpha,
        "N": N,
        "T": T,
    }
