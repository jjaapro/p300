"""Accounting fixtures: calendar returns reconcile to per-trade net, costs and funding are side-correct."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orb_metrics as met  # noqa: E402
from test_orb_engine import I0, UP, market, run  # noqa: E402


def test_intraday_trade_day_return_equals_net_bp():
    mkt = market({I0 + 20: UP, I0 + 21: (100.12, 100.15, 100.10, 100.12)}, funding=[(I0 + 100, 0.0002)])
    led = run(mkt)
    tr = met.trades(led).iloc[0]
    for sc in ("gross", "db", "rt30"):
        daily = met.daily_returns(led, mkt, "2021-06-01", "2021-06-03", sc)
        assert daily["2021-06-01"] == pytest.approx(met.net_bp(met.trades(led), sc).iloc[0] / 1e4)
        assert daily.drop("2021-06-01").abs().sum() == 0
    funding = -1e4 * 0.0002 * 100.0 / 100.12           # long pays, marked at the settlement minute
    assert tr["funding_bp"] == pytest.approx(funding)
    assert met.net_bp(met.trades(led), "db").iloc[0] == pytest.approx(
        tr["gross_bp"] + funding - 5.8 * (1 + tr["exit_px"] / tr["fill_px"]))


def test_multi_day_position_compounds_to_the_trade_net_and_books_funding_on_its_day():
    fill = I0 + 21
    bars = {I0 + 20: UP, 1439: (100.0, 100.5, 100.0, 100.5),         # day-1 mark 100.5
            fill + 1440: (100.8, 100.8, 100.8, 100.8)}               # censored exit at 100.8
    mkt = market(bars, funding=[(I0 + 100, 0.0001), (1470, 0.0003)])
    led = run(mkt, time_exit="none", censor_days=1)
    tr = met.trades(led)
    assert tr.iloc[0]["exit_reason"] == "censored_horizon" and tr.iloc[0]["exit_px"] == pytest.approx(100.8)
    gross_days = met.daily_returns(led, mkt, "2021-06-01", "2021-06-03", "gross")
    assert gross_days["2021-06-01"] == pytest.approx(0.005)                    # 100 -> 100.5
    assert gross_days["2021-06-02"] == pytest.approx(0.003 / 1.005)            # 100.5 -> 100.8 on grown equity
    for sc in ("gross", "db"):
        daily = met.daily_returns(led, mkt, "2021-06-01", "2021-06-03", sc)
        growth = float(np.prod(1 + daily.to_numpy()))
        assert growth - 1 == pytest.approx(met.net_bp(tr, sc).iloc[0] / 1e4, abs=1e-12)
        assert (daily != 0).sum() == 2
    db = met.daily_returns(led, mkt, "2021-06-01", "2021-06-03", "db")
    day2_pnl = 0.003 - 0.0003 * 100.0 / 100.0 - 5.8e-4 * 100.8 / 100.0        # funding at 1470 booked on day 2
    day1_pnl = 0.005 - 0.0001 - 5.8e-4
    assert db["2021-06-02"] == pytest.approx(day2_pnl / (1 + day1_pnl))


def test_block_indices_are_circular_blocks():
    idx = met.block_indices(10, block=4, n_boot=3, seed=1)
    assert idx.shape == (3, 10)
    d = np.diff(idx, axis=1) % 10
    assert np.all((d == 1) | (np.arange(1, 10) % 4 == 0)[None, :])


def test_ratio_bootstrap_and_pvalue():
    sums = np.array([10.0, 0.0, -5.0, 20.0])
    counts = np.array([1.0, 0.0, 1.0, 2.0])
    idx = np.array([[0, 1, 2, 3], [3, 3, 3, 3]])
    assert met.boot_ratio(sums, counts, idx).tolist() == [25 / 4, 10.0]
    assert met.p_greater_than_zero(1.0, np.array([0.5, 1.5, 2.5, 3.0])) == pytest.approx(3 / 5)


def test_holm_steps_down():
    rej = met.holm({"a": 0.01, "b": 0.02, "c": 0.04}, alpha=0.05)
    assert rej == {"a": True, "b": True, "c": True}
    rej = met.holm({"a": 0.01, "b": 0.03, "c": 0.04}, alpha=0.05)
    assert rej == {"a": True, "b": False, "c": False}
