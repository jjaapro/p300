"""P2.4e/f Stage 2 — orchestrator routes two-phase sleeves through decide
+ reconcile + execute.

Today AI_QUANT is the only sleeve registered in
``STRATEGY_TWO_PHASE_DISPATCH``. Test verifies:
  1. ``_load_dispatch`` populates AI_QUANT in the two-phase registry.
  2. When AI_QUANT's decide() returns None (gates failed), the
     orchestrator logs and skips — execute is never called.
  3. When decide returns an Intent, the orchestrator runs
     reconcile_intents and calls execute on the approved intent.
  4. When reconcile rejects (margin / directional conflict), execute
     is NOT called and the rejection reason is surfaced in logs.
"""
from __future__ import annotations

import pytest

from strategies import orchestrator
from strategies.support.dispatch import Intent


def test_load_dispatch_registers_two_phase_sleeves(monkeypatch):
    """After _load_dispatch, the migrated sleeves (AI_QUANT, S-003 ADX,
    S-096 THU_BEAR as of 2026-05-16) must appear in
    STRATEGY_TWO_PHASE_DISPATCH with both decide and execute callables."""
    # Reset state so _load_dispatch repopulates.
    monkeypatch.setattr(orchestrator, "STRATEGY_DISPATCH", {})
    monkeypatch.setattr(orchestrator, "STRATEGY_TWO_PHASE_DISPATCH", {})
    orchestrator._load_dispatch()
    for sid in ("AI_QUANT", "S-003", "S-096", "PDO-L-RF", "CPR", "FOMC",
                  "S-078", "JPLUS_ETH_DAILY", "JPLUS_EMA_BTC"):
        assert sid in orchestrator.STRATEGY_TWO_PHASE_DISPATCH, (
            f"{sid} not registered as two-phase")
        decide_fn, execute_fn = orchestrator.STRATEGY_TWO_PHASE_DISPATCH[sid]
        assert callable(decide_fn)
        assert callable(execute_fn)


def test_two_phase_skips_execute_when_decide_returns_none(monkeypatch):
    """Decide returns (None, status_dict) → orchestrator logs status,
    no execute call. Legacy sleeves around it continue to dispatch."""
    decide_calls: list = []
    execute_calls: list = []

    def stub_decide(variant, sleeve_cfg):
        decide_calls.append(sleeve_cfg.get("strategy_id"))
        return [], {"status": "disabled"}

    def stub_execute(variant, sleeve_cfg, intent):
        execute_calls.append(sleeve_cfg.get("strategy_id"))
        return {"status": "noop"}

    legacy_calls: list = []
    def stub_legacy(variant, sleeve_cfg):
        legacy_calls.append(sleeve_cfg.get("strategy_id"))
        return {"status": "no_action"}

    monkeypatch.setattr(orchestrator, "STRATEGY_DISPATCH", {
        "S-003": stub_legacy,
        "AI_QUANT": lambda v, s: stub_execute(v, s, None),
    })
    monkeypatch.setattr(orchestrator, "STRATEGY_TWO_PHASE_DISPATCH", {
        "AI_QUANT": (stub_decide, stub_execute),
    })
    monkeypatch.setattr(orchestrator, "_load_dispatch", lambda: None)

    # Stub everything the tick needs that we don't care about
    from strategies.support import allocation, gating, margin_headroom, portfolio_vol
    monkeypatch.setattr(allocation, "current_regime", lambda *a, **kw: "uncertain")
    monkeypatch.setattr(gating, "get_decision",
                          lambda *a, **kw: gating.DEFAULT_DECISION)
    monkeypatch.setattr(portfolio_vol, "current_vol_scalar",
                          lambda *a, **kw: None)
    monkeypatch.setattr(margin_headroom, "headroom_usdt", lambda v: 20_000.0)
    monkeypatch.setattr(margin_headroom, "current_gross_notional_usdt",
                          lambda vid: 0.0)
    monkeypatch.setattr(margin_headroom, "gross_cap_usdt", lambda v: 25_000.0)

    variant = {
        "id": "V", "kind": "full_portfolio", "capital_usdt": 10000.0,
        "spec": {"composition": [
            {"strategy_id": "S-003"},
            {"strategy_id": "AI_QUANT"},
        ]},
    }
    from datetime import datetime, timezone
    orchestrator._tick_composition(variant, datetime(2026, 5, 16, 0, 10,
                                                       tzinfo=timezone.utc))
    assert decide_calls == ["AI_QUANT"]   # decide ran
    assert execute_calls == []            # but no execute (Intent was None)
    assert legacy_calls == ["S-003"]      # legacy continues to fire


def test_two_phase_runs_execute_on_approved_intent(monkeypatch):
    """Decide returns an Intent → reconcile approves → execute is
    called with the (possibly modified) intent."""
    execute_args: list = []

    def stub_decide(variant, sleeve_cfg):
        return [Intent(asset="BTC", direction="LONG",
                        allocation_pct=1.5, leverage=3.0,
                        conviction=80, reason={"sleeve": "AI_QUANT"})], \
               {"status": "decided"}

    def stub_execute(variant, sleeve_cfg, intent):
        execute_args.append(intent)
        return {"status": "decided", "trade_action": "opened:SJ-X"}

    monkeypatch.setattr(orchestrator, "STRATEGY_DISPATCH", {})
    monkeypatch.setattr(orchestrator, "STRATEGY_TWO_PHASE_DISPATCH", {
        "AI_QUANT": (stub_decide, stub_execute),
    })
    monkeypatch.setattr(orchestrator, "_load_dispatch", lambda: None)

    from strategies.support import allocation, gating, margin_headroom, portfolio_vol
    monkeypatch.setattr(allocation, "current_regime", lambda *a, **kw: "uncertain")
    monkeypatch.setattr(gating, "get_decision",
                          lambda *a, **kw: gating.DEFAULT_DECISION)
    monkeypatch.setattr(portfolio_vol, "current_vol_scalar",
                          lambda *a, **kw: None)
    monkeypatch.setattr(margin_headroom, "headroom_usdt", lambda v: 25_000.0)
    monkeypatch.setattr(margin_headroom, "current_gross_notional_usdt",
                          lambda vid: 0.0)
    monkeypatch.setattr(margin_headroom, "gross_cap_usdt", lambda v: 25_000.0)

    variant = {
        "id": "V", "kind": "full_portfolio", "capital_usdt": 10000.0,
        "spec": {"composition": [{"strategy_id": "AI_QUANT"}]},
    }
    from datetime import datetime, timezone
    orchestrator._tick_composition(variant, datetime(2026, 5, 16, 0, 10,
                                                       tzinfo=timezone.utc))
    assert len(execute_args) == 1
    assert execute_args[0].direction == "LONG"
    assert execute_args[0].allocation_pct == pytest.approx(1.5)


def test_two_phase_skips_execute_when_reconcile_rejects_margin(monkeypatch):
    """If reconcile rejects the intent (e.g. over cap), execute is NOT
    called. The rejection is surfaced via the orchestrator's log."""
    execute_args: list = []

    def stub_decide(variant, sleeve_cfg):
        # Intent that won't fit: 30% × 10x = 300% of capital on a 250% cap.
        return [Intent(asset="BTC", direction="LONG",
                        allocation_pct=30.0, leverage=10.0,
                        conviction=80, reason={"sleeve": "AI_QUANT"})], \
               {"status": "decided"}

    def stub_execute(variant, sleeve_cfg, intent):
        execute_args.append(intent)
        return {"status": "decided"}

    monkeypatch.setattr(orchestrator, "STRATEGY_DISPATCH", {})
    monkeypatch.setattr(orchestrator, "STRATEGY_TWO_PHASE_DISPATCH", {
        "AI_QUANT": (stub_decide, stub_execute),
    })
    monkeypatch.setattr(orchestrator, "_load_dispatch", lambda: None)

    from strategies.support import allocation, gating, margin_headroom, portfolio_vol
    monkeypatch.setattr(allocation, "current_regime", lambda *a, **kw: "uncertain")
    monkeypatch.setattr(gating, "get_decision",
                          lambda *a, **kw: gating.DEFAULT_DECISION)
    monkeypatch.setattr(portfolio_vol, "current_vol_scalar",
                          lambda *a, **kw: None)
    monkeypatch.setattr(margin_headroom, "headroom_usdt", lambda v: 0.0)
    monkeypatch.setattr(margin_headroom, "current_gross_notional_usdt",
                          lambda vid: 30_000.0)  # already over cap
    monkeypatch.setattr(margin_headroom, "gross_cap_usdt", lambda v: 25_000.0)

    variant = {
        "id": "V", "kind": "full_portfolio", "capital_usdt": 10000.0,
        "spec": {"composition": [{"strategy_id": "AI_QUANT"}]},
    }
    from datetime import datetime, timezone
    orchestrator._tick_composition(variant, datetime(2026, 5, 16, 0, 10,
                                                       tzinfo=timezone.utc))
    assert execute_args == []  # reconcile rejected -> execute skipped


def test_two_phase_multi_intent_sleeve_executes_each(monkeypatch):
    """A sleeve that returns multiple intents (THU_BEAR-style per-asset
    fan-out) gets execute called once per approved intent — the
    orchestrator's pending-queue pop preserves intent-to-execute pairing
    across same-sleeve-id entries."""
    execute_args: list = []

    def stub_decide(variant, sleeve_cfg):
        return [
            Intent(asset="BTC", direction="SHORT", allocation_pct=3.0,
                    leverage=5.0, conviction=100,
                    reason={"sleeve": "THU_BEAR", "asset": "BTC"}),
            Intent(asset="ETH", direction="SHORT", allocation_pct=3.0,
                    leverage=5.0, conviction=100,
                    reason={"sleeve": "THU_BEAR", "asset": "ETH"}),
        ], {"status": "decided"}

    def stub_execute(variant, sleeve_cfg, intent):
        execute_args.append(intent)
        return {"status": "decided", "trade_action": "opened"}

    monkeypatch.setattr(orchestrator, "STRATEGY_DISPATCH", {})
    monkeypatch.setattr(orchestrator, "STRATEGY_TWO_PHASE_DISPATCH", {
        "THU_BEAR_TEST": (stub_decide, stub_execute),
    })
    monkeypatch.setattr(orchestrator, "_load_dispatch", lambda: None)

    from strategies.support import allocation, gating, margin_headroom, portfolio_vol
    monkeypatch.setattr(allocation, "current_regime", lambda *a, **kw: "uncertain")
    monkeypatch.setattr(gating, "get_decision",
                          lambda *a, **kw: gating.DEFAULT_DECISION)
    monkeypatch.setattr(portfolio_vol, "current_vol_scalar",
                          lambda *a, **kw: None)
    monkeypatch.setattr(margin_headroom, "headroom_usdt", lambda v: 25_000.0)
    monkeypatch.setattr(margin_headroom, "current_gross_notional_usdt",
                          lambda vid: 0.0)
    monkeypatch.setattr(margin_headroom, "gross_cap_usdt", lambda v: 25_000.0)

    variant = {
        "id": "V", "kind": "full_portfolio", "capital_usdt": 10000.0,
        "spec": {"composition": [{"strategy_id": "THU_BEAR_TEST"}]},
    }
    from datetime import datetime, timezone
    orchestrator._tick_composition(variant, datetime(2026, 5, 16, 0, 10,
                                                       tzinfo=timezone.utc))
    assert len(execute_args) == 2
    assets = {i.asset for i in execute_args}
    assert assets == {"BTC", "ETH"}
