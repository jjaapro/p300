"""Model Confidence Set (Hansen-Lunde-Nason 2011).

Ported from the trader repo root module ``model_confidence_set.py``
(``mcs_t_max``); registry loaders, memo writer and CLI are dropped.  The
bootstrap index sampler is imported from ``stepm``.

Identifies the set M* of strategies statistically indistinguishable from the
best at confidence level (1 - alpha). Complement to Romano-Wolf StepM:
  - StepM answers: "which strategies beat an external benchmark?"
  - MCS answers:   "which strategies are tied at the top among each other?"

Reference: Hansen, P. R., Lunde, A., Nason, J. M. (2011).
"The Model Confidence Set." Econometrica 79(2):453-497.

Algorithm (T_max elimination):
  1. For survivor set M, compute loss-deviation d_i,t = L_i,t - mean_{j in M}(L_j,t)
  2. Studentize: t_i = mean_t(d_i) / se(mean_t(d_i)), with se from block bootstrap
  3. T_max = max_i t_i (one-sided -- we test "all means equal" and want to
     eliminate the WORST performer on rejection)
  4. Bootstrap null distribution of T_max by recentering; p-value = P(T*_max >= T_max)
  5. If p >= alpha: STOP, M is the MCS
  6. Else: eliminate argmax_i t_i, repeat

Bootstrap: circular block bootstrap (Politis-Romano), default B=10000 per HLN
recommendation. Block length default = ceil(T^(1/3)).
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from .stepm import circular_block_bootstrap_indices


def mcs_t_max(loss_matrix: Any, alpha: float = 0.25, n_bootstrap: int = 10000,
              block_length: int | None = None, seed: int = 42,
              verbose: bool = False) -> dict:
    """Hansen-Lunde-Nason MCS via T_max elimination.

    Parameters
    ----------
    loss_matrix : array (N, T)
        Rows = candidates, cols = time. Higher loss = worse.
        Typical: loss_matrix[i, t] = -daily_return[i, t].
    alpha : float
        MCS confidence = 1 - alpha. Default 0.25 per HLN recommendation.
    n_bootstrap : int
        Bootstrap replicates. HLN recommends B >= 10000.
    block_length : int or None
        Circular block length. Default ceil(T**(1/3)).
    seed : int
        RNG seed for reproducibility.
    verbose : bool

    Returns
    -------
    dict with keys: mcs_indices (list), iterations (list), alpha, n_bootstrap,
                    block_length, N, T.
    """
    L = np.asarray(loss_matrix, dtype=np.float64)
    N, T = L.shape
    if block_length is None:
        block_length = max(1, int(np.ceil(T ** (1.0 / 3.0))))

    # Pre-compute bootstrap index sets (shared across iterations)
    rng = np.random.default_rng(seed)
    boot_indices = np.empty((n_bootstrap, T), dtype=np.int64)
    for b in range(n_bootstrap):
        boot_indices[b] = circular_block_bootstrap_indices(T, block_length, rng)

    # Pre-compute bootstrap means per strategy: L_boot[b, i] = mean over resampled
    # time of L[i, :]. This lets each MCS iteration compute d_boot cheaply.
    if verbose:
        t0 = time.time()
    L_boot = np.empty((n_bootstrap, N), dtype=np.float64)
    for b in range(n_bootstrap):
        L_boot[b] = L[:, boot_indices[b]].mean(axis=1)
    if verbose:
        print(f"  Bootstrap means computed in {time.time() - t0:.1f}s")

    # Iteratively eliminate
    survivors = list(range(N))
    iterations = []
    step = 0
    while len(survivors) > 1:
        step += 1
        surv_arr = np.array(survivors, dtype=np.int64)
        sub = L[surv_arr]                        # (|M|, T)

        # d_i,t = L_i,t - mean_{j in M} L_j,t
        mean_by_t = sub.mean(axis=0)             # (T,)
        d = sub - mean_by_t[None, :]             # (|M|, T)
        bar_d = d.mean(axis=1)                   # (|M|,)

        # Bootstrap: bar_d_boot[b, i] = L_boot[b, i] - mean_{j in M} L_boot[b, j]
        L_boot_sub = L_boot[:, surv_arr]         # (B, |M|)
        boot_mean_all = L_boot_sub.mean(axis=1)  # (B,)
        bar_d_boot = L_boot_sub - boot_mean_all[:, None]  # (B, |M|)

        # Bootstrap SE estimate per strategy
        var_d = bar_d_boot.var(axis=0, ddof=1)   # (|M|,)
        se_d = np.sqrt(var_d)
        se_d = np.where(se_d <= 0, np.nan, se_d)

        # Studentized test statistics + max
        t_stats = bar_d / se_d
        T_max = float(np.nanmax(t_stats))

        # Bootstrap null distribution (recentered: subtract original bar_d)
        t_boot = (bar_d_boot - bar_d[None, :]) / se_d[None, :]   # (B, |M|)
        T_max_boot = np.nanmax(t_boot, axis=1)                    # (B,)

        p_value = float((T_max_boot >= T_max).mean())

        it = {
            "step": step,
            "survivors_before": list(survivors),
            "bar_d_per_survivor": {s: float(bar_d[i]) for i, s in enumerate(survivors)},
            "t_per_survivor": {s: float(t_stats[i]) for i, s in enumerate(survivors)},
            "T_max": T_max,
            "p_value": p_value,
        }

        if p_value >= alpha:
            it["action"] = "accept MCS (stop)"
            iterations.append(it)
            break

        # Eliminate the worst (argmax t_i)
        worst_idx = int(np.nanargmax(t_stats))
        worst = survivors[worst_idx]
        it["eliminated"] = worst
        it["action"] = "eliminate and continue"
        iterations.append(it)
        survivors = [s for s in survivors if s != worst]

    return {
        "mcs_indices": list(survivors),
        "iterations": iterations,
        "alpha": alpha,
        "n_bootstrap": n_bootstrap,
        "block_length": block_length,
        "N": N,
        "T": T,
    }
