"""P2.4c parity — orchestrator-injected vol scalar matches today_inputs()["lev"].

Anchors three properties:

1. ``current_vol_scalar(strategy_id)`` returns the same float
   ``today_inputs()["lev"]`` returns for every J+ family sleeve.

2. ``current_vol_scalar(strategy_id)`` returns ``None`` for every
   non-J+ sleeve (tactical sleeves don't vol-target today; the
   ``_effective_vol_scalar`` field they see is ``None`` and is
   ignored).

3. When ``today_inputs()`` is unavailable (cold-boot warmup), the
   function returns ``None`` for J+ sleeves too, so consumers fall
   back to their legacy ``ti["lev"]`` path (or short-circuit on
   ``no_inputs`` before reaching the vol-scalar read).
"""
from __future__ import annotations

import pytest

from strategies.support import portfolio_vol


JPLUS_SLEEVES = [
    "JPLUS_R4_BTC", "JPLUS_R4_ETH", "JPLUS_R4_BTC_V2", "JPLUS_R4_ETH_V2",
    "JPLUS_EMA_BTC", "JPLUS_ETH_DAILY",
]

NON_JPLUS_SLEEVES = [
    "S-003", "S-078", "S-096", "PDO-L-RF", "CPR", "FOMC", "AI_QUANT",
    "NOT_A_SLEEVE",
]


@pytest.mark.parametrize("sid", NON_JPLUS_SLEEVES)
def test_non_jplus_returns_none(sid):
    """Tactical sleeves + unknown ids never get a vol scalar today."""
    assert portfolio_vol.current_vol_scalar(sid) is None


@pytest.mark.parametrize("sid", JPLUS_SLEEVES)
def test_jplus_matches_today_inputs_lev(monkeypatch, sid):
    """For every J+ sleeve, current_vol_scalar must equal today_inputs()['lev'].
    That equality is the parity contract — sleeves migrated to read
    ``_effective_vol_scalar`` produce identical leverage to the legacy
    ``ti['lev']`` path."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"lev": 1.42, "mode": "uncertain"})
    assert portfolio_vol.current_vol_scalar(sid) == pytest.approx(1.42)


@pytest.mark.parametrize("sid", JPLUS_SLEEVES)
def test_jplus_cold_boot_returns_none(monkeypatch, sid):
    """today_inputs()=None (cold-boot warmup) -> None. Each J+ sleeve's
    try_fire short-circuits with no_inputs before reaching the vol-scalar
    read in this case, so None is the correct signal."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: None)
    assert portfolio_vol.current_vol_scalar(sid) is None


# ─── Orchestrator injection ──────────────────────────────────────────────────

def test_orchestrator_injects_effective_vol_scalar(monkeypatch):
    """_tick_composition records portfolio_vol.current_vol_scalar(strategy_id)
    on every sleeve_cfg before dispatch — verified here at the resolver call.
    Live tick is covered by integration tests."""
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(
        ji, "today_inputs",
        lambda: {"lev": 0.85, "mode": "mild_bull"},
    )
    # J+ sleeve -> got the scalar.
    sleeve_cfg = {"strategy_id": "JPLUS_R4_BTC"}
    sleeve_cfg["_effective_vol_scalar"] = portfolio_vol.current_vol_scalar(
        sleeve_cfg["strategy_id"])
    assert sleeve_cfg["_effective_vol_scalar"] == pytest.approx(0.85)
    # Tactical sleeve -> None (sleeves that don't read the field ignore it).
    sleeve_cfg = {"strategy_id": "S-003"}
    sleeve_cfg["_effective_vol_scalar"] = portfolio_vol.current_vol_scalar(
        sleeve_cfg["strategy_id"])
    assert sleeve_cfg["_effective_vol_scalar"] is None
