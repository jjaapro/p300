"""haircut, flat_max, crisis_alpha, triple_barrier, changepoint, kelly,
fundamental_law, metrics, dd_duration, sizing_risk, alpha_halflife,
reverse_stress -- constructed inputs with known answers."""
from __future__ import annotations

import math

import numpy as np
import pytest

from studies.lib.validation import (
    alpha_halflife,
    changepoint,
    crisis_alpha,
    dd_duration,
    flat_max,
    fundamental_law,
    haircut,
    kelly,
    metrics,
    reverse_stress,
    sizing_risk,
    triple_barrier,
)


# --- haircut --------------------------------------------------------------------

def test_haircut_triple_ordering_and_monotonicity():
    h = haircut.haircut_triple(2.0, 1000, 100, freq_per_year=365)
    obs = h["observed_sharpe"]
    bonf = h["bonferroni"]["sharpe_adj"]
    holm = h["holm"]["sharpe_adj"]
    bhy = h["bhy"]["sharpe_adj"]
    # rank-1 Holm == Bonferroni; BHY's M*c(M) is the larger penalty; all below observed
    assert 0.0 < bhy < holm <= obs
    assert holm == pytest.approx(bonf)
    for m in ("bonferroni", "holm", "bhy"):
        assert 0.0 <= h[m]["haircut_pct"] <= 100.0
        assert h[m]["p_adj"] >= h["p_observed"]
    assert h["bhy"]["haircut_pct"] > h["bonferroni"]["haircut_pct"]
    # more trials -> more haircut; weak Sharpe -> floored at zero
    assert haircut.haircut_triple(2.0, 1000, 1000, 365)["bhy"]["sharpe_adj"] < bhy
    weak = haircut.haircut_triple(0.3, 300, 300, 365)
    assert weak["bonferroni"]["sharpe_adj"] == 0.0 and weak["bonferroni"]["p_adj"] == 1.0
    # extreme tail stays finite (the log-p-space path)
    huge = haircut.haircut_triple(8.0, 2000, 300, 252)
    assert math.isfinite(huge["bhy"]["sharpe_adj"]) and 0.0 < huge["bhy"]["sharpe_adj"] < 8.0
    assert haircut.observed_sharpe_to_pvalue(0.0, 100) == pytest.approx(0.5)
    assert haircut.log_adjustment_factor("bhy", 1) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        haircut.log_adjustment_factor("nope", 3)


def test_haircut_normal_tail_helpers_round_trip():
    for t in (0.5, 2.0, 5.0, 9.0, 40.0):
        assert haircut.inverse_logsf(haircut.norm_logsf(t)) == pytest.approx(t, rel=1e-3)
    assert haircut.norm_sf(1.0) == pytest.approx(1.0 - haircut.normal_cdf(1.0))
    assert haircut.norm_isf(0.025) == pytest.approx(1.959964, abs=1e-5)
    assert haircut.inverse_logsf(0.0) == float("-inf")


# --- flat max -------------------------------------------------------------------

def test_flat_max_plateau_vs_sharp_peak():
    grid = [0.3, 0.4, 0.5, 0.6, 0.7]
    plateau = {0.3: 1.9, 0.4: 2.0, 0.5: 2.0, 0.6: 2.0, 0.7: 2.0}
    r_flat = flat_max.flat_max_1d(grid, 0.5, plateau.__getitem__)
    assert r_flat["verdict"] == "FLAT"
    assert r_flat["peak_score"] == r_flat["chosen_score"]      # tie: peak_param is the first max
    assert r_flat["flat_max_score"] > -0.5 and abs(r_flat["relative_drop"]) < 0.10
    r_const = flat_max.flat_max_1d(grid, 0.5, lambda p: 1.5)
    assert r_const["verdict"] == "FLAT" and r_const["flat_max_score"] == 0.0
    sharp = {0.3: 0.5, 0.4: 0.6, 0.5: 3.0, 0.6: 0.6, 0.7: 0.5}
    r_sharp = flat_max.flat_max_1d(grid, 0.5, sharp.__getitem__)
    assert r_sharp["verdict"] == "SHARP_PEAK" and r_sharp["chosen_is_peak"]
    assert r_sharp["flat_max_score"] < -1.5 and r_sharp["relative_drop"] < -0.4
    assert r_sharp["local_params"] == grid
    with pytest.raises(ValueError):
        flat_max.flat_max_1d(grid, 0.55, lambda p: 1.0)
    flat_max.flat_max_report(r_sharp, "demo")


# --- crisis alpha ---------------------------------------------------------------

def test_crisis_alpha_gate_sub_criteria(capsys):
    bh = {"2018": -73.0, "2019": 92.0, "2020": 300.0, "2021": 60.0, "2022": -64.0, "2023": 155.0}
    strat = {"2018": 5.0, "2019": 10.0, "2020": 20.0, "2021": 5.0, "2022": -10.0, "2023": 8.0}
    v = crisis_alpha.validate_crisis_alpha(strat, bh, crisis_threshold_pct=-15.0,
                                           defensive_margin_pp=5.0, label="synthetic")
    assert v["crisis_years"] == ["2018", "2022"] and v["n_crisis"] == 2
    assert v["absolute_overall"] == "FAIL"                # 2022 = -10%
    assert v["defensive_overall"] == "PASS"
    assert v["defensive_plus_overall"] == "PASS"
    assert (v["n_absolute"], v["n_defensive"], v["n_defensive_plus"]) == (1, 2, 2)
    by_year = {r["year"]: r for r in v["results"]}
    assert by_year["2022"]["absolute"] == "FAIL" and by_year["2022"]["defensive_plus"] == "PASS"
    assert by_year["2018"]["outperformance"] == pytest.approx(78.0)
    # margin criterion: beating B&H by less than the margin fails DEFENSIVE+
    v_tight = crisis_alpha.validate_crisis_alpha({"2018": -70.0, "2022": -60.0}, bh)
    assert v_tight["defensive_overall"] == "PASS" and v_tight["defensive_plus_overall"] == "FAIL"
    # a missing year is reported and fails the overall verdict
    v_missing = crisis_alpha.validate_crisis_alpha({"2018": 5.0}, bh)
    assert {r["year"]: r["absolute"] for r in v_missing["results"]}["2022"] == "MISSING"
    assert v_missing["absolute_overall"] == "FAIL"
    # no crisis years -> N/A
    assert crisis_alpha.validate_crisis_alpha(strat, {"2019": 92.0})["absolute_overall"] == "N/A"
    crisis_alpha.report_validation(v)
    crisis_alpha.report_validation(v_missing)
    out = capsys.readouterr().out
    assert "synthetic" in out and "MISSING" in out


def test_per_year_buy_and_hold_from_bars():
    bars = [("2020-01-01", {"open": 100.0, "close": 110.0}),
            ("2020-06-01", {"open": 110.0, "close": 120.0}),
            ("2021-01-01", {"open": 120.0, "close": 60.0})]
    assert crisis_alpha.per_year_buy_and_hold(bars) == {"2020": pytest.approx(20.0), "2021": pytest.approx(-50.0)}


# --- triple barrier -------------------------------------------------------------

def test_triple_barrier_labels_on_known_paths():
    B = triple_barrier.Barriers(0.10, 0.10, 5)
    out = triple_barrier.triple_barrier_labels(np.array([0.0, 0.12, -0.15, -0.10, 0.0, 0.0]), B)
    assert out["labels"][0] == 1 and out["touch_side"][0] == "upper" and out["touch_day"][0] == 1
    out = triple_barrier.triple_barrier_labels(np.array([0.0, -0.12, 0.15, 0.10, 0.0, 0.0]), B)
    assert out["labels"][0] == -1 and out["touch_side"][0] == "lower"
    assert out["path_min"][0] == pytest.approx(-0.12)
    out = triple_barrier.triple_barrier_labels(np.array([0.0, 0.01, -0.02, 0.01, 0.0, -0.01]), B)
    assert out["labels"][0] == 0 and out["touch_side"][0] == "timeout" and out["touch_day"][0] == 5
    assert out["path_min"][0] == pytest.approx(-0.01) and out["path_max"][0] == pytest.approx(0.01)
    # path-asymmetric: fixed-horizon says MDD, first-touch says rally
    r_d = np.array([0.0, 0.12, -0.13, -0.08, 0.0, 0.0])
    assert triple_barrier.triple_barrier_labels(r_d, B)["labels"][0] == 1
    assert min(np.cumsum(r_d[1:])) < -0.05
    # compounding variant and binary wrapper
    out_c = triple_barrier.triple_barrier_labels(np.array([0.0, -0.12, 0.15, 0.10, 0.0, 0.0]), B, use_log=False)
    assert out_c["labels"][0] == -1
    assert triple_barrier.mdd_binary_labels(np.array([0.0, -0.12, 0.15, 0.10, 0.0, 0.0]), 0.10, 0.10, 5).tolist() == [1]
    with pytest.raises(ValueError):
        triple_barrier.triple_barrier_labels(np.zeros(5), B)
    w = triple_barrier.sample_uniqueness_weights(5, 20)
    assert len(w) == 20 and w.mean() == pytest.approx(1.0)
    assert w[0] > w[10] and w[10] == pytest.approx(w[9])
    assert triple_barrier.sample_uniqueness_weights(5, 0).size == 0


# --- BOCPD ----------------------------------------------------------------------

def test_bocpd_flags_planted_mean_shift():
    rng = np.random.default_rng(42)
    xs = np.concatenate([rng.standard_normal(700), rng.standard_normal(300) + 5.0])
    rows = changepoint.run_series(xs)
    assert len(rows) == 1000
    assert all(math.isnan(v) for v in rows[changepoint.WARMUP_BARS - 1].values())
    assert all(math.isfinite(v) for v in rows[changepoint.WARMUP_BARS].values())
    before, after = rows[699], rows[705]
    assert before["run_mean"] > 200 and after["run_mean"] < 50
    assert before["cp_prob_short_20"] < 0.1 and after["cp_prob_short_20"] > 0.5
    assert after["severity"] > before["severity"]
    # causal: the prefix run is bit-identical
    short = changepoint.run_series(xs[:750])
    for i in (600, 700, 749):
        assert short[i] == rows[i]
    # NaN observations pass through without touching the state
    rows_nan = changepoint.run_series([1.0, float("nan"), 2.0])
    assert all(math.isnan(v) for v in rows_nan[1].values())
    state = changepoint.init_state()
    changepoint.bocpd_step(0.3, state)
    assert state.t == 1 and state.R.size == 2 and state.R.sum() == pytest.approx(1.0)
    with pytest.raises(ValueError):
        changepoint.bocpd_step(float("inf"), state)


# --- Kelly / sizing -------------------------------------------------------------

def test_kelly_full_matches_mu_over_var():
    rng = np.random.default_rng(3)
    r = 0.001 + 0.01 * rng.standard_normal(2000)
    f = kelly.kelly_full(r)
    assert f == pytest.approx(r.mean() / r.var(ddof=1))
    assert kelly.kelly_fractional(r, 0.5) == pytest.approx(0.5 * f)
    assert abs(kelly.kelly_exact_log(r) - f) / f < 0.2
    assert kelly.kelly_full([0.01]) == 0.0 and kelly.kelly_full([0.01, 0.01]) == 0.0
    assert kelly.mc_dd_probability(r, 0.0, n_sims=20, horizon=50)["p_dd_exceed"] == 0.0
    rc = kelly.kelly_risk_constrained(r, dd_threshold=0.20, dd_prob=0.05, horizon=126,
                                      n_sims=200, k_hi=5.0, tol=0.25)
    assert 0.0 < rc["leverage"] < f
    assert rc.get("saturated_at_k_hi") is not True and rc["p_dd_exceed"] <= 0.05
    joint = kelly.per_sleeve_rc_kelly({"a": r[:500], "b": 0.5 * r[500:1000]}, {"a": 0.6, "b": 0.4},
                                      horizon=60, n_sims=100, k_hi=4.0, tol=0.5, max_iters=1)
    assert set(joint["leverages"]) == {"a", "b"} and joint["iterations"][0]["iter"] == 1


def test_sizing_risk_primitives():
    r = np.array([-0.05, -0.02, 0.01, 0.03, 0.02])
    assert sizing_risk.cvar_empirical(r, alpha=0.2) == pytest.approx(0.05)
    assert sizing_risk.cvar_empirical(r, alpha=0.4) == pytest.approx(0.035)
    assert sizing_risk.cvar_leverage_cap(r, 0.10, alpha=0.2) == pytest.approx(2.0)
    assert sizing_risk.cvar_leverage_cap(np.array([0.01, 0.02]), 0.1) == float("inf")
    dd = sizing_risk.drawdown_series(np.array([0.1, -0.05, -0.05, 0.2]))
    assert dd.tolist() == pytest.approx([0.0, 0.05, 0.10, 0.0])
    assert sizing_risk.cdar_empirical(np.array([0.1, -0.05, -0.05, 0.2]), alpha=0.25) == pytest.approx(0.10)
    assert sizing_risk.grossman_zhou_cap(70.0, 100.0, floor_pct=0.20) == 0.0
    assert sizing_risk.grossman_zhou_cap(100.0, 100.0, 0.20, 0.02, 4, 2.5) == pytest.approx(20.0 / 10.0)
    rng = np.random.default_rng(7)
    hist = 0.001 + 0.02 * rng.standard_normal(2000)
    assert sizing_risk.vince_optimal_f(hist) > 0
    caps = sizing_risk.compute_day_caps(hist, 100.0, 100.0, 0.02)
    assert caps.composite == pytest.approx(min(caps.cvar_k, caps.cdar_k, caps.grossman_zhou_k, caps.vince_half_f))
    assert 0.0 <= caps.composite <= 2.0


# --- fundamental law ------------------------------------------------------------

def test_required_ic_monotone_and_verdicts():
    assert fundamental_law.required_ic(1.0, 100) == pytest.approx(0.1)
    assert fundamental_law.required_ic(1.0, 0) == float("inf")
    ics = [fundamental_law.required_ic(s, 50) for s in (0.5, 1.0, 2.0, 4.0)]
    assert all(b > a for a, b in zip(ics, ics[1:]))
    ics_b = [fundamental_law.required_ic(2.0, b) for b in (10, 50, 100, 1000)]
    assert all(b < a for a, b in zip(ics_b, ics_b[1:]))
    assert fundamental_law.implied_sharpe(0.1, 100) == pytest.approx(1.0)
    assert fundamental_law.breadth_verdict(1.0, 100)["verdict"] == "plausible"
    assert fundamental_law.breadth_verdict(3.0, 50)["verdict"] == "elevated"
    assert fundamental_law.breadth_verdict(6.0, 50)["verdict"] == "suspicious"
    v = fundamental_law.breadth_verdict(10.45, 12)
    assert v["verdict"] == "impossible" and v["memo_verdict"] == "impossible"
    assert fundamental_law.breadth_verdict(-6.0, 50)["verdict"] == "suspicious"   # magnitude
    assert fundamental_law.breadth_verdict(0.5, 100)["memo_verdict"] == "comfortable"
    assert fundamental_law.breadth_verdict(1.0, 100)["memo_verdict"] == "plausible"


# --- metrics --------------------------------------------------------------------

def test_metrics_basics():
    assert metrics.max_drawdown([1.0, -2.0, 1.0, -3.0]) == pytest.approx(4.0)
    assert metrics.max_drawdown([1.0, 2.0]) == 0.0
    assert metrics.max_drawdown([]) == 0.0
    assert metrics.max_drawdown([-1.0, float("nan"), -1.0]) == pytest.approx(2.0)
    rets = [0.5, -0.2, 0.3, 0.1, -0.4, 0.2]
    assert metrics.daily_sharpe(rets, 365) == pytest.approx(metrics.sharpe(rets, 365))
    assert metrics.daily_sharpe(rets + [float("nan")], 365) == pytest.approx(metrics.sharpe(rets, 365))
    assert metrics.sharpe([1.0]) == 0.0 and metrics.daily_sharpe([1.0, 1.0]) == 0.0
    assert metrics.trade_sharpe(rets, 100) == pytest.approx(metrics.sharpe(rets, 100))
    assert metrics.trade_sharpe([0.1, 0.1, 0.1], 100) == 0.0
    mean, sd, skew, ek = metrics.moments([1.0, 2.0, 3.0, 4.0])
    assert (mean, sd) == (2.5, pytest.approx(math.sqrt(1.25))) and skew == 0.0 and ek == pytest.approx(-1.36)
    assert metrics.month_count("2020-01-15", "2020-03-01") == 3
    split = metrics.era_split(["2023-12-31", "2024-01-11", "2024-06-01"], [1, 2, 3])
    assert split == {"pre_etf": [1], "post_etf": [2, 3]}
    assert metrics.era_split(["2024-07-22", "2024-07-23"], [1, 2], cutoff=metrics.ETH_SPOT_ETF) == {"pre_etf": [1], "post_etf": [2]}
    with pytest.raises(ValueError):
        metrics.era_split(["2024-01-01"], [1, 2])

    class Trade:
        def __init__(self, exit_time, pnl_pct, closed=True):
            self.exit_time, self.pnl_pct, self.closed = exit_time, pnl_pct, closed

    trades = [Trade("2020-01-10", 1.0), Trade("2020-02-20", -0.5), Trade("2020-02-25", 2.0),
              Trade("2020-05-01", 9.0), Trade("2020-03-01", 1.0, closed=False)]
    assert metrics.trades_to_monthly_returns(trades, 3, "2020-01-01", "2020-03-31") == [1.0, 1.5, 0.0]


# --- drawdown duration ----------------------------------------------------------

def test_drawdown_durations_and_equity_curve(capsys):
    trades = [("2021-01-01", 0.10), ("2021-01-05", -0.05), ("2021-01-10", 0.10)]
    curve = dd_duration.equity_curve_from_trades(trades)
    assert len(curve) == 10 and curve[0] == ("2021-01-01", pytest.approx(1.1))
    dds = dd_duration.drawdown_durations(curve)
    assert len(dds) == 1
    assert dds[0]["start"] == "2021-01-04" and dds[0]["end"] == "2021-01-10"
    assert dds[0]["duration_days"] == 6 and dds[0]["recovered"] is True
    assert dds[0]["depth_pct"] == pytest.approx(5.0)          # (1.1 - 1.045) / 1.1
    open_dd = dd_duration.drawdown_durations(curve[:8])
    assert len(open_dd) == 1 and open_dd[0]["recovered"] is False and open_dd[0]["duration_days"] == 4
    summary = dd_duration.summarize_drawdowns(dds, "synthetic")
    assert summary["n"] == 1 and summary["max_duration_days"] == 6
    assert dd_duration.summarize_drawdowns([], "empty") is None
    assert dd_duration.equity_curve_from_trades([]) == []
    assert dd_duration.drawdown_durations(curve[:1]) == []
    assert "synthetic" in capsys.readouterr().out


# --- alpha half-life ------------------------------------------------------------

def test_alpha_halflife_forward_returns_and_fit():
    p = np.exp(np.array([0.0, 1.0, 2.0, 3.0]))
    fr = alpha_halflife.forward_return(p, 1)
    assert fr[:3].tolist() == pytest.approx([1.0, 1.0, 1.0]) and math.isnan(fr[3])
    fr2 = alpha_halflife.forward_return(p, 2, cumulative=True)
    assert fr2[:2].tolist() == pytest.approx([2.0, 2.0]) and np.isnan(fr2[2:]).all()
    assert np.isnan(alpha_halflife.forward_return(p, 10)).all()
    taus = np.array([1, 2, 3, 5, 7, 10, 15, 20])
    ic = 0.2 * np.exp(-np.log(2) / 7.0 * taus)
    fit = alpha_halflife.fit_exponential_halflife(taus, ic)
    assert fit.halflife == pytest.approx(7.0, rel=1e-6) and fit.r_squared == pytest.approx(1.0)
    assert fit.A == pytest.approx(0.2) and fit.policy == "medium"
    assert alpha_halflife.fit_exponential_halflife(taus[:1], ic[:1]).policy == "undefined"
    # synthetic AR(1) signal with a 7-day half-life embedded in a price path
    rng = np.random.default_rng(7)
    T, rho = 1500, np.exp(-np.log(2) / 7.0)
    s = np.zeros(T)
    eps = rng.normal(0, 1, T)
    for t in range(1, T):
        s[t] = rho * s[t - 1] + eps[t]
    log_ret = np.zeros(T)
    for t in range(1, T):
        log_ret[t] = 0.02 * s[t - 1] + rng.normal(0, 0.02)
    prices = np.exp(np.cumsum(log_ret))
    curve = alpha_halflife.ic_decay_curve(s, prices, [1, 2, 3, 5, 7, 10, 15, 20, 30])
    assert list(curve.columns) == ["tau", "ic", "n"] and curve["ic"].iloc[0] > 0.3
    fit2 = alpha_halflife.fit_exponential_halflife(curve["tau"].values, curve["ic"].values)
    assert 4 < fit2.halflife < 12
    assert math.isfinite(alpha_halflife.ic_at_horizon(s, prices, 1, spearman=True))


# --- reverse stress -------------------------------------------------------------

def test_reverse_stress_joint_paths_and_attribution():
    rng = np.random.default_rng(7)
    T = 600
    base = rng.normal(0.001, 0.015, T)
    sleeves = {"a": 0.7 * base + rng.normal(0, 0.005, T),
               "b": 0.4 * base + rng.normal(0, 0.010, T),
               "c": rng.normal(0.0008, 0.012, T)}
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    paths = reverse_stress.joint_bootstrap_paths(sleeves, weights, horizon=100, n_paths=200, block=20, seed=1)
    assert paths["composite"].shape == (200, 100) and paths["mdd"].shape == (200,)
    assert paths["sleeve_paths"]["a"].shape == (200, 100)
    comp = sum(weights[n] * paths["sleeve_paths"][n] for n in sleeves)
    assert np.allclose(comp, paths["composite"])
    assert (paths["mdd"] >= 0).all() and (paths["mdd"] <= 1).all()
    L = float(np.median(paths["mdd"]))
    attrib = reverse_stress.attribute_failure(paths, L)
    assert attrib["n_total"] == 200 and 0.4 <= attrib["p_failure"] <= 0.6
    assert set(attrib["sleeve_attrib"]) == {"a", "b", "c"}
    assert attrib["sleeve_attrib"]["a"]["mean_cum_on_failure"] < attrib["sleeve_attrib"]["a"]["mean_cum_baseline"]
    corr = reverse_stress.pairwise_failure_correlation(paths, L)
    assert corr["failure_corr"].shape == (3, 3) and np.allclose(np.diag(corr["baseline_corr"]), 1.0)
    none = reverse_stress.attribute_failure(paths, 2.0)
    assert none["n_failure"] == 0 and math.isnan(none["sleeve_attrib"]["a"]["mean_cum_on_failure"])
    assert reverse_stress.pairwise_failure_correlation(paths, 2.0)["failure_corr"] is None
