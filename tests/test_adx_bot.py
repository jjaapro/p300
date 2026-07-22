"""Tests for bots/adx — the standalone ADX (S-003 T2) bot."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

import botlib
from bots.adx import runner
from strategies.support.dispatch import Intent


def _mk_intent(entry=100_000.0, stop=92_000.0, direction="LONG"):
    return Intent(asset="BTC", direction=direction, allocation_pct=15.0,
                  leverage=5.0, conviction=100, priority=100.0,
                  reason={"_entry_price": entry, "_stop_price": stop},
                  scheduled_exit_dt=None)


def test_fixed_r_sizing_over_effective_stop():
    # 8% stop -> notional = 10_000 * 2% / 8% = 2_500 (cap far away)
    resized, info = runner.size_intent(_mk_intent(), capital=10_000.0)
    assert info["stop_pct"] == pytest.approx(0.08)
    assert info["notional"] == pytest.approx(2_500.0)
    assert not info["at_cap"]
    assert resized.allocation_pct == 100.0
    assert resized.leverage == pytest.approx(0.25)


def test_tick_stale_mgmt_skips(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr(
        "strategies.sleeves.adx.signal.try_decide_for_variant", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"cd_spot_binance": 99999.0}
        if "cd_spot_binance" in (tables or []) else {})
    out = runner.tick({"id": "x", "capital_usdt": 10_000.0}, {})
    assert out["status"] == "stale_mgmt_inputs"
    assert out["hb_status"] == "degraded"


def test_atr_trail_level_ratchets():
    """Hand-computed trail on synthetic candles: seeds at the anchor bar's
    close - 4*ATR and ratchets up with later closes (LONG)."""
    from strategies.sleeves.adx import signal as adx_sig
    from strategies.support.indicators import atr as atr_fn

    candles = []
    price = 100.0
    for i in range(40):
        candles.append({"dt": f"2026-01-{i + 1:02d}" if i < 31
                        else f"2026-02-{i - 30:02d}",
                        "ts": 0, "open": price, "high": price + 2,
                        "low": price - 2, "close": price + 1})
        price += 1.0

    entry_iso = "2026-02-05T10:00:00+00:00"      # anchor = last dt < 02-05
    level = adx_sig._atr_trail_level(candles, entry_iso, "LONG")
    a = atr_fn(candles, adx_sig.ATR_TRAIL_PERIOD)
    anchor = max(i for i, c in enumerate(candles) if c["dt"] < "2026-02-05")
    expected = max(candles[j]["close"] - adx_sig.ATR_TRAIL_MULT * a[j]
                   for j in range(anchor, len(candles))
                   if not math.isnan(a[j]))
    assert level == pytest.approx(expected)
    # rising closes -> the newest bar dominates the ratchet
    assert level == pytest.approx(
        candles[-1]["close"] - adx_sig.ATR_TRAIL_MULT * a[-1])
