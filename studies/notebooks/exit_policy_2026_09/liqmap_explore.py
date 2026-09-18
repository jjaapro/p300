"""Section 7 secondaries of the liquidation-map study: reported after the verdict, never part of it.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\liqmap_explore.py

The verdict (`results/liqmap/verdict.json`) is written by `liqmap_run.py` and is not touched here. This script reads
the saved state and event tables and answers the question section 7 reserved for after the fact: **how much of Q1's
paired touch difference is the map's placement, and how much is simply that its level sits nearer price than the
control map's?** Section 7 asks for "Delta by distance bin and by sign and size of d_A - d_B"; that is this file.

Output: `results/liqmap/secondary_section7.json`.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import liqmap_lib as L  # noqa: E402
import micro_lib as M  # noqa: E402

RESULTS = HERE / "results" / "liqmap"
FILES = {"BTC": "q1_states.csv.gz", "ETH": "q1_states_eth.csv.gz"}
TOLERANCES = (0.0005, 0.001, 0.002, 0.005)          # |d_A| - |d_B| tolerance in fractions of price: 5, 10, 20, 50 bp


def _stat(rows: pd.DataFrame, value: str) -> dict:
    r = rows.sort_values("ts", kind="mergesort")
    axis = M.day_axis(r["day"])
    return L.paired_stats(r["day"], r[value].to_numpy(float), axis, M.block_indices(len(axis)), claimed_sign=+1)


def load(asset: str) -> pd.DataFrame:
    q = pd.read_csv(RESULTS / FILES[asset]).sort_values("ts", kind="mergesort")
    q["dA"], q["dB"] = q["d_A"].abs(), q["d_B"].abs()
    q["gap"] = q["dA"] - q["dB"]                      # negative: the actual map's cluster sits nearer price
    q["touch_delta"] = q["touch_A"] - q["touch_B"]
    return q


def by_sign(q: pd.DataFrame) -> dict:
    out = {}
    for name, m in (("actual_nearer", q["gap"] < 0), ("control_nearer", q["gap"] > 0), ("equal", q["gap"] == 0)):
        s = q[m]
        out[name] = {"n": int(len(s)), "share": float(len(s) / len(q)),
                     "touch_delta": float(s["touch_delta"].mean()) if len(s) else float("nan"),
                     "touch_rate_actual": float(s["touch_A"].mean()) if len(s) else float("nan"),
                     "touch_rate_control": float(s["touch_B"].mean()) if len(s) else float("nan")}
    return out


def distance_matched(q: pd.DataFrame) -> dict:
    """The paired touch difference among states where the two levels sit at almost the same distance from price."""
    out = {}
    for tol in TOLERANCES:
        s = q[q["gap"].abs() <= tol]
        if len(s) < 50:
            continue
        out[f"within_{int(tol * 10_000)}bp"] = {
            "n": int(len(s)), **_stat(s, "touch_delta"),
            "touch_rate_actual": float(s["touch_A"].mean()), "touch_rate_control": float(s["touch_B"].mean())}
    s = q[q["gap"].abs().between(1e-9, 0.002)]        # the same, excluding states where both maps name one bucket
    out["distinct_levels_within_20bp"] = {"n": int(len(s)), **_stat(s, "touch_delta")}
    both = q[(q["touch_A"] == 1) & (q["touch_B"] == 1)].copy()
    both["turn_delta"] = both["turn_A"] - both["turn_B"]
    s = both[both["gap"].abs() <= 0.002]
    out["turn_within_20bp"] = {"n": int(len(s)), **_stat(s, "turn_delta")}
    return out


def by_distance_bin(q: pd.DataFrame, bins: int = 5) -> dict:
    """Section 7's Delta by distance bin: the actual cluster's own distance decides the bin."""
    edges = np.quantile(q["dA"], np.arange(bins + 1) / bins)
    k = np.clip(np.digitize(q["dA"], edges[1:-1]), 0, bins - 1)
    out = {}
    for b in range(bins):
        s = q[k == b]
        out[f"bin{b}"] = {"n": int(len(s)), "d_actual_range": [float(edges[b]), float(edges[b + 1])],
                          "mean_d_actual": float(s["dA"].mean()), "mean_d_control": float(s["dB"].mean()),
                          "touch_delta": float(s["touch_delta"].mean()),
                          "touch_rate_actual": float(s["touch_A"].mean()),
                          "touch_rate_control": float(s["touch_B"].mean())}
    return out


def write_eth_trades() -> Path:
    """The ETH chento population `holdout` walked, so the results folder alone reproduces its Q2 bootstrap axis.

    `holdout()` saved its event rows but no trades table, and the Q2 day axis spans the whole population, not only the
    event trades; without this file an interval rebuilt from the event rows alone moves in the third decimal.
    """
    tr = pd.read_csv(M.RESULTS / "trades.csv.gz")
    eth = tr[(tr["pop"] == "chento") & (tr["asset"] == "ETH")
             & (tr["entry_ts"] >= 1_640_995_200)].reset_index(drop=True)      # 2022-01-01, section 2
    cols = ["pop", "tid", "asset", "direction", "entry_ts", "entry_day", "entry", "risk", "i0", "x", "kind",
            "exit_price"]
    out = RESULTS / "trades_eth.csv.gz"
    eth[cols].to_csv(out, index=False)
    return out


def main() -> dict:
    out = {"note": "Section 7 secondaries, computed after the verdict was written. Nothing here changes the verdict; "
                   "it explains what the replicated Q1 touch difference is made of.",
           "verdict_sha256": M.sha256(RESULTS / "verdict.json")}
    for asset in FILES:
        q = load(asset)
        out[asset] = {"states": int(len(q)), "mean_gap": float(q["gap"].mean()),
                      "median_d_actual": float(q["dA"].median()), "median_d_control": float(q["dB"].median()),
                      "all_states_touch_delta": float(q["touch_delta"].mean()),
                      "all_states": _stat(q, "touch_delta"),
                      "by_sign_of_gap": by_sign(q), "distance_matched": distance_matched(q),
                      "by_distance_bin": by_distance_bin(q)}
    eth = write_eth_trades()
    out["eth_trades_written"] = {"file": eth.name, "rows": int(len(pd.read_csv(eth))),
                                 "sha256": M.sha256(eth)}
    M.write_json(RESULTS / "secondary_section7.json", out)
    return out


if __name__ == "__main__":
    res = main()
    for asset in FILES:
        a = res[asset]
        print(f"{asset}: all states {a['all_states_touch_delta']:+.4f}; "
              f"actual cluster nearer in {a['by_sign_of_gap']['actual_nearer']['share']:.1%} of states")
        for k, v in a["distance_matched"].items():
            print(f"   {k:28s} n={v['n']:5d} delta {v['mean']:+.4f} "
                  f"ci[{v['ci95'][0]:+.4f},{v['ci95'][1]:+.4f}] p={v['p_one_sided']:.3f}")
