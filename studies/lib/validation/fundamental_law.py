"""Grinold's Fundamental Law of Active Management as a Sharpe-plausibility gate.

New module.  Its ancestors in the trader repo are the driver
``probes/diagnostic_fundamental_law.py`` and the methodology memo
``fundamental_law_diagnostic.md`` ("Fundamental Law diagnostic (Grinold) is
a promotion gate").

    IR = IC * sqrt(BR)

Grinold, R. C. (1989). "The Fundamental Law of Active Management." Journal
of Portfolio Management 15(3): 30-37; Grinold & Kahn (2000), "Active
Portfolio Management", ch. 6.  IR is the annualised information ratio
(Sharpe for a self-financed sleeve), BR the number of *independent* bets per
year (trades, not calendar days), IC the per-bet information coefficient,
bounded in [-1, 1].

Inverting the law gives the IC a claimed Sharpe *requires* at the sleeve's
breadth: ``required_ic = sharpe / sqrt(bets_per_year)``.  An IC above 1 is
impossible; an IC a discretionary trader would envy is a sign the Sharpe is
an artefact -- long-hold PnL smearing over calendar days, sparse-series
annualisation by sqrt(365), survivorship.

Verdict scale (``breadth_verdict``), on |required IC|:

    < 0.3        plausible
    0.3 - 0.7    elevated
    0.7 - 1.0    suspicious
    > 1.0        impossible

The memo's own four-tier scale (< 0.1 comfortable, 0.1-0.5 plausible,
0.5-1.0 SUSPICIOUS, > 1.0 IMPOSSIBLE) is returned alongside as
``memo_verdict`` so the two vocabularies can be reconciled.  The magnitude
is used so a large negative Sharpe is flagged as loudly as a large positive
one; the diagnostic tests plausibility, not profitability.

Which breadth to use: per-trade for position strategies (weekly / monthly
signals); per-day only where each day is a genuinely independent bet
(structural carry accrual).
"""
from __future__ import annotations

import math

# (upper bound exclusive except the last, label) -- this module's scale
IC_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.3, "plausible"),
    (0.7, "elevated"),
    (1.0, "suspicious"),
)
IC_IMPOSSIBLE = "impossible"

# The methodology memo's scale, reported as ``memo_verdict``
IC_THRESHOLDS_MEMO: tuple[tuple[float, str], ...] = (
    (0.1, "comfortable"),
    (0.5, "plausible"),
    (1.0, "suspicious"),
)


def required_ic(sharpe: float, bets_per_year: float) -> float:
    """IC a Sharpe of ``sharpe`` requires at ``bets_per_year`` independent bets.

    ``IC = IR / sqrt(BR)``.  Returns +inf for non-positive breadth (no bets
    can support any Sharpe).  Sign follows the Sharpe.
    """
    if bets_per_year <= 0:
        return float("inf")
    return sharpe / math.sqrt(bets_per_year)


def implied_sharpe(ic: float, bets_per_year: float) -> float:
    """Sharpe the law supports at a per-bet IC and breadth: ``IC * sqrt(BR)``."""
    if bets_per_year <= 0:
        return 0.0
    return ic * math.sqrt(bets_per_year)


def _classify(ic_abs: float) -> str:
    if ic_abs < IC_THRESHOLDS[0][0]:
        return IC_THRESHOLDS[0][1]
    if ic_abs < IC_THRESHOLDS[1][0]:
        return IC_THRESHOLDS[1][1]
    if ic_abs <= IC_THRESHOLDS[2][0]:
        return IC_THRESHOLDS[2][1]
    return IC_IMPOSSIBLE


def _classify_memo(ic_abs: float) -> str:
    if ic_abs > IC_THRESHOLDS_MEMO[2][0]:
        return IC_IMPOSSIBLE
    if ic_abs >= IC_THRESHOLDS_MEMO[1][0]:
        return IC_THRESHOLDS_MEMO[2][1]
    if ic_abs >= IC_THRESHOLDS_MEMO[0][0]:
        return IC_THRESHOLDS_MEMO[1][1]
    return IC_THRESHOLDS_MEMO[0][1]


def breadth_verdict(sharpe: float, bets_per_year: float) -> dict:
    """Plausibility verdict for a claimed Sharpe at a given breadth.

    Returns {"sharpe", "bets_per_year", "ic_required", "verdict",
    "memo_verdict"}.  ``verdict`` is one of "plausible" (< 0.3),
    "elevated" (0.3-0.7), "suspicious" (0.7-1.0), "impossible" (> 1.0) on
    |ic_required|; ``memo_verdict`` is the methodology memo's tier
    ("comfortable" / "plausible" / "suspicious" / "impossible").
    """
    ic = required_ic(sharpe, bets_per_year)
    ic_abs = abs(ic)
    return {
        "sharpe": float(sharpe),
        "bets_per_year": float(bets_per_year),
        "ic_required": ic,
        "verdict": _classify(ic_abs),
        "memo_verdict": _classify_memo(ic_abs),
    }
