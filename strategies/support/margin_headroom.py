"""Per-variant margin headroom — pure-read accounting layer (P2.4d).

Today each sleeve sizes its trades independently from a static
``weight_pct × leverage``. There's no cross-sleeve visibility, so live
notional drifts above ``gross_notional_target_x`` (mean concurrent 81%,
P99 148% over the 2.6yr replay — well above the 225% target on
the leverage side, but the *intent* was an envelope, not an unbounded
sum). This module gives the orchestrator a single function it can ask
"how much more notional can this variant open right now?" before
dispatching the next sleeve.

P2.4d ships in stages:

  1. **This commit (scaffold).** The math + a public accessor:
     :func:`current_gross_notional_usdt`, :func:`gross_cap_usdt`,
     :func:`headroom_usdt`. Orchestrator and backtest_runner inject
     ``_effective_margin_headroom_usdt`` into every dispatched
     sleeve_cfg, but no sleeve consumes it — behavior is unchanged.
     Tests anchor the math.

  2. **Follow-up.** Sleeves consult ``_effective_margin_headroom_usdt``
     before opening. Default policy: SKIP a candidate trade when its
     notional would push the variant above the cap (cheap; mirrors
     the conservative skip-if-over policy that
     :mod:`strategies.support.risk_caps` uses for the PDO+CPR BTC cap).
     The proportional-scale-down policy ("reduce" per BACKLOG P2.4d)
     is a Stage 3 refinement once we have enough live data to confirm
     skip is too coarse.

  3. **Priority.** When two sleeves want to open on the same tick and
     both fit individually but not together, the orchestrator needs a
     priority order to decide which yields. Today the dispatch loop
     processes ``spec.composition`` in registration order; we can
     formalize that as the priority unless the operator wants a
     different policy.

The gross cap comes from ``variant.spec.allocator_notes.gross_notional_target_x``
(default 2.5 — slightly above the 2.25× documented intent so transient
overruns from one tick don't immediately block the next). Multiplied
by ``variant.capital_usdt`` gives the cap in USDT.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from strategies.support import db

log = logging.getLogger("p300.margin_headroom")


DEFAULT_GROSS_NOTIONAL_TARGET_X = 2.5


def current_gross_notional_usdt(variant_id: str) -> float:
    """Sum of (size_usdt × leverage) across this variant's open paper trades.

    This is the directional-perp gross — what the bot would have on the
    exchange right now if every open trade were placed at its recorded
    size + leverage. CARRY's spot leg is intentionally NOT counted: the
    perp leg of a CARRY pair carries the leverage; the spot leg is
    collateral, not a position the cap should bound.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(size_usdt * leverage), 0) FROM trades "
            "WHERE strategy_variant = ? AND status = 'open' "
            "  AND execution_mode = 'paper'",
            (variant_id,),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        con.close()


def gross_cap_usdt(variant: dict) -> float:
    """Return the variant's max gross notional in USDT.

    Pulled from ``variant.spec.allocator_notes.gross_notional_target_x``;
    defaults to :data:`DEFAULT_GROSS_NOTIONAL_TARGET_X` when the spec
    doesn't set it. Multiplied by ``variant.capital_usdt``.
    """
    capital = float(variant.get("capital_usdt") or 10000)
    spec = variant.get("spec") or {}
    notes = spec.get("allocator_notes") or {}
    target_x = float(notes.get("gross_notional_target_x",
                                DEFAULT_GROSS_NOTIONAL_TARGET_X))
    return capital * target_x


def headroom_usdt(variant: dict) -> float:
    """Remaining notional capacity in USDT. Negative when the variant
    is already over its cap (today's reality on some ticks per the
    P99=148% number from the 2.6yr replay)."""
    return gross_cap_usdt(variant) - current_gross_notional_usdt(variant["id"])


def can_open(variant: dict, candidate_notional_usdt: float
              ) -> tuple[bool, Optional[str]]:
    """True if opening a new trade with ``candidate_notional_usdt``
    notional (== ``size_usdt × leverage``) would keep the variant at or
    below its gross cap. ``False, reason`` otherwise.

    Sleeves that opt into enforcement call this just before
    :func:`strategies.trades.open_paper_trade`; if it returns False
    they short-circuit with status ``margin_constrained`` and surface
    ``reason`` in their dispatch log line.
    """
    current = current_gross_notional_usdt(variant["id"])
    cap = gross_cap_usdt(variant)
    if current + candidate_notional_usdt <= cap + 1e-9:
        return True, None
    return False, (
        f"margin_cap: current={current:,.0f} "
        f"+ candidate={candidate_notional_usdt:,.0f} "
        f"> cap={cap:,.0f}"
    )
