"""benchmark -- exposure-matched buy-and-hold, start-date sensitivity, paired block bootstrap, bar-phase
sweep, compounded drawdown -- constructed inputs with known answers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from studies.lib.validation import benchmark as bm


def test_exposure_matched_scalar_and_array():
    r = np.array([0.01, -0.02, 0.03])
    assert np.allclose(bm.exposure_matched_buy_and_hold(r, 0.5), r * 0.5)
    assert np.allclose(bm.exposure_matched_buy_and_hold(r, [1.0, 2.0, 0.0]), [0.01, -0.04, 0.0])


def test_cagr_and_start_date_sensitivity():
    # benchmark: +1 % every period for 730 periods; strategy: flat then equal to benchmark in the second year
    n = 730
    b = np.full(n, 0.01)
    s = np.concatenate([np.zeros(365), np.full(365, 0.01)])
    dates = np.arange(n)
    out = bm.start_date_sensitivity(s, b, dates, [0, 365])
    assert out["0"]["cagr_strategy"] < out["0"]["cagr_benchmark"]
    assert abs(out["365"]["diff"]) < 1e-12          # identical from the later start
    assert abs(bm.cagr(b, 365.0) - (1.01 ** 365 - 1)) < 1e-9


def test_paired_block_boot_diff_identical_series_is_zero():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, 500)
    out = bm.paired_block_boot_diff(a, a.copy(), lambda x: float(x.mean()), block=20, n_iter=200)
    assert out["point"] == 0.0 and out["ci"][0] == 0.0 and out["ci"][2] == 0.0
    # and a series that is a shifted copy has a point difference equal to the shift
    out2 = bm.paired_block_boot_diff(a + 0.001, a, lambda x: float(x.mean()), block=20, n_iter=200)
    assert abs(out2["point"] - 0.001) < 1e-12 and out2["p_gt_0"] == 1.0


def test_daily_bars_at_phase_completeness_and_phase():
    # 48 hourly bars from 2024-01-01 00:00 UTC: two complete days at phase 0, one complete day at phase 6
    t0 = 1704067200
    ts = t0 + 3600 * np.arange(48)
    o = np.arange(48, dtype=float); h = o + 1; l = o - 1; c = o + 0.5
    d0 = bm.daily_bars_at_phase(ts, o, h, l, c, 0, 3600)
    assert len(d0) == 2 and d0.loc[0, "open"] == 0.0 and d0.loc[0, "close"] == 23.5 and d0.loc[0, "high"] == 24.0
    d6 = bm.daily_bars_at_phase(ts, o, h, l, c, 6, 3600)
    assert len(d6) == 1 and d6.loc[0, "ts"] == t0 + 6 * 3600 and d6.loc[0, "open"] == 6.0
    incomplete = bm.daily_bars_at_phase(ts, o, h, l, c, 6, 3600, require_complete=False)
    assert len(incomplete) == 3
    sweep = bm.bar_phase_sweep(ts, o, h, l, c, lambda bars: dict(n=len(bars)), phases=(0, 6))
    assert list(sweep.n) == [2, 1]
    assert bm.phase_summary(sweep) == {"n": [1.0, 1.5, 2.0]}


def test_compounded_drawdown_and_mar():
    assert abs(bm.compounded_max_drawdown([0.1, -0.5]) - 0.5) < 1e-12
    assert bm.compounded_max_drawdown([0.01, 0.02]) == 0.0
    assert bm.mar([0.01, 0.02]) == np.inf
    r = [0.10, -0.50, 0.30]
    assert abs(bm.mar(r, 3.0) - bm.cagr(r, 3.0) / 0.5) < 1e-12
