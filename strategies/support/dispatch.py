"""Two-phase dispatch scaffold (P2.4e/f Stage 2).

Today the orchestrator dispatches each sleeve once per tick via
``try_fire_for_variant``, which is a one-shot "decide and open". The
sleeve evaluates its signal AND calls
:func:`strategies.trades.open_paper_trade` in the same function. With
no visibility into what other sleeves will do, cross-sleeve
reconciliation (priority-based conflict resolution, signal pooling,
fair margin allocation) is structurally impossible from the
orchestrator side — it's all baked into each sleeve's own checks.

The Stage 2 design splits each sleeve into two phases:

  1. ``try_decide_for_variant(variant, sleeve_cfg) -> Intent | None``
     reads inputs, evaluates signals, returns a structured
     description of "what I'd open if approved". No DB writes; no
     trade opens.

  2. The orchestrator collects intents across every sleeve, applies
     reconciliation:
       - Priority-based conflict resolution (highest-conviction LONG
         beats lower-conviction SHORT on the same asset).
       - Signal pooling (multiple LONGs on the same asset combine
         into one pooled position).
       - Margin allocation under the variant's gross cap (top-priority
         intents get full size; later intents get reduce or skip).

  3. ``execute_for_variant(variant, sleeve_cfg, intent) -> result``
     opens the (possibly modified) approved intent. Returns the same
     status dict shape ``try_fire_for_variant`` returns today.

The migration is incremental — sleeves implement
``try_decide_for_variant`` as they're refactored; the orchestrator
falls back to the legacy ``try_fire_for_variant`` for sleeves that
don't. Mixed mode loses some reconciliation visibility (only intents
from migrated sleeves enter the reconcile pass) but doesn't break.

This module ships the scaffold — the :class:`Intent` dataclass and a
docstring contract for the new methods. AI_QUANT is the natural pilot
because its decision (LLM call) is already separate from its open;
other sleeves follow as time permits.

P2.4e Stage 2 goal: replace the first-come-first-served conflict
resolution in :mod:`strategies.support.conflict_resolver` with
intent-based reconciliation. AI_QUANT's LLM-derived ``conviction_0_100``
becomes the natural priority for that sleeve; other sleeves can
expose a fixed conviction or read it from regime / params.

P2.4f Stage 2 goal: replace the read-only concordant-detection in
:mod:`strategies.support.signal_aggregator` with active pooling —
the orchestrator collects N concordant intents and opens ONE pooled
trade at their combined size, conviction-weighted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Intent:
    """A sleeve's "what I'd open if approved" payload, returned from
    :func:`try_decide_for_variant`. Frozen so the orchestrator can
    safely cache + reorder intents during the reconcile pass.

    Fields mirror the shape of :func:`strategies.trades.open_paper_trade`'s
    arguments (``allocation_pct``, ``leverage``, ``asset``,
    ``direction``, ``reason``, ``scheduled_exit_dt``) plus a few
    reconciliation-time extras (``conviction``, ``priority``).

    Attributes:
        asset: Trade asset (``"BTC"``, ``"ETH"``).
        direction: ``"LONG"``, ``"SHORT"``, or ``"FLAT"``. ``FLAT``
            signals "explicitly close existing position" rather than
            "no intent" — caller returns None for the latter.
        allocation_pct: Intended pre-leverage allocation in percent
            (e.g. ``15.0`` for 15% of variant capital).
        leverage: Intended leverage multiplier (the orchestrator may
            adjust this in the reconcile pass via vol-target / margin
            cap).
        conviction: 0-100. Used by the reconcile pass to break ties
            on directional conflicts and to weight pooled positions.
            Sleeves without an explicit conviction signal pass 100
            (full confidence in their fixed signal).
        priority: Reconciliation tie-break. Lower = wins. Defaults
            to 100 (matches composition iteration default in P2.4d (c)).
        reason: Free-form payload persisted into ``trades.notes``;
            structurally identical to today's reason dicts.
        scheduled_exit_dt: Optional preset exit. None for
            signal-driven sleeves that exit on their own logic.
    """
    asset: str
    direction: str
    allocation_pct: float
    leverage: float = 1.0
    conviction: int = 100
    priority: float = 100.0
    reason: Optional[dict] = field(default=None)
    scheduled_exit_dt: Optional[datetime] = field(default=None)


# ─── Sleeve protocol (informational; not enforced) ────────────────────────────

#: Sleeves that have migrated to two-phase dispatch implement this
#: signature alongside (or instead of) ``try_fire_for_variant``.
#: The orchestrator looks for the attribute and routes through it
#: when present. See module docstring for the contract.
#:
#:     def try_decide_for_variant(variant: dict, sleeve_cfg: dict) -> Intent | None: ...
#:     def execute_for_variant(variant: dict, sleeve_cfg: dict, intent: Intent | None) -> dict: ...
TWO_PHASE_PROTOCOL = "(documented contract; no runtime enforcement)"


# ─── Reconcile pass ───────────────────────────────────────────────────────────

# CARRY's perp SHORT is delta-neutral collateral; mirror the
# conflict_resolver / signal_aggregator exclusion lists.
_NEUTRAL_STRATEGIES = frozenset({"CARRY", "S-078"})


@dataclass(frozen=True)
class ReconcileResult:
    """Decision for one intent emerging from the reconcile pass.

    Attributes:
        intent: The (possibly modified) intent to execute. ``None``
            when the intent was rejected entirely.
        status: One of ``approved``, ``approved_reduced``,
            ``rejected_directional_conflict``,
            ``rejected_margin``, ``approved_pooled`` (P2.4f Stage 2).
        reason: Human-readable reason string (for logs / status dicts).
        sleeve_id: Strategy ID of the originator (helps the orchestrator
            map back to its dispatch entry).
    """
    intent: Optional[Intent]
    status: str
    reason: str = ""
    sleeve_id: str = ""


def _priority_key(item: tuple[str, Intent]) -> tuple[float, float]:
    """Sort key for intents: lower priority wins; ties broken by higher
    conviction. Returns (priority, -conviction) so default sort works."""
    _, intent = item
    return (intent.priority, -float(intent.conviction))


def reconcile_intents(
    intents: list[tuple[str, Intent]],
    current_gross_used_usdt: float,
    gross_cap_usdt: float,
    capital_usdt: float,
    min_reduce_fraction: float = 0.50,
) -> list[ReconcileResult]:
    """Run the cross-sleeve reconciliation pass over a tick's intents.

    Args:
        intents: List of ``(strategy_id, Intent)`` pairs collected from
            every sleeve that opted into two-phase dispatch this tick.
            None-intents (sleeves passing the tick) are NOT included.
        current_gross_used_usdt: Variant's current open gross notional.
        gross_cap_usdt: Variant's gross-notional cap.
        capital_usdt: Variant capital (for converting alloc_pct ↔ notional).
        min_reduce_fraction: Below this fraction of the intended notional,
            the reduce policy rejects rather than opening a token-sized
            position. Mirrors :data:`strategies.support.margin_headroom.DEFAULT_MIN_REDUCE_FRACTION`.

    Returns:
        A list of :class:`ReconcileResult`, ordered by priority (winner
        first). Each result describes the orchestrator's decision for
        one input intent. The orchestrator then calls
        ``execute_for_variant`` on the result's (possibly-modified)
        intent for ``approved`` and ``approved_reduced``, and surfaces
        the rejection reason in the status dict for the rest.

    Pass order:
      1. Sort intents by (priority, -conviction).
      2. For each intent in order:
         a. Skip CARRY's neutral SHORT from conflict checks.
         b. Reject if an earlier-approved intent on the same asset
            has the opposite direction (directional conflict).
         c. Compute candidate_notional = capital × alloc_pct/100 × leverage.
         d. Check headroom = cap - (current_used + approved_notional_so_far).
            - If candidate fits, approve full size.
            - If headroom ≥ min_reduce_fraction × candidate, approve reduced
              to ``headroom``.
            - Otherwise reject (margin).

    Signal pooling (P2.4f Stage 2 follow-up): when multiple concordant
    intents land on the same (asset, direction) pair, today this pass
    treats them as separate trades that stack. A future iteration will
    merge them into one weighted intent before running the margin check.
    """
    # Already-approved per asset → {asset: ("LONG"|"SHORT", notional_taken)}
    approved_by_asset: dict[str, tuple[str, float]] = {}
    approved_notional = 0.0
    results: list[ReconcileResult] = []

    ordered = sorted(intents, key=_priority_key)

    for sleeve_id, intent in ordered:
        if intent.direction not in ("LONG", "SHORT"):
            # FLAT intents (close-existing) don't go through the
            # reconcile pass — they're always approved as-is.
            results.append(ReconcileResult(
                intent=intent, status="approved", sleeve_id=sleeve_id,
            ))
            continue

        # Directional conflict — but CARRY's neutral leg is excluded.
        if sleeve_id not in _NEUTRAL_STRATEGIES:
            prior = approved_by_asset.get(intent.asset)
            if prior is not None:
                prior_dir, _ = prior
                if prior_dir != intent.direction:
                    results.append(ReconcileResult(
                        intent=None, status="rejected_directional_conflict",
                        reason=(f"opposing {prior_dir} already approved on "
                                f"{intent.asset}"),
                        sleeve_id=sleeve_id,
                    ))
                    continue

        # Margin check — current_used + approved_so_far + candidate ≤ cap.
        candidate_notional = (
            capital_usdt * (intent.allocation_pct / 100.0) * intent.leverage
        )
        used = current_gross_used_usdt + approved_notional
        headroom = gross_cap_usdt - used
        if candidate_notional <= 0:
            # No-op intent — pass through without margin impact.
            results.append(ReconcileResult(
                intent=intent, status="approved", sleeve_id=sleeve_id,
            ))
            continue
        if used + candidate_notional <= gross_cap_usdt + 1e-9:
            # Full size fits.
            results.append(ReconcileResult(
                intent=intent, status="approved", sleeve_id=sleeve_id,
            ))
            if sleeve_id not in _NEUTRAL_STRATEGIES:
                approved_by_asset[intent.asset] = (intent.direction, candidate_notional)
            approved_notional += candidate_notional
            continue
        if headroom <= 0:
            results.append(ReconcileResult(
                intent=None, status="rejected_margin",
                reason=(f"no_headroom: used={used:,.0f} "
                        f">= cap={gross_cap_usdt:,.0f}"),
                sleeve_id=sleeve_id,
            ))
            continue
        if headroom < min_reduce_fraction * candidate_notional:
            results.append(ReconcileResult(
                intent=None, status="rejected_margin",
                reason=(f"too_small: headroom={headroom:,.0f} < "
                        f"{min_reduce_fraction:.0%} × candidate="
                        f"{candidate_notional:,.0f}"),
                sleeve_id=sleeve_id,
            ))
            continue
        # Reduce to fit.
        clamped_notional = headroom
        new_alloc = clamped_notional * 100.0 / (capital_usdt * intent.leverage)
        reduced = Intent(
            asset=intent.asset, direction=intent.direction,
            allocation_pct=new_alloc, leverage=intent.leverage,
            conviction=intent.conviction, priority=intent.priority,
            reason=intent.reason, scheduled_exit_dt=intent.scheduled_exit_dt,
        )
        results.append(ReconcileResult(
            intent=reduced, status="approved_reduced",
            reason=(f"clamped {candidate_notional:,.0f} -> {clamped_notional:,.0f}"),
            sleeve_id=sleeve_id,
        ))
        if sleeve_id not in _NEUTRAL_STRATEGIES:
            approved_by_asset[intent.asset] = (intent.direction, clamped_notional)
        approved_notional += clamped_notional

    return results
