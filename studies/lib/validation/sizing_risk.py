"""Risk-based sizing primitives -- CVaR, CDaR, Grossman-Zhou, Vince f.

Ported verbatim from the trader repo root module ``sizing_risk.py`` (all
functions; the self-test is dropped).

Implements:
  - CVaR_alpha (Rockafellar-Uryasev 2000): coherent tail-loss measure
  - CDaR_alpha (Chekhlov-Uryasev-Zabarankin 2005): conditional drawdown-at-risk
  - Grossman-Zhou 1993: DD floor (never below floor_pct of rolling HWM)
  - Vince optimal-f: TWR-maximizing leverage from historical PnL

Sizing is applied as an OUTER multiplier on top of already-validated sleeves
-- not as a rewrite of an existing vol-target service.

Empirical estimators throughout -- no CVXPY scenario-LP dependency. The
Rockafellar-Uryasev LP formulation is the full portfolio-optimization
version; the empirical form below is equivalent for a single-strategy
return stream.

References:
  - Rockafellar, R.T. & Uryasev, S. (2000). "Optimization of Conditional
    Value-at-Risk." J. Risk 2(3): 21-42.
  - Chekhlov, A., Uryasev, S. & Zabarankin, M. (2005). "Drawdown
    Measure in Portfolio Optimization." IJTAF 8(1): 13-58.
  - Grossman, S. & Zhou, Z. (1993). "Optimal investment strategies for
    controlling drawdowns." Math. Finance 3(3): 241-276.
  - Vince, R. (1990). "Portfolio Management Formulas."
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# --- CVaR (Rockafellar-Uryasev 2000) -----------------------------------------

def cvar_empirical(returns: np.ndarray, alpha: float = 0.05) -> float:
    """Empirical CVaR (expected shortfall) at level alpha.

    Returns a POSITIVE number representing expected loss in the worst
    alpha-tail. alpha=0.05 means "average of the worst 5% daily returns".
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) == 0:
        return 0.0
    losses = -r
    k = max(1, int(np.ceil(alpha * len(losses))))
    worst = np.sort(losses)[-k:]
    return float(worst.mean())


def cvar_leverage_cap(returns: np.ndarray,
                      budget_cvar: float,
                      alpha: float = 0.05) -> float:
    """Max leverage k such that empirical CVaR(k * r) <= budget.

    Uses positive homogeneity of CVaR: CVaR(k*r) = k*CVaR(r). If historical
    CVaR is <= budget, leverage is uncapped (returns infinity-like cap).
    """
    cv = cvar_empirical(returns, alpha)
    if cv <= 0:
        return float("inf")
    return budget_cvar / cv


# --- CDaR (Chekhlov-Uryasev-Zabarankin 2005) ---------------------------------

def drawdown_series(returns: np.ndarray) -> np.ndarray:
    """Running drawdown as a POSITIVE series: D_t = HWM_t - equity_t.

    Assumes simple-additive equity (cum sum of returns). For decimal simple
    returns with compounding, pre-transform to log-returns.
    """
    r = np.asarray(returns, dtype=np.float64)
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    return peak - eq


def cdar_empirical(returns: np.ndarray, alpha: float = 0.10) -> float:
    """Empirical CDaR at tail probability alpha.

    Expected drawdown conditional on being in the worst alpha tail of the
    drawdown series. alpha=0.10 means "average of the worst 10% of DD days".
    """
    dd = drawdown_series(returns)
    if len(dd) == 0:
        return 0.0
    k = max(1, int(np.ceil(alpha * len(dd))))
    worst = np.sort(dd)[-k:]
    return float(worst.mean())


def cdar_leverage_cap(returns: np.ndarray,
                      budget_cdar: float,
                      alpha: float = 0.10) -> float:
    """Max leverage k such that empirical CDaR(k * r) <= budget.

    Uses positive homogeneity of CDaR w.r.t. scale of returns.
    """
    cd = cdar_empirical(returns, alpha)
    if cd <= 0:
        return float("inf")
    return budget_cdar / cd


# --- Grossman-Zhou 1993 DD floor ---------------------------------------------

def grossman_zhou_cap(current_equity: float,
                      rolling_hwm: float,
                      floor_pct: float = 0.20,
                      sigma_estimate: float = 0.02,
                      horizon_days: int = 5,
                      z_tail: float = 2.33) -> float:
    """Max leverage to keep equity >= (1 - floor_pct) * rolling_hwm with
    z_tail-sigma confidence over `horizon_days`.

    Heuristic (not the full optimal control of Grossman-Zhou):
      we allow a z_tail-sigma move against us over horizon_days. Require:
        current_equity - k * sigma * sqrt(horizon) * z_tail * equity
            >= (1 - floor_pct) * rolling_hwm

    Solving for k. If already below the floor, returns 0 (force de-risk).
    If no daylight, returns 0.
    """
    if rolling_hwm <= 0 or current_equity <= 0:
        return 0.0
    floor_level = (1.0 - floor_pct) * rolling_hwm
    daylight = current_equity - floor_level
    if daylight <= 0:
        return 0.0
    worst_move_per_unit_leverage = sigma_estimate * np.sqrt(horizon_days) * z_tail * current_equity
    if worst_move_per_unit_leverage <= 0:
        return float("inf")
    return daylight / worst_move_per_unit_leverage


# --- Vince optimal-f (and f/2 cap) -------------------------------------------

def vince_optimal_f(returns: np.ndarray, f_grid: np.ndarray | None = None) -> float:
    """Empirical optimal-f that maximizes terminal wealth relative (TWR).

    For each candidate f in grid, compute TWR = prod(1 + f * r). Return
    argmax. Assumes r already in decimal units and represents *per-bet*
    return (NOT leveraged -- we're finding the right leverage).

    Classical Vince cap to apply: f_safe = f_star / 2 (half-Kelly equivalent).
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) == 0:
        return 0.0
    if f_grid is None:
        f_grid = np.linspace(0.0, 5.0, 501)  # 0 to 5x leverage in 0.01 steps
    min_r = r.min()
    if min_r <= -1:
        # Bankruptcy-risk f is 1 / |worst loss|
        cap = 1.0 / abs(min_r) * 0.99
        f_grid = f_grid[f_grid < cap]
        if len(f_grid) == 0:
            return 0.0
    twrs = []
    for f in f_grid:
        ret_series = 1.0 + f * r
        if np.any(ret_series <= 0):
            twrs.append(0.0)
        else:
            twrs.append(float(np.prod(ret_series)))
    twrs = np.array(twrs)
    best_idx = int(np.argmax(twrs))
    return float(f_grid[best_idx])


# --- Composite sizing (take min of all caps) ---------------------------------

@dataclass
class SizingCaps:
    cvar_k: float
    cdar_k: float
    grossman_zhou_k: float
    vince_half_f: float

    @property
    def composite(self) -> float:
        """Take min of all caps and floor at 0."""
        return max(0.0, min(self.cvar_k, self.cdar_k,
                            self.grossman_zhou_k, self.vince_half_f))


def compute_day_caps(hist_returns: np.ndarray,
                     current_equity: float,
                     rolling_hwm: float,
                     sigma_estimate: float,
                     budget_cvar: float = 0.06,
                     budget_cdar: float = 0.12,
                     alpha_cvar: float = 0.05,
                     alpha_cdar: float = 0.10,
                     gz_floor_pct: float = 0.20,
                     gz_horizon: int = 5,
                     max_k: float = 2.0) -> SizingCaps:
    """Compute all four caps for one day's sizing decision.

    Defaults are conservative first-pass values; a probe sweeps these to
    find the Pareto-efficient setting.
    """
    k_cv = min(max_k, cvar_leverage_cap(hist_returns, budget_cvar, alpha_cvar))
    k_cd = min(max_k, cdar_leverage_cap(hist_returns, budget_cdar, alpha_cdar))
    k_gz = min(max_k, grossman_zhou_cap(current_equity, rolling_hwm,
                                        floor_pct=gz_floor_pct,
                                        sigma_estimate=sigma_estimate,
                                        horizon_days=gz_horizon))
    f_star = vince_optimal_f(hist_returns)
    k_v = min(max_k, f_star / 2.0)
    return SizingCaps(k_cv, k_cd, k_gz, k_v)
