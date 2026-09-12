"""The central cfg->kwargs translation must equal the per-sleeve one it replaces.

Phase C left an `_unpack` inside each of the six refactored sleeves. Phase D
step 21 gathers them into `strategies/support/cfg_adapter.py`, on the
orchestrator's side of the boundary, so step 23 can delete the sleeve copies.

These tests are the licence to do that deletion: for every sleeve, and for a
range of cfg shapes including the empty one, the central unpacker and the
sleeve's own must produce the SAME keywords. Once that holds, removing the
sleeve copy cannot change dispatch behaviour.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

import pytest

from strategies.support import cfg_adapter as ca

# (central unpacker, module path, the sleeve's own _unpack)
PAIRS = [
    ("adx", ca.adx, "strategies.sleeves.adx.signal"),
    ("carry", ca.carry, "strategies.sleeves.carry.signal"),
    ("chento", ca.chento, "strategies.sleeves.chento_triple_v3.signal"),
    ("short_squeeze", ca.short_squeeze, "strategies.sleeves.short_squeeze.signal"),
    ("squeeze_bull", ca.squeeze_bull, "strategies.sleeves.squeeze_bull.signal"),
    ("r4", ca.r4, "strategies.sleeves.timing_anomalies.internal.r4.signal"),
]

CFGS = [
    {},                                             # the empty cfg — the bots' shape
    {"weight_pct": 15.0},
    {"_effective_weight_pct": 8.0, "weight_pct": 2.0},
    {"_effective_leverage": 4.0, "priority": 30.0},
    {"params": {"stop_loss_pct": 7.5}},
    {"params": None},
    {"use_stop": False, "count_diag": False},
    {"_effective_gate": None, "_effective_vol_scalar": 1.5},
    {"_effective_weight_pct": 25.0, "_effective_gate": object(),
     "_effective_vol_scalar": 2.0, "priority": 7.0,
     "params": {"stop_loss_pct": 3.0}, "use_stop": False, "count_diag": False},
]


def _sleeve_unpack(modpath):
    import importlib
    return getattr(importlib.import_module(modpath), "_unpack", None)


@pytest.mark.parametrize("name, central, modpath", PAIRS)
@pytest.mark.parametrize("cfg", CFGS)
def test_central_unpack_matches_the_sleeve_copy(name, central, modpath, cfg):
    own = _sleeve_unpack(modpath)
    if own is None:
        pytest.skip(f"{name} no longer carries its own _unpack (phase D done)")
    assert central(cfg) == own(cfg), (
        f"{name}: central cfg_adapter disagrees with the sleeve's own _unpack "
        f"for {cfg!r} — deleting the sleeve copy would change dispatch")


# ── the distinctions a table would have flattened ──────────────────────────

def test_weight_falls_back_to_zero_not_one_hundred():
    """A sleeve dispatched with no weight opened nothing. "Tidying" this to
    100.0 would turn a no-op into a full-size trade."""
    for name, central, _ in PAIRS:
        if name == "r4":
            continue
        assert central({})["weight_pct"] == 0.0, name


def test_effective_weight_overrides_the_static_one():
    for name, central, _ in PAIRS:
        got = central({"_effective_weight_pct": 11.0, "weight_pct": 99.0})
        assert got["weight_pct"] == (11.0 if name != "r4" else 11.0), name


def test_r4_passes_absence_through_as_none():
    """r4's live path. The bot supplies no weight/gate/vol_scalar, so the
    sleeve must fall back to the timing-anomaly weights table (including its
    bear-regime zero). Coercing these to numbers disables the regime gate."""
    got = ca.r4({})
    assert got["weight_pct"] is None
    assert got["gate"] is None
    assert got["vol_scalar"] is None
    assert got["priority"] == 100.0


def test_r4_never_asks_for_leverage():
    assert "leverage" not in ca.r4({"_effective_leverage": 9.0})


def test_adx_is_the_only_one_reading_nested_params():
    assert ca.adx({"params": {"stop_loss_pct": 3.0}})["stop_loss_pct"] == 3.0
    assert ca.adx({})["stop_loss_pct"] == 10.0          # a risk default, not 0
    assert ca.adx({"params": None})["stop_loss_pct"] == 10.0
    for name, central, _ in PAIRS:
        if name != "adx":
            assert "stop_loss_pct" not in central({"params": {"stop_loss_pct": 3.0}}), name


def test_only_the_squeeze_sleeves_carry_use_stop():
    for name, central, _ in PAIRS:
        has = "use_stop" in central({"use_stop": False})
        assert has == (name in ("short_squeeze", "squeeze_bull")), name


def test_only_short_squeeze_carries_count_diag():
    for name, central, _ in PAIRS:
        has = "count_diag" in central({"count_diag": False})
        assert has == (name == "short_squeeze"), name


# ── the dispatch builders ──────────────────────────────────────────────────

def test_decide_entry_translates_and_forwards():
    seen = {}

    def fake(variant, **kw):
        seen.update(variant=variant, kw=kw)
        return [], {"status": "ok"}

    entry = ca.decide_entry(fake, ca.carry)
    entry({"id": "v"}, {"_effective_weight_pct": 5.0})
    assert seen["kw"] == {"weight_pct": 5.0, "leverage": 1.0, "priority": 100.0}


def test_execute_entry_drops_the_cfg():
    seen = {}
    entry = ca.execute_entry(
        lambda v, i: seen.update(v=v, i=i) or {"status": "opened"})
    sentinel = object()
    entry({"id": "v"}, {"anything": 1}, sentinel)
    assert seen == {"v": {"id": "v"}, "i": sentinel}


def test_fire_entry_returns_the_status_when_nothing_fires():
    entry = ca.fire_entry(lambda v, **kw: ([], {"status": "no_flush"}),
                          lambda v, i: {"status": "opened"}, ca.carry)
    assert entry({"id": "v"}, {}) == {"status": "no_flush"}


def test_fire_entry_default_returns_only_the_execute_result():
    """adx / carry / chento / short_squeeze all returned the execute result
    alone from try_fire. Reproducing that keeps the registry switch a no-op."""
    entry = ca.fire_entry(lambda v, **kw: ([object()], {"status": "decided",
                                                        "diag": 1}),
                          lambda v, i: {"status": "opened", "trade_id": "SJ-1"},
                          ca.carry)
    assert entry({"id": "v"}, {}) == {"status": "opened", "trade_id": "SJ-1"}


def test_fire_entry_can_merge_like_squeeze_bull_did():
    entry = ca.fire_entry(lambda v, **kw: ([object()], {"status": "decided",
                                                        "diag": 1}),
                          lambda v, i: {"status": "opened", "trade_id": "SJ-1"},
                          ca.squeeze_bull, merge_status=True)
    assert entry({"id": "v"}, {}) == {"status": "opened", "trade_id": "SJ-1",
                                      "diag": 1}


def test_known_keys_covers_every_key_the_unpackers_read():
    """Documentation that cannot rot. KNOWN_KEYS claims to be the complete
    cfg surface; derive the real one from the source and compare, so a sleeve
    that starts reading a new key forces the list to be updated."""
    import inspect
    import re

    src = inspect.getsource(ca)
    read = set(re.findall(r'(?:cfg|params)\.get\(\s*"([^"]+)"', src))
    missing = read - set(ca.KNOWN_KEYS)
    assert not missing, f"cfg keys read but undocumented in KNOWN_KEYS: {missing}"
    # And nothing documented that nothing reads.
    stale = set(ca.KNOWN_KEYS) - read
    assert not stale, f"KNOWN_KEYS lists keys no unpacker reads: {stale}"
