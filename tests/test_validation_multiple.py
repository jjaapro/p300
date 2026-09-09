"""stepm / hansen_spa / mcs_t_max -- size under the null, power on a planted SR=2 series."""
from __future__ import annotations

import math

import numpy as np

from studies.lib.validation import mcs, spa, stepm

SR_DAILY = 2.0 / math.sqrt(365)      # annualised Sharpe 2 as a per-day Sharpe


def _panel(rng: np.random.Generator, n: int, T: int, planted: bool = False) -> np.ndarray:
    """(n, T) iid-noise excess returns (1% daily vol); row 0 optionally carries SR=2."""
    x = rng.standard_normal((n, T)) * 0.01
    if planted:
        x[0] += SR_DAILY * 0.01
    return x


def test_stepm_rejects_nothing_on_noise_and_finds_planted():
    false_rejections = 0
    for seed in range(20):
        rng = np.random.default_rng(100 + seed)
        res = stepm.stepm(_panel(rng, 5, 500), n_bootstrap=300, seed=seed)
        assert res["N"] == 5 and res["T"] == 500 and res["block_length"] == 8
        false_rejections += bool(res["rejected"])
    assert false_rejections <= 3          # >= 85% of seeds reject nothing at alpha = 0.10

    rng = np.random.default_rng(999)
    res = stepm.stepm(_panel(rng, 5, 1000, planted=True), n_bootstrap=500, seed=1)
    assert res["rejected"] == {0}
    assert res["t_stats"][0] > 2.0
    assert res["iterations"][0]["rejected_this_step"] == [0]


def test_spa_rejects_nothing_on_noise_and_finds_planted():
    false_rejections = 0
    for seed in range(20):
        rng = np.random.default_rng(200 + seed)
        panel = _panel(rng, 4, 300)
        res = spa.hansen_spa({f"s{i}": panel[i].tolist() for i in range(4)},
                             n_bootstrap=200, rng_seed=seed)
        # port parity: the trader original centres "l" exactly like "u", so
        # p_l == p_u and the consistent p-value is the (weakly) smallest one
        assert res["p_value_l"] == res["p_value_u"] >= res["p_value_c"]
        false_rejections += res["p_value_c"] < 0.05
    assert false_rejections <= 3

    rng = np.random.default_rng(777)
    panel = _panel(rng, 4, 1000, planted=True)
    res = spa.hansen_spa({f"s{i}": panel[i].tolist() for i in range(4)},
                         n_bootstrap=400, rng_seed=3)
    assert res["best_strategy"] == "s0"
    assert res["p_value_c"] < 0.05
    assert res["best_studentized"] > 2.0


def test_mcs_calibrated_on_noise_and_isolates_planted():
    """MCS's default alpha=0.25 is a set-construction confidence, so under
    the null the full set is retained ~75% of the time; the 85% no-rejection
    check runs at alpha=0.05 and the default-alpha run checks calibration
    (first-step p-values centred on 0.5)."""
    p_first, full_at_default, full_at_05 = [], 0, 0
    for seed in range(20):
        rng = np.random.default_rng(300 + seed)
        L = -_panel(rng, 5, 500)                       # loss = -return
        res = mcs.mcs_t_max(L, n_bootstrap=500, seed=seed)
        p_first.append(res["iterations"][0]["p_value"])
        full_at_default += len(res["mcs_indices"]) == 5
        res05 = mcs.mcs_t_max(L, alpha=0.05, n_bootstrap=500, seed=seed)
        full_at_05 += len(res05["mcs_indices"]) == 5
    assert 0.3 < float(np.mean(p_first)) < 0.7
    assert full_at_default >= 10
    assert full_at_05 >= 17                            # >= 85% of seeds reject nothing

    rng = np.random.default_rng(31)
    L = -_panel(rng, 3, 2000, planted=True)
    res = mcs.mcs_t_max(L, n_bootstrap=1000, seed=2)
    assert 0 in res["mcs_indices"]
    assert len(res["mcs_indices"]) < 3
    assert res["iterations"][0]["eliminated"] != 0
