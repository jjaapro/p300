"""Harvey-Liu-Zhu multiple-testing haircut of an observed Sharpe ratio.

Ported from the trader repo root module ``harvey_liu_haircut.py``
(``log_adjustment_factor``, ``inverse_logsf``, ``observed_sharpe_to_pvalue``,
``haircut_triple``); the registry driver / memo / CLI are dropped.  The only
numerical change is the scipy swap: ``scipy.stats.norm.sf / logsf / isf``
are replaced by ``1 - normal_cdf`` (evaluated through ``math.erfc`` so the
far tail does not underflow) and ``normal_ppf`` from ``dsr_pbo``.

Governance number: takes an observed annualized Sharpe and reports the
"haircut" Sharpe after a multiple-testing correction under three
frameworks:
  - Bonferroni (FWER, conservative)
  - Holm       (FWER, stepwise; identical to Bonferroni for the top rank)
  - BHY        (FDR via Benjamini-Yekutieli; economically correct for
                portfolio contexts per HL 2015)

Complements DSR which gives a PROBABILITY that the observed Sharpe exceeds a
threshold after multiple testing.  HL gives a Sharpe-UNIT number -- directly
communicable ("after MT correction at BHY/FDR, Sharpe is X.XX").

References:
  Harvey, C. R., Liu, Y. (2015). "Backtesting." JPM 42(1):13-28.
  Harvey, C. R., Liu, Y., Zhu, H. (2016). "...and the Cross-Section of
    Expected Returns." RFS 29(1):5-68.

Default trials prior: 300 (HL 2015 baseline industry assumption).
"""
from __future__ import annotations

import math

from .dsr_pbo import normal_cdf, normal_ppf

TRADING_DAYS_PER_YEAR = 252
DEFAULT_TRIALS_PRIOR = 300  # HL 2015 industry-baseline assumption


# --- normal-tail helpers (the scipy replacements) -----------------------------

def norm_sf(x: float) -> float:
    """Survival function 1 - normal_cdf(x), via erfc so it does not underflow to 0 until x ~ 38."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def norm_logsf(x: float) -> float:
    """log(1 - normal_cdf(x)); asymptotic expansion once erfc underflows (x > ~38)."""
    sf = norm_sf(x)
    if sf > 0.0:
        return math.log(sf)
    # log_sf(t) ~ -t^2/2 - log(t) - 0.5*log(2*pi) for large t
    return -0.5 * x * x - math.log(x) - 0.5 * math.log(2.0 * math.pi)


def norm_isf(p: float) -> float:
    """Inverse survival function: the t with norm_sf(t) = p.

    Equals ``normal_ppf(1 - p)``; computed as ``-normal_ppf(p)`` (the
    quantile is odd-symmetric) to avoid the ``1 - p`` cancellation for tiny p.
    """
    return -normal_ppf(p)


# --- Core HL haircut functions -------------------------------------------------

def log_adjustment_factor(method: str, m: int) -> float:
    """log of the multiplicative p-value adjustment factor, rank=1 (top)."""
    if method == "bonferroni":
        return math.log(m)
    if method == "holm":
        # Top-ranked Holm == Bonferroni; the stepwise gain is for lower ranks
        return math.log(m)
    if method == "bhy":
        # BHY under arbitrary dependence for rank=1:
        #   p_adj = p * M * c(M)  where c(M) = harmonic number
        c_m = sum(1.0 / k for k in range(1, m + 1))
        return math.log(m * c_m)
    raise ValueError(f"unknown method: {method}")


def inverse_logsf(log_sf: float) -> float:
    """Invert the log-survival function of the standard normal.

    log_sf must be <= 0 (since sf in [0, 1]). Returns the t such that
    norm_logsf(t) = log_sf. Handles the extreme-tail regime (log_sf < -30)
    via an asymptotic approximation where a direct inverse underflows.

    Port fix: the trader original's Newton correction in the asymptotic
    branch had its sign flipped (``t0 - delta / (-t0)``), stepping away
    from the root -- e.g. it returned 9.68 for norm_logsf(9.0) and could hand
    back an adjusted Sharpe above the observed one for daily Sharpes > ~6.
    The step below is the correct ``t0 - delta / t0``.
    """
    if log_sf >= 0:
        return float("-inf")  # survival = 1, t = -inf
    if log_sf > -30:
        # Still representable; direct inverse
        p = math.exp(log_sf)
        return float(norm_isf(p))
    # Asymptotic for very large t: log_sf(t) ~ -t^2/2 - log(t*sqrt(2*pi))
    # First-order: t ~ sqrt(-2 * log_sf)
    # One Newton correction for accuracy:
    t0 = math.sqrt(-2.0 * log_sf)
    # log_sf(t) at t0: -t0^2/2 - log(t0) - 0.5*log(2*pi)
    log_sf_at_t0 = -0.5 * t0 * t0 - math.log(t0) - 0.5 * math.log(2.0 * math.pi)
    delta = log_sf - log_sf_at_t0
    # d(log_sf)/dt ~ -t (for large t): Newton step t1 = t0 - (f(t0) - target) / f'(t0)
    t1 = t0 - delta / t0
    return float(t1)


def observed_sharpe_to_pvalue(sharpe: float, n_obs: int,
                              freq_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    """One-sided p-value under null of zero Sharpe (for display)."""
    t_stat = sharpe * math.sqrt(n_obs / freq_per_year)
    return float(max(norm_sf(t_stat), 0.0))


def haircut_triple(observed_sharpe: float, n_obs: int, n_trials: int,
                   freq_per_year: float = TRADING_DAYS_PER_YEAR) -> dict:
    """Return all three haircut numbers for one observed Sharpe.

    Works in log-p space throughout so extreme tail-significance inputs
    (daily-stream Sharpes of 6+ with 1500+ observations) don't collapse
    to +inf via floating-point underflow.

    Returns {"observed_sharpe", "n_obs", "n_trials", "p_observed", and one
    dict per method ("bonferroni" / "holm" / "bhy") with "p_adj",
    "log_p_adj", "sharpe_adj", "haircut_pct"}.  A method whose adjusted
    p-value reaches 1 floors the adjusted Sharpe at 0.
    """
    t_obs = observed_sharpe * math.sqrt(n_obs / freq_per_year)
    log_sf_obs = float(norm_logsf(t_obs))
    p_obs = float(math.exp(log_sf_obs)) if log_sf_obs > -700 else 0.0

    out = {
        "observed_sharpe": observed_sharpe,
        "n_obs": n_obs,
        "n_trials": n_trials,
        "p_observed": p_obs,
    }
    for method in ("bonferroni", "holm", "bhy"):
        log_adj = log_adjustment_factor(method, n_trials)
        log_sf_adj = log_sf_obs + log_adj
        if log_sf_adj >= 0:
            # p_adj >= 1 after correction -> null not rejected; Sharpe floor to 0
            t_adj = 0.0
            p_adj = 1.0
            sr_adj = 0.0
        else:
            t_adj = inverse_logsf(log_sf_adj)
            p_adj = math.exp(log_sf_adj) if log_sf_adj > -700 else 0.0
            sr_adj = t_adj * math.sqrt(freq_per_year / n_obs)

        haircut_pct = (
            (1.0 - sr_adj / observed_sharpe) * 100.0
            if observed_sharpe != 0 else 0.0
        )
        out[method] = {
            "p_adj": p_adj,
            "log_p_adj": log_sf_adj,
            "sharpe_adj": float(sr_adj),
            "haircut_pct": haircut_pct,
        }
    return out
