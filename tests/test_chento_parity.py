"""Byte-equivalence parity: chento_triple_v3 sleeve math vs the research
implementations it was ported from (feedback_verify_byte_equivalence rule).

Runs the sleeve's feature functions and the research validation-script
functions on the SAME data slice from the real prod.db and asserts the
outputs are numerically identical (allclose at 1e-9, identical NaN masks).
This is the drift tripwire for the standalone bot extraction: any future
"harmless" refactor of the sleeve math that changes values fails here.

Read-only against the live DB; skipped when prod.db is absent (CI without
data). B1/B5/B7 windows come from the sleeve's production config so a
config-vs-call divergence is also caught.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("CHENTO_V3_DIAG", "0")   # never write live diag from tests

from strategies.support import db as _db_mod  # noqa: E402

pytestmark = pytest.mark.skipif(
    not _db_mod.PROD_DB.exists() or _db_mod.PROD_DB.stat().st_size == 0,
    reason="real prod.db not available")


def _assert_series_equal(name, a, b, rtol=1e-9):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    assert a.shape == b.shape, f"{name}: shape {a.shape} vs {b.shape}"
    nan_a, nan_b = np.isnan(a), np.isnan(b)
    assert (nan_a == nan_b).all(), (
        f"{name}: NaN masks differ at {int((nan_a != nan_b).sum())} positions")
    ok = np.allclose(a[~nan_a], b[~nan_b], rtol=rtol, atol=1e-12)
    if not ok:
        diff = np.abs(a[~nan_a] - b[~nan_b])
        raise AssertionError(f"{name}: max abs diff {diff.max():.3e}")


@pytest.fixture(scope="module")
def btc_15m():
    from studies.notebooks.chento_journal.validation_B1_moneyflow_divergence \
        import load_btc_15m
    df = load_btc_15m()
    assert len(df) > 10_000, "cd_futures_15m unexpectedly small"
    # Last ~250 days is plenty: covers full 30d rolling warmup + live era.
    return df[df.index >= df.index.max() - np.timedelta64(250, "D")].copy()


def test_b1_moneyflow_parity(btc_15m):
    from studies.notebooks.chento_journal import (
        validation_B1_moneyflow_divergence as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import (
        B1_CVD_WINDOW_BARS, B1_VEL_WINDOW_BARS)

    r = research.compute_moneyflow_signal(
        btc_15m, cvd_window_bars=B1_CVD_WINDOW_BARS,
        velocity_window_bars=B1_VEL_WINDOW_BARS)
    s = sleeve.compute_moneyflow_signal(
        btc_15m, cvd_window_bars=B1_CVD_WINDOW_BARS,
        velocity_window_bars=B1_VEL_WINDOW_BARS)
    _assert_series_equal("cvd_z", r["cvd_z"], s["cvd_z"])
    _assert_series_equal("vel_z", r["vel_z"], s["vel_z"])


def test_atr_parity(btc_15m):
    from studies.notebooks.chento_journal import (
        validation_B1_moneyflow_divergence as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import ATR_PERIOD

    _assert_series_equal(
        "atr",
        research.compute_atr(btc_15m, ATR_PERIOD),
        sleeve.compute_atr(btc_15m, ATR_PERIOD))


def test_b5_lsr_parity():
    from studies.notebooks.chento_journal import (
        validation_B5_lsr_extremes as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import B5_ROLLING_DAYS

    lsr = research.load_lsr("BTC")
    assert len(lsr) > 1000, "ca_long_short_ratio unexpectedly small"
    r = research.compute_lsr_extremes(lsr, rolling_days=B5_ROLLING_DAYS)
    s = sleeve.compute_lsr_extremes(lsr, rolling_days=B5_ROLLING_DAYS)
    for col in ("lp_p10", "lp_p50", "lp_p90"):
        _assert_series_equal(col, r[col], s[col])


def test_b7_multitf_cvd_parity(btc_15m):
    from studies.notebooks.chento_journal import (
        validation_B7_multitf_cvd as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import B7_TIMEFRAMES

    r = research.compute_multitf_cvd(btc_15m)
    s = sleeve.compute_multitf_cvd_z(btc_15m, B7_TIMEFRAMES)
    for tf in B7_TIMEFRAMES:
        # research names cvd_{tf}_z; the sleeve names cvd_z_{tf}
        _assert_series_equal(f"cvd[{tf}]", r[f"cvd_{tf}_z"], s[f"cvd_z_{tf}"])
