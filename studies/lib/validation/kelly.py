"""Kelly and risk-constrained Kelly sizing.

Ported verbatim from the trader repo root module ``kelly_sizing.py`` (all
pure functions including ``per_sleeve_rc_kelly``; the self-test is dropped).

Implements:
  - Classic Kelly for a single-strategy return stream: f* = mu/sigma^2
    (equivalent to maximizing E[log(1 + f*r)] for small returns)
  - Fractional-Kelly (half, quarter, arbitrary frac)
  - Risk-constrained Kelly (Busseti-Ryu-Boyd 2016 *J. Investing* 25:118-134):
    highest leverage k such that P(DD > dd_threshold over horizon) <= dd_prob
  - Monte Carlo DD distribution via circular block bootstrap

The BRB 2016 paper formulates this as a convex program with a log-sum-exp
DD-probability constraint solvable via CVXPY. For a single-strategy sizing
problem with empirical DD, an MC + bisection implementation is equivalent
and more transparent. Both approaches deliver the same leverage answer to
within bootstrap noise.

References:
  - Kelly, J. L. (1956). "A New Interpretation of Information Rate."
    Bell Sys. Tech. J. 35: 917-926.
  - Busseti, E., Ryu, E. K. & Boyd, S. (2016). "Risk-Constrained Kelly
    Gambling." J. Investing 25(3): 118-134.
  - Thorp, E. O. (2006). "The Kelly Criterion in Blackjack Sports Betting
    and the Stock Market."
"""
from __future__ import annotations

import numpy as np


# --- Kelly formulas ---------------------------------------------------------

def kelly_full(returns: np.ndarray) -> float:
    """Classic Kelly for a continuous-returns stream.

    f* = mu / sigma^2  (unitless multiplier on the return series)

    Uses the log-wealth-growth-rate approximation. For discrete decimal
    returns with non-infinitesimal magnitudes, see ``kelly_exact_log``
    which solves exactly via numerical log-sum optimization.
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 2:
        return 0.0
    mu = float(r.mean())
    var = float(r.var(ddof=1))
    if var <= 0:
        return 0.0
    return mu / var


def kelly_fractional(returns: np.ndarray, frac: float = 0.5) -> float:
    """Fractional Kelly. frac=0.5 is half-Kelly; frac=0.25 is quarter-Kelly."""
    return frac * kelly_full(returns)


def kelly_exact_log(returns: np.ndarray, k_grid: np.ndarray | None = None) -> float:
    """Empirical log-wealth-maximizing leverage (no Gaussian assumption).

    Iterates leverage k over a grid, picks argmax of sum(log(1 + k*r)).
    Protects against bankruptcy (1 + k*r <= 0) by skipping those k values.
    """
    r = np.asarray(returns, dtype=np.float64)
    if len(r) == 0:
        return 0.0
    if k_grid is None:
        # Cap at f-bankruptcy / 2
        min_r = r.min()
        if min_r < 0:
            k_max = (1.0 / abs(min_r)) * 0.99
        else:
            k_max = 20.0
        k_grid = np.linspace(0.0, k_max, 501)

    best_log = -np.inf
    best_k = 0.0
    for k in k_grid:
        lv = 1.0 + k * r
        if np.any(lv <= 0):
            continue
        ll = float(np.sum(np.log(lv)))
        if ll > best_log:
            best_log = ll
            best_k = float(k)
    return best_k


# --- Monte Carlo drawdown distribution --------------------------------------

def _circular_block_bootstrap(returns: np.ndarray,
                              horizon: int, block: int,
                              rng: np.random.Generator) -> np.ndarray:
    """One bootstrap path of length `horizon` built from circular blocks."""
    n = len(returns)
    n_blocks = (horizon + block - 1) // block
    starts = rng.integers(0, n, size=n_blocks)
    path = np.empty(n_blocks * block, dtype=np.float64)
    for k, s in enumerate(starts):
        # circular indices
        for j in range(block):
            path[k * block + j] = returns[(s + j) % n]
    return path[:horizon]


def _max_drawdown(path_returns: np.ndarray) -> float:
    """Max drawdown on a leveraged-return path. Returns positive magnitude."""
    eq = np.cumprod(1.0 + path_returns)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    return float(dd.max())


def mc_dd_probability(returns: np.ndarray,
                      leverage: float,
                      dd_threshold: float = 0.20,
                      horizon: int = 252,
                      n_sims: int = 5000,
                      block: int = 20,
                      seed: int = 42) -> dict:
    """Probability that leveraged strategy exceeds dd_threshold within horizon.

    Returns dict with:
      p_dd_exceed : fraction of sims with max DD > dd_threshold
      dd_q50, dd_q95, dd_q99 : DD quantiles across sims
      ret_q05, ret_q50, ret_q95 : terminal return quantiles
    """
    r = np.asarray(returns, dtype=np.float64)
    rng = np.random.default_rng(seed)
    dds = np.empty(n_sims)
    rets = np.empty(n_sims)
    for s in range(n_sims):
        path = _circular_block_bootstrap(r, horizon, block, rng)
        lev_path = leverage * path
        # Avoid bankruptcy (1 + lev*r > 0). If any step bankrupts, max-DD is ~1.
        min_step = (1.0 + lev_path).min()
        if min_step <= 0:
            dds[s] = 1.0
            rets[s] = -1.0
            continue
        dds[s] = _max_drawdown(lev_path)
        rets[s] = float(np.prod(1.0 + lev_path) - 1.0)
    return {
        "leverage": leverage,
        "p_dd_exceed": float((dds > dd_threshold).mean()),
        "dd_q50": float(np.quantile(dds, 0.50)),
        "dd_q95": float(np.quantile(dds, 0.95)),
        "dd_q99": float(np.quantile(dds, 0.99)),
        "ret_q05": float(np.quantile(rets, 0.05)),
        "ret_q50": float(np.quantile(rets, 0.50)),
        "ret_q95": float(np.quantile(rets, 0.95)),
    }


# --- Risk-constrained Kelly (Busseti-Ryu-Boyd 2016) -------------------------

def kelly_risk_constrained(returns: np.ndarray,
                           dd_threshold: float = 0.20,
                           dd_prob: float = 0.05,
                           horizon: int = 252,
                           n_sims: int = 3000,
                           block: int = 20,
                           k_lo: float = 0.0,
                           k_hi: float = 5.0,
                           tol: float = 0.05,
                           seed: int = 42) -> dict:
    """Highest leverage s.t. MC P(DD > dd_threshold) <= dd_prob.

    Bisection on leverage grid. This is the empirical-MC form of BRB 2016's
    convex DD-chance-constrained Kelly program -- same result without CVXPY.
    """
    r = np.asarray(returns, dtype=np.float64)

    def _p(k):
        res = mc_dd_probability(r, k, dd_threshold=dd_threshold,
                                horizon=horizon, n_sims=n_sims, block=block,
                                seed=seed)
        return res["p_dd_exceed"], res

    # If even k_hi satisfies the constraint, return k_hi.
    p_hi, res_hi = _p(k_hi)
    if p_hi <= dd_prob:
        return {"leverage": k_hi, "saturated_at_k_hi": True, **res_hi}

    # If k_lo fails, constraint is infeasible even at 0 leverage (shouldn't happen).
    p_lo, res_lo = _p(k_lo)
    if p_lo > dd_prob:
        return {"leverage": k_lo, "infeasible": True, **res_lo}

    # Bisection
    lo, hi = k_lo, k_hi
    best_res = res_lo
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        p_mid, res_mid = _p(mid)
        if p_mid <= dd_prob:
            lo = mid
            best_res = res_mid
        else:
            hi = mid
    return {"leverage": lo, **best_res}


# --- Per-sleeve joint RC-Kelly (coordinate ascent) --------------------------

def _joint_block_bootstrap(sleeve_returns: dict[str, np.ndarray],
                           horizon: int, block: int,
                           rng: np.random.Generator,
                           ) -> dict[str, np.ndarray]:
    """Circular block bootstrap SHARED across sleeves.

    Preserves cross-sleeve correlation structure: every sleeve's path is
    sampled at the SAME time indices, so pairs of days that were correlated
    in the original panel remain correlated in the resampled path.
    """
    names = list(sleeve_returns.keys())
    n = len(sleeve_returns[names[0]])
    n_blocks = (horizon + block - 1) // block
    starts = rng.integers(0, n, size=n_blocks)
    idx = np.empty(n_blocks * block, dtype=np.int64)
    for k, s in enumerate(starts):
        for j in range(block):
            idx[k * block + j] = (s + j) % n
    idx = idx[:horizon]
    return {name: sleeve_returns[name][idx] for name in names}


def mc_portfolio_dd(sleeve_returns: dict[str, np.ndarray],
                    leverages: dict[str, float],
                    weights: dict[str, float],
                    dd_threshold: float = 0.20,
                    horizon: int = 252,
                    n_sims: int = 3000,
                    block: int = 20,
                    seed: int = 42) -> dict:
    """Portfolio DD probability with per-sleeve leverage.

    Portfolio daily return = sum_i w_i * k_i * r_i[t] across sleeves i.
    Uses joint circular block bootstrap so cross-sleeve correlation is
    preserved in the sim paths.
    """
    names = list(sleeve_returns.keys())
    r = {n: np.asarray(sleeve_returns[n], dtype=np.float64) for n in names}
    rng = np.random.default_rng(seed)
    dds = np.empty(n_sims)
    rets = np.empty(n_sims)
    for s in range(n_sims):
        paths = _joint_block_bootstrap(r, horizon, block, rng)
        port_path = np.zeros(horizon)
        for name in names:
            port_path += weights[name] * leverages[name] * paths[name]
        min_step = (1.0 + port_path).min()
        if min_step <= 0:
            dds[s] = 1.0
            rets[s] = -1.0
            continue
        dds[s] = _max_drawdown(port_path)
        rets[s] = float(np.prod(1.0 + port_path) - 1.0)
    return {
        "leverages": dict(leverages),
        "p_dd_exceed": float((dds > dd_threshold).mean()),
        "dd_q50": float(np.quantile(dds, 0.50)),
        "dd_q95": float(np.quantile(dds, 0.95)),
        "dd_q99": float(np.quantile(dds, 0.99)),
        "ret_q05": float(np.quantile(rets, 0.05)),
        "ret_q50": float(np.quantile(rets, 0.50)),
        "ret_q95": float(np.quantile(rets, 0.95)),
    }


def per_sleeve_rc_kelly(sleeve_returns: dict[str, np.ndarray],
                        weights: dict[str, float],
                        dd_threshold: float = 0.20,
                        dd_prob: float = 0.05,
                        horizon: int = 252,
                        n_sims: int = 3000,
                        block: int = 20,
                        k_lo: float = 0.0,
                        k_hi: float = 10.0,
                        tol: float = 0.1,
                        max_iters: int = 4,
                        seed: int = 42,
                        start_leverages: dict[str, float] | None = None,
                        ) -> dict:
    """Per-sleeve risk-constrained Kelly via coordinate ascent.

    Holds (M-1) sleeves fixed, bisects the remaining sleeve's leverage to
    the largest value that keeps portfolio P(DD > dd_threshold) <= dd_prob.
    Rotates through all sleeves each iteration; converges when leverage
    moves are below `tol` across a full pass.

    The result is a leverage vector k = {sleeve: k_i} that is locally
    optimal in the "maximum per-sleeve k subject to joint DD constraint"
    sense. This is NOT Sharpe-maximizing globally -- it's the frontier
    point where each sleeve is individually saturated given the others.
    For four or fewer sleeves that's the practically relevant answer.

    Returns dict:
      leverages       -- final per-sleeve k
      portfolio_dd    -- MC DD stats at final leverages
      iterations      -- per-iteration snapshots
      converged       -- True if all moves in last pass were below tol
    """
    names = list(sleeve_returns.keys())
    k_cur = {n: 1.0 for n in names}
    if start_leverages:
        k_cur.update(start_leverages)

    def _p(lev):
        return mc_portfolio_dd(sleeve_returns, lev, weights,
                               dd_threshold=dd_threshold, horizon=horizon,
                               n_sims=n_sims, block=block, seed=seed)

    snapshots: list[dict] = []
    moved = 0.0
    for it in range(max_iters):
        moved = 0.0
        for focus in names:
            # Bisect k[focus] while holding the rest fixed at their current values.
            lo, hi = k_lo, k_hi
            # If even k_hi passes, saturate there.
            trial = dict(k_cur); trial[focus] = hi
            p_hi = _p(trial)["p_dd_exceed"]
            if p_hi <= dd_prob:
                k_new = hi
            else:
                # If k_lo fails, constraint infeasible -- keep current.
                trial[focus] = lo
                p_lo = _p(trial)["p_dd_exceed"]
                if p_lo > dd_prob:
                    k_new = lo
                else:
                    while hi - lo > tol:
                        mid = 0.5 * (lo + hi)
                        trial[focus] = mid
                        p_mid = _p(trial)["p_dd_exceed"]
                        if p_mid <= dd_prob:
                            lo = mid
                        else:
                            hi = mid
                    k_new = lo
            moved = max(moved, abs(k_new - k_cur[focus]))
            k_cur[focus] = k_new
        snapshots.append({"iter": it + 1, "leverages": dict(k_cur),
                          "max_move": moved})
        if moved <= tol:
            break
    final = _p(k_cur)
    return {
        "leverages": dict(k_cur),
        "portfolio_dd": final,
        "iterations": snapshots,
        "converged": moved <= tol,
    }
