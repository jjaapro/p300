"""Cross-sleeve risk caps — live enforcement of the simulator's composition
constraints.

The backtest simulator in `register_p300.py` applies two post-hoc caps to the
composition before computing daily returns:

  1. `max_net_btc` (default 15%) — on `pdo_retouch_btc + cpr_btc`. If the sum
     exceeds 15% of capital (pre-leverage), both are scaled down.
  2. `bucket_b_target` (default 40%) — on total tactical gross. If exceeded,
     all tactical sleeves are scaled proportionally.

Live services, by contrast, each open using their static `weight_pct` — no
cross-sleeve visibility. That divergence means live notional can exceed the
backtested notional, a checklist §7 inflation.

This module provides a lightweight pre-open check: given the open-position
snapshot for a variant, decide whether a new candidate trade is admissible
under the caps. We use a skip-if-over-cap policy (stricter than the
simulator's proportional down-scale) because scaling individual opens
to fractional allocations is error-prone; skipping a single day's fire is
a close-enough approximation when sleeves don't all fire every day.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from services import db

log = logging.getLogger("dashboard.risk_caps")

# Per-variant spec override keys (read from variant.spec.allocator_notes).
_DEFAULT_MAX_NET_BTC_PCT = 15.0


def _open_btc_long_alloc_pct(variant_id: str) -> float:
    """Sum of allocation_pct across currently-open LONG-BTC trades from the
    BTC-capped sleeves (PDO_RETOUCH, CPR). Pre-leverage percentage of capital.
    """
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(allocation_pct), 0) FROM trades "
            "WHERE strategy_variant = ? AND status = 'open' "
            "AND asset = 'BTC' AND direction = 'LONG' "
            "AND strategy IN ('PDO_RETOUCH', 'CPR')",
            (variant_id,),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        con.close()


def btc_long_cap_allows(variant: dict, candidate_alloc_pct: float) -> bool:
    """Return True if opening a BTC-LONG trade for `variant` at
    `candidate_alloc_pct` keeps the total open BTC-long allocation (across
    PDO_RETOUCH + CPR) at or below the variant's max_net_btc cap. Pre-leverage.
    """
    spec = variant.get("spec") or {}
    notes = spec.get("allocator_notes") or {}
    cap = float(notes.get("max_net_btc_non_core_pct", _DEFAULT_MAX_NET_BTC_PCT))
    current = _open_btc_long_alloc_pct(variant["id"])
    if current + candidate_alloc_pct > cap + 1e-9:
        log.info(f"[risk_caps] {variant['id']} BTC-long cap blocks new "
                 f"{candidate_alloc_pct:.2f}% (open={current:.2f}%, cap={cap:.2f}%)")
        return False
    return True
