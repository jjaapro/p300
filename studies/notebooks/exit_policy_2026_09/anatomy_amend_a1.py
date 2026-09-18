"""squeeze_bull top anatomy, stage A amendment A1 (EXPLORATORY): the top / false-top overlay on the same fires.

    venv\Scripts\python.exe studies\notebooks\exit_policy_2026_09\anatomy_amend_a1.py

PROTOCOL_TOP_ANATOMY.md, Amendment A1. Uses anatomy_lib unchanged; writes manifest_A1.json before computing.
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
import anatomy_lib as A  # noqa: E402
import anatomy_run as RUN  # noqa: E402
import micro_lib as ML  # noqa: E402

CI_LAGS = (-240, -120, -60, -30, -15, 0)
FILES = ["PROTOCOL_TOP_ANATOMY.md", "anatomy_amend_a1.py", "anatomy_lib.py", "results/top_anatomy/shapes.csv",
         "results/top_anatomy/manifest_A.json"]


def manifest() -> dict:
    current = {f: ML.sha256(HERE / f) for f in FILES}
    target = A.RESULTS / "manifest_A1.json"
    if target.exists():
        old = json.loads(target.read_text())
        if old["files"] != current:
            raise SystemExit("manifest_A1.json exists with different hashes")
        return old
    out = {"stage": "A amendment A1 (exploratory)", "created_utc": RUN.now_utc(), "files": current}
    A.write_json(target, out)
    return out


def paired_profiles(mkt: A.Market, sh: pd.DataFrame, step: int = 5) -> dict:
    lags = np.arange(-A.PROFILE_MIN, A.PROFILE_MIN + 1, step)
    tops, falses = {n: [] for n in A.FEATURES}, {n: [] for n in A.FEATURES}
    fids = []
    for f in sh[sh["shape"] == "spike_reversal"].itertuples(index=False):
        t = int(f.T)
        ft = A.false_tops(mkt.high[int(f.i0):int(f.i0) + A.HORIZON_MIN], int(f.S), t)
        if len(ft) == 0:
            continue
        fids.append(f.fid)
        feats = A.fire_features(mkt, f, int(ft.min()) - A.PROFILE_MIN, t + A.PROFILE_MIN + 1).set_index("rel_min")
        for name in A.FEATURES:
            col = feats[name]
            tops[name].append(col.reindex(t + lags).to_numpy(float))
            with np.errstate(all="ignore"):
                falses[name].append(np.nanmean(np.vstack([col.reindex(m + lags).to_numpy(float) for m in ft]), axis=0))
    rng = np.random.default_rng(A.SEED)
    out = {"lags_min": lags.tolist(), "fires": fids}
    for name in A.FEATURES:
        T, F = np.vstack(tops[name]), np.vstack(falses[name])
        D = T - F
        with np.errstate(all="ignore"):
            rec = {"top_median": np.nanmedian(T, axis=0).tolist(), "false_median": np.nanmedian(F, axis=0).tolist(),
                   "diff_median": np.nanmedian(D, axis=0).tolist(), "at_lags": {}}
        for lag in CI_LAGS:
            d = D[:, int(np.where(lags == lag)[0][0])]
            d = d[np.isfinite(d)]
            if len(d) == 0:
                continue
            boots = np.median(d[rng.integers(0, len(d), size=(A.N_BOOT, len(d)))], axis=1)
            rec["at_lags"][str(lag)] = {"fires": int(len(d)), "median_diff": float(np.median(d)),
                                        "ci95": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
                                        "share_top_higher": float((d > 0).mean())}
        out[name] = rec
    return out


def main() -> None:
    man = manifest()
    mkt = A.load_market()
    fires = A.load_fires(mkt)
    sh = fires.merge(pd.read_csv(A.RESULTS / "shapes.csv")[["fid", "shape", "S", "T", "C"]], on="fid")
    res = paired_profiles(mkt, sh)
    res["manifest_created_utc"] = man["created_utc"]
    A.write_json(A.RESULTS / "profiles_paired.json", res)
    for name in A.FEATURES:
        print(name, {k: (round(v["median_diff"], 3), [round(x, 3) for x in v["ci95"]]) for k, v in res[name]["at_lags"].items()})


if __name__ == "__main__":
    main()
