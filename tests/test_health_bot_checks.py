"""health.py's two structural checks police the FLEET, not the dead composition.

Until 2026-09-12 they checked the legacy `p300_aggressive_v2_v1_0` variant and
its orchestrator dispatch wiring — a variant that has not traded since
2026-06-11 — and checked nothing about SHORT_SQUEEZE, SQUEEZE_BULL or the R4
windows, i.e. none of what the fleet actually runs.

These tests exist mainly to prove the replacements can FAIL. A structural check
that cannot go red is worse than none, because it reads as coverage.
"""
from __future__ import annotations

import pytest

import health


# ── entry points ───────────────────────────────────────────────────────────

def test_entrypoint_check_passes_on_the_real_fleet():
    health.check_bot_entrypoints()


def test_entrypoint_check_fails_when_a_runner_entry_point_is_renamed(monkeypatch):
    """The refactor renames r4's entry points (BACKLOG step 18/23). If this
    check cannot see that, it is not protecting the repoint."""
    monkeypatch.setitem(health.BOT_ENTRYPOINTS, "r4", (
        "bots.r4.strategy.signal",
        ("r4_btc_decide", "r4_eth_decide_RENAMED", "_r4_execute")))
    with pytest.raises(health.HealthError) as e:
        health.check_bot_entrypoints()
    assert e.value.code == 4
    assert "r4_eth_decide_RENAMED" in str(e.value)


def test_entrypoint_check_fails_when_the_module_will_not_import(monkeypatch):
    monkeypatch.setitem(health.BOT_ENTRYPOINTS, "adx",
                        ("strategies.sleeves.no_such_module",
                         ("try_decide_for_variant",)))
    with pytest.raises(health.HealthError) as e:
        health.check_bot_entrypoints()
    assert e.value.code == 4


def test_entrypoint_check_rejects_a_non_callable_attribute(monkeypatch):
    """A name that exists but is a constant is not an entry point."""
    monkeypatch.setitem(health.BOT_ENTRYPOINTS, "adx",
                        ("bots.adx.strategy.signal", ("COST_BP_RT",)))
    with pytest.raises(health.HealthError):
        health.check_bot_entrypoints()


def test_every_running_bot_is_covered():
    """A bot with no entry-point row would be silently unchecked.
    chento_v3_eth and carry_eth reuse their BTC runners, so they have none of
    their own."""
    assert set(health.BOT_ENTRYPOINTS) == {
        "adx", "carry", "chento_v3", "short_squeeze", "squeeze_bull", "r4"}
    assert set(health.BOT_CONFIGS) == set(health.BOT_ENTRYPOINTS) | {"chento_v3_eth", "carry_eth"}


def test_entry_points_match_what_the_runners_actually_call():
    """Guards the table against drifting away from the runners it describes:
    every name listed must appear in that bot's runner source."""
    import pathlib
    repo = pathlib.Path(health.__file__).resolve().parent
    for bot, (_mod, names) in health.BOT_ENTRYPOINTS.items():
        src = (repo / "bots" / bot / "runner.py").read_text(encoding="utf-8")
        for n in names:
            assert n in src, f"health lists {n} for {bot}, but its runner never calls it"


# ── variant registration ───────────────────────────────────────────────────

def test_variant_check_passes_on_the_real_fleet():
    health.check_bot_variants_registered()


def test_variant_check_is_derived_from_bot_config_not_hardcoded(monkeypatch):
    """A variant added to a bot's VARIANTS but never registered must fail.
    That is the case a hardcoded list of nine ids would miss."""
    import bots.squeeze_bull.config as sb
    monkeypatch.setattr(sb, "VARIANTS", list(sb.VARIANTS) + [
        {"id": "bot_squeeze_bull_never_registered_v1",
         "short_name": "ghost", "use_stop": True}])
    with pytest.raises(health.HealthError) as e:
        health.check_bot_variants_registered()
    assert e.value.code == 3
    assert "never_registered" in str(e.value)


def test_main_runs_the_data_checks_before_the_structural_ones():
    """_fail raises, so the first failing check aborts the report. A missing
    entry point must not hide the data-continuity output an operator opened
    health.py to read."""
    import inspect
    src = inspect.getsource(health.main)
    order = [src.index(f"check_{n}(") for n in
             ("data_continuity", "single_open_invariant",
              "bot_variants_registered", "bot_entrypoints")]
    assert order == sorted(order), "structural checks must come last"
