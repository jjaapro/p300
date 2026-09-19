"""Fixtures for SQUEEZE_BULL on ETH (README.md): the frame's features, the frozen detector through the ledger, the
re-cost, the fidelity gate and the decision rule."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqb_eth_lib as L  # noqa: E402


def frame(n_hours: int = 24 * 40, price: float = 100.0, oi: float = 1000.0) -> pd.DataFrame:
    idx = pd.date_range("2022-02-01", periods=n_hours, freq="h", tz="UTC")
    return pd.DataFrame({"open": price, "high": price + 0.1, "low": price - 0.1, "close": price, "oi_close": oi}, index=idx)


def test_frozen_numbers():
    assert (L.JUNE_COST_BP, L.LIVE_COST_BP, L.BUILD_MIN_N, L.BUILD_MIN_MEAN_R, L.BUILD_MIN_MAR, L.JACCARD_MIN) == \
        (18.0, 10.0, 10, 0.10, 1.5, 0.80)
    assert L.RECOST_R == pytest.approx(0.04)
    assert str(L.FULL_SAMPLE_START)[:10] == "2022-01-30" and str(L.OOS_START)[:10] == "2026-04-14"


def test_features_follow_the_revalidation_recipe():
    df = frame()
    df.loc[df.index[100:], "close"] = 110.0                      # a step up on day 4
    df.loc[df.index[200:], "oi_close"] = 900.0                   # a 10 % OI drop at hour 200
    f = L.add_features(df)
    assert f["oi_chg_4h"].iloc[203] == pytest.approx(-0.1) and f["oi_chg_4h"].iloc[204] == 0.0
    assert f["px_chg_4h"].iloc[103] == pytest.approx(0.1)
    # ret_30d is the daily close's 30-day change, forward-filled; backonly lags it by one day
    day31 = f.loc[f.index[31 * 24]]                            # day 31 vs day 1: 110 / 100
    assert day31["ret_30d"] == pytest.approx(0.1) and np.isnan(f["ret_30d"].iloc[0])
    d = f["ret_30d_backonly"].resample("1D").last()
    r = f["ret_30d"].resample("1D").last()
    assert d.iloc[35] == pytest.approx(r.iloc[34])


def test_ledger_uses_the_june_detector_and_replay_with_the_recost():
    df = frame(n_hours=24 * 60)
    ts = df.index
    # a bull regime: the price ramps for 40 days, then a flush at hour 1100: OI −3 % over 4 h, price −1 %
    ramp = np.linspace(100.0, 130.0, len(df))
    df["close"] = ramp; df["open"] = ramp; df["high"] = ramp + 0.1; df["low"] = ramp - 0.1
    df.loc[ts[1097:], "oi_close"] = 970.0
    df.loc[ts[1100:], ["close", "open"]] = df.loc[ts[1100:], ["close", "open"]].to_numpy() * 0.985
    df.loc[ts[1100:], "high"] = df.loc[ts[1100:], "close"] + 0.1
    df.loc[ts[1100:], "low"] = df.loc[ts[1100:], "close"] - 0.1
    f = L.add_features(df)
    led = L.ledger(f)
    assert len(led) >= 1
    fire = led.iloc[0]
    assert fire["oi_chg_4h"] <= -0.02 and fire["px_chg_4h"] <= -0.005
    assert fire["regime_backonly"] == "bull_30d"
    assert fire["r_10bp"] == pytest.approx(fire["r_outcome"] + 0.04)
    assert (led["ts"] >= L.FULL_SAMPLE_START).all()
    b = L.bull_gated(led)
    assert len(b) == len(led[(led["regime_backonly"] == "bull_30d") & led["resolved"]])


def test_stats_halves_mar_and_dsr():
    ts = pd.date_range("2023-01-01", periods=6, freq="7D", tz="UTC")
    led = pd.DataFrame({"ts": ts, "r_outcome": [1.0, -1.0, 2.0, -0.5, 1.5, 0.5], "exit_kind": ["target", "stop", "target", "stop", "target", "tif"]})
    s = L.stats(led)
    assert s["n"] == 6 and s["mean_R"] == pytest.approx(0.5833, abs=1e-3) and s["maxDD"] == pytest.approx(-1.0)
    assert s["first_half_mean_R"] == pytest.approx(2.0 / 3) and s["second_half_mean_R"] == pytest.approx(0.5)
    assert s["MAR"] == pytest.approx(s["annual_R"] / 1.0) and s["exit_mix"] == {"target": 3, "stop": 2, "tif": 1}
    assert L.stats(led.iloc[:0]) == {"n": 0}


def test_fidelity_jaccard_and_shared_r():
    ts = pd.date_range("2023-01-01", periods=10, freq="D", tz="UTC")
    a = pd.DataFrame({"ts": ts[:8], "r_outcome": np.arange(8, dtype=float), "regime_backonly": ["bull_30d"] * 8})
    b = pd.DataFrame({"ts": ts[2:10], "r_outcome": np.arange(2, 10, dtype=float), "regime_backonly": ["bull_30d"] * 8})
    f = L.fidelity(a, b)
    assert f["common_span"][0][:10] == "2023-01-03" and f["shared_bull"] == 6 and f["jaccard_bull"] == 1.0
    assert f["max_abs_r_diff_shared"] == 0.0 and f["pass"]
    b.loc[b["ts"] == ts[5], "regime_backonly"] = "flat_30d"        # one fire gated differently
    f = L.fidelity(a, b)
    assert f["shared_bull"] == 5 and f["jaccard_bull"] == pytest.approx(5 / 6) and f["pass"]
    b["r_outcome"] = b["r_outcome"] + 0.001
    assert not L.fidelity(a, b)["pass"]


def test_decision_rule():
    fid_ok, fid_bad = {"pass": True}, {"pass": False}
    good_oos = {"n": 12, "mean_R": 0.2}
    good_full = {"n": 60, "mean_R": 0.25, "MAR": 1.8, "first_half_mean_R": 0.2, "second_half_mean_R": 0.3}
    assert L.decide(good_oos, good_full, fid_ok, None)["verdict"] == "BUILD"
    assert L.decide(good_oos, good_full, fid_bad, None)["verdict"] == "DESCRIPTIVE"
    assert L.decide({"n": 12, "mean_R": -0.05}, good_full, fid_ok, None)["verdict"] == "KILL"
    assert L.decide(good_oos, {**good_full, "mean_R": -0.1}, fid_ok, None)["verdict"] == "KILL"
    d = L.decide({"n": 4, "mean_R": 0.5}, good_full, fid_ok, {"bull_days_needed": 30})
    assert d["verdict"] == "INCONCLUSIVE" and "a_oos" in d["reason"] and d["projection"]["bull_days_needed"] == 30
    assert L.decide(good_oos, {**good_full, "MAR": 1.2}, fid_ok, None)["reason"].startswith("failing: b_mar")
    assert L.decide(good_oos, {**good_full, "second_half_mean_R": -0.1}, fid_ok, None)["reason"].startswith("failing: c_halves")
