"""Portfolio-level vol-target scalar — P2.4c.

Today's vol-target lives inside the J+ family: ``today_inputs()["lev"]``
exposes a regime-conditional scalar (computed by
:mod:`strategies.support.voltarget`) and the six J+ sleeves each multiply
it into their leverage at trade-open. Tactical sleeves don't vol-target
at all.

This module is the orchestrator-facing API for that scalar. Goal of
P2.4c: ONE scalar produced per tick from a portfolio-vol estimate (so
correlation between sleeves doesn't get double-counted), injected
uniformly into every sleeve dispatch. The migration ships in two steps:

  1. **This commit (parity-preserving):** ``current_vol_scalar`` returns
     ``today_inputs()["lev"]`` for J+ sleeves and ``None`` for everyone
     else. The orchestrator injects ``_effective_vol_scalar`` per
     sleeve; J+ sleeves consume it instead of reading ``ti["lev"]``
     directly. Tactical sleeves see the field but ignore it — leverage
     unchanged. Behavior identical to today.

  2. **Follow-up:** the implementation here gets replaced with a real
     portfolio-vol estimate (built from the trade ledger / NAV series).
     Tactical sleeves opt into consuming the scalar. The orchestrator
     wiring above does not change.

Splitting the two steps keeps the behavioral change scoped to the
math + the per-sleeve opt-ins, not to the dispatch surface.
"""
from __future__ import annotations

from typing import Optional


# J+ family — these sleeves use the vol-target scalar today.
_JPLUS_SLEEVES = frozenset({
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
})


def current_vol_scalar(strategy_id: str) -> Optional[float]:
    """Today's vol-target leverage scalar for ``strategy_id``.

    Returns a float (typically 0.6–2.5) for J+ family sleeves — delegates
    to ``jplus_inputs.today_inputs()["lev"]`` which today is the
    regime-conditional, BTC-vol-rank-based scalar.

    Returns ``None`` for sleeves that aren't part of the J+ family (or
    when ``today_inputs()`` can't classify a regime yet — cold-boot
    warmup). The orchestrator records ``None`` as
    ``_effective_vol_scalar=None``; sleeves that don't read the field
    (every tactical sleeve today) just ignore it, and J+ sleeves fall
    back to their existing ``ti["lev"]`` path.
    """
    if strategy_id not in _JPLUS_SLEEVES:
        return None
    from strategies.support import jplus_inputs
    ti = jplus_inputs.today_inputs()
    if ti is None:
        return None
    return float(ti["lev"])
