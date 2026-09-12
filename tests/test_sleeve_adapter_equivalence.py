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
