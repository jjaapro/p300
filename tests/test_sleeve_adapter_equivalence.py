"""The legacy `(variant, sleeve_cfg)` adapters must be pure passthroughs.

During the orchestrator strip each sleeve grows a plain-keyword
`decide()`/`execute()` as its real implementation, and its old entry points
shrink to adapters that unpack the cfg dict. Two surfaces are live at once:
the bot runners and `backtest_runner` still call the old names, the goldens
call the new ones.

That is only safe while the adapters hold NO logic. These tests pin the
unpacking itself — the one place a divergence could hide — by capturing what
the adapter forwards. A sleeve appears here the commit it is migrated.

Note the defaults are the sleeve's OWN, not uniform: squeeze_bull's
`weight_pct` falls back to 0.0 (not 100.0) when the cfg omits it, which is the
behaviour the orchestrator relied on. Changing that during a "tidy-up" would
silently size every orchestrator-path trade at zero.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

import pathlib

import pytest

VARIANT = {"id": "v_equiv", "capital_usdt": 10_000.0}


def _capture(monkeypatch, mod, name="decide"):
    seen = {}

    def fake(variant, **kw):
        seen["variant"] = variant
        seen["kwargs"] = kw
        return [], {"status": "captured"}

    monkeypatch.setattr(mod, name, fake)
    return seen


# ── squeeze_bull ───────────────────────────────────────────────────────────

def test_squeeze_bull_adapter_forwards_the_full_cfg_surface(monkeypatch):
    from strategies.sleeves.squeeze_bull import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {
        "_effective_weight_pct": 42.0, "weight_pct": 7.0,
        "_effective_leverage": 3.0, "priority": 55.0, "use_stop": False})
    assert seen["variant"] is VARIANT
    assert seen["kwargs"] == {"weight_pct": 42.0, "leverage": 3.0,
                              "priority": 55.0, "use_stop": False}


def test_squeeze_bull_adapter_prefers_effective_weight_over_plain(monkeypatch):
    """`_effective_weight_pct` wins when both are present — that is the
    orchestrator's injection overriding the composition's static weight."""
    from strategies.sleeves.squeeze_bull import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {"_effective_weight_pct": 11.0,
                                            "weight_pct": 99.0})
    assert seen["kwargs"]["weight_pct"] == 11.0


def test_squeeze_bull_adapter_falls_back_to_plain_weight(monkeypatch):
    from strategies.sleeves.squeeze_bull import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {"weight_pct": 99.0})
    assert seen["kwargs"]["weight_pct"] == 99.0


def test_squeeze_bull_adapter_defaults_match_the_pre_strip_behaviour(monkeypatch):
    """An empty cfg must produce exactly what the old inline `.get` chain
    produced: weight 0.0, leverage 1.0, priority 100, use_stop True."""
    from strategies.sleeves.squeeze_bull import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": 0.0, "leverage": 1.0,
                              "priority": 100.0, "use_stop": True}


def test_squeeze_bull_execute_adapter_ignores_the_cfg(monkeypatch):
    """execute() takes no cfg at all — everything it needs is on the Intent.
    The adapter must drop the dict rather than smuggle anything out of it."""
    from strategies.sleeves.squeeze_bull import signal as sleeve

    seen = {}
    monkeypatch.setattr(sleeve, "execute",
                        lambda variant, intent: seen.update(
                            variant=variant, intent=intent) or {"status": "ok"})
    sentinel = object()
    sleeve.execute_for_variant(VARIANT, {"anything": "at all"}, sentinel)
    assert seen == {"variant": VARIANT, "intent": sentinel}


def test_squeeze_bull_try_fire_still_routes_through_the_adapters(monkeypatch):
    """try_fire_for_variant is the ONLY dispatch backtest_runner consults, so
    it must keep working for the whole strip — it outlives the two adapters."""
    from strategies.sleeves.squeeze_bull import signal as sleeve

    monkeypatch.setattr(sleeve, "decide",
                        lambda variant, **kw: ([], {"status": "no_flush"}))
    assert sleeve.try_fire_for_variant(VARIANT, {}) == {"status": "no_flush"}


@pytest.mark.parametrize("name", ["decide", "execute", "try_decide_for_variant",
                                  "execute_for_variant", "try_fire_for_variant"])
def test_squeeze_bull_exposes_both_surfaces(name):
    from strategies.sleeves.squeeze_bull import signal as sleeve
    assert callable(getattr(sleeve, name, None)), \
        f"squeeze_bull lost {name}; both surfaces must stay live until phase D"


# ── carry ──────────────────────────────────────────────────────────────────

def test_carry_adapter_forwards_the_full_cfg_surface(monkeypatch):
    from strategies.sleeves.carry import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {
        "_effective_weight_pct": 8.0, "weight_pct": 1.0,
        "_effective_leverage": 2.0, "priority": 30.0})
    assert seen["variant"] is VARIANT
    # carry has no use_stop / params / count_diag — three keys, that is all.
    assert seen["kwargs"] == {"weight_pct": 8.0, "leverage": 2.0,
                              "priority": 30.0}


def test_carry_adapter_defaults_match_the_pre_strip_behaviour(monkeypatch):
    from strategies.sleeves.carry import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": 0.0, "leverage": 1.0,
                              "priority": 100.0}


def test_carry_execute_adapter_ignores_the_cfg(monkeypatch):
    from strategies.sleeves.carry import signal as sleeve

    seen = {}
    monkeypatch.setattr(sleeve, "execute",
                        lambda variant, intent: seen.update(
                            variant=variant, intent=intent) or {"status": "ok"})
    sentinel = object()
    sleeve.execute_for_variant(VARIANT, {"anything": "at all"}, sentinel)
    assert seen == {"variant": VARIANT, "intent": sentinel}


def test_carry_exit_sweep_still_runs_when_no_intent_is_returned(monkeypatch):
    """CARRY's decide() closes the WHOLE BOOK on the 30-day cumulative-funding
    rule and returns no Intent when it does. The adapter must not swallow that
    status — SJ-4242 has been open since 2026-07-22 and this is its exit."""
    from strategies.sleeves.carry import signal as sleeve

    monkeypatch.setattr(sleeve, "decide", lambda variant, **kw: (
        [], {"status": "closed", "trade_ids": ["SJ-4242"]}))
    assert sleeve.try_fire_for_variant(VARIANT, {}) == {
        "status": "closed", "trade_ids": ["SJ-4242"]}


@pytest.mark.parametrize("name", ["decide", "execute", "try_decide_for_variant",
                                  "execute_for_variant", "try_fire_for_variant"])
def test_carry_exposes_both_surfaces(name):
    from strategies.sleeves.carry import signal as sleeve
    assert callable(getattr(sleeve, name, None)), \
        f"carry lost {name}; both surfaces must stay live until phase D"


# ── chento_triple_v3 ───────────────────────────────────────────────────────

def test_chento_adapter_forwards_the_full_cfg_surface(monkeypatch):
    from strategies.sleeves.chento_triple_v3 import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {
        "_effective_weight_pct": 10.0, "weight_pct": 2.0,
        "_effective_leverage": 5.0, "priority": 20.0})
    assert seen["kwargs"] == {"weight_pct": 10.0, "leverage": 5.0,
                              "priority": 20.0}


def test_chento_adapter_defaults_match_the_pre_strip_behaviour(monkeypatch):
    from strategies.sleeves.chento_triple_v3 import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": 0.0, "leverage": 1.0,
                              "priority": 100.0}


def test_chento_package_reexports_both_surfaces():
    """The bot imports the PACKAGE, not the signal module, so the new names
    must be re-exported there or the repoint in phase C step 15-20 fails."""
    from strategies.sleeves import chento_triple_v3 as pkg
    for name in ("decide", "execute", "try_decide_for_variant",
                 "execute_for_variant", "try_fire_for_variant"):
        assert callable(getattr(pkg, name, None)),             f"chento package does not re-export {name}"
        assert name in pkg.__all__, f"{name} missing from chento __all__"


def test_chento_package_and_module_are_the_same_functions():
    from strategies.sleeves import chento_triple_v3 as pkg
    from strategies.sleeves.chento_triple_v3 import signal
    assert pkg.decide is signal.decide
    assert pkg.execute is signal.execute


# ── short_squeeze ──────────────────────────────────────────────────────────

def test_short_squeeze_adapter_forwards_the_full_cfg_surface(monkeypatch):
    """The widest surface of the six: five keys, including both per-variant
    flags that separate the live paper twins."""
    from strategies.sleeves.short_squeeze import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {
        "_effective_weight_pct": 6.0, "weight_pct": 1.0,
        "_effective_leverage": 4.0, "priority": 15.0,
        "use_stop": False, "count_diag": False})
    assert seen["kwargs"] == {"weight_pct": 6.0, "leverage": 4.0,
                              "priority": 15.0, "use_stop": False,
                              "count_diag": False}


def test_short_squeeze_adapter_defaults_match_the_pre_strip_behaviour(monkeypatch):
    from strategies.sleeves.short_squeeze import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": 0.0, "leverage": 1.0,
                              "priority": 100.0, "use_stop": True,
                              "count_diag": True}


def test_short_squeeze_count_diag_is_independent_of_use_stop(monkeypatch):
    """The runner passes count_diag=(k==0) and use_stop per variant, so the
    two must not be conflated — variant 0 counts diagnostics AND keeps its
    stop, variant 1 does neither."""
    from strategies.sleeves.short_squeeze import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {"use_stop": True,
                                            "count_diag": False})
    assert seen["kwargs"]["use_stop"] is True
    assert seen["kwargs"]["count_diag"] is False


@pytest.mark.parametrize("name", ["decide", "execute", "try_decide_for_variant",
                                  "execute_for_variant", "try_fire_for_variant"])
def test_short_squeeze_exposes_both_surfaces(name):
    from strategies.sleeves.short_squeeze import signal as sleeve
    assert callable(getattr(sleeve, name, None)),         f"short_squeeze lost {name}; both surfaces must stay live until phase D"


# ── adx ────────────────────────────────────────────────────────────────────

def test_adx_adapter_forwards_the_full_cfg_surface(monkeypatch):
    """ADX is the only sleeve reading a NESTED params dict, and the only one
    whose leverage is load-bearing rather than decorative."""
    from strategies.sleeves.adx import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {
        "_effective_weight_pct": 15.0, "weight_pct": 3.0,
        "_effective_leverage": 4.0, "priority": 40.0,
        "params": {"stop_loss_pct": 7.5}})
    assert seen["kwargs"] == {"weight_pct": 15.0, "leverage": 4.0,
                              "priority": 40.0, "stop_loss_pct": 7.5}


def test_adx_adapter_defaults_match_the_pre_strip_behaviour(monkeypatch):
    """Note stop_loss_pct defaults to 10.0, not 0.0 — it is a risk parameter
    whose absence must not mean "no stop"."""
    from strategies.sleeves.adx import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": 0.0, "leverage": 1.0,
                              "priority": 100.0, "stop_loss_pct": 10.0}


def test_adx_adapter_survives_a_missing_params_dict(monkeypatch):
    """`sleeve_cfg.get("params") or {}` — a None params must not raise. The
    orchestrator omits the key entirely for sleeves with no parameters."""
    from strategies.sleeves.adx import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {"params": None})
    assert seen["kwargs"]["stop_loss_pct"] == 10.0


def test_adx_adapter_keeps_leverage_reaching_the_stop_semantics(monkeypatch):
    """The coupling that makes ADX different: leverage feeds
    effective_price_move_sl_pct, whose result stop_path reads back off the
    trade notes on the live close path. The adapter must forward it, not
    default it."""
    from strategies.sleeves.adx import signal as sleeve

    seen = _capture(monkeypatch, sleeve)
    sleeve.try_decide_for_variant(VARIANT, {"_effective_leverage": 9.0})
    assert seen["kwargs"]["leverage"] == 9.0


@pytest.mark.parametrize("name", ["decide", "execute", "try_decide_for_variant",
                                  "execute_for_variant", "try_fire_for_variant"])
def test_adx_exposes_both_surfaces(name):
    from strategies.sleeves.adx import signal as sleeve
    assert callable(getattr(sleeve, name, None)),         f"adx lost {name}; both surfaces must stay live until phase D"


# ── r4 ─────────────────────────────────────────────────────────────────────
#
# The one sleeve where the adapter must forward ABSENCE, not a default. Its
# runner passes only {"priority": 100}, so weight/gate/vol_scalar arrive as
# None and the fallback arms — the timing-anomaly weights table with its
# bear-regime zero, the gated inner leverage, the vol leverage — are the live
# behaviour. An adapter that substituted numbers would disable the regime
# kill switch, and r4's next enabled fire is 2026-10-02, so nothing live
# would show it until October.

def _r4():
    from strategies.sleeves.timing_anomalies.internal.r4 import signal
    return signal


@pytest.mark.parametrize("legacy, new", [
    ("r4_btc_decide", "decide_btc"),
    ("r4_eth_decide", "decide_eth"),
    ("r4_btc_v2_decide", "decide_btc_v2"),
    ("r4_eth_v2_decide", "decide_eth_v2"),
])
def test_r4_adapters_forward_the_full_cfg_surface(monkeypatch, legacy, new):
    sleeve = _r4()
    gate = object()
    seen = _capture(monkeypatch, sleeve, new)
    getattr(sleeve, legacy)(VARIANT, {
        "_effective_weight_pct": 25.0, "_effective_gate": gate,
        "_effective_vol_scalar": 1.5, "priority": 7.0})
    assert seen["kwargs"] == {"weight_pct": 25.0, "gate": gate,
                              "vol_scalar": 1.5, "priority": 7.0}


@pytest.mark.parametrize("legacy, new", [
    ("r4_btc_decide", "decide_btc"),
    ("r4_eth_decide", "decide_eth"),
    ("r4_btc_v2_decide", "decide_btc_v2"),
    ("r4_eth_v2_decide", "decide_eth_v2"),
])
def test_r4_adapters_forward_absence_as_none(monkeypatch, legacy, new):
    """THE r4 invariant. An empty cfg — which is what the bot sends — must
    arrive as None, never as 0.0 or a substituted default."""
    sleeve = _r4()
    seen = _capture(monkeypatch, sleeve, new)
    getattr(sleeve, legacy)(VARIANT, {})
    assert seen["kwargs"] == {"weight_pct": None, "gate": None,
                              "vol_scalar": None, "priority": 100.0}


def test_r4_execute_adapter_keeps_its_private_name(monkeypatch):
    """bots/r4/runner.py calls r4._r4_execute by that exact private name. If
    the strip renames it without updating the runner, the bot raises on its
    first fire — in October, with no other coverage."""
    sleeve = _r4()
    seen = {}
    monkeypatch.setattr(sleeve, "execute",
                        lambda variant, intent: seen.update(
                            variant=variant, intent=intent) or {"status": "ok"})
    sentinel = object()
    sleeve._r4_execute(VARIANT, {"anything": "at all"}, sentinel)
    assert seen == {"variant": VARIANT, "intent": sentinel}

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "bots" / "r4" / "runner.py").read_text(encoding="utf-8")
    assert "_r4_execute" in src


@pytest.mark.parametrize("name", [
    "decide_btc", "decide_eth", "decide_btc_v2", "decide_eth_v2", "execute",
    "r4_btc_decide", "r4_eth_decide", "r4_btc_v2_decide", "r4_eth_v2_decide",
    "_r4_execute", "r4_btc_try_fire", "r4_eth_try_fire",
    "r4_btc_v2_try_fire", "r4_eth_v2_try_fire", "_r4_v2_try_fire"])
def test_r4_exposes_both_surfaces(name):
    assert callable(getattr(_r4(), name, None)),         f"r4 lost {name}; both surfaces must stay live until phase D"
