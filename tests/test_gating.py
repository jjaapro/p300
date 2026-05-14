"""P2.4b — gating framework contract + R4 vol-gate parity.

Anchors:
  1. The GateDecision dataclass is frozen, defaults are pass-through.
  2. Unregistered strategy_ids get the default (fire=True, mult=1.0).
  3. Registered R4 sleeves return leverage_mult ∈ {0.4, 1.0} keyed by
     today_inputs()["gated"] — same value the legacy
     R4_INNER_LEV_GATED/_UNGATED branch computes.
  4. Orchestrator's _tick_composition injects an _effective_gate into
     every dispatched sleeve_cfg (smoke-tested via the resolver call
     chain; we don't ride the live tick here).
"""
from __future__ import annotations

import pytest

from strategies.support import gating


def test_gate_decision_defaults_are_pass_through():
    d = gating.GateDecision()
    assert d.fire is True
    assert d.leverage_mult == 1.0
    assert d.reason == ""
    assert d.metadata is None


def test_gate_decision_frozen():
    d = gating.GateDecision()
    with pytest.raises(Exception):  # FrozenInstanceError
        d.fire = False  # type: ignore[misc]


def test_default_decision_constant_is_pass_through():
    assert gating.DEFAULT_DECISION.fire is True
    assert gating.DEFAULT_DECISION.leverage_mult == 1.0


@pytest.mark.parametrize("sid", [
    "S-003", "S-078", "S-096", "PDO-L-RF", "CPR", "FOMC", "AI_QUANT",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",  # no gates registered yet
    "NOT_A_SLEEVE",
])
def test_unregistered_sleeve_returns_default(sid):
    out = gating.get_decision(sid, "strong_bull", None)
    assert out is gating.DEFAULT_DECISION


@pytest.mark.parametrize("sid", [
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
])
def test_r4_sleeves_have_a_gate_registered(sid):
    assert sid in gating.GATE_REGISTRY


@pytest.mark.parametrize("sid", [
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
])
def test_r4_gate_matches_today_inputs_gated(monkeypatch, sid):
    """The gate's leverage_mult must equal R4_INNER_LEV_GATED /
    R4_INNER_LEV_UNGATED (=0.4) when ti['gated'] is True, and 1.0
    when False. Same numbers the legacy branch produces."""
    from strategies.support import jplus_inputs as ji

    fake = {"gated": True, "mode": "strong_bull"}
    monkeypatch.setattr(ji, "today_inputs", lambda: fake)
    d = gating.get_decision(sid, "strong_bull", None)
    assert d.fire is True
    assert d.leverage_mult == pytest.approx(0.4)
    assert d.reason == "r4_vol_gated"

    fake["gated"] = False
    d = gating.get_decision(sid, "strong_bull", None)
    assert d.leverage_mult == pytest.approx(1.0)
    assert d.reason == "r4_vol_ungated"


def test_r4_gate_cold_boot_returns_default(monkeypatch):
    """When today_inputs() is None (insufficient warmup), the R4 gate
    returns the pass-through default so the sleeve's own no_inputs
    short-circuit takes over."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: None)
    d = gating.get_decision("JPLUS_R4_BTC", None, None)
    assert d is gating.DEFAULT_DECISION


def test_r4_inner_lev_equivalence(monkeypatch):
    """Verify the leverage_mult arithmetic against the legacy constants:

      inner_lev (legacy)        == inner_lev (gate-based)
      R4_INNER_LEV_GATED        == R4_INNER_LEV_UNGATED * 0.4
      R4_INNER_LEV_UNGATED      == R4_INNER_LEV_UNGATED * 1.0
    """
    from strategies.sleeves.r4.signal import R4_INNER_LEV_GATED, R4_INNER_LEV_UNGATED
    assert R4_INNER_LEV_UNGATED * 0.4 == pytest.approx(R4_INNER_LEV_GATED)
    assert R4_INNER_LEV_UNGATED * 1.0 == pytest.approx(R4_INNER_LEV_UNGATED)


# ─── Orchestrator injection ──────────────────────────────────────────────────

def test_orchestrator_injects_effective_gate(monkeypatch):
    """_tick_composition runs gating.get_decision per sleeve and stores
    it on sleeve_cfg before dispatch. Smoke test via direct call to
    get_decision (the live tick path is exercised by integration tests)."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"gated": True, "mode": "uncertain"})

    d = gating.get_decision("JPLUS_R4_BTC", "uncertain", None)
    assert isinstance(d, gating.GateDecision)
    assert d.fire is True
    assert d.leverage_mult == pytest.approx(0.4)

    # Composition path (manually mimicking _tick_composition):
    sleeve_cfg = {"strategy_id": "JPLUS_R4_BTC"}
    sleeve_cfg["_effective_gate"] = gating.get_decision(
        sleeve_cfg["strategy_id"], "uncertain", None)
    assert sleeve_cfg["_effective_gate"].leverage_mult == pytest.approx(0.4)
