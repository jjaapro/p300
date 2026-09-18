"""Fixtures for the ETH/BTC regime spread (README.md): episodes, the P&L arithmetic with funding and cost, the
daily equity path, the placebo's exposure profile, the hedge on a trade, and the decision rules."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spread_lib as L  # noqa: E402


def days_of(n: int, start: str = "2024-01-01") -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=n, freq="D")]


def frame(days: list[str], opens: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"open": opens, "close": closes}, index=days)


def test_frozen_numbers():
    assert (L.LEG_RT_COST, L.PAIR_COST, L.MIN_NET_ANN_PCT, L.DSR_BAR, L.PLACEBO_ALPHA, L.HEDGE_COST_BUDGET_R) == \
        (0.0010, 0.0020, 5.0, 0.95, 0.05, -0.05)
    assert (L.N_BOOT_EPISODES, L.N_PLACEBO, L.SEED, L.START_DAY, L.SPLIT_DAY) == (5000, 1000, 42, "2020-01-01", "2023-06-09")


def test_episodes_are_maximal_runs_and_the_tail_is_kept():
    days = days_of(8)
    modes = dict(zip(days, ["bear", "strong_bull", "strong_bull", "mild_bull", "strong_bull", "bear", "strong_bull",
                            "strong_bull"]))
    assert L.episodes_from_modes(days, modes, ("strong_bull",)) == [(1, 2), (4, 4), (6, 7)]
    assert L.episodes_from_modes(days, modes, ("strong_bull", "mild_bull")) == [(1, 4), (6, 7)]
    assert L.episodes_from_modes(days, modes, ("uncertain",)) == []


def test_episode_pnl_with_funding_and_cost_and_the_daily_path():
    days = days_of(5)
    # ETH: 100 -> 110 over the episode (days 1-2), BTC: 100 -> 105; exit at the open of day 3
    eth = frame(days, [100, 100, 104, 110, 110], [100, 104, 108, 110, 110])
    btc = frame(days, [100, 100, 102, 105, 105], [100, 102, 104, 105, 105])
    t1 = L.day_ts(days[1])
    f_eth = pd.Series([0.001, 0.001], index=[t1, t1 + 8 * 3600])           # long ETH pays 0.2 % over the episode
    f_btc = pd.Series([0.0005, 0.0005, 0.0005], index=[t1 - 8 * 3600, t1 + 16 * 3600, L.day_ts(days[3])])
    ep, daily = L.run_episodes(days, eth, btc, [(1, 2)], f_btc, f_eth)
    r = ep.iloc[0]
    assert (r.entry_day, r.exit_day, r.days, bool(r.censored)) == (days[1], days[3], 2, False)
    assert r.gross == pytest.approx(0.10 - 0.05)
    # settlements at or after entry and before exit: ETH two (0.002 paid), BTC one at t1+16h (0.0005 received)
    assert r.funding == pytest.approx(-0.002 + 0.0005)
    assert r.cost == pytest.approx(0.002)
    assert r.net == pytest.approx(0.05 - 0.0015 - 0.002)
    # the daily path sums to the same net: marks close-to-close, the exit day to the open
    assert daily.sum() == pytest.approx(r.net)
    # day 1: the marks, the cost, both ETH settlements (00:00 and 08:00) and the BTC one at 16:00
    assert daily[days[1]] == pytest.approx((104 - 100) / 100 - (102 - 100) / 100 - 0.002 - 0.002 + 0.0005)
    assert daily[days[3]] == pytest.approx((110 - 108) / 100 - (105 - 104) / 100)            # the exit day, to the open
    assert daily[days[0]] == 0.0 and daily[days[4]] == 0.0


def test_a_censored_episode_closes_at_the_last_close():
    days = days_of(3)
    eth = frame(days, [100, 100, 105], [100, 105, 110])
    btc = frame(days, [100, 100, 100], [100, 100, 100])
    ep, daily = L.run_episodes(days, eth, btc, [(1, 2)], pd.Series(dtype=float), pd.Series(dtype=float))
    r = ep.iloc[0]
    assert bool(r.censored) and r.exit_day == days[2] and r.gross == pytest.approx(0.10)
    assert daily.sum() == pytest.approx(0.10 - L.PAIR_COST)


def test_single_leg_arms_use_one_leg_and_one_cost():
    days = days_of(3)
    eth = frame(days, [100, 100, 110], [100, 110, 110])
    btc = frame(days, [100, 100, 90], [100, 90, 90])
    t1 = L.day_ts(days[1])
    f_eth = pd.Series([0.001], index=[t1])
    ep_e, _ = L.run_episodes(days, eth, btc, [(1, 1)], pd.Series(dtype=float), f_eth, legs=(1.0, 0.0), cost=L.LEG_RT_COST)
    assert ep_e.iloc[0].gross == pytest.approx(0.10) and ep_e.iloc[0].funding == pytest.approx(-0.001)
    assert ep_e.iloc[0].net == pytest.approx(0.10 - 0.001 - 0.001)
    ep_b, _ = L.run_episodes(days, eth, btc, [(1, 1)], pd.Series(dtype=float), f_eth, legs=(0.0, 1.0), cost=L.LEG_RT_COST)
    assert ep_b.iloc[0].gross == pytest.approx(-0.10) and ep_b.iloc[0].funding == 0.0


def test_summary_drawdown_mar_and_halves():
    days = days_of(6)
    closes = [100, 110, 99, 99, 90, 120]
    eth = frame(days, [100] + closes[:-1], closes)                  # each day opens at the previous close
    btc = frame(days, [100] * 6, [100] * 6)
    ep, daily = L.run_episodes(days, eth, btc, [(1, 1), (4, 4)], pd.Series(dtype=float), pd.Series(dtype=float))
    s = L.summarize(ep, daily, years=1.0)
    assert s["episodes"] == 2 and s["days_held"] == 2 and s["censored"] == 0
    # episode 1: 100 -> 110 (exit at the open of day 2); episode 2: 99 -> 90 (exit at the open of day 5)
    assert ep["gross"].tolist() == pytest.approx([0.10, 90 / 99 - 1])
    assert s["net_total_pct"] == pytest.approx((0.10 + 90 / 99 - 1 - 2 * L.PAIR_COST) * 100)
    eq = daily.cumsum().to_numpy()
    assert s["max_dd_pct"] == pytest.approx(L.max_drawdown(eq) * 100)
    assert s["max_dd_pct"] == pytest.approx((1 - 90 / 99 + L.PAIR_COST) * 100)   # the second episode's loss
    assert s["mar"] == pytest.approx(s["net_ann_pct"] / s["max_dd_pct"])
    h = L.halves(ep)
    assert h["first_n"] == 1 and h["second_n"] == 1
    assert h["first"] == pytest.approx((0.10 - L.PAIR_COST) * 100) and h["second"] == pytest.approx((90 / 99 - 1 - L.PAIR_COST) * 100)


def test_placebo_keeps_the_exposure_profile_per_year():
    days = days_of(800, "2023-01-01")
    modes = {d: "bear" for d in days}
    for i in list(range(10, 15)) + list(range(100, 102)) + list(range(500, 520)):
        modes[days[i]] = "strong_bull"
    eps = L.episodes_from_modes(days, modes, ("strong_bull",))
    rng = np.random.default_rng(0)
    for _ in range(20):
        pe = L.placebo_episodes(days, eps, rng)
        assert sorted(b - a + 1 for a, b in pe) == sorted(b - a + 1 for a, b in eps)
        by_year = lambda e: sorted((days[a][:4], b - a + 1) for a, b in e)  # noqa: E731
        assert by_year(pe) == by_year(eps)
        for (a1, b1), (a2, b2) in zip(pe, pe[1:]):
            assert b1 + 1 < a2                                  # non-overlapping, a gap of at least one day


def test_hedge_on_a_trade_is_the_other_assets_move_reversed():
    closes = {"BTC": np.array([100.0, 100.0, 102.0, 104.0, 105.0, np.nan]),
              "ETH": np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0])}
    t = pd.DataFrame([{"asset": "ETH", "s": 1, "i0": 1, "x": 4, "entry": 10.0, "risk": 0.5, "exit_price": 10.5, "R": 1.0,
                       "entry_ts": 0, "entry_day": "2024-01-01"},
                      {"asset": "ETH", "s": -1, "i0": 1, "x": 5, "entry": 10.0, "risk": 0.5, "exit_price": 9.5, "R": 1.0,
                       "entry_ts": 0, "entry_day": "2024-01-01"}])
    h = L.hedge_trades(t, closes)
    # long ETH hedged short BTC: BTC 100 -> 105 loses 5 % of notional = 5 % x (10 / 0.5 = 20) = -1.0 R; cost 0.02 R
    assert h["hedge_R"].iloc[0] == pytest.approx(-0.05 * 20)
    assert h["hedge_cost_R"].iloc[0] == pytest.approx(0.001 * 20)
    assert h["R_hedged"].iloc[0] == pytest.approx(1.0 - 1.0 - 0.02)
    # short ETH hedged long BTC, exit minute dead -> the last finite close (105) is used
    assert h["h_exit"].iloc[1] == 105.0 and h["hedge_R"].iloc[1] == pytest.approx(+1.0)


def test_hedge_decision_needs_mar_on_both_assets_both_halves_and_the_budget():
    def rec(mu, mh, mu1, mh1, mu2, mh2, diff):
        return {"unhedged": {"mar": mu}, "hedged": {"mar": mh},
                "halves": {"first": {"unhedged": {"mar": mu1}, "hedged": {"mar": mh1}},
                           "second": {"unhedged": {"mar": mu2}, "hedged": {"mar": mh2}}},
                "paired_diff": {"mean": diff}}
    good = {"BTC": rec(1.0, 1.5, 1.0, 1.2, 1.0, 1.3, -0.02), "ETH": rec(1.0, 1.5, 1.0, 1.2, 1.0, 1.3, 0.0)}
    assert L.hedge_decision(good)["verdict"] == "PROPOSE a hedged paper variant"
    bad = dict(good); bad["ETH"] = rec(1.0, 1.5, 1.0, 1.2, 1.0, 0.9, 0.0)
    assert L.hedge_decision(bad)["verdict"] == "KEEP UNHEDGED"
    bad["ETH"] = rec(1.0, 1.5, 1.0, 1.2, 1.0, 1.3, -0.06)
    assert L.hedge_decision(bad)["verdict"] == "KEEP UNHEDGED"


def test_r_metrics_and_drawdown():
    R = np.array([1.0, -1.0, -1.0, 2.0])
    m = L.r_metrics(R, years=2.0)
    assert m["annual_R"] == pytest.approx(0.5) and m["max_dd_R"] == pytest.approx(2.0) and m["mar"] == pytest.approx(0.25)
    assert L.max_drawdown(np.array([1.0, 0.5, 2.0, 1.0])) == pytest.approx(1.0)
