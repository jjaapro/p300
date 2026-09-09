"""Black-Scholes (European, r = 0, as used for crypto options) — price, delta,
vega and a bisection implied-vol solver. Verbatim port of the functions in
trader research/probe_vrp_straddle_v2.py (2026-04) so hedged P&L numbers
reproduce; stdlib only.
"""
from __future__ import annotations

import math

IV_MIN = 0.05
IV_MAX = 5.0
IV_MAX_ITERS = 50
IV_TOL = 1e-5


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(S: float, K: float, T: float, sigma: float) -> float:
    return (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))


def call_price(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = _d1(S, K, T, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm_cdf(d1) - K * norm_cdf(d2)


def put_price(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return max(0.0, K - S)
    d1 = _d1(S, K, T, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return K * norm_cdf(-d2) - S * norm_cdf(-d1)


def call_delta(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    return norm_cdf(_d1(S, K, T, sigma))


def put_delta(S: float, K: float, T: float, sigma: float) -> float:
    return call_delta(S, K, T, sigma) - 1.0


def vega(S: float, K: float, T: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return S * math.sqrt(T) * norm_pdf(_d1(S, K, T, sigma))


def implied_vol(price: float, S: float, K: float, T: float, is_call: bool) -> float | None:
    """Bisection IV solver (robust where Newton diverges at deep ITM/OTM).
    Returns None when the price is below intrinsic or T <= 0."""
    if T <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, S - K) if is_call else max(0.0, K - S)
    if price < intrinsic - 1.0:
        return None
    if price <= intrinsic + 0.01:
        return IV_MIN
    lo, hi = IV_MIN, IV_MAX
    mid = 0.5 * (lo + hi)
    for _ in range(IV_MAX_ITERS):
        mid = 0.5 * (lo + hi)
        est = call_price(S, K, T, mid) if is_call else put_price(S, K, T, mid)
        if abs(est - price) < IV_TOL:
            return mid
        if est > price:
            hi = mid
        else:
            lo = mid
    return mid
