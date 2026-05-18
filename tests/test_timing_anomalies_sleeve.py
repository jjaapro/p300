"""Unit tests for strategies.sleeves.timing_anomalies.

Coverage:
  - internal registry resolves all canonical substrategies
  - dispatcher returns no intents when substrategies dict is empty/missing
  - dispatcher tags every intent with _origin_substrategy
  - dispatcher concatenates intents from multiple substrategies
  - dispatcher gracefully handles disabled/unknown substrategies
  - dispatcher swallows substrategy decide exceptions (logs and continues)
  - execute_for_variant routes back to the right substrategy
  - execute_for_variant returns a status dict for malformed intents
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from strategies.sleeves.timing_anomalies import signal as ta_signal
from strategies.sleeves.timing_anomalies import internal as ta_internal
from strategies.sleeves.timing_anomalies.config import CANONICAL_SUBSTRATEGIES
from strategies.support.dispatch import Intent


# ─── Registry ────────────────────────────────────────────────────────────────

def test_registry_knows_all_canonical_substrategies():
    known = ta_internal.known_substrategies()
    for name in CANONICAL_SUBSTRATEGIES:
        assert name in known, f"canonical substrategy {name} not in registry"


def test_registry_get_dispatch_returns_callables_for_known():
    for name in ["FOMC", "THU_BEAR", "PDO_L_RF", "CPR",
                  "R4_BTC", "R4_ETH", "R4_BTC_V2", "R4_ETH_V2"]:
        dispatch = ta_internal.get_dispatch(name)
        assert dispatch is not None, f"get_dispatch({name}) returned None"
        decide_fn, execute_fn = dispatch
        assert callable(decide_fn), f"{name} decide_fn not callable"
        assert callable(execute_fn), f"{name} execute_fn not callable"


def test_registry_get_dispatch_unknown_returns_none():
    assert ta_internal.get_dispatch("NOT_A_SUBSTRATEGY") is None


def test_registry_get_dispatch_case_insensitive():
    a = ta_internal.get_dispatch("FOMC")
    b = ta_internal.get_dispatch("fomc")
    assert a is b   # cache returns same tuple


# ─── Dispatcher contract ─────────────────────────────────────────────────────

def _mk_intent(asset: str = "BTC", direction: str = "LONG", reason_extra: dict | None = None):
    return Intent(
        asset=asset, direction=direction,
        allocation_pct=5.0, leverage=1.0,
        conviction=100, priority=100,
        reason=dict(reason_extra or {}),
        scheduled_exit_dt=datetime(2026, 5, 18, tzinfo=timezone.utc),
    )


def test_decide_no_substrategies_returns_empty():
    intents, status = ta_signal.try_decide_for_variant(
        {"id": "v1"},
        {"params": {}},
    )
    assert intents == []
    assert status["status"] == "no_substrategies_configured"


def test_decide_empty_substrategies_dict_returns_empty():
    intents, status = ta_signal.try_decide_for_variant(
        {"id": "v1"},
        {"params": {"substrategies": {}}},
    )
    assert intents == []
    assert status["status"] == "no_substrategies_configured"


def test_decide_disabled_substrategy_skipped():
    intents, status = ta_signal.try_decide_for_variant(
        {"id": "v1"},
        {"params": {"substrategies": {
            "FOMC": {"enabled": False, "weight_pct": 5.0, "params": {}},
        }}},
    )
    assert intents == []
    assert status["per_substrategy"]["FOMC"]["status"] == "disabled"


def test_decide_unknown_substrategy_skipped():
    intents, status = ta_signal.try_decide_for_variant(
        {"id": "v1"},
        {"params": {"substrategies": {
            "NOT_REAL": {"enabled": True, "weight_pct": 5.0, "params": {}},
        }}},
    )
    assert intents == []
    assert status["per_substrategy"]["NOT_REAL"]["status"] == "unknown_substrategy"


def test_decide_invalid_substrategy_config_skipped():
    intents, status = ta_signal.try_decide_for_variant(
        {"id": "v1"},
        {"params": {"substrategies": {
            "FOMC": "not a dict",   # malformed
        }}},
    )
    assert intents == []
    assert status["per_substrategy"]["FOMC"]["status"] == "invalid_config"


def test_decide_tags_intent_with_origin_substrategy():
    fake_intent = _mk_intent()
    fake_decide = lambda variant, cfg: ([fake_intent], {"status": "decided"})
    fake_execute = lambda variant, cfg, intent: {"status": "opened"}

    with patch.object(ta_signal, "_get_dispatch", return_value=(fake_decide, fake_execute)):
        intents, status = ta_signal.try_decide_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "params": {}},
            }}},
        )
    assert len(intents) == 1
    assert intents[0].reason["_origin_substrategy"] == "FOMC"


def test_decide_concatenates_intents_from_multiple_substrategies():
    fomc_intent  = _mk_intent("BTC")
    pdo_intent_a = _mk_intent("BTC")
    pdo_intent_b = _mk_intent("ETH")

    def fake_get_dispatch(name):
        if name == "FOMC":
            return (lambda v, c: ([fomc_intent], {"status": "ok"}),
                    lambda v, c, i: {})
        if name == "PDO_L_RF":
            return (lambda v, c: ([pdo_intent_a, pdo_intent_b], {"status": "ok"}),
                    lambda v, c, i: {})
        return None

    with patch.object(ta_signal, "_get_dispatch", side_effect=fake_get_dispatch):
        intents, status = ta_signal.try_decide_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC":     {"enabled": True, "weight_pct": 5.0, "params": {}},
                "PDO_L_RF": {"enabled": True, "weight_pct": 9.0, "params": {}},
            }}},
        )
    assert len(intents) == 3   # 1 FOMC + 2 PDO
    origins = [i.reason["_origin_substrategy"] for i in intents]
    assert origins.count("FOMC") == 1
    assert origins.count("PDO_L_RF") == 2


def test_decide_swallows_substrategy_exception():
    def raises(*a, **kw): raise RuntimeError("boom")

    with patch.object(ta_signal, "_get_dispatch",
                       return_value=(raises, lambda *a, **kw: {})):
        intents, status = ta_signal.try_decide_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "params": {}},
            }}},
        )
    assert intents == []
    assert status["per_substrategy"]["FOMC"]["status"] == "decide_exception"


def test_decide_passes_through_substrategy_weight_and_leverage():
    captured = {}
    def fake_decide(variant, cfg):
        captured.update(cfg)
        return [], {"status": "captured"}

    # Pin allocator lookup off so the test exercises the static-fallback path.
    with patch.object(ta_signal, "_get_dispatch",
                       return_value=(fake_decide, lambda *a, **kw: {})), \
         patch.object(ta_signal, "_resolve_substrategy_weight",
                       side_effect=lambda name, fallback: fallback):
        ta_signal.try_decide_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "leverage": 10.0,
                          "params": {"stop_loss_pct": 5.0}},
            }}},
        )

    assert captured["weight_pct"] == 5.0
    assert captured["leverage"] == 10.0
    assert captured["params"]["stop_loss_pct"] == 5.0
    assert captured["_substrategy_name"] == "FOMC"


def test_resolve_substrategy_weight_uses_allocator_when_regime_known():
    """The meta-sleeve translates substrategy_name -> allocator strategy_id
    and queries allocation.get_weight_pct. Verifies the translation hop
    and that the returned value flows through."""
    import strategies.support.allocation as allocation
    with patch.object(allocation, "current_regime", return_value="mild_bull"), \
         patch.object(allocation, "get_weight_pct",
                       return_value=20.0) as get_w:
        w = ta_signal._resolve_substrategy_weight("R4_BTC", fallback=0.0)
    # Allocator was called with the LEGACY strategy_id, not the substrategy name
    get_w.assert_called_once_with("JPLUS_R4_BTC", "mild_bull")
    assert w == 20.0


def test_resolve_substrategy_weight_falls_back_when_regime_unknown():
    import strategies.support.allocation as allocation
    with patch.object(allocation, "current_regime", return_value=None):
        w = ta_signal._resolve_substrategy_weight("R4_BTC", fallback=15.0)
    assert w == 15.0


def test_resolve_substrategy_weight_falls_back_when_unknown_substrategy():
    w = ta_signal._resolve_substrategy_weight("NOT_A_SUBSTRATEGY", fallback=7.5)
    assert w == 7.5


# ─── Execute routing ─────────────────────────────────────────────────────────

def test_execute_missing_origin_returns_status():
    intent = _mk_intent(reason_extra={"foo": "bar"})
    result = ta_signal.execute_for_variant({"id": "v1"}, {"params": {}}, intent)
    assert result["status"] == "missing_origin"


def test_execute_unknown_origin_returns_status():
    intent = _mk_intent(reason_extra={"_origin_substrategy": "GHOST"})
    result = ta_signal.execute_for_variant({"id": "v1"}, {"params": {}}, intent)
    assert result["status"] == "unknown_origin"
    assert result["origin"] == "GHOST"


def test_execute_routes_to_origin_substrategy():
    routed_args = {}
    def fake_execute(variant, cfg, intent):
        routed_args["variant"] = variant
        routed_args["cfg"] = cfg
        routed_args["intent"] = intent
        return {"status": "opened", "trade_id": "T-123"}

    intent = _mk_intent(reason_extra={"_origin_substrategy": "FOMC"})

    with patch.object(ta_signal, "_get_dispatch",
                       return_value=(lambda *a, **kw: ([], {}), fake_execute)):
        result = ta_signal.execute_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "leverage": 10.0,
                          "params": {"stop_loss_pct": 5.0}},
            }}},
            intent,
        )

    assert result["status"] == "executed"
    assert result["origin"] == "FOMC"
    assert result["result"]["trade_id"] == "T-123"
    # The per-substrategy cfg was correctly rebuilt from the meta-sleeve cfg
    assert routed_args["cfg"]["weight_pct"] == 5.0
    assert routed_args["cfg"]["leverage"] == 10.0


def test_execute_swallows_substrategy_exception():
    def raises(*a, **kw): raise RuntimeError("execute boom")

    intent = _mk_intent(reason_extra={"_origin_substrategy": "FOMC"})

    with patch.object(ta_signal, "_get_dispatch",
                       return_value=(lambda *a, **kw: ([], {}), raises)):
        result = ta_signal.execute_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "params": {}},
            }}},
            intent,
        )
    assert result["status"] == "execute_exception"
    assert result["origin"] == "FOMC"


# ─── Orchestrator wiring ─────────────────────────────────────────────────────

def test_orchestrator_recognizes_timing_anomalies():
    from strategies import orchestrator
    orchestrator._load_dispatch()
    assert "TIMING_ANOMALIES" in orchestrator.STRATEGY_DISPATCH
    assert "TIMING_ANOMALIES" in orchestrator.STRATEGY_TWO_PHASE_DISPATCH


# ─── try_fire_for_variant wraps decide + execute ─────────────────────────────

def test_try_fire_runs_decide_and_executes_each_intent():
    intent = _mk_intent(reason_extra={"already_tagged": False})

    decide_calls = {"n": 0}
    def fake_decide(variant, cfg):
        decide_calls["n"] += 1
        return [intent], {"status": "decided"}

    execute_calls = {"n": 0}
    def fake_execute(variant, cfg, i):
        execute_calls["n"] += 1
        return {"trade_id": "T-X"}

    with patch.object(ta_signal, "_get_dispatch",
                       return_value=(fake_decide, fake_execute)):
        result = ta_signal.try_fire_for_variant(
            {"id": "v1"},
            {"params": {"substrategies": {
                "FOMC": {"enabled": True, "weight_pct": 5.0, "params": {}},
            }}},
        )
    assert decide_calls["n"] == 1
    assert execute_calls["n"] == 1
    assert result["status"] == "fired"
