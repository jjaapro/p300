"""Hansen's Superior Predictive Ability (SPA) Test.

Ported verbatim from the trader repo root module ``spa_test.py``
(``hac_long_run_std``, ``stationary_bootstrap_indices``, ``hansen_spa``).

Reference: Hansen, P. R. (2005). "A Test for Superior Predictive Ability."
           J. Business & Economic Statistics, 23(4), 365-380.

Tests whether the best strategy in a candidate set is genuinely better
than a benchmark, after accounting for the multiple-testing burden
introduced by selecting the best-in-sample performer.

Null hypothesis (H0): max_i E[L_i] <= 0
  where L_i = loss/gain differential of strategy i vs benchmark.

Decision: reject H0 if p-value < alpha (default 0.05).

This improves on White's Reality Check (2000) by using studentized
statistics (accounting for per-strategy variance) and Hansen's
conservative recentering (which makes the test less conservative
than White's when some strategies are very poor).

Port note (parity kept, not fixed): in the trader original the "l" and "u"
variants both centre every strategy at its sample mean, so ``p_value_l ==
p_value_u`` always and only ``p_value_c`` (Hansen's recommended, consistent
p-value) applies the poor-strategy recentering; the port therefore returns
``p_value_c <= p_value_u == p_value_l``.  Hansen's actual lower bound keeps
negative sample means (``mu_l = min(mu_hat, 0)``) and would give
``p_value_l <= p_value_c``.  Use ``p_value_c`` for decisions.

USAGE:
    from studies.lib.validation.spa import hansen_spa
    result = hansen_spa(
        loss_diffs={"strat1": [...], "strat2": [...], ...},
        n_bootstrap=5000,
        block_size=None,  # auto-selects sqrt(T)
        rng_seed=42,
    )
    # result["p_value_c"] is Hansen's consistent p-value; _l / _u the bounds
"""
from __future__ import annotations

import math
import random
from typing import Sequence


def hac_long_run_std(x: Sequence[float], bandwidth: int | None = None) -> float:
    """Newey-West HAC estimate of LONG-RUN STD DEVIATION of the series
    (not of the mean). Used in Hansen's studentized statistic:
        t_i = sqrt(T) * mean(x) / long_run_std(x)

    bandwidth: if None, use floor(4 * (T/100)^(2/9)) per Newey-West rule.
    """
    n = len(x)
    if n < 2:
        return 1e-12
    mu = sum(x) / n
    if bandwidth is None:
        bandwidth = int(4 * (n / 100) ** (2/9))
    gamma_0 = sum((x[i] - mu) ** 2 for i in range(n)) / n
    long_run_var = gamma_0
    for lag in range(1, bandwidth + 1):
        w = 1.0 - lag / (bandwidth + 1)
        gamma = sum((x[i] - mu) * (x[i - lag] - mu) for i in range(lag, n)) / n
        long_run_var += 2 * w * gamma
    long_run_var = max(long_run_var, 1e-12)
    return math.sqrt(long_run_var)


def stationary_bootstrap_indices(n: int, avg_block: int, rng: random.Random) -> list[int]:
    """Politis-Romano stationary bootstrap: sample indices with geometric block lengths.

    avg_block: mean block length (geometric parameter p = 1/avg_block).
    Returns list of n indices in [0, n).
    """
    p = 1.0 / max(avg_block, 1)
    indices = []
    i = rng.randint(0, n - 1)
    while len(indices) < n:
        indices.append(i)
        if rng.random() < p:
            i = rng.randint(0, n - 1)
        else:
            i = (i + 1) % n
    return indices[:n]


def hansen_spa(
    loss_diffs: dict[str, Sequence[float]],
    n_bootstrap: int = 5000,
    block_size: int | None = None,
    rng_seed: int = 42,
) -> dict:
    """Run Hansen's SPA test.

    loss_diffs: {strategy_id: [daily_gain_vs_benchmark, ...]}.
        All strategies must have same length T (aligned on dates).
        For a returns-based test, gain_vs_benchmark = strat_return - bench_return.

    n_bootstrap: number of bootstrap replicates (default 5000).
    block_size: avg block length for stationary bootstrap. If None, uses
        floor(sqrt(T)) as a reasonable default for daily returns.
    rng_seed: reproducibility.

    Returns dict with:
        observed_spa_c: observed SPA consistent test statistic
        p_value_c: consistent p-value (Hansen's recommended)
        p_value_l: lower p-value (most permissive, analogous to White's)
        p_value_u: upper p-value (most conservative)
        best_strategy: name of strategy with highest mean
        best_mean: mean of best strategy
        n_strategies: count
        n_observations: T
        block_size: block size used
    """
    names = list(loss_diffs.keys())
    L = len(names)
    if L == 0:
        raise ValueError("Empty candidate set.")
    T = len(next(iter(loss_diffs.values())))
    for n, series in loss_diffs.items():
        if len(series) != T:
            raise ValueError(f"Length mismatch: {n} has {len(series)}, expected {T}")
    if T < 30:
        raise ValueError(f"Too few observations (T={T} < 30)")

    if block_size is None:
        block_size = max(2, int(math.sqrt(T)))

    # Per-strategy statistics
    means = {}
    long_run_std = {}
    for name in names:
        x = loss_diffs[name]
        means[name] = sum(x) / T
        long_run_std[name] = hac_long_run_std(x)

    # Hansen's studentized statistic: t_i = sqrt(T) * mean(x_i) / long_run_std(x_i)
    studentized = {
        name: math.sqrt(T) * means[name] / max(long_run_std[name], 1e-12)
        for name in names
    }

    observed_spa_c = max(max(studentized.values()), 0.0)
    best_strategy = max(studentized, key=studentized.get)

    # Hansen's recentering for three variants:
    # u (upper): center all means at 0 -> conservative
    # l (lower): subtract mean from each -> most permissive (White's-like)
    # c (consistent): keep poor strategies (mean below threshold) centered at 0,
    #                 subtract mean from others
    # Hansen's threshold: studentized > -sqrt(2*log(log(T))) keeps the strategy in the pool
    threshold = -math.sqrt(2 * math.log(math.log(T)))  # in studentized units

    centered_u = {name: [x - means[name] for x in loss_diffs[name]] for name in names}
    centered_l = {name: [x - means[name] for x in loss_diffs[name]] for name in names}
    centered_c = {}
    for name in names:
        x = loss_diffs[name]
        if studentized[name] >= threshold:
            # Keep -- subtract mean (center at 0)
            centered_c[name] = [v - means[name] for v in x]
        else:
            # Poor strategy -- recenter to avoid giving it free lift under null
            # (leave uncentered; equivalent to keeping its negative mean)
            centered_c[name] = list(x)

    # Bootstrap
    rng = random.Random(rng_seed)
    count_u = count_l = count_c = 0

    for b in range(n_bootstrap):
        idx = stationary_bootstrap_indices(T, block_size, rng)
        # For u variant: all means centered at 0 -- but we still need max studentized
        b_spa_u = 0.0
        b_spa_l = 0.0
        b_spa_c = 0.0
        for name in names:
            sampled_u = [centered_u[name][i] for i in idx]
            sampled_l = [centered_l[name][i] for i in idx]
            sampled_c = [centered_c[name][i] for i in idx]
            mu_u = sum(sampled_u) / T
            mu_l = sum(sampled_l) / T
            mu_c = sum(sampled_c) / T
            lrs = long_run_std[name]
            s_u = math.sqrt(T) * mu_u / max(lrs, 1e-12)
            s_l = math.sqrt(T) * mu_l / max(lrs, 1e-12)
            s_c = math.sqrt(T) * mu_c / max(lrs, 1e-12)
            b_spa_u = max(b_spa_u, s_u)
            b_spa_l = max(b_spa_l, s_l)
            b_spa_c = max(b_spa_c, s_c)
        b_spa_u = max(b_spa_u, 0.0)
        b_spa_l = max(b_spa_l, 0.0)
        b_spa_c = max(b_spa_c, 0.0)
        if b_spa_u >= observed_spa_c:
            count_u += 1
        if b_spa_l >= observed_spa_c:
            count_l += 1
        if b_spa_c >= observed_spa_c:
            count_c += 1

    return {
        "observed_spa_c": observed_spa_c,
        "p_value_u": count_u / n_bootstrap,  # upper bound (most conservative)
        "p_value_c": count_c / n_bootstrap,  # Hansen's consistent (recommended)
        "p_value_l": count_l / n_bootstrap,  # lower bound (most permissive)
        "best_strategy": best_strategy,
        "best_mean": means[best_strategy],
        "best_long_run_std": long_run_std[best_strategy],
        "best_studentized": studentized[best_strategy],
        "n_strategies": L,
        "n_observations": T,
        "block_size": block_size,
        "all_means": means,
        "all_studentized": studentized,
    }
