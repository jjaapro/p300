"""Adams-MacKay (2007) Bayesian Online Changepoint Detection.

Ported verbatim from the trader repo root module ``changepoint_detector.py``
(``BocpdState``, ``init_state``, ``bocpd_step``, ``extract_features``,
``run_series`` and the constants); the ``_test_*`` functions, the crisis
demo and ``main`` are dropped.

Single-file, dependency-light (numpy only). Plain functions + one small
dataclass for state.

Model: Gaussian observations with unknown mean + variance, Normal-Inverse-
Gamma conjugate prior. Constant-hazard prior on changepoint location.

Hard rules (enforced by structure, not trust):
  1. bocpd_step(x_t, state) mutates state using ONLY state produced from
     x_0..x_{t-1}. There is no indexing into a future array -- the function
     does not know the future exists.
  2. Features are extracted from state AFTER bocpd_step has absorbed x_t,
     which means features at time t reflect all info through close-of-t
     (same semantics as daily aggregate features).
  3. Truncating prefix inputs and re-running must produce bit-identical
     posterior for the overlapping tail (tested in tests/test_validation_misc.py).

References:
  Adams, R. P. & MacKay, D. J. C. (2007). "Bayesian Online Changepoint
    Detection." arXiv:0710.3742.
  Wood, Roberts, Zohren (2022). "Slow Momentum with Fast Reversion." JFDS.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np


# --- Defaults ------------------------------------------------------------------

DEFAULT_HAZARD = 1.0 / 250.0
DEFAULT_PRIOR = {"mu0": 0.0, "kappa0": 0.01, "alpha0": 1.0, "beta0": 0.01}
DEFAULT_MAX_RUN = 1000   # truncate run-length posterior past this bound
WARMUP_BARS = 500        # features before this are NaN


# --- State ---------------------------------------------------------------------

@dataclass
class BocpdState:
    """Online BOCPD state. Arrays are length R where R = current run-length
    support size, bounded above by max_run. Index 0 always = fresh-run prior.
    """
    mu: np.ndarray
    kappa: np.ndarray
    alpha: np.ndarray
    beta: np.ndarray
    R: np.ndarray
    hazard: float
    max_run: int
    prior_mu0: float
    prior_kappa0: float
    prior_alpha0: float
    prior_beta0: float
    t: int


def init_state(hazard: float = DEFAULT_HAZARD, prior: dict[str, float] | None = None,
               max_run: int = DEFAULT_MAX_RUN) -> BocpdState:
    p = dict(DEFAULT_PRIOR if prior is None else prior)
    return BocpdState(
        mu=np.array([p["mu0"]], dtype=np.float64),
        kappa=np.array([p["kappa0"]], dtype=np.float64),
        alpha=np.array([p["alpha0"]], dtype=np.float64),
        beta=np.array([p["beta0"]], dtype=np.float64),
        R=np.array([1.0], dtype=np.float64),
        hazard=float(hazard),
        max_run=int(max_run),
        prior_mu0=float(p["mu0"]),
        prior_kappa0=float(p["kappa0"]),
        prior_alpha0=float(p["alpha0"]),
        prior_beta0=float(p["beta0"]),
        t=0,
    )


# --- Student-t predictive log-pdf (vectorized over run lengths) ----------------

def _student_t_logpdf(x, df, loc, scale):
    z = (x - loc) / scale
    lg = np.array([math.lgamma((d + 1.0) / 2.0) - math.lgamma(d / 2.0) for d in df])
    return lg - 0.5 * np.log(df * math.pi * scale * scale) - ((df + 1.0) / 2.0) * np.log1p(z * z / df)


# --- One online step -----------------------------------------------------------

def bocpd_step(x: float, state: BocpdState) -> None:
    """Absorb one observation x into `state`. Mutates state in place.
    x must be finite; NaN observations should be filtered by the caller."""
    if not math.isfinite(x):
        raise ValueError(f"BOCPD observed non-finite x at t={state.t}")

    # Posterior-predictive Student-t scale^2 = beta * (kappa+1) / (alpha*kappa)
    scale2 = state.beta * (state.kappa + 1.0) / (state.alpha * state.kappa)
    scale = np.sqrt(scale2)
    df = 2.0 * state.alpha
    log_pi = _student_t_logpdf(x, df, state.mu, scale)
    # Numerically stable: subtract max log before exp; absolute scale cancels
    # in the subsequent normalization step.
    pi = np.exp(log_pi - log_pi.max())

    growth = state.R * pi * (1.0 - state.hazard)
    cp = (state.R * pi * state.hazard).sum()
    new_R = np.concatenate(([cp], growth))
    Z = new_R.sum()
    if Z > 0 and math.isfinite(Z):
        new_R /= Z
    else:
        new_R = np.zeros_like(new_R)
        new_R[0] = 1.0

    # Update sufficient statistics: index 0 is fresh-run prior; index r+1 is
    # posterior of old run-length-r absorbing x.
    new_mu    = np.concatenate(([state.prior_mu0],    (state.kappa * state.mu + x) / (state.kappa + 1.0)))
    new_kappa = np.concatenate(([state.prior_kappa0], state.kappa + 1.0))
    new_alpha = np.concatenate(([state.prior_alpha0], state.alpha + 0.5))
    new_beta  = np.concatenate(([state.prior_beta0],  state.beta + state.kappa * (x - state.mu) ** 2 / (2.0 * (state.kappa + 1.0))))

    if new_R.size > state.max_run:
        new_R = new_R[: state.max_run]
        s = new_R.sum()
        if s > 0:
            new_R /= s
        new_mu = new_mu[: state.max_run]
        new_kappa = new_kappa[: state.max_run]
        new_alpha = new_alpha[: state.max_run]
        new_beta = new_beta[: state.max_run]

    state.R = new_R
    state.mu = new_mu
    state.kappa = new_kappa
    state.alpha = new_alpha
    state.beta = new_beta
    state.t += 1


# --- Feature extraction from current state ------------------------------------

def extract_features(state: BocpdState) -> dict[str, float]:
    """Six scalars from the run-length posterior.

    Design note: in standard constant-hazard Adams-MacKay, R[0] after
    normalization equals the hazard rate regardless of the data -- it is the
    prior P(CP), not a posterior. The data-dependent CP signal lives in the
    SHAPE of R: if a CP happened recently, mass concentrates at small r. So
    we report posterior mass on short run-lengths (cp_prob_short_5 /
    cp_prob_short_20) instead of R[0]. This matches the Wood-Roberts-Zohren
    usage of "run-length posterior mode / mean" as the CP indicator.
    """
    R = state.R
    if R.size == 0 or state.t <= WARMUP_BARS:
        nan = float("nan")
        return dict(run_mean=nan, run_mode=nan, cp_prob_short_5=nan,
                    cp_prob_short_20=nan, severity=nan, entropy=nan)
    lengths = np.arange(R.size, dtype=np.float64)
    run_mean = float((lengths * R).sum())
    run_mode = int(R.argmax())
    # Posterior P(run length < k) -- indicates a recent CP.
    cp_prob_short_5 = float(R[: min(5, R.size)].sum())
    cp_prob_short_20 = float(R[: min(20, R.size)].sum())
    ent_ps = R[R > 0]
    entropy = float(-(ent_ps * np.log(ent_ps)).sum())
    # Severity: |posterior mean at the modal run-length minus prior mean|.
    # How far the most-likely run's Gaussian has moved from the prior.
    severity = float(abs(state.mu[run_mode] - state.prior_mu0))
    return dict(
        run_mean=run_mean,
        run_mode=float(run_mode),
        cp_prob_short_5=cp_prob_short_5,
        cp_prob_short_20=cp_prob_short_20,
        severity=severity,
        entropy=entropy,
    )


# --- Convenience: batch-run over a series (still causal) -----------------------

def run_series(xs: Iterable[Any], hazard: float = DEFAULT_HAZARD,
               prior: dict[str, float] | None = None,
               max_run: int = DEFAULT_MAX_RUN) -> list[dict[str, float]]:
    """Run BOCPD over a sequence of observations. Returns per-bar feature dicts.
    Causal by construction: each step only sees xs[:t+1].
    NaN in xs is passed through (feature row becomes all NaN, state untouched)."""
    state = init_state(hazard=hazard, prior=prior, max_run=max_run)
    rows = []
    nan_row = dict(run_mean=float("nan"), run_mode=float("nan"),
                   cp_prob_short_5=float("nan"), cp_prob_short_20=float("nan"),
                   severity=float("nan"), entropy=float("nan"))
    for x in xs:
        if x is None or not math.isfinite(x):
            rows.append(dict(nan_row))
            continue
        bocpd_step(float(x), state)
        rows.append(extract_features(state))
    return rows
