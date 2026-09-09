"""Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO).

Ported from the trader repo root modules ``deflate.py`` (``normal_cdf``,
``normal_ppf``, ``expected_max_sharpe``, ``deflated_sharpe``, ``cscv_pbo``)
and ``dsr_pbo_wednesday.py`` (``deflated_sharpe(rs, n_trials)`` ->
``dsr_from_returns``; ``pbo_cscv``).  Hand-rolled normal CDF / quantile
(Acklam's rational approximation, ~1e-9 relative error) -- no scipy.

Papers
------
* Bailey, D. H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
  Journal of Portfolio Management 40(5): 94-107.
* Bailey, Borwein, Lopez de Prado & Zhu (2015). "The Probability of Backtest
  Overfitting." Journal of Computational Finance 20(4): 39-69 (CSCV).

Units: the DSR works in *per-observation* Sharpe units with ``n_obs``
observations.  Do not pass an annualised Sharpe.

Kurtosis convention: ``deflated_sharpe`` takes the FULL (non-excess)
kurtosis -- 3 for a Gaussian -- matching the paper's
``sqrt(1 - g3*SR + (g4-1)/4 * SR^2)`` denominator and
``dsr_pbo_wednesday.py``.  ``deflate.py`` fed *excess* kurtosis into that
slot (effectively ``(g4-3)/4``); the two trader originals disagreed and this
port follows the paper.  If you hold excess kurtosis (e.g. from
``metrics.moments``) add 3 before calling.
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Sequence

import numpy as np

# --- Stats utilities (no scipy) -----------------------------------------------


def normal_cdf(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def normal_ppf(p: float) -> float:
    """Standard normal inverse CDF (Beasley-Springer-Moro approximation).
    Accurate to ~7 digits in the body, ~4 digits in the tails."""
    if p <= 0.0 or p >= 1.0:
        return float("inf") if p >= 1.0 else float("-inf")

    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]

    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# --- Deflated Sharpe Ratio ----------------------------------------------------

EULER = 0.5772156649015329  # Euler-Mascheroni


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Bailey & Lopez de Prado (2014) expected maximum Sharpe under H0:
        E[max SR] = sqrt(Var(SRs)) * ((1 - gamma) * Phi^-1(1 - 1/N)
                                      + gamma * Phi^-1(1 - 1/(N*e)))
    Returns 0.0 for fewer than two trials or non-positive variance.
    """
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    sd_sr = math.sqrt(sr_variance)
    a = normal_ppf(1.0 - 1.0 / n_trials)
    b = normal_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd_sr * ((1.0 - EULER) * a + EULER * b)


def _dsr_z(observed_sr: float, n_trials: int, n_obs: int,
           skew: float, kurt: float,
           sr_variance: float | None) -> float | None:
    """DSR test statistic, or None when the inputs are degenerate."""
    if n_obs < 2:
        return None
    if sr_variance is None:
        # One-trial iid variance of the Sharpe estimator under a zero-SR null.
        sr_variance = 1.0 / (n_obs - 1)
    sr_null = expected_max_sharpe(n_trials, sr_variance)
    radicand = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr ** 2
    if radicand <= 0:
        return None
    return (observed_sr - sr_null) * math.sqrt(n_obs - 1) / math.sqrt(radicand)


def deflated_sharpe_z(observed_sr: float, n_trials: int, n_obs: int,
                      skew: float, kurt: float,
                      sr_variance: float | None = None) -> float:
    """The DSR z-statistic ``(SR - SR0) * sqrt(n_obs - 1) / sqrt(1 - g3*SR + (g4-1)/4*SR^2)``.

    Same arguments as ``deflated_sharpe``; returns 0.0 for degenerate inputs.
    """
    z = _dsr_z(observed_sr, n_trials, n_obs, skew, kurt, sr_variance)
    return 0.0 if z is None else z


def deflated_sharpe(observed_sr: float, n_trials: int, n_obs: int,
                    skew: float, kurt: float,
                    sr_variance: float | None = None) -> float:
    """Deflated Sharpe Ratio: P(true SR > 0 | best of ``n_trials`` trials).

    Parameters
    ----------
    observed_sr : per-observation Sharpe of the selected trial (NOT annualised).
    n_trials    : number of trials the selection was made from (1 = no search).
    n_obs       : observations behind ``observed_sr``.
    skew, kurt  : sample skewness and FULL kurtosis (3 = Gaussian) of the
                  selected trial's returns.
    sr_variance : variance of the Sharpe estimates across trials (the paper's
                  V[SR_n]).  None -> the iid one-trial value 1/(n_obs - 1).

    DSR > 0.95 rejects the null (real edge).  Degenerate inputs (n_obs < 2,
    non-positive radicand) return 0.5, as ``deflate.py`` did.
    """
    z = _dsr_z(observed_sr, n_trials, n_obs, skew, kurt, sr_variance)
    return 0.5 if z is None else normal_cdf(z)


def _sample_moments_full(rs: Sequence[float]) -> tuple[float, float, float, float, int]:
    """(mean, sd, skew, full kurtosis, n) with sample (n-1) sd -- dsr_pbo_wednesday convention."""
    n = len(rs)
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return mean, 0.0, 0.0, 0.0, n
    m3 = sum((r - mean) ** 3 for r in rs) / n
    m4 = sum((r - mean) ** 4 for r in rs) / n
    skew = m3 / (sd ** 3)
    kurt = m4 / (sd ** 4)  # full kurtosis (3 = normal)
    return mean, sd, skew, kurt, n


def dsr_from_returns(returns: Any, n_trials: int,
                     periods_per_year: float | None = None,
                     sr_variance: float | None = None) -> dict | None:
    """Bailey & Lopez de Prado DSR straight from a return series.

    Computes the per-observation Sharpe and its sample moments inside (the
    ``dsr_pbo_wednesday.deflated_sharpe(rs, n_trials)`` convenience form).
    Non-finite entries are dropped.  ``periods_per_year`` only adds the
    annualised ``sr_ann`` / ``sr_expected_ann`` fields for reporting.

    Returns a dict (``dsr`` is the probability, ``dsr_z`` the statistic), or
    None when fewer than two finite observations, zero spread, or a
    non-positive denominator make the statistic undefined.
    """
    rs = [float(v) for v in np.asarray(returns, dtype=np.float64).ravel()
          if math.isfinite(float(v))]
    if len(rs) < 2:
        return None
    mean, sd, skew, kurt, n = _sample_moments_full(rs)
    if sd == 0:
        return None
    sr = mean / sd
    if sr_variance is None:
        sr_variance = 1.0 / (n - 1)
    sr_expected = expected_max_sharpe(n_trials, sr_variance)
    radicand = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if radicand <= 0:
        return None
    dsr_z = (sr - sr_expected) * math.sqrt(n - 1) / math.sqrt(radicand)
    out = dict(sr_per_obs=sr, sr_expected=sr_expected, sr_variance=sr_variance,
               skew=skew, kurt=kurt, n=n, n_trials=n_trials,
               dsr=normal_cdf(dsr_z), dsr_z=dsr_z)
    if periods_per_year:
        k = math.sqrt(periods_per_year)
        out["sr_ann"] = sr * k
        out["sr_expected_ann"] = sr_expected * k
    return out


# --- PBO via CSCV (Bailey/Borwein/Lopez de Prado/Zhu) -------------------------

def cscv_pbo(returns_matrix: Sequence[Sequence[float]], s: int = 10) -> tuple[float, list[float]]:
    """Compute Probability of Backtest Overfitting via CSCV.

    returns_matrix: rows = time bins (T), cols = trials (N).
    s: number of equal-sized time bins to split (must be even). Total combos = C(s, s/2).

    Returns (PBO, list_of_logits).

    Approach:
      1. Split T rows into s equal blocks.
      2. For each combination C of s/2 blocks (the "in-sample"):
           - The other s/2 blocks are "out-of-sample".
           - Score each trial in-sample by its mean return; pick the best (n*).
           - Find the OOS rank of n*: r = rank / (N+1).
           - Logit: w = log(r / (1 - r)).
      3. PBO = fraction of combinations with w <= 0 (i.e., the in-sample winner
         underperformed the OOS median).
    """
    T = len(returns_matrix)
    if T < s:
        return 0.5, []
    N = len(returns_matrix[0])
    if N < 2:
        return 0.5, []

    # Truncate to multiple of s
    T_eff = (T // s) * s
    block_size = T_eff // s
    blocks = [list(range(i * block_size, (i + 1) * block_size)) for i in range(s)]

    logits = []
    half = s // 2
    for in_blocks in combinations(range(s), half):
        in_set = set(in_blocks)
        out_blocks = [i for i in range(s) if i not in in_set]
        # In-sample mean per trial
        in_means = [0.0] * N
        in_count = 0
        for bi in in_blocks:
            for ti in blocks[bi]:
                row = returns_matrix[ti]
                for j in range(N):
                    in_means[j] += row[j]
                in_count += 1
        in_means = [m / in_count for m in in_means]
        best = max(range(N), key=lambda j: in_means[j])

        # OOS mean per trial
        out_means = [0.0] * N
        out_count = 0
        for bi in out_blocks:
            for ti in blocks[bi]:
                row = returns_matrix[ti]
                for j in range(N):
                    out_means[j] += row[j]
                out_count += 1
        out_means = [m / out_count for m in out_means]

        # OOS rank of the in-sample winner
        sorted_oos = sorted(out_means)
        # rank: 1..N (1 = worst). Find where best's OOS mean lands.
        rank = 1
        for v in sorted_oos:
            if v < out_means[best]:
                rank += 1
        rel_rank = rank / (N + 1)
        # Avoid log(0)
        rel_rank = max(min(rel_rank, 1.0 - 1e-9), 1e-9)
        w = math.log(rel_rank / (1.0 - rel_rank))
        logits.append(w)

    pbo = sum(1 for w in logits if w <= 0) / len(logits)
    return pbo, logits


def pbo_from_trial_matrix(returns_matrix: Any, s: int = 10) -> tuple[float, list[float]]:
    """CSCV PBO for a trial-major matrix: rows = trials, cols = periods.

    Transposes to the time-major layout ``cscv_pbo`` expects and returns
    ``(pbo, logits)``.  ``s`` must be even; C(s, s/2) combinations are
    evaluated (s=10 -> 252, s=16 -> 12870).
    """
    m = np.asarray(returns_matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError(f"returns_matrix must be 2-D (trials x periods), got shape {m.shape}")
    if s < 2 or s % 2:
        raise ValueError(f"s must be a positive even integer, got {s}")
    return cscv_pbo(m.T.tolist(), s=s)


def pbo_cscv(seq: Sequence[tuple[int, float]], s: int = 16) -> tuple[float, int]:
    """Combinatorially-Symmetric Cross-Validation for PBO.
    seq: list of (dow, return) tuples in chronological order.
    Strategy = pick the best day-of-week on the train half, score it on the test half.
    Returns PBO and number of folds.
    """
    n = len(seq)
    block_size = n // s
    blocks = [seq[i*block_size:(i+1)*block_size] for i in range(s)]

    def sharpe_per_dow(block_set):
        """Mean return per dow from the union of blocks. Returns dict dow->sharpe."""
        agg = {d: [] for d in range(7)}
        for blk in block_set:
            for dow, r in blk:
                agg[dow].append(r)
        out = {}
        for d, rs in agg.items():
            if len(rs) < 5:
                out[d] = float('-inf')
                continue
            mean = sum(rs)/len(rs)
            var = sum((r-mean)**2 for r in rs)/(len(rs)-1)
            sd = math.sqrt(var) if var>0 else 1
            out[d] = mean/sd if sd>0 else 0
        return out

    n_folds = 0
    n_overfit = 0
    half = s // 2
    for train_idx in combinations(range(s), half):
        train_set_idx = set(train_idx)
        test_set_idx = set(range(s)) - train_set_idx
        train_blocks = [blocks[i] for i in train_set_idx]
        test_blocks  = [blocks[i] for i in test_set_idx]

        train_sr = sharpe_per_dow(train_blocks)
        test_sr  = sharpe_per_dow(test_blocks)

        # Pick best dow on train
        best_dow = max(train_sr, key=lambda d: train_sr[d])
        # Get its rank on test (lower rank = worse)
        sorted_test = sorted(test_sr.items(), key=lambda kv: kv[1])
        rank_of_best = [d for d,_ in sorted_test].index(best_dow) + 1
        median_rank = (len(sorted_test)+1)/2
        if rank_of_best < median_rank:
            n_overfit += 1
        n_folds += 1

    return n_overfit / n_folds, n_folds
