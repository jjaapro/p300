"""Cross-sleeve concordant-signal aggregation (P2.4f).

The dual of :mod:`strategies.support.conflict_resolver`. Where the
resolver flags opposing-direction trades, this module flags
*concordant* trades — multiple sleeves opening same-direction perp
positions on the same asset within the same variant — so the
orchestrator can later pool them into one conviction-weighted
exposure instead of N independent positions each paying its own
funding + fees.

Today, S-003 LONG BTC + S-096 LONG BTC (the V3 path, rare) +
JPLUS_R4_BTC LONG on a Mon wk1-2 06:00 UTC = three open BTC-LONG
positions each sized by its own ``allocation_pct × leverage``. They
combine into a single net BTC-LONG exposure at the exchange but pay
three sets of funding accruals and three round-trip fee budgets.
Pooling them would let the variant carry the same net exposure at
roughly 1/3 the friction.

P2.4f ships in stages:

  1. **Stage 1 — detect-only (shipped 2026-05-15).**
     :func:`detect_concordant_opens` and :func:`summarize_concordant`
     do the read-side accounting against the trades ledger. CARRY's
     perp SHORT is excluded (delta-neutral, same reasoning as
     :mod:`conflict_resolver`). Operator dashboards / sleeves consume
     directly.

  2. **Stage 2 — active pooling (shipped 2026-05-16).** The
     orchestrator's two-phase reconcile pass
     (:func:`strategies.support.dispatch.reconcile_intents` →
     :func:`strategies.support.dispatch._pool_concordant_allocations`)
     redistributes pre-open allocations among same-(asset, direction)
     intents via conviction-weighted averaging. Each participating
     sleeve still opens its own trade row (so per-sleeve close
     semantics survive); total exposure converges to the conviction-
     weighted average alloc rather than the sum. This module's
     post-open detection layer is still useful as a telemetry /
     audit surface on the live ledger.

Scope: directional perp positions only. CARRY's neutral leg is
excluded.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from strategies.support import db

log = logging.getLogger("p300.signal_aggregator")


# Mirror of conflict_resolver._NEUTRAL_STRATEGIES — CARRY's delta-neutral
# SHORT-perp shouldn't be counted as a concordant signal with another
# sleeve's directional SHORT.
_NEUTRAL_STRATEGIES = frozenset({"CARRY", "S-078"})


def detect_concordant_opens(variant_id: str,
                             asset: str,
                             direction: str,
                             excluded_strategies: Optional[frozenset[str]] = None,
                             ) -> list[dict]:
    """Return every open paper trade for ``variant_id`` on ``asset``
    with the *same* ``direction`` as the candidate — i.e. positions the
    candidate would stack onto rather than oppose.

    Args:
        variant_id: candidate sleeve's variant id.
        asset: candidate asset (``"BTC"`` / ``"ETH"``).
        direction: candidate direction (``"LONG"`` / ``"SHORT"``). The
            search returns trades whose direction matches.
        excluded_strategies: optional override for the "neutral" set;
            defaults to :data:`_NEUTRAL_STRATEGIES`.

    Returns:
        List of dicts (one per concordant open trade) sorted by
        ``actual_entry_time`` ascending. Each dict has keys ``id``,
        ``strategy``, ``direction``, ``size_usdt``, ``leverage``,
        ``entry_price``, ``actual_entry_time``, ``allocation_pct``.
        Empty list if no concordant trades exist.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    excluded = excluded_strategies if excluded_strategies is not None else _NEUTRAL_STRATEGIES

    placeholders = ",".join("?" * len(excluded)) if excluded else "''"
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        if excluded:
            rows = con.execute(
                f"SELECT id, strategy, direction, size_usdt, leverage, "
                f"       entry_price, actual_entry_time, allocation_pct "
                f"FROM trades "
                f"WHERE strategy_variant = ? AND status = 'open' "
                f"  AND execution_mode = 'paper' "
                f"  AND asset = ? AND direction = ? "
                f"  AND strategy NOT IN ({placeholders}) "
                f"ORDER BY actual_entry_time",
                (variant_id, asset.upper(), direction, *excluded),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, strategy, direction, size_usdt, leverage, "
                "       entry_price, actual_entry_time, allocation_pct "
                "FROM trades "
                "WHERE strategy_variant = ? AND status = 'open' "
                "  AND execution_mode = 'paper' "
                "  AND asset = ? AND direction = ? "
                "ORDER BY actual_entry_time",
                (variant_id, asset.upper(), direction),
            ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def summarize_concordant(variant_id: str) -> list[dict]:
    """List every (asset, direction) bucket with two or more open
    same-direction trades. CARRY's neutral leg excluded.

    Useful for operator surveys ("what stacking is happening right
    now?") and as a baseline for the Stage 2 pooling decision —
    a bucket with N concordant trades is exactly a "pool these"
    candidate.

    Returns a list of dicts. Each dict::

        {
            "asset": "BTC",
            "direction": "LONG",
            "n": 3,
            "total_notional_usdt": 45000.0,   # sum size_usdt * leverage
            "total_alloc_pct": 30.0,          # sum allocation_pct
            "trades": [<row>, <row>, <row>],
        }

    Buckets with N < 2 are omitted (a single trade isn't stacking
    anything).
    """
    excluded = list(_NEUTRAL_STRATEGIES)
    placeholders = ",".join("?" * len(excluded))
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"SELECT id, strategy, asset, direction, size_usdt, leverage, "
            f"       entry_price, actual_entry_time, allocation_pct "
            f"FROM trades "
            f"WHERE strategy_variant = ? AND status = 'open' "
            f"  AND execution_mode = 'paper' "
            f"  AND strategy NOT IN ({placeholders}) "
            f"ORDER BY asset, direction, actual_entry_time",
            (variant_id, *excluded),
        ).fetchall()
    finally:
        con.close()

    buckets: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        d = dict(r)
        key = (d["asset"], d["direction"])
        buckets.setdefault(key, []).append(d)

    out: list[dict] = []
    for (asset, direction), trades in buckets.items():
        if len(trades) < 2:
            continue
        total_notional = sum(float(t["size_usdt"] or 0) * float(t["leverage"] or 1)
                              for t in trades)
        total_alloc = sum(float(t["allocation_pct"] or 0) for t in trades)
        out.append({
            "asset": asset,
            "direction": direction,
            "n": len(trades),
            "total_notional_usdt": round(total_notional, 2),
            "total_alloc_pct": round(total_alloc, 2),
            "trades": trades,
        })
    return out
