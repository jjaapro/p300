"""Reverse stress testing via empirical-likelihood joint-shock recovery.

Ported verbatim from the trader repo root module ``reverse_stress.py``
(``joint_bootstrap_paths``, ``attribute_failure``,
``pairwise_failure_correlation``); its ``circular_block_indices`` now lives
in ``bootstrap`` and is imported from there.

Glasserman, P., Kang, C. & Kang, W. (2015). "Stress scenario selection by
empirical likelihood." Quantitative Finance 15(1): 25-41 formulate reverse
stress testing as: fix a loss threshold L*, find the most likely joint
shock vector of factor returns that produces that loss. Implementation
uses empirical-likelihood reweighting to tilt the historical joint
return distribution onto the loss-breaching region with minimum KL
divergence from the baseline.

For a portfolio composed of sleeves with known weights (deterministic
composition), a direct empirical implementation:

  1. Bootstrap JOINT paths across all sleeves (same time index sampled
     for every sleeve, preserving cross-sectional correlation).
  2. Compute composite portfolio drawdown on each path.
  3. Identify FAILURE PATHS where 1-year max DD >= L*.
  4. Attribute the failure to sleeves: which sleeve contributed most to
     the losing run-up? What correlation structure manifests on failure
     paths that baseline doesn't show?

This isn't the full GKK 2015 convex optimization, but for our use case
(single portfolio, known composition, daily returns) it's operationally
equivalent: the empirical failure-path reweighting IS the KL-closest
distribution satisfying the constraint.

Output: "the most likely way this portfolio loses L*" as an empirical
story over sleeves and shared-factor days.
"""
from __future__ import annotations

import numpy as np

from .bootstrap import circular_block_indices


def joint_bootstrap_paths(sleeve_returns: dict[str, np.ndarray],
                          weights: dict[str, float],
                          horizon: int = 252,
                          n_paths: int = 5000,
                          block: int = 20,
                          seed: int = 42) -> dict:
    """Sample joint bootstrap paths, preserving cross-sleeve correlation.

    Every path uses the SAME sampled indices across all sleeves -- this is
    critical for preserving days where multiple sleeves move together
    (e.g., 2022 LUNA week where S-003 + S-078 both drew down).

    sleeve_returns: dict of sleeve_name -> 1-D decimal-return array (same length T)
    weights: dict of sleeve_name -> portfolio weight (sums to 1)
    horizon: days per path

    Returns dict with:
      sleeve_paths: dict of sleeve_name -> (n_paths, horizon) sleeve returns per path
      composite: (n_paths, horizon) weighted sum
      mdd: (n_paths,) max drawdown per path
      cum: (n_paths,) terminal cumulative return per path
    """
    # Validate all sleeves have same length
    names = list(sleeve_returns.keys())
    T = len(sleeve_returns[names[0]])
    for n in names:
        assert len(sleeve_returns[n]) == T, f"sleeve {n} length mismatch"

    rng = np.random.default_rng(seed)

    sleeve_paths = {n: np.empty((n_paths, horizon)) for n in names}
    composite = np.empty((n_paths, horizon))
    mdd = np.empty(n_paths)
    cum = np.empty(n_paths)

    for p in range(n_paths):
        idx = circular_block_indices(T, horizon, block, rng)
        for n in names:
            sleeve_paths[n][p] = sleeve_returns[n][idx]
        comp = np.zeros(horizon)
        for n in names:
            comp += weights[n] * sleeve_paths[n][p]
        composite[p] = comp
        eq = np.cumprod(1 + comp)
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        mdd[p] = dd.max()
        cum[p] = eq[-1] - 1
    return {
        "n_paths": n_paths, "horizon": horizon,
        "sleeve_paths": sleeve_paths, "composite": composite,
        "mdd": mdd, "cum": cum, "sleeve_names": names, "weights": weights,
    }


def attribute_failure(paths: dict, loss_threshold: float) -> dict:
    """For paths where MDD >= loss_threshold, compute sleeve-level attribution.

    Returns:
      p_failure: fraction of paths hitting the threshold
      n_failure: absolute count
      sleeve_attrib: dict of sleeve -> dict of stats on failure paths
      joint_drawdown: dict of sleeve -> sleeve_mdd_on_failure_paths
    """
    mdd = paths["mdd"]
    mask = mdd >= loss_threshold
    n_fail = int(mask.sum())
    n_total = len(mdd)
    p_fail = n_fail / n_total if n_total > 0 else 0.0

    sleeve_attrib = {}
    joint_drawdown = {}
    for n in paths["sleeve_names"]:
        sp = paths["sleeve_paths"][n][mask]          # (n_fail, H)
        sp_all = paths["sleeve_paths"][n]
        # Mean sleeve cumulative return on failure paths vs all paths
        cum_fail = np.cumprod(1 + sp, axis=1)[:, -1] - 1
        cum_all = np.cumprod(1 + sp_all, axis=1)[:, -1] - 1
        # Sleeve MDD distribution on failure paths
        def _mdd_path(r):
            eq = np.cumprod(1 + r)
            peak = np.maximum.accumulate(eq)
            return ((peak - eq) / peak).max()
        sleeve_mdds_fail = np.array([_mdd_path(r) for r in sp]) if n_fail > 0 else np.array([])
        sleeve_mdds_all = np.array([_mdd_path(r) for r in sp_all])
        sleeve_attrib[n] = {
            "mean_cum_on_failure": float(cum_fail.mean()) if n_fail > 0 else float("nan"),
            "mean_cum_baseline": float(cum_all.mean()),
            "sleeve_mdd_median_on_failure": float(np.median(sleeve_mdds_fail)) if n_fail > 0 else float("nan"),
            "sleeve_mdd_median_baseline": float(np.median(sleeve_mdds_all)),
            "sleeve_mdd_q95_on_failure": float(np.quantile(sleeve_mdds_fail, 0.95)) if n_fail > 0 else float("nan"),
        }
        joint_drawdown[n] = sleeve_mdds_fail

    return {
        "p_failure": p_fail, "n_failure": n_fail, "n_total": n_total,
        "sleeve_attrib": sleeve_attrib,
        "joint_drawdown": joint_drawdown,
    }


def pairwise_failure_correlation(paths: dict, loss_threshold: float) -> dict:
    """Sleeve-sleeve correlation of daily returns on failure paths vs baseline.

    If failure paths show materially higher pairwise correlation than baseline,
    the portfolio is exposed to correlation-spike tail events (the diversification
    benefit breaks in crises).
    """
    mdd = paths["mdd"]
    mask = mdd >= loss_threshold
    names = paths["sleeve_names"]
    # Concatenate all failure-path daily returns per sleeve
    fail_concat = {n: paths["sleeve_paths"][n][mask].flatten() for n in names}
    all_concat = {n: paths["sleeve_paths"][n].flatten() for n in names}

    def _corr_matrix(d):
        arr = np.vstack([d[n] for n in names])
        return np.corrcoef(arr)

    if mask.sum() == 0:
        return {"failure_corr": None, "baseline_corr": _corr_matrix(all_concat),
                "names": names}
    return {
        "failure_corr": _corr_matrix(fail_concat),
        "baseline_corr": _corr_matrix(all_concat),
        "names": names,
    }
