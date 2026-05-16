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
