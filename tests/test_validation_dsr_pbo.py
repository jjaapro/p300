"""studies.lib.validation.dsr_pbo -- synthetic data, fixed seeds."""
from __future__ import annotations

import math

import numpy as np
import pytest

from studies.lib.validation import dsr_pbo


def test_normal_ppf_inverts_normal_cdf():
    for x in np.linspace(-3.0, 3.0, 25):
        x = float(x)
        assert dsr_pbo.normal_ppf(dsr_pbo.normal_cdf(x)) == pytest.approx(x, abs=1e-6)
    assert dsr_pbo.normal_ppf(0.0) == float("-inf")
    assert dsr_pbo.normal_ppf(1.0) == float("inf")
    assert dsr_pbo.normal_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert dsr_pbo.normal_cdf(0.0) == pytest.approx(0.5)


def test_expected_max_sharpe_grows_with_trials():
    var = 1.0 / 999
    vals = [dsr_pbo.expected_max_sharpe(n, var) for n in (1, 2, 10, 100, 1000)]
    assert vals[0] == 0.0
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert dsr_pbo.expected_max_sharpe(100, 0.0) == 0.0


def test_dsr_pure_noise_best_of_100_is_calibrated():
    """Best-of-100 noise trials deflated by N=100 -> DSR centred on 0.5;
    the same winners judged as single trials (N=1) look 'significant'."""
    rng = np.random.default_rng(7)
    n_obs, n_trials = 500, 100
    deflated, undeflated = [], []
    for _ in range(40):
        trials = rng.standard_normal((n_trials, n_obs))
        srs = trials.mean(axis=1) / trials.std(axis=1, ddof=1)
        best = trials[int(np.argmax(srs))]
        res = dsr_pbo.dsr_from_returns(best, n_trials=n_trials)
        assert res is not None
        deflated.append(res["dsr"])
        undeflated.append(dsr_pbo.dsr_from_returns(best, n_trials=1)["dsr"])
    med = float(np.median(deflated))
    assert 0.3 < med < 0.7, med
    assert float(np.median(undeflated)) > 0.95


def test_dsr_strong_series_passes_at_n1():
    """A daily series standardised to an exact per-obs SR of 0.10 (annualised
    ~1.9) at N=1 clears 0.95.  (At annualised SR 1.0 over 1000 obs the DSR
    z-stat is 1.65 -- exactly the 0.95 line -- so the test uses a Sharpe
    comfortably inside the pass region.)"""
    rng = np.random.default_rng(11)
    z = rng.standard_normal(1000)
    z = (z - z.mean()) / z.std(ddof=1)
    r = 0.001 + 0.01 * z
    res = dsr_pbo.dsr_from_returns(r, n_trials=1, periods_per_year=365)
    assert res["sr_per_obs"] == pytest.approx(0.10, abs=1e-9)
    assert res["sr_ann"] == pytest.approx(0.10 * math.sqrt(365))
    assert res["sr_expected"] == 0.0
    assert res["dsr"] > 0.95
    # zero-mean version of the same noise is an exact coin flip
    res0 = dsr_pbo.dsr_from_returns(0.01 * z, n_trials=1)
    assert res0["dsr"] == pytest.approx(0.5, abs=1e-9)
    # the explicit-moments form reproduces the convenience form
    p = dsr_pbo.deflated_sharpe(res["sr_per_obs"], 1, res["n"], res["skew"], res["kurt"])
    assert p == pytest.approx(res["dsr"])
    zst = dsr_pbo.deflated_sharpe_z(res["sr_per_obs"], 1, res["n"], res["skew"], res["kurt"])
    assert zst == pytest.approx(res["dsr_z"])


def test_deflated_sharpe_conventions_and_degenerate_inputs():
    # Gaussian kurtosis (3) -> denominator sqrt(1 + SR^2/2), per the paper
    z = dsr_pbo.deflated_sharpe_z(0.2, 1, 101, 0.0, 3.0)
    assert z == pytest.approx(0.2 * 10 / math.sqrt(1 + 0.02))
    # explicit cross-trial variance raises the null and lowers the DSR
    p_iid = dsr_pbo.deflated_sharpe(0.1, 50, 500, 0.0, 3.0)
    p_wide = dsr_pbo.deflated_sharpe(0.1, 50, 500, 0.0, 3.0, sr_variance=4.0 / 499)
    assert p_wide < p_iid
    # degenerate inputs
    assert dsr_pbo.deflated_sharpe(1.0, 10, 1, 0.0, 3.0) == 0.5
    assert dsr_pbo.deflated_sharpe_z(1.0, 10, 1, 0.0, 3.0) == 0.0
    assert dsr_pbo.dsr_from_returns([0.01, 0.01, 0.01], n_trials=3) is None
    assert dsr_pbo.dsr_from_returns([0.01], n_trials=1) is None


def test_pbo_pure_noise_near_half():
    rng = np.random.default_rng(3)
    pbos = []
    for _ in range(3):
        m = rng.standard_normal((20, 200))   # 20 trials x 200 periods
        pbo, logits = dsr_pbo.pbo_from_trial_matrix(m, s=10)
        assert len(logits) == math.comb(10, 5)
        pbos.append(pbo)
    assert 0.3 <= float(np.mean(pbos)) <= 0.7, pbos


def test_pbo_dominant_trial_is_low():
    rng = np.random.default_rng(5)
    m = rng.standard_normal((20, 200))
    m[0] += 0.5
    pbo, logits = dsr_pbo.pbo_from_trial_matrix(m, s=10)
    assert pbo < 0.15
    assert float(np.median(logits)) > 0


def test_cscv_pbo_layout_and_guards():
    rng = np.random.default_rng(9)
    m = rng.standard_normal((20, 200))
    pbo_a, la = dsr_pbo.pbo_from_trial_matrix(m, s=10)
    pbo_b, lb = dsr_pbo.cscv_pbo(m.T.tolist(), s=10)
    assert pbo_a == pbo_b and la == lb
    assert dsr_pbo.cscv_pbo([[0.1, 0.2]] * 4, s=10) == (0.5, [])   # T < s
    with pytest.raises(ValueError):
        dsr_pbo.pbo_from_trial_matrix(m, s=7)


def test_pbo_cscv_day_of_week():
    rng = np.random.default_rng(2)
    seq = [(i % 7, float(x)) for i, x in enumerate(rng.standard_normal(420))]
    pbo, n_folds = dsr_pbo.pbo_cscv(seq, s=6)
    assert n_folds == math.comb(6, 3)
    assert 0.0 <= pbo <= 1.0
    # a day-of-week with a real edge is picked in-sample and holds OOS
    seq2 = [(d, r + (1.0 if d == 2 else 0.0)) for d, r in seq]
    pbo2, _ = dsr_pbo.pbo_cscv(seq2, s=6)
    assert pbo2 < 0.15
