"""Cross-sleeve risk caps — live enforcement of the simulator's composition
constraints.

The backtest simulator in `register_p300.py` applies two post-hoc caps to the
composition before computing daily returns:

  1. `max_net_btc_pdo_cpr_pct` (default 15%) — on `pdo_retouch_btc +
     cpr_btc`. If the sum exceeds 15% of capital (pre-leverage), both
     are scaled down. **PDO + CPR only** — other BTC-LONG sleeves
     (ADX/FOMC/CARRY/R4_BTC/R4_BTC_V2/EMA_BTC) are NOT covered by this
     cap, by design. Per-sleeve allocations already set those sleeves'
     individual ceilings; the simulator and live both rely on the
     50/50 Core/Tactical envelope to bound combined gross. The cap
     name was `max_net_btc_non_core_pct` pre-2026-05-13, which suggested
     it covered Core too — it never did. See AUDIT_2026_05_13 Methodology.
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
from strategies.support import db

log = logging.getLogger("dashboard.risk_caps")

# Per-variant spec override keys (read from variant.spec.allocator_notes).
_DEFAULT_MAX_NET_BTC_PCT = 15.0
# Sleeves whose BTC-LONG allocation counts toward the PDO+CPR cap. Kept
# as a module-level constant so the SQL filter and the cap's scope are
# defined together in one place — adding a sleeve to this set is the
# only change needed to extend the cap.
_PDO_CPR_BTC_SLEEVES = ("PDO_RETOUCH", "CPR")


def _open_btc_long_alloc_pct(variant_id: str) -> float:
    """Sum of allocation_pct across currently-open LONG-BTC trades from the
    PDO+CPR sleeve pair. Pre-leverage percentage of capital. Other
    BTC-LONG sleeves are intentionally not summed here — see module docstring.
    """
    placeholders = ",".join("?" * len(_PDO_CPR_BTC_SLEEVES))
    con = sqlite3.connect(str(db.DASH_DB))
    try:
        row = con.execute(
            f"SELECT COALESCE(SUM(allocation_pct), 0) FROM trades "
            f"WHERE strategy_variant = ? AND status = 'open' "
            f"  AND asset = 'BTC' AND direction = 'LONG' "
            f"  AND strategy IN ({placeholders})",
            (variant_id, *_PDO_CPR_BTC_SLEEVES),
        ).fetchone()
        return float(row[0] or 0.0)
    finally:
        con.close()


def btc_long_cap_allows(variant: dict, candidate_alloc_pct: float) -> bool:
    """Return True if opening a BTC-LONG trade for `variant` at
    `candidate_alloc_pct` keeps the open BTC-long allocation across
    PDO_RETOUCH + CPR at or below the variant's
    `max_net_btc_pdo_cpr_pct` cap. Pre-leverage. The cap intentionally
    excludes Core (R4_BTC, R4_BTC_V2, EMA_BTC), ADX, CARRY (spot leg),
    and FOMC — see module docstring. Back-compat: also honours the
    pre-2026-05-13 key `max_net_btc_non_core_pct` if set.
    """
    spec = variant.get("spec") or {}
    notes = spec.get("allocator_notes") or {}
    cap = float(notes.get("max_net_btc_pdo_cpr_pct",
                          notes.get("max_net_btc_non_core_pct",
                                     _DEFAULT_MAX_NET_BTC_PCT)))
    current = _open_btc_long_alloc_pct(variant["id"])
    if current + candidate_alloc_pct > cap + 1e-9:
        log.info(f"[risk_caps] {variant['id']} BTC-long cap (PDO+CPR) "
                 f"blocks new {candidate_alloc_pct:.2f}% "
                 f"(open={current:.2f}%, cap={cap:.2f}%)")
        return False
    return True
