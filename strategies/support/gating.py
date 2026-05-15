"""Per-sleeve gating framework (P2.4b).

A gate decides whether a sleeve should fire at all on a given tick and,
optionally, scales the leverage it would use. Today's three gates are
hand-tuned inside their host sleeves:

  - R4 vol-gate (``jplus_inputs._gate_for_today``):
        BTC realized-vol percentile -> half the inner R4 leverage.
        Modulator, not a binary block.
  - THU_BEAR V4 (``thu_bear.signal._v4_passes``):
        Event-calendar adjacency + OPEX exclusion -> binary entry block.
  - FOMC composite (``fomc.signal.evaluate``):
        Phase x F&G x Polymarket expected action -> binary entry block.

This module unifies them behind a single :class:`GateDecision` and a
``GATE_REGISTRY[strategy_id] -> callable`` lookup. The orchestrator
classifies regime once per tick and calls :func:`get_decision` per
sleeve; the result is injected as ``sleeve_cfg["_effective_gate"]``
alongside the existing ``_effective_leverage`` / ``_effective_weight_pct``.

Migration order:
  1. **R4 vol-gate** (this commit) — pilot. R4 sleeves consume
     ``_effective_gate.leverage_mult`` to set inner leverage; fall back
     to the legacy ``ti["gated"]`` path for direct callers (tests).
  2. THU_BEAR V4 — convert ``_v4_passes`` into a gate function returning
     ``GateDecision(fire=False, reason="v4_opex_adjacent")`` etc.
     Sleeve code shrinks to ``if not eff_gate.fire: return ...``.
  3. FOMC composite — same shape; ``evaluate``'s ``decision == "skip"``
     becomes ``fire=False`` with the same reason text.

Future work (post-P2.4):
  - Walk-forward CV protocol for gate calibration (each gate should
    have a documented out-of-sample sharpe/expectancy benchmark).
  - Compose gates: ``ChainGate([g1, g2])`` so a sleeve can register
    multiple gates and the orchestrator AND-merges their decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


@dataclass(frozen=True)
class GateDecision:
    """The orchestrator-level view of a sleeve's gate state.

    Attributes:
        fire: Whether the sleeve should attempt entry this tick.
            ``False`` is a hard block — the sleeve's dispatch logic
            should short-circuit and return a status dict noting the
            reason. ``True`` does NOT guarantee a trade fires (the
            sleeve still owns its own signal logic); it just says
            "the gate is not blocking you."
        leverage_mult: Multiplier applied to the sleeve's leverage at
            trade-open time. ``1.0`` = unchanged. R4 vol-gate uses
            ``0.4`` to de-lever from 2.5x to 1.0x when in a high-vol
            regime. Sleeves that don't modulate leverage ignore this.
        reason: Short human-readable string for logs / audit. Should
            be machine-parseable (snake_case, no spaces in keywords)
            so downstream filtering / dashboards can pivot on it.
        metadata: Gate-specific extras (event_window for V4, phase /
            fg / expected_action for FOMC). Optional.
    """
    fire: bool = True
    leverage_mult: float = 1.0
    reason: str = ""
    metadata: Optional[dict] = field(default=None)


# The default "no gate registered" decision — pass-through.
DEFAULT_DECISION = GateDecision(fire=True, leverage_mult=1.0, reason="no_gate")


# Gate function signature: (strategy_id, regime, now_utc) -> GateDecision.
# Implementations are free to ignore regime / now_utc if they don't need them
# (e.g., the R4 vol-gate reads BTC closes through clock.now() implicitly via
# today_inputs).
GateFn = Callable[[str, Optional[str], Optional[datetime]], GateDecision]


# ─── R4 vol-gate ──────────────────────────────────────────────────────────────

# R4's gated/ungated inner leverage values (see strategies.support.jplus_inputs
# constants). The gate's leverage_mult is the ratio:
#   gated   -> R4_INNER_LEV_GATED / R4_INNER_LEV_UNGATED  = 1.0 / 2.5 = 0.4
#   ungated -> 1.0
_R4_GATED_MULT = 0.4


def _r4_vol_gate(strategy_id: str,
                 regime: Optional[str],
                 now_utc: Optional[datetime]) -> GateDecision:
    """Wrap ``today_inputs()['gated']`` as a :class:`GateDecision`.

    Returns ``leverage_mult=0.4`` when the vol-percentile gate fires
    (de-lever), ``1.0`` otherwise. ``fire`` is always ``True`` — the
    R4 gate modulates size, it does not block entry.

    If ``today_inputs()`` is unavailable (cold-boot warmup), returns
    the default pass-through so the R4 sleeve's own ``no_inputs``
    short-circuit takes over.
    """
    from strategies.support import jplus_inputs
    ti = jplus_inputs.today_inputs()
    if ti is None:
        return DEFAULT_DECISION
    gated = bool(ti.get("gated"))
    return GateDecision(
        fire=True,
        leverage_mult=_R4_GATED_MULT if gated else 1.0,
        reason="r4_vol_gated" if gated else "r4_vol_ungated",
        metadata={"vol_gate": gated, "regime": ti.get("mode")},
    )


# ─── THU_BEAR V4 event filter ─────────────────────────────────────────────────

def _v4_gate(strategy_id: str,
             regime: Optional[str],
             now_utc: Optional[datetime]) -> GateDecision:
    """V4 = "Thursday, with CPI/NFP adjacency, not OPEX-adjacent."

    Only meaningful on Thursdays (S-096's firing day). On other days,
    returns the pass-through default so the orchestrator's per-tick call
    is a cheap no-op. On Thursdays, delegates to
    ``thu_bear.signal._v4_passes`` (the event-window math stays close to
    its sleeve since it owns the scheduled_events lookup + per-session
    cache).
    """
    if now_utc is None:
        return DEFAULT_DECISION
    if now_utc.weekday() != 3:  # 3 = Thursday
        return DEFAULT_DECISION
    from strategies.sleeves.thu_bear.signal import _v4_passes
    today_iso = now_utc.date().isoformat()
    ok, reason = _v4_passes(today_iso)
    return GateDecision(
        fire=ok,
        leverage_mult=1.0,
        reason=reason,
        metadata={"today_iso": today_iso},
    )


# ─── Registry ─────────────────────────────────────────────────────────────────

GATE_REGISTRY: dict[str, GateFn] = {
    "JPLUS_R4_BTC":    _r4_vol_gate,
    "JPLUS_R4_ETH":    _r4_vol_gate,
    "JPLUS_R4_BTC_V2": _r4_vol_gate,
    "JPLUS_R4_ETH_V2": _r4_vol_gate,
    "S-096":           _v4_gate,
    # FOMC composite — pending migration; see module docstring. The FOMC
    # sleeve owns calendar lookups + an observer-table cache for evaluate()
    # results, so wiring its gate through here cleanly needs its own commit.
}


def get_decision(strategy_id: str,
                 regime: Optional[str] = None,
                 now_utc: Optional[datetime] = None) -> GateDecision:
    """Look up the gate for ``strategy_id`` and return its decision.

    Sleeves with no registered gate get :data:`DEFAULT_DECISION` — a
    pass-through that doesn't block entry and doesn't modify leverage.
    """
    fn = GATE_REGISTRY.get(strategy_id)
    if fn is None:
        return DEFAULT_DECISION
    return fn(strategy_id, regime, now_utc)
