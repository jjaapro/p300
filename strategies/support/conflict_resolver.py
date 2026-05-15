"""Cross-sleeve directional conflict detection (P2.4e).

Today two sleeves can open opposite-direction trades on the same asset
within the same variant on the same day — e.g. S-003 reads LONG BTC
while S-096 V4 reads SHORT BTC on a Thursday that happens to be both
ADX-bullish and V4-event-eligible. Both trades open independently; the
positions cancel at the exchange but each leg still pays funding +
fees. The trade ledger shows two trades that net to a wash.

The orchestrator currently dispatches sleeves sequentially. Each sleeve's
``try_fire_for_variant`` is a one-shot "decide + open" — by the time the
opposing sleeve is called, the first has already opened. True
reconciliation (collect intents from every sleeve, then decide which to
fire) would need a two-phase dispatch — a substantial refactor of every
sleeve's signal.py.

This module does the lighter thing: **post-open detection** that sleeves
can opt into. A sleeve that has computed its intended ``(asset,
direction)`` calls :func:`detect_opposing_open` just before
:func:`strategies.trades.open_paper_trade`; if an opposite-direction
trade on the same asset is already open for this variant, the sleeve
can short-circuit with status ``directional_conflict`` rather than
opening a redundant offsetting position.

The trade-off is "first-come-first-served": whichever sleeve runs first
in the dispatch loop wins the slot. Composition order in the variant
spec defines that. The full P2.4e goal (priority-based reconciliation
with conviction comparison) is a Stage 2 follow-up — sleeves will
need to surface their conviction as part of the intent for that to
matter.

Scope: only directional perp positions. CARRY's delta-neutral pair is
SHORT-perp + LONG-spot on the same asset and is intentionally not
flagged as a "conflict" with another sleeve's directional LONG-perp
(they ride at different layers).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from strategies.support import db

log = logging.getLogger("p300.conflict_resolver")


# CARRY's perp leg is delta-neutral; it doesn't compete with directional
# perp opens. Treat CARRY's open SHORT-BTC perp as "not a conflict" with
# another sleeve's LONG-BTC perp open. The directional cap (P2.4d) and
# margin headroom enforcement still apply — this just means CARRY isn't
# flagged as a conflict source here.
_NEUTRAL_STRATEGIES = frozenset({"CARRY", "S-078"})


def detect_opposing_open(variant_id: str,
                          asset: str,
                          direction: str,
                          excluded_strategies: Optional[frozenset[str]] = None,
                          ) -> Optional[dict]:
    """Return the first open paper trade for ``variant_id`` on ``asset``
    with direction *opposite* to ``direction``, or ``None`` if no such
    trade exists.

    Used by a candidate sleeve to decide whether opening its trade would
    net against an already-open position on the same asset.

    Args:
        variant_id: the candidate sleeve's variant id.
        asset: the candidate asset (``"BTC"`` or ``"ETH"``).
        direction: the candidate sleeve's intended direction
            (``"LONG"`` or ``"SHORT"``). The function searches for
            trades whose direction is the OPPOSITE.
        excluded_strategies: optional override for which sleeves are
            considered "neutral" (delta-neutral pairs, etc.). Defaults
            to :data:`_NEUTRAL_STRATEGIES` (CARRY).

    Returns:
        A dict with keys ``id``, ``strategy``, ``direction``,
        ``size_usdt``, ``leverage``, ``entry_price``, or ``None`` if
        no opposing open trade exists.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"direction must be LONG or SHORT, got {direction!r}")
    opposite = "SHORT" if direction == "LONG" else "LONG"
    excluded = excluded_strategies if excluded_strategies is not None else _NEUTRAL_STRATEGIES

    placeholders = ",".join("?" * len(excluded)) if excluded else "''"
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    try:
        if excluded:
            row = con.execute(
                f"SELECT id, strategy, direction, size_usdt, leverage, entry_price "
                f"FROM trades "
                f"WHERE strategy_variant = ? AND status = 'open' "
                f"  AND execution_mode = 'paper' "
                f"  AND asset = ? AND direction = ? "
                f"  AND strategy NOT IN ({placeholders}) "
                f"ORDER BY actual_entry_time LIMIT 1",
                (variant_id, asset.upper(), opposite, *excluded),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT id, strategy, direction, size_usdt, leverage, entry_price "
                "FROM trades "
                "WHERE strategy_variant = ? AND status = 'open' "
                "  AND execution_mode = 'paper' "
                "  AND asset = ? AND direction = ? "
                "ORDER BY actual_entry_time LIMIT 1",
                (variant_id, asset.upper(), opposite),
            ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return dict(row)


def summarize_conflicts(variant_id: str) -> list[dict]:
    """List every (asset, [LONG-trade, SHORT-trade]) pair currently open
    in ``variant_id`` where directional perp positions on the same asset
    point opposite ways. CARRY's neutral pair is excluded.

    Useful for one-shot operator surveys ("show me what's currently
    cancelling"). Not called in the per-tick path; returns the
    snapshot at call time.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    con.row_factory = sqlite3.Row
    excluded = list(_NEUTRAL_STRATEGIES)
    placeholders = ",".join("?" * len(excluded))
    try:
        rows = con.execute(
            f"SELECT id, strategy, asset, direction, size_usdt, leverage, "
            f"       entry_price, actual_entry_time "
            f"FROM trades "
            f"WHERE strategy_variant = ? AND status = 'open' "
            f"  AND execution_mode = 'paper' "
            f"  AND strategy NOT IN ({placeholders}) "
            f"ORDER BY asset, direction, actual_entry_time",
            (variant_id, *excluded),
        ).fetchall()
    finally:
        con.close()

    by_asset: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        d = dict(r)
        a = d["asset"]
        by_asset.setdefault(a, {"LONG": [], "SHORT": []})[d["direction"]].append(d)

    out: list[dict] = []
    for asset, sides in by_asset.items():
        if sides["LONG"] and sides["SHORT"]:
            out.append({
                "asset": asset,
                "long_trades": sides["LONG"],
                "short_trades": sides["SHORT"],
            })
    return out
