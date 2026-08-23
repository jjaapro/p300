"""Byte-equivalence parity for the multi-asset chento ETH leg
(feedback_verify_byte_equivalence rule; multi_asset_chento_plan Phase B gate).

Same contract as test_chento_parity.py, on ETH data: the sleeve's feature
functions and the research validation functions must produce numerically
identical outputs on the same cd_futures_eth_15m / ETH-LSR slices. Any sleeve
refactor that would make the ETH leg drift from the validated research
pipeline fails here.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("CHENTO_V3_DIAG", "0")

from strategies.support import db as _db_mod  # noqa: E402

pytestmark = pytest.mark.skipif(
    not _db_mod.PROD_DB.exists() or _db_mod.PROD_DB.stat().st_size == 0,
    reason="real prod.db not available")

from tests.test_chento_parity import _assert_series_equal  # noqa: E402


@pytest.fixture(scope="module")
def eth_15m():
    from studies.notebooks.chento_journal.validation_multi_asset import load_perp_15m
    df = load_perp_15m("ETH")
    assert len(df) > 10_000, "cd_futures_eth_15m unexpectedly small"
    return df[df.index >= df.index.max() - np.timedelta64(250, "D")].copy()


def test_b1_moneyflow_parity_eth(eth_15m):
    from studies.notebooks.chento_journal import (
        validation_B1_moneyflow_divergence as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import (
        B1_CVD_WINDOW_BARS, B1_VEL_WINDOW_BARS)

    r = research.compute_moneyflow_signal(
        eth_15m, cvd_window_bars=B1_CVD_WINDOW_BARS,
        velocity_window_bars=B1_VEL_WINDOW_BARS)
    s = sleeve.compute_moneyflow_signal(
        eth_15m, cvd_window_bars=B1_CVD_WINDOW_BARS,
        velocity_window_bars=B1_VEL_WINDOW_BARS)
    _assert_series_equal("cvd_z[ETH]", r["cvd_z"], s["cvd_z"])
    _assert_series_equal("vel_z[ETH]", r["vel_z"], s["vel_z"])


def test_b5_lsr_parity_eth():
    from studies.notebooks.chento_journal import (
        validation_B5_lsr_extremes as research)
    from studies.notebooks.chento_journal.validation_multi_asset import load_lsr_asset
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import B5_ROLLING_DAYS

    lsr = load_lsr_asset("ETH")
    assert len(lsr) > 1000, "ETH rows in ca_long_short_ratio unexpectedly few"
    r = research.compute_lsr_extremes(lsr, rolling_days=B5_ROLLING_DAYS)
    s = sleeve.compute_lsr_extremes(lsr, rolling_days=B5_ROLLING_DAYS)
    for col in ("lp_p10", "lp_p50", "lp_p90"):
        _assert_series_equal(f"{col}[ETH]", r[col], s[col])


def test_b7_multitf_cvd_parity_eth(eth_15m):
    from studies.notebooks.chento_journal import (
        validation_B7_multitf_cvd as research)
    from strategies.sleeves.chento_triple_v3 import math as sleeve
    from strategies.sleeves.chento_triple_v3.config import B7_TIMEFRAMES

    r = research.compute_multitf_cvd(eth_15m)
    s = sleeve.compute_multitf_cvd_z(eth_15m, B7_TIMEFRAMES)
    for tf in B7_TIMEFRAMES:
        _assert_series_equal(f"cvd[{tf}][ETH]", r[f"cvd_{tf}_z"], s[f"cvd_z_{tf}"])


def test_asset_config_resolution():
    """The sleeve config must resolve ETH tables when CHENTO_V3_ASSET=ETH
    (checked via a subprocess so the env is read at import time, as in the
    real per-asset bot processes) and default to BTC otherwise."""
    import subprocess
    import sys
    code = ("import os; os.environ['CHENTO_V3_ASSET']='ETH'; "
            "from strategies.sleeves.chento_triple_v3 import config as c; "
            "print(c.PERP_15M_TABLE, c.OKX_1H_TABLE, c.LSR_ASSET, c.FILTER_NO_TILT)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(_db_mod.PROD_DB.parents[2]))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [
        "cd_futures_eth_15m", "okx_perp_eth_1h", "ETH", "False"]

    from strategies.sleeves.chento_triple_v3 import config as c
    assert c.PERP_15M_TABLE == "cd_futures_15m"
    assert c.FILTER_NO_TILT is True
