"""Alpha half-life measurement framework.

Ported verbatim from the trader repo root module ``alpha_halflife.py`` (all
functions; the self-test is dropped).

Given a signal series s_t and a forward-return series r_{t,t+tau}, measure
how long a signal retains predictive power by computing:

    IC(tau) = corr(s_t, r_{t,t+tau})   for tau in a grid

Then fit an exponential decay |IC(tau)| = A * exp(-lambda * tau) and derive
half-life = log(2) / lambda.

Use cases:
  - Diagnose rebalance-cadence sensitivity: if half-life is 5 days, trading
    a weekly-rebalanced version throws away ~40% of the IC.
  - Classify signals for execution-policy assignment (Aquilina-Budish-O'Neill
    2022 race-closed regime for sub-30-second half-life signals).
  - Compare signals on a single axis -- signals with longer half-life are
    forgiving to cadence/latency; short half-life demands tighter execution.

References:
  - Grinold & Kahn (2000). "Active Portfolio Management", Chapter 10
    (information horizon / IC decay).
  - Jegadeesh-Titman (1993). Momentum persistence -- positive IC beyond 1 day.
  - Aquilina, Budish & O'Neill (2022 QJE). Latency arbitrage race -- when
    half-life is sub-second, only the winner trades.

Design notes:
  - IC is Pearson by default. Use `spearman=True` for rank-IC (robust to
    outliers, standard in equity factor research).
  - Forward returns are **non-overlapping** by construction -- we take
    sample(t, t+tau) and require tau_step >= tau to avoid overlap. For
    overlapping horizons the IC t-stat is inflated (AFML Ch 4).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# --- IC computation ---------------------------------------------------------

def forward_return(prices: np.ndarray, tau: int,
                   cumulative: bool = False) -> np.ndarray:
    """Log-return at offset tau. NaN-padded at tail.

    cumulative=False (Grinold-Kahn alpha-halflife convention):
        return the single-period return between t+tau-1 and t+tau.
        IC(signal_t, single_ret_{t+tau}) measures how long the signal
        retains information about one specific future bar.

    cumulative=True:
        return log(p_{t+tau}) - log(p_t). IC(signal_t, cum_ret) measures
        the information signal_t has about the *total* PnL of holding
        for tau bars; this is an integral over single-period ICs and
        decays more slowly than Grinold-Kahn half-life.
    """
    p = np.asarray(prices, dtype=np.float64)
    out = np.full_like(p, np.nan)
    if tau < 1 or len(p) <= tau:
        return out
    if cumulative:
        out[:-tau] = np.log(p[tau:]) - np.log(p[:-tau])
    else:
        # single-period return from t+tau-1 to t+tau, attributed to time t
        out[:-tau] = np.log(p[tau:]) - np.log(p[tau - 1:-1])
    return out


def ic_at_horizon(signal: np.ndarray, prices: np.ndarray, tau: int,
                  spearman: bool = False, cumulative: bool = False) -> float:
    """IC = correlation between signal_t and forward-return at horizon tau.

    By default (cumulative=False): IC is vs single-period return at t+tau,
    matching Grinold-Kahn 2000 "alpha half-life" convention.
    """
    s = np.asarray(signal, dtype=np.float64)
    fret = forward_return(np.asarray(prices, dtype=np.float64), tau,
                          cumulative=cumulative)
    mask = np.isfinite(s) & np.isfinite(fret)
    if mask.sum() < 3:
        return float("nan")
    ss = s[mask]
    rr = fret[mask]
    if spearman:
        ss = pd.Series(ss).rank().to_numpy()
        rr = pd.Series(rr).rank().to_numpy()
    sd_s = ss.std(ddof=1)
    sd_r = rr.std(ddof=1)
    if sd_s == 0 or sd_r == 0:
        return float("nan")
    return float(((ss - ss.mean()) * (rr - rr.mean())).sum() / ((len(ss) - 1) * sd_s * sd_r))


def ic_decay_curve(signal: np.ndarray, prices: np.ndarray,
                   taus: list[int], spearman: bool = False,
                   cumulative: bool = False) -> pd.DataFrame:
    """Compute IC at each tau in `taus`. Returns DataFrame [tau, ic, n]."""
    rows = []
    for tau in taus:
        s = np.asarray(signal, dtype=np.float64)
        fret = forward_return(np.asarray(prices, dtype=np.float64), tau, cumulative)
        mask = np.isfinite(s) & np.isfinite(fret)
        rows.append({"tau": tau,
                     "ic": ic_at_horizon(s, prices, tau, spearman, cumulative),
                     "n": int(mask.sum())})
    return pd.DataFrame(rows)


# --- Half-life estimation ---------------------------------------------------

@dataclass
class HalflifeFit:
    A: float           # IC(0) amplitude (extrapolated)
    lam: float         # decay rate
    halflife: float    # log(2) / lam
    r_squared: float   # fit quality
    taus: np.ndarray
    ic_observed: np.ndarray
    ic_predicted: np.ndarray
    policy: str        # 'race', 'short', 'medium', 'long', 'undefined'


def fit_exponential_halflife(tau_grid: np.ndarray,
                             ic_values: np.ndarray) -> HalflifeFit:
    """Fit log|IC(tau)| = log(A) - lam*tau.

    Uses only the positive-IC, tau >= 1 entries (log of negative is undefined
    and tau=0 is boundary). Returns NaN halflife if fit fails.
    """
    tau_grid = np.asarray(tau_grid, dtype=np.float64)
    ic_values = np.asarray(ic_values, dtype=np.float64)
    mask = (tau_grid >= 1) & np.isfinite(ic_values) & (np.abs(ic_values) > 1e-6)
    if mask.sum() < 2:
        return HalflifeFit(A=np.nan, lam=np.nan, halflife=np.nan, r_squared=np.nan,
                           taus=tau_grid, ic_observed=ic_values,
                           ic_predicted=np.full_like(tau_grid, np.nan),
                           policy="undefined")
    t = tau_grid[mask]
    absic = np.abs(ic_values[mask])
    y = np.log(absic)
    X = np.column_stack([np.ones_like(t), -t])
    try:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    except np.linalg.LinAlgError:
        return HalflifeFit(A=np.nan, lam=np.nan, halflife=np.nan, r_squared=np.nan,
                           taus=tau_grid, ic_observed=ic_values,
                           ic_predicted=np.full_like(tau_grid, np.nan),
                           policy="undefined")
    logA, lam = beta[0], beta[1]
    A = float(np.exp(logA))
    lam = float(lam)
    halflife = float(np.log(2) / lam) if lam > 0 else float("nan")

    # R^2 on log-scale
    y_hat = logA - lam * t
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    # Predict IC across full tau_grid
    ic_pred = np.sign(ic_values) * A * np.exp(-lam * tau_grid)
    # Policy classification (half-life here is in bars matching forward-return unit)
    if not np.isfinite(halflife) or halflife <= 0:
        policy = "undefined"
    elif halflife < 1:
        policy = "race"
    elif halflife < 5:
        policy = "short"
    elif halflife < 20:
        policy = "medium"
    else:
        policy = "long"
    return HalflifeFit(A=A, lam=lam, halflife=halflife, r_squared=r2,
                       taus=tau_grid, ic_observed=ic_values, ic_predicted=ic_pred,
                       policy=policy)
