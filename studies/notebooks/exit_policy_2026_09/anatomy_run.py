"""squeeze_bull top anatomy, stage A (EXPLORATORY): manifest, then every table and figure input in one run.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\anatomy_run.py

The manifest (protocol, code, tests and data hashes) is written before anything is computed; a later run with changed
files refuses to overwrite it. Nothing here decides anything: PROTOCOL_TOP_ANATOMY.md section 6 picks candidates for a
separate stage B, which runs on holdout data this script never touches.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import anatomy_lib as A  # noqa: E402
import micro_lib as ML  # noqa: E402

FILES = ["PROTOCOL_TOP_ANATOMY.md", "anatomy_lib.py", "anatomy_data.py", "anatomy_run.py", "tests/test_anatomy.py",
         "cache/BTCUSDT_metrics_5m.meta.json", "cache/BTCUSDT_premium_1m.meta.json", "cache/BTCUSDT_case_SJ-4250.meta.json",
         "cache/BTCUSDT_book1pct_1m.meta.json", "data/raw/anatomy/manifest.json"]
INPUTS = {"perp_1m": A.ORB_CACHE / "BTCUSDT_perp_1m.npz", "metrics_5m": A.CACHE / "BTCUSDT_metrics_5m.npz",
          "premium_1m": A.CACHE / "BTCUSDT_premium_1m.npz", "book_1m": A.CACHE / "BTCUSDT_book1pct_1m.npz",
          "case_panel": A.CACHE / "BTCUSDT_case_SJ-4250.npz", "ledger": A.LEDGER, "hourly_snapshot": A.HOURLY}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manifest() -> dict:
    current = {"files": {f: ML.sha256(HERE / f) for f in FILES}, "inputs": {k: ML.sha256(p) for k, p in INPUTS.items()}}
    target = A.RESULTS / "manifest_A.json"
    if target.exists():
        old = json.loads(target.read_text())
        if old["files"] != current["files"] or old["inputs"] != current["inputs"]:
            raise SystemExit("manifest_A.json exists with different hashes; stage A is not rerun on changed files")
        return old
    tests = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests" / "test_anatomy.py"), "-q", "-p",
                            "no:cacheprovider"], capture_output=True, text=True)
    if tests.returncode != 0:
        raise SystemExit("fixtures fail; nothing computed")
    out = {"stage": "A (exploratory)", "created_utc": now_utc(), "fixtures": tests.stdout.strip().splitlines()[-1], **current,
           "note": "Written before any feature on a fire's path after entry was computed."}
    A.write_json(target, out)
    return out


def case_study(mkt: A.Market) -> tuple[dict, pd.DataFrame]:
    f = pd.Series(A.fire_row(mkt, A.CASE_ID, A.CASE_BAR_TS, A.CASE_ENTRY))
    i0 = int(f["i0"])
    end = len(mkt.close)
    high, low = mkt.high[i0:end], mkt.low[i0:end]
    sh = A.bounce_shape(high, low, float(f["entry"]))
    fire = type("Fire", (), f.to_dict())
    feats = A.fire_features(mkt, fire, -24 * 60, end - i0)
    pr, orr = feats.set_index("rel_min")["PR"], feats.set_index("rel_min")["OR"]
    after = feats[feats["rel_min"] >= 0]

    def t(rel):
        return None if rel is None or rel < 0 else datetime.fromtimestamp(mkt.time_s(i0 + int(rel)), tz=timezone.utc).isoformat()

    first_pr = A.first_true(after["PR"].to_numpy(float) >= 1)
    first_or = A.first_true(after["OR"].to_numpy(float) >= 1)
    summary = {"fire": f.to_dict(), "shape": sh, "panel_end_utc": t(end - 1 - i0),
               "entry_utc": t(0), "time_stop_utc": datetime.fromtimestamp(A.CASE_EXIT_TS, tz=timezone.utc).isoformat(),
               "spike_start_utc": t(sh["S"]), "top_utc": t(sh["T"]), "reversal_utc": t(sh["C"]),
               "top_price": float(high[sh["T"]]) if sh["T"] >= 0 else None,
               "top_runup_pct": float(high[sh["T"]] / f["entry"] - 1) * 100 if sh["T"] >= 0 else None,
               "PR_at_top": float(pr.get(sh["T"], np.nan)) if sh["T"] >= 0 else None,
               "OR_at_top": float(orr.get(sh["T"], np.nan)) if sh["T"] >= 0 else None,
               "first_PR_ge_1_utc": t(first_pr), "first_OR_ge_1_utc": t(first_or),
               "false_tops_utc": [t(m) for m in A.false_tops(high, sh["S"], sh["T"])] if sh["shape"] == "spike_reversal" else [],
               "P0_minus_entry_pct": float(f["P0"] / f["entry"] - 1) * 100,
               "oi_flush_pct_archive": float(f["OI_E"] / f["OI_0"] - 1) * 100}
    feats["time_utc"] = [datetime.fromtimestamp(mkt.time_s(i0 + int(r)), tz=timezone.utc).isoformat() for r in feats["rel_min"]]
    return summary, feats.iloc[::5].reset_index(drop=True)


def gallery(mkt: A.Market, sh: pd.DataFrame, step: int = 5) -> tuple[np.ndarray, pd.DataFrame]:
    lags = np.arange(-1440, 1441, step)
    rows, mat = [], []
    for f in sh[sh["shape"] == "spike_reversal"].itertuples(index=False):
        top_i = int(f.i0) + int(f.T)
        top = mkt.high[top_i]
        mat.append((mkt.close[top_i + lags] / top - 1) * 100)
        rows.append({"fid": f.fid, "entry_pct_of_top": (f.entry / top - 1) * 100, "P0_pct_of_top": (f.P0 / top - 1) * 100,
                     "time_stop_rel_min": int(f.i0) + 2880 - top_i, "spike_rel_min": int(f.S) - int(f.T),
                     "entry_rel_min": -int(f.T), "top_runup_pct": f.top_runup_pct})
    return np.vstack(mat), pd.DataFrame(rows).assign(lags_from=int(lags[0]), lag_step=step)


def main() -> dict:
    man = manifest()
    mkt = A.load_market()
    fires = A.load_fires(mkt)
    R = A.RESULTS
    A.write_json(R / "data_checks.json", A.data_checks(mkt, fires))
    sh = A.shapes(mkt, fires)
    sh.to_csv(R / "shapes.csv", index=False)
    per_fire, tvf = A.top_vs_false(mkt, sh)
    per_fire.to_csv(R / "top_vs_false_per_fire.csv", index=False)
    A.write_json(R / "top_vs_false.json", tvf)
    A.write_json(R / "profiles.json", A.profiles(mkt, sh))
    rep_per, rep = A.repair(mkt, sh)
    rep_per.to_csv(R / "repair_per_fire.csv", index=False)
    A.write_json(R / "repair.json", rep)
    states, smap = A.state_map(mkt, fires)
    states.to_csv(R / "states.csv.gz", index=False)
    A.write_json(R / "state_map.json", smap)
    mat, gal = gallery(mkt, sh)
    np.savez_compressed(R / "gallery.npz", close_pct_of_top=mat)
    gal.to_csv(R / "gallery.csv", index=False)
    del mkt
    case, case_feats = case_study(A.load_case_market())
    A.write_json(R / "case_SJ-4250.json", case)
    case_feats.to_csv(R / "case_SJ-4250_features.csv.gz", index=False)
    sr = sh[sh["shape"] == "spike_reversal"]
    summary = {"created_utc": now_utc(), "manifest_created_utc": man["created_utc"], "fires": int(len(sh)),
               "shapes": sh["shape"].value_counts().to_dict(),
               "spike_reversal": {"median_hours_to_spike": float(sr["hours_to_spike"].median()),
                                  "median_hours_to_top": float(sr["hours_to_top"].median()),
                                  "median_hours_spike_to_top": float(sr["hours_spike_to_top"].median()),
                                  "median_top_runup_pct": float(sr["top_runup_pct"].median()),
                                  "tops_after_48h": int(sr["top_after_48h"].sum()),
                                  "median_fall_24h_after_top_pct": float(sr["fall_24h_after_top_pct"].median()),
                                  "fires_with_false_tops": int((sr["n_false_tops"] > 0).sum())},
               "case": {k: case[k] for k in ("shape", "top_utc", "top_runup_pct", "PR_at_top", "OR_at_top")}}
    A.write_json(R / "summary.json", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(main(), indent=1, default=str))
