"""studies.lib.validation.bootstrap -- coverage, block structure, DSR thresholds."""
from __future__ import annotations

import numpy as np
import pytest

from studies.lib.validation import bootstrap, metrics


def test_percentile_ci_covers_true_per_trade_sharpe():
    """90% percentile interval of the per-trade Sharpe over 200 synthetic
    60-trade samples with true SR = 0.2 (mu 1%, sd 5%)."""
    true_sr = 0.2
    rng = np.random.default_rng(21)
    n_draws, hits = 200, 0
    for k in range(n_draws):
        rs = (0.01 + 0.05 * rng.standard_normal(60)).tolist()
        res = bootstrap.bootstrap_sharpe(rs, n_iter=300, seed=k, n_per_year=12)
        assert res["n"] == 60 and res["n_iter"] == 300
        assert res["sr_p05"] <= res["sr_p50"] <= res["sr_p95"]
        hits += res["sr_p05"] <= true_sr <= res["sr_p95"]
    coverage = hits / n_draws
    assert 0.80 <= coverage <= 0.98, coverage


def test_bootstrap_sharpe_threshold_and_guards():
    rng = np.random.default_rng(1)
    rs = (0.01 + 0.05 * rng.standard_normal(80)).tolist()
    res = bootstrap.bootstrap_sharpe(rs, n_iter=500, threshold=0.0, seed=3)
    assert res["threshold"] == 0.0
    assert 0.0 <= res["p_at_or_above"] <= 1.0
    assert res["sr_point_ann"] == pytest.approx(res["sr_point"] * 12 ** 0.5)
    assert bootstrap.bootstrap_sharpe([0.01], n_iter=10) is None


def test_block_bootstrap_preserves_lag1_autocorrelation_sign():
    rng = np.random.default_rng(4)
    T, phi = 1000, 0.6
    x = np.empty(T)
    x[0] = 0.0
    for t in range(1, T):
        x[t] = phi * x[t - 1] + rng.standard_normal()

    def ac1(v: np.ndarray) -> float:
        v = v - v.mean()
        return float((v[1:] * v[:-1]).sum() / (v * v).sum())

    assert ac1(x) > 0.4
    rng2 = np.random.default_rng(5)
    block_ac = [ac1(x[bootstrap.circular_block_indices(T, T, 20, rng2)]) for _ in range(50)]
    iid_ac = [ac1(x[rng2.integers(0, T, T)]) for _ in range(50)]
    assert min(block_ac) > 0.0
    assert float(np.mean(block_ac)) > 0.3
    assert abs(float(np.mean(iid_ac))) < 0.1


def test_circular_block_indices_structure():
    rng = np.random.default_rng(0)
    idx = bootstrap.circular_block_indices(T=100, length=45, block=10, rng=rng)
    assert idx.shape == (45,) and idx.dtype == np.int64
    assert idx.min() >= 0 and idx.max() < 100
    # consecutive indices inside a block differ by 1 modulo T
    for b in range(4):
        blk = idx[b * 10:(b + 1) * 10]
        assert all((blk[j + 1] - blk[j]) % 100 == 1 for j in range(9))


def test_block_bootstrap_sharpe_ci_and_p_positive():
    rng = np.random.default_rng(8)
    r = 0.001 + 0.01 * rng.standard_normal(600)
    res = bootstrap.block_bootstrap_sharpe(r, block=20, n_iter=400, seed=1, periods_per_year=365)
    assert res["n"] == 600 and res["block"] == 20
    assert res["sr_p05"] <= res["sr_p50"] <= res["sr_p95"]
    assert res["sr_point"] == pytest.approx(metrics.daily_sharpe(r, 365))
    assert 0.0 <= res["p_sr_gt_0"] <= 1.0
    strong = 0.005 + 0.01 * rng.standard_normal(600)
    assert bootstrap.block_bootstrap_sharpe(strong, block=20, n_iter=400, seed=1)["p_sr_gt_0"] > 0.99
    assert bootstrap.block_bootstrap_sharpe([0.01], n_iter=10) is None


def test_dsr_required_sr_increases_with_trials():
    vals = [bootstrap.dsr_required_sr(100, n) for n in (1, 5, 20, 100, 1000)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    # N=1: just the one-sided 95% quantile of the SR estimator
    assert vals[0] == pytest.approx(1.6449 / 99 ** 0.5, abs=1e-3)
    # more observations -> lower required per-trade Sharpe
    assert bootstrap.dsr_required_sr(400, 20) < bootstrap.dsr_required_sr(100, 20)
    assert bootstrap.dsr_required_sr(1, 10) is None
