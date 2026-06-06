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
    """Sum of ``size_usdt`` across this variant's open paper trades.

    ``size_usdt`` is computed at trade-open as
    ``capital × allocation_pct/100 × leverage`` (see
    :func:`strategies.trades.open_paper_trade`) — i.e. it's already the
    *leveraged* notional, not the margin posted. Summing it gives the
    directional-perp gross that's actually exposed to the market.

    CARRY's spot leg is implicitly excluded because the CARRY sleeve
    only writes ONE trade row for the pair (the perp leg). The spot
    side is collateral inside the same trade, not a separate row.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(size_usdt), 0) FROM trades "
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


# CARRY's perp SHORT is delta-neutral collateral; excluded from
# concentration-risk accounting (matches conflict_resolver and dispatch).
_NEUTRAL_STRATEGIES = frozenset({"CARRY", "S-078"})


def current_gross_by_direction_usdt(variant_id: str) -> dict[str, float]:
    """Sum of ``size_usdt`` per direction across this variant's open
    paper trades. CARRY's delta-neutral SHORT excluded.

    Returns ``{"LONG": X, "SHORT": Y}`` — both keys always present
    (zero if no positions in that direction). Used by the orchestrator
    to seed :func:`strategies.support.dispatch.reconcile_intents`'s
    per-direction bucket state when ``same_direction_cap_usdt`` is
    active.
    """
    placeholders = ",".join("?" * len(_NEUTRAL_STRATEGIES))
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        rows = con.execute(
            f"SELECT direction, COALESCE(SUM(size_usdt), 0) "
            f"FROM trades "
            f"WHERE strategy_variant = ? AND status = 'open' "
            f"  AND execution_mode = 'paper' "
            f"  AND direction IN ('LONG', 'SHORT') "
            f"  AND strategy NOT IN ({placeholders}) "
            f"GROUP BY direction",
            (variant_id, *_NEUTRAL_STRATEGIES),
        ).fetchall()
    finally:
        con.close()
    out = {"LONG": 0.0, "SHORT": 0.0}
    for direction, total in rows:
        if direction in out:
            out[direction] = float(total or 0.0)
    return out


DEFAULT_SAME_DIRECTION_TARGET_X: Optional[float] = None
"""Default per-direction cap multiplier. ``None`` = no cap enforced
(legacy behavior preserved for any variant whose spec hasn't opted
into the policy yet)."""


def same_direction_cap_usdt(variant: dict) -> Optional[float]:
    """Return the variant's per-direction notional cap in USDT, or
    ``None`` if the spec hasn't opted into the policy.

    Pulled from ``variant.spec.allocator_notes.same_direction_target_x``;
    multiplied by ``variant.capital_usdt``. Returning ``None`` causes
    :func:`reconcile_intents` to skip the per-direction check
    entirely (its existing behavior pre-cap).
    """
    spec = variant.get("spec") or {}
    notes = spec.get("allocator_notes") or {}
    target_x = notes.get("same_direction_target_x", DEFAULT_SAME_DIRECTION_TARGET_X)
    if target_x is None:
        return None
    capital = float(variant.get("capital_usdt") or 10000)
    return capital * float(target_x)


def headroom_usdt(variant: dict) -> float:
    """Remaining notional capacity in USDT. Negative when the variant
    is already over its cap (today's reality on some ticks per the
    P99=148% number from the 2.6yr replay)."""
    return gross_cap_usdt(variant) - current_gross_notional_usdt(variant["id"])


def can_open(variant: dict, candidate_notional_usdt: float
              ) -> tuple[bool, Optional[str]]:
    """True if opening a new trade with ``candidate_notional_usdt``
    notional (already leveraged — same shape as ``size_usdt`` in
    :mod:`strategies.trades`: ``capital × alloc_pct/100 × leverage``)
    would keep the variant at or below its gross cap.
    ``False, reason`` otherwise.

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


# Below this fraction of the intended notional, the reduced trade is
# too small to be worth the round-trip fees and slippage. Sleeves that
# opt into the reduce policy treat anything below this as a skip
# instead of an open.
DEFAULT_MIN_REDUCE_FRACTION = 0.50


def clamp_to_headroom(variant: dict,
                       candidate_notional_usdt: float,
                       min_reduce_fraction: float = DEFAULT_MIN_REDUCE_FRACTION,
                       ) -> tuple[float, str, Optional[str]]:
    """Return the candidate notional clamped to remaining headroom.

    Result tuple is ``(clamped_notional, status, reason)``:

      - ``("full", None)`` — the full candidate fits; no clamp needed.
      - ``("reduced", reason)`` — clamped to remaining headroom; the
        reduced size is still at least ``min_reduce_fraction`` of the
        original candidate. Sleeve opens at the reduced size.
      - ``("too_small", reason)`` — headroom is positive but less than
        ``min_reduce_fraction × candidate``; the reduced trade would
        be too small to bother with. Sleeve skips.
      - ``("no_headroom", reason)`` — headroom is zero or negative.
        Sleeve skips.

    Sleeves that opt into the proportional-reduce policy replace::

        ok, reason = margin_headroom.can_open(variant, candidate)
        if not ok: return {"status": "margin_constrained", ...}

    with::

        clamped, status, reason = margin_headroom.clamp_to_headroom(
            variant, candidate)
        if status in ("too_small", "no_headroom"):
            return {"status": "margin_constrained", ...}
        if status == "reduced":
            # Scale the sleeve's alloc_pct / leverage / weight down so
            # the size_usdt that open_paper_trade will compute equals
            # `clamped`. For sleeves where size = capital × alloc/100 ×
            # leverage, that's: new_alloc = clamped × 100 / (capital × leverage).
            ...

    Defaults to a 50% floor — below half the intended size the trade
    likely doesn't capture the calibrated edge anyway. Set
    ``min_reduce_fraction=0.0`` to always open at headroom.
    """
    current = current_gross_notional_usdt(variant["id"])
    cap = gross_cap_usdt(variant)
    headroom = cap - current
    if candidate_notional_usdt <= 0:
        # Sleeve passed a non-positive candidate — short-circuit as
        # "full" so the sleeve's own no-op path can run.
        return (0.0, "full", None)
    if current + candidate_notional_usdt <= cap + 1e-9:
        return (candidate_notional_usdt, "full", None)
    if headroom <= 0:
        return (0.0, "no_headroom", (
            f"margin_cap: current={current:,.0f} >= cap={cap:,.0f}, "
            f"candidate={candidate_notional_usdt:,.0f}"
        ))
    if headroom < min_reduce_fraction * candidate_notional_usdt:
        return (0.0, "too_small", (
            f"margin_cap: headroom={headroom:,.0f} < "
            f"{min_reduce_fraction:.0%} × candidate={candidate_notional_usdt:,.0f}"
        ))
    # Reduce: candidate doesn't fit but headroom is at least
    # min_reduce_fraction of it. Open at headroom.
    return (headroom, "reduced", (
        f"margin_cap: candidate={candidate_notional_usdt:,.0f} clamped "
        f"to headroom={headroom:,.0f}"
    ))
