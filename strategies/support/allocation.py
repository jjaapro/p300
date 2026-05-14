"""Single source of truth for per-sleeve, per-regime allocation.

Today there are two parallel allocation code paths: tactical sleeves
read a static ``weight_pct`` from the composition spec in
``register_p300.py``; J+ sub-sleeves read regime-weighted sizing from
``today_inputs()``. This module unifies both into a single
``WEIGHT_TABLE[strategy_id][regime] -> float`` lookup that any sleeve
can consume identically.

Regime vocabulary: the four J+ modes
(``strong_bull`` / ``mild_bull`` / ``uncertain`` / ``bear``). The
tactical-regime classifier (``regime_tactical.py``) keeps its own
vocabulary for sleeve-internal gating; allocation only needs one
regime per tick.

Cap policy: ``CORE_ALLOC_CAP=0.50`` is preserved as a *transitional*
runtime cap on the J+ family while orchestrator-level allocation is
being built out (see P2.4 in BACKLOG.md). It is NOT a policy split
between Core and Tactical — that 50/50 tier split was dropped
2026-05-14 (memory ``feedback_no_core_tactical_tiers``). The cap
exists so a regime-table change can't silently over-allocate the J+
family before full cross-sleeve allocation logic lands.

Migration path (P2.4a):
  1. Orchestrator injects ``_effective_weight_pct`` into each sleeve
     dispatch via ``_resolve_sleeve_weight``.
  2. Each sleeve's ``signal.py`` switches from
     ``sleeve_cfg["weight_pct"]`` to
     ``sleeve_cfg["_effective_weight_pct"]`` with a fallback to
     ``weight_pct`` for unit-test sleeve_cfg dicts that don't go
     through orchestrator dispatch.
  3. Parity test (``tests/test_allocation_parity.py``) asserts the
     new table reproduces today's behavior for every (sleeve, regime).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("p300.allocation")


REGIMES: tuple[str, ...] = ("strong_bull", "mild_bull", "uncertain", "bear")


# ─── J+ family ────────────────────────────────────────────────────────────────

# Six sub-sleeves whose combined raw weight is capped to CORE_ALLOC_CAP at
# lookup time. Pulled from REGIME_WEIGHTS_FULL in jplus_inputs.py so the two
# tables stay in lock-step until the J+ sleeves migrate fully.
_CORE_SLEEVES = frozenset({
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
})

CORE_ALLOC_CAP = 0.50


# ─── Weight table ─────────────────────────────────────────────────────────────

# Values are pre-leverage fractions of variant capital (NOT percentages).
# Tactical rows: regime-independent — same value across all four regimes.
# Per-regime tactical tuning is deferred (see BACKLOG P2.4a decisions).
# J+ rows: copied from jplus_inputs.REGIME_WEIGHTS_FULL — raw values before
# CORE_ALLOC_CAP scaling. get_weight_pct() applies the cap at lookup time.
WEIGHT_TABLE: dict[str, dict[str, float]] = {
    # Tactical — values mirror register_p300.py composition weight_pct/100.
    "S-003":           {r: 0.15 for r in REGIMES},   # ADX
    "S-078":           {r: 0.08 for r in REGIMES},   # CARRY
    "S-096":           {r: 0.06 for r in REGIMES},   # THU_BEAR V4
    "PDO-L-RF":        {r: 0.09 for r in REGIMES},
    "CPR":             {r: 0.05 for r in REGIMES},
    "FOMC":            {r: 0.05 for r in REGIMES},
    "AI_QUANT":        {r: 0.02 for r in REGIMES},
    # J+ sub-sleeves — raw values from REGIME_WEIGHTS_FULL.
    "JPLUS_R4_BTC":    {"strong_bull": 0.15,  "mild_bull": 0.20, "uncertain": 0.30, "bear": 0.00},
    "JPLUS_R4_ETH":    {"strong_bull": 0.15,  "mild_bull": 0.30, "uncertain": 0.40, "bear": 0.00},
    "JPLUS_R4_BTC_V2": {"strong_bull": 0.075, "mild_bull": 0.10, "uncertain": 0.15, "bear": 0.00},
    "JPLUS_R4_ETH_V2": {"strong_bull": 0.075, "mild_bull": 0.15, "uncertain": 0.20, "bear": 0.00},
    "JPLUS_EMA_BTC":   {"strong_bull": 0.50,  "mild_bull": 0.30, "uncertain": 0.30, "bear": 0.30},
    "JPLUS_ETH_DAILY": {"strong_bull": 0.20,  "mild_bull": 0.10, "uncertain": 0.00, "bear": 0.00},
}


# ─── Public API ───────────────────────────────────────────────────────────────

def current_regime(now_utc: Optional[datetime] = None) -> Optional[str]:
    """Return today's J+ regime label, or None if warmup is insufficient.

    Delegates to ``jplus_inputs.today_inputs()``. ``now_utc`` is currently
    informational — today_inputs reads the simulated/wall clock directly
    and produces the same answer for any time of day until midnight UTC.
    """
    from strategies.support import jplus_inputs
    ti = jplus_inputs.today_inputs()
    if ti is None:
        return None
    return ti.get("mode")


def get_weight_pct(strategy_id: str,
                   regime: Optional[str] = None) -> Optional[float]:
    """Return today's pre-leverage allocation as a percentage (e.g. 15.0
    for 15% of variant capital).

    Args:
        strategy_id: composition entry's ``strategy_id`` (e.g. ``"S-003"``).
        regime: explicit regime override. ``None`` looks up the current
            regime via :func:`current_regime`.

    Returns:
        Allocation percentage as a float. ``None`` means "I have no
        answer — caller should fall back to ``sleeve_cfg['weight_pct']``"
        and signals one of three cases:

          * regime classification not yet possible (cold-boot warmup),
          * unknown ``strategy_id`` (table miss),
          * regime label not present on the row (typo / future regime).

    For sleeves in the J+ family (:data:`_CORE_SLEEVES`), the returned
    value is scaled by :data:`CORE_ALLOC_CAP` when the family's raw
    combined weight for the current regime exceeds the cap (matches
    ``jplus_inputs._cap_core_weights``).
    """
    if regime is None:
        regime = current_regime()
    if regime is None:
        return None
    row = WEIGHT_TABLE.get(strategy_id)
    if row is None:
        return None
    raw = row.get(regime)
    if raw is None:
        return None
    if strategy_id in _CORE_SLEEVES:
        raw = _scale_for_core_cap(regime, raw)
    return raw * 100.0


def _scale_for_core_cap(regime: str, raw: float) -> float:
    """Scale ``raw`` proportionally if the J+ family's combined raw weight
    for ``regime`` exceeds :data:`CORE_ALLOC_CAP`. Mirrors
    ``_cap_core_weights`` in :mod:`strategies.support.jplus_inputs`."""
    total = sum(WEIGHT_TABLE[sid].get(regime, 0.0) for sid in _CORE_SLEEVES)
    if total <= CORE_ALLOC_CAP or total <= 0:
        return raw
    return raw * (CORE_ALLOC_CAP / total)
