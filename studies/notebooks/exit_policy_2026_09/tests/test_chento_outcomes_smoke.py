"""End-to-end smoke run of the chento outcome path on synthetic random-walk markets (opt-in, ~30 s).

    set EXIT_SMOKE=1 && venv\\Scripts\\python.exe -m pytest studies/notebooks/exit_policy_2026_09/tests -q

Checks that compute_outcomes runs every arm, the bootstrap, Holm, walk-forward, the verdict rule, the forward
profile, the sequence simulation and the DSR on data shaped like the real study, and that the paired design
behaves: a trade A0 closes before 72 h on a stop or target is identical under the no-time-exit arm.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chento_walk import C, ctm  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("EXIT_SMOKE") != "1", reason="slow; set EXIT_SMOKE=1")


def synthetic_market(asset: str, seed: int) -> C.Market:
    start = int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 9, 12, tzinfo=timezone.utc).timestamp())
    ts = np.arange(start, end, 900, dtype=np.int64)
    rng = np.random.default_rng(seed)
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.0025, len(ts))))
    high = close * np.exp(np.abs(rng.normal(0, 0.001, len(ts))))
    low = close * np.exp(-np.abs(rng.normal(0, 0.001, len(ts))))
    bars = C.L.Bars(ts, high, low, close)
    cvd = rng.normal(0, 1, len(ts))
    vel = rng.normal(0, 1, len(ts))
    trig = np.sort(rng.choice(ts[(ts >= start + 40 * 86400) & (ts < end - 10 * 86400)], 120, replace=False))
    dirs = rng.choice(["long", "short"], len(trig))
    opp = {"long": trig[dirs == "short"], "short": trig[dirs == "long"]}
    fs = np.arange(start, end, 8 * 3600, dtype=np.int64)
    return C.Market(asset, bars, cvd, vel, opp, fs, np.full(len(fs), 1e-4))


def synthetic_trades(markets: dict) -> pd.DataFrame:
    rows = []
    lo = int(datetime(2021, 4, 1, tzinfo=timezone.utc).timestamp())
    hi = int(datetime(2026, 9, 8, tzinfo=timezone.utc).timestamp())
    for i, (asset, mkt) in enumerate(markets.items()):
        rng = np.random.default_rng(100 + i)
        ts = mkt.bars.ts[(mkt.bars.ts >= lo) & (mkt.bars.ts < hi)]
        for t in np.sort(rng.choice(ts, 40, replace=False)):
            p = mkt.bars.pos(int(t))
            entry = float(mkt.bars.close[p])
            rows.append({"asset": asset, "t": int(t), "direction": rng.choice(["long", "short"]),
                         "entry": entry, "atr": entry * 0.002, "risk": entry * 0.01})
    tr = pd.DataFrame(rows)
    tr["entry_ts"] = tr["t"] + C.BAR_S
    tr["entry_day"] = [C.utc_day(x) for x in tr["entry_ts"]]
    return tr.sort_values(["entry_ts", "asset"], kind="mergesort").reset_index(drop=True)


def test_compute_outcomes_runs_and_the_design_is_paired():
    import chento_run as R
    markets = {"BTC": synthetic_market("BTC", 1), "ETH": synthetic_market("ETH", 2)}
    trades = synthetic_trades(markets)
    walks, report, decision = R.compute_outcomes(ctm, markets, trades, 3.0)
    assert len(walks) == len(trades) * len(C.ARMS)
    assert set(report["candidates"]) == set(C.CANDIDATES)
    assert decision["verdict"].split("(")[0] in {"CHANGE_SUPPORTED", "KEEP_72H", "INCONCLUSIVE"}
    assert decision["hypothesis_time_stop_harmful"] in {"supported", "contradicted", "not settled"}
    assert report["walk_forward"]["n_folds"] >= 3
    a0 = walks[walks["arm"] == "A0"].set_index(["asset", "t"])
    a1 = walks[walks["arm"] == "A1"].set_index(["asset", "t"])
    early = a0.index[a0["kind"].isin(["stop", "target"])]
    assert np.allclose(a0.loc[early, "net_R"], a1.loc[early, "net_R"])
    seq = report["sequence"]["A1"]["BTC"]
    assert seq["trades"] > 0 and seq["max_concurrent"] >= 1 and seq["mtm_max_drawdown_pct"] <= 0
    print("\nsynthetic verdict:", decision["verdict"], "| hypothesis:", decision["hypothesis_time_stop_harmful"],
          "| A1 mean d", round(report["candidates"]["A1"]["mean_d"], 3))
