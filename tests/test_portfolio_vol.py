"""P2.4c — portfolio vol scalar tests.

Two layers:

  - Legacy parity (no variant argument): J+ sleeves get
    ``today_inputs()['lev']``; tactical sleeves get None. This was the
    only path until 2026-05-15.

  - Portfolio-vol opt-in (variant has ``allocator_notes.use_portfolio_vol=True``):
    every sleeve receives a scalar computed from realized NAV via
    ``compute_portfolio_vol_scalar``. Falls back to legacy when the
    variant has fewer than MIN_OBS_FOR_VOL trade-days of history.
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


# ─── Portfolio-vol math ─────────────────────────────────────────────────────

def _variant(use_portfolio_vol: bool = True,
              target_vol_annual: float | None = None,
              capital: float = 10000.0) -> dict:
    notes: dict = {"use_portfolio_vol": use_portfolio_vol}
    if target_vol_annual is not None:
        notes["target_vol_annual"] = target_vol_annual
    return {"id": "V", "capital_usdt": capital,
            "spec": {"allocator_notes": notes}}


def test_compute_returns_none_with_no_history(monkeypatch):
    """No trade history -> trades_daily_returns returns < MIN_OBS_FOR_VOL
    rows -> None."""
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: [],
    )
    assert portfolio_vol.compute_portfolio_vol_scalar("V", 10000.0) is None


def test_compute_returns_target_over_realized(monkeypatch):
    """Realized vol = 0.20 ann, target = 0.30 -> scalar = 1.5."""
    import math
    # Want pstdev(rets) * sqrt(365) == 0.20  -> pstdev = 0.20 / sqrt(365).
    sigma_daily = 0.20 / math.sqrt(365)
    # Build a 30-day series alternating +sigma, -sigma so pstdev = sigma.
    daily = [("2026-05-01", sigma_daily * 100.0 * (1 if i % 2 == 0 else -1))
              for i in range(30)]
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: daily,
    )
    scalar = portfolio_vol.compute_portfolio_vol_scalar("V", 10000.0,
                                                          target_vol_annual=0.30)
    assert scalar == pytest.approx(1.5, rel=1e-3)


def test_compute_clamps_to_floor(monkeypatch):
    """A super-high realized vol clamps to LEV_FLOOR (0.5)."""
    import math
    sigma_daily = 1.0 / math.sqrt(365)  # 100% annualized
    daily = [("2026-05-01", sigma_daily * 100.0 * (1 if i % 2 == 0 else -1))
              for i in range(30)]
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: daily,
    )
    scalar = portfolio_vol.compute_portfolio_vol_scalar("V", 10000.0,
                                                          target_vol_annual=0.30)
    # 0.30 / 1.0 = 0.30 -> clamped to 0.5
    assert scalar == pytest.approx(0.5)


def test_compute_clamps_to_cap(monkeypatch):
    """A near-zero realized vol clamps to LEV_CAP (3.0)."""
    import math
    sigma_daily = 0.05 / math.sqrt(365)  # 5% annualized
    daily = [("2026-05-01", sigma_daily * 100.0 * (1 if i % 2 == 0 else -1))
              for i in range(30)]
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: daily,
    )
    scalar = portfolio_vol.compute_portfolio_vol_scalar("V", 10000.0,
                                                          target_vol_annual=0.30)
    # 0.30 / 0.05 = 6.0 -> clamped to 3.0
    assert scalar == pytest.approx(3.0)


def test_compute_returns_none_when_zero_vol(monkeypatch):
    """All-zero returns -> pstdev == 0 -> None (caller falls back)."""
    daily = [("2026-05-01", 0.0)] * 30
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: daily,
    )
    assert portfolio_vol.compute_portfolio_vol_scalar("V", 10000.0) is None


def test_current_vol_scalar_opt_in_uses_portfolio_vol(monkeypatch):
    """When variant.spec.allocator_notes.use_portfolio_vol=True and we have
    NAV history, every sleeve (including tactical) gets the portfolio
    scalar — not None for tactical, not today_inputs()['lev'] for J+."""
    import math
    sigma_daily = 0.20 / math.sqrt(365)
    daily = [("2026-05-01", sigma_daily * 100.0 * (1 if i % 2 == 0 else -1))
              for i in range(30)]
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: daily,
    )
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"lev": 99.0, "mode": "x"})  # legacy ignored
    v = _variant(use_portfolio_vol=True, target_vol_annual=0.30)
    # Tactical sleeve picks up the portfolio scalar.
    assert portfolio_vol.current_vol_scalar("S-003", v) == pytest.approx(1.5, rel=1e-3)
    # J+ sleeve also picks up the portfolio scalar (NOT the legacy 99.0).
    assert portfolio_vol.current_vol_scalar("JPLUS_R4_BTC", v) == pytest.approx(1.5, rel=1e-3)


def test_current_vol_scalar_falls_back_when_no_history(monkeypatch):
    """Flag is on but no NAV history -> portfolio-vol returns None; fall
    back to legacy (J+ -> today_inputs()['lev'], tactical -> None)."""
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: [],  # no history
    )
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"lev": 1.23, "mode": "x"})
    v = _variant(use_portfolio_vol=True)
    assert portfolio_vol.current_vol_scalar("S-003", v) is None
    assert portfolio_vol.current_vol_scalar("JPLUS_R4_BTC", v) == pytest.approx(1.23)


def test_current_vol_scalar_flag_off_keeps_legacy(monkeypatch):
    """Flag absent / False -> legacy semantics regardless of NAV history."""
    monkeypatch.setattr(
        "strategies.support.strategy_health.trades_daily_returns",
        lambda *a, **kw: [("2026-05-01", 1.0)] * 30,  # would yield a scalar
    )
    from strategies.support import jplus_inputs as ji
    monkeypatch.setattr(ji, "today_inputs", lambda: {"lev": 1.23, "mode": "x"})
    v = _variant(use_portfolio_vol=False)
    assert portfolio_vol.current_vol_scalar("S-003", v) is None
    assert portfolio_vol.current_vol_scalar("JPLUS_R4_BTC", v) == pytest.approx(1.23)
