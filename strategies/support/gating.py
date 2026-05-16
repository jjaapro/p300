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

Three gates registered:
  1. **R4 vol-gate** — wraps ``today_inputs()['gated']``. Returns
     ``leverage_mult=0.4`` (gated) or ``1.0`` (ungated). Always
     ``fire=True``. Registered for the four R4 sleeves; R4 handlers
     read ``_effective_gate.leverage_mult`` directly.
  2. **THU_BEAR V4** — wraps ``_v4_passes``. Calendar-checks
     Thursday-only, then queries the scheduled_events table for
     CPI/NFP adjacency + OPEX exclusion. ``fire=False`` blocks entry.
     Registered for S-096; sleeve consumes via ``_effective_gate``.
  3. **FOMC composite** — reads the ``fomc_observer`` table for the
     cached ``evaluate()`` result on the next FOMC date. Calendar-
     checks for FOMC within 2 days first. Returns ``fire`` based on
     ``decision == "trade"``. Registered for FOMC; the sleeve still
     owns its own decision logic (the gate mirrors it for operator
     visibility — health-report sees the same value the sleeve will
     act on).

Walk-forward CV protocol for new gates: see ``GATE_VALIDATION.md`` at
the repo root. Future gates (and re-builds of the in-sample V4 /
FOMC composite) must produce a stitched-OOS notebook + a docstring
uplift figure before live promotion.

Future work (post-P2.4):
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


# ─── FOMC composite filter ────────────────────────────────────────────────────

def _fomc_gate(strategy_id: str,
               regime: Optional[str],
               now_utc: Optional[datetime]) -> GateDecision:
    """Composite filter: phase × F&G bucket × Polymarket expected action.

    The FOMC sleeve maintains an observer table
    (``fomc_observer`` in ``data/databases/prod.db``) where each FOMC date's
    composite ``evaluate()`` result is cached for the lifetime of that
    meeting cycle. This gate reads the cache rather than running
    ``evaluate()`` per tick — the call hits three external services and
    is expensive.

    On non-FOMC ticks (most), short-circuits to pass-through after a
    cheap calendar lookup. On FOMC days where the sleeve has not yet
    pre-decided (Phase 1 runs at T-11h), also returns pass-through;
    the sleeve's own Phase 1 logic will populate the observer row.
    """
    if now_utc is None:
        return DEFAULT_DECISION
    # Calendar lookup (~ms). Returns None when no FOMC is within window.
    try:
        from strategies.sleeves.fomc.signal import next_fomc_date
    except ImportError:
        return DEFAULT_DECISION
    fomc_date = next_fomc_date(now_utc, lookahead_days=2)
    if fomc_date is None:
        return DEFAULT_DECISION

    import sqlite3
    from strategies.support import db
    con = sqlite3.connect(str(db.TRADER_DB))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT decision, reason, phase, fear_greed_bucket, expected_action "
            "FROM fomc_observer WHERE fomc_date = ?",
            (fomc_date,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None  # table missing — fall back to pass-through
    finally:
        con.close()

    if row is None:
        return DEFAULT_DECISION

    decision = (row["decision"] or "").lower()
    fire = (decision == "trade")
    reason = row["reason"] or ("fomc_eval_trade" if fire else "fomc_eval_skip")
    return GateDecision(
        fire=fire,
        leverage_mult=1.0,
        reason=reason[:200],
        metadata={
            "fomc_date": fomc_date,
            "phase": row["phase"],
            "fear_greed_bucket": row["fear_greed_bucket"],
            "expected_action": row["expected_action"],
        },
    )


# ─── Registry ─────────────────────────────────────────────────────────────────

GATE_REGISTRY: dict[str, GateFn] = {
    "JPLUS_R4_BTC":    _r4_vol_gate,
    "JPLUS_R4_ETH":    _r4_vol_gate,
    "JPLUS_R4_BTC_V2": _r4_vol_gate,
    "JPLUS_R4_ETH_V2": _r4_vol_gate,
    "S-096":           _v4_gate,
    "FOMC":            _fomc_gate,
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
