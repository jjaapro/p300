"""Fixtures for SHORT_SQUEEZE on ETH (README.md): the panel-to-table builders, the close-of-hour open interest,
the net cost, the trigger-set agreement and the decision rule."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ss_eth_lib as L  # noqa: E402

T0 = 1_640_995_200      # 2022-01-01 00:00 UTC


def minutes(n: int, price: float = 100.0) -> dict:
    return {"open": np.full(n, price), "high": np.full(n, price + 1), "low": np.full(n, price - 1),
            "close": np.full(n, price), "volume": np.ones(n), "taker_buy_volume": np.full(n, 0.25)}


def test_frozen_numbers():
    assert (L.COST_BP_RT, L.MIN_TRIGGERS, L.MIN_NET_R, L.DSR_BAR, L.JACCARD_MIN, L.N_BOOT, L.SEED) == \
        (10.0, 30, 0.20, 0.95, 0.90, 5000, 42)
    assert L.E0_EXPECT == dict(n=70, win=0.443, mean_r=0.40, pf=1.65, end="2026-05-18")


def test_bars_aggregate_present_minutes_in_the_tables_shape():
    a = minutes(60)
    a["open"][15], a["high"][20], a["low"][25], a["close"][29] = 101.0, 105.0, 95.0, 102.0
    a["volume"][15:18] = 0.0                                  # the bar's first three minutes dead
    a["open"][18] = 100.5
    a["volume"][45:60] = 0.0                                  # the last bar wholly dead
    df = L.bars_from_minutes(a, T0, 15)
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "volume_buy", "volume_sell"]
    assert len(df) == 3 and df["timestamp"].tolist() == [T0, T0 + 900, T0 + 1800]
    r = df.iloc[1]
    assert (r.open, r.high, r.low, r.close) == (100.5, 105.0, 95.0, 102.0)
    assert r.volume == 12.0 and r.volume_buy == pytest.approx(3.0) and r.volume_sell == pytest.approx(9.0)
    assert str(df.index[0]) == "2022-01-01 00:00:00+00:00"


def test_oi_close_of_hour_is_the_snapshot_at_h_plus_1h():
    oi = np.arange(0, 48, dtype=float) * 10          # 48 five-minute snapshots = 4 hours
    df = L.oi_hourly(oi, T0)
    assert df["timestamp"].tolist() == [T0, T0 + 3600, T0 + 7200]     # the last hour has no H + 1 h snapshot
    assert df["oi_close"].tolist() == [120.0, 240.0, 360.0]         # snapshot index 12, 24, 36
    assert df["oi_open"].tolist() == [0.0, 120.0, 240.0]
    oi[24] = np.nan
    df = L.oi_hourly(oi, T0)
    assert df["timestamp"].tolist() == [T0, T0 + 7200]              # a missing close drops the hour


def test_net_charges_the_round_trip_against_the_trades_own_risk():
    sim = pd.DataFrame({"trigger_ts": [pd.Timestamp("2024-01-01", tz="UTC")], "pnl_R": [1.0], "risk_pct": [0.005],
                        "exit_reason": ["target"], "entry": [100.0], "stop": [99.5], "target": [101.5],
                        "exit_price": [101.5]})
    net = L.with_net(sim)
    assert net["cost_R"].iloc[0] == pytest.approx(0.2) and net["net_R"].iloc[0] == pytest.approx(0.8)
    assert net["day"].iloc[0] == "2024-01-01"


def test_jaccard_on_the_common_span_only():
    idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    a = pd.Series(False, index=idx); b = pd.Series(False, index=idx[:8])
    a.iloc[[1, 3, 9]] = True                                    # 9 is outside b's span
    b.iloc[[1, 5]] = True
    f = L.jaccard(a, b)
    assert (f["a"], f["b"], f["shared"]) == (2, 2, 1) and f["jaccard"] == pytest.approx(1 / 3)


def test_describe_halves_pf_and_mar():
    ts = pd.date_range("2024-01-01", periods=4, freq="7D", tz="UTC")
    sim = pd.DataFrame({"trigger_ts": ts, "net_R": [1.0, -1.0, 2.0, -0.5], "exit_reason": ["target", "stop", "target", "time"],
                        "risk_pct": [0.005] * 4})
    d = L.describe(sim, "net_R")
    assert d["n"] == 4 and d["win_rate"] == 0.5 and d["pf"] == pytest.approx(2.0)
    assert d["first_half_mean_R"] == 0.0 and d["second_half_mean_R"] == pytest.approx(0.75)
    assert d["max_dd_R"] == pytest.approx(1.0) and d["exit_mix"] == {"target": 2, "stop": 1, "time": 1}


def test_decision_rule():
    fid_ok = {"jaccard": 0.95, "pnl_agree": True}
    good = {"n": 40, "mean_R": 0.3, "first_half_mean_R": 0.2, "second_half_mean_R": 0.4, "dsr": 0.97}
    assert L.decide(good, {"ci90": [0.05, 0.5]}, fid_ok)["verdict"] == "RECOMMEND an ETH paper twin"
    assert L.decide(good, {"ci90": [-0.01, 0.5]}, fid_ok)["verdict"] == "KILL for ETH"
    assert L.decide({**good, "n": 29}, {"ci90": [0.05, 0.5]}, fid_ok)["failing"] == ["a_n"]
    assert L.decide({**good, "second_half_mean_R": -0.1}, {"ci90": [0.05, 0.5]}, fid_ok)["failing"] == ["c_both_halves"]
    assert L.decide({**good, "dsr": 0.9}, {"ci90": [0.05, 0.5]}, fid_ok)["failing"] == ["d_dsr"]
    assert L.decide(good, {"ci90": [0.05, 0.5]}, {"jaccard": 0.8, "pnl_agree": True})["verdict"].startswith("DESCRIPTIVE")
    assert L.decide(good, {"ci90": [0.05, 0.5]}, {"jaccard": 0.95, "pnl_agree": False})["verdict"].startswith("DESCRIPTIVE")
