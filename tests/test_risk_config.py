"""services.risk_config — SL-semantic switch."""
from __future__ import annotations

import pytest

from services import risk_config


def test_default_semantic_is_price_move(monkeypatch):
    monkeypatch.delenv("P300_STOP_SEMANTICS", raising=False)
    assert risk_config.sl_semantic() == "price_move"


@pytest.mark.parametrize("val,expected", [
    ("price_move", "price_move"),
    ("margin", "margin"),
    ("margin_loss", "margin"),
    ("notional", "margin"),
    ("notional_loss", "margin"),
    ("MARGIN", "margin"),   # case-insensitive
    (" margin ", "margin"),  # whitespace tolerant
    ("garbage", "price_move"),  # unknown → default
    ("", "price_move"),
])
def test_env_var_dispatch(monkeypatch, val, expected):
    monkeypatch.setenv("P300_STOP_SEMANTICS", val)
    assert risk_config.sl_semantic() == expected


def test_price_move_is_passthrough(monkeypatch):
    monkeypatch.delenv("P300_STOP_SEMANTICS", raising=False)
    assert risk_config.effective_price_move_sl_pct(10.0, 5.0) == 10.0
    assert risk_config.effective_price_move_sl_pct(10.0, 1.0) == 10.0
    assert risk_config.effective_price_move_sl_pct(7.5, 3.0) == 7.5


def test_margin_divides_by_leverage(monkeypatch):
    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    # 10% margin loss at 5x = 2% price move
    assert risk_config.effective_price_move_sl_pct(10.0, 5.0) == pytest.approx(2.0)
    # 10% margin loss at 1x = 10% price move (unchanged)
    assert risk_config.effective_price_move_sl_pct(10.0, 1.0) == pytest.approx(10.0)
    # 5% margin at 2.5x = 2% price move
    assert risk_config.effective_price_move_sl_pct(5.0, 2.5) == pytest.approx(2.0)


def test_margin_falls_back_to_passthrough_on_zero_leverage(monkeypatch):
    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    # Defensive: don't divide by zero — return configured as-is.
    assert risk_config.effective_price_move_sl_pct(10.0, 0.0) == 10.0
