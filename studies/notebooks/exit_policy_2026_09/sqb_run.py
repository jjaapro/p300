"""Squeeze_bull exit arm (REPORT-ONLY): preconditions, freeze, one outcome run.

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\sqb_run.py checks
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\sqb_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\sqb_run.py outcomes

`compute_report` is pure (path, fires, flush bars) so the notebook can recompute it and compare. Nothing here
decides anything: the pre-registration makes every number report-only until squeeze_bull's n = 20 / 30 re-cuts.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sqb_lib as S  # noqa: E402

INPUTS = {
    "ledger": S.LEDGER,
    "perp_1m": S.ORB_CACHE / "BTCUSDT_perp_1m.npz",
    "funding": S.ORB_CACHE / "BTCUSDT_funding.npz",
    "hourly_snapshot": S.HOURLY,
}


def input_hashes() -> dict:
    return {k: S.sha256(p) for k, p in INPUTS.items()}


def checks() -> dict:
    out = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "inputs_sha256": input_hashes()}
    snap = json.loads((S.RESULTS / "hourly_snapshot.json").read_text())
    out["Q1"] = {"hourly_snapshot_matches_capture": out["inputs_sha256"]["hourly_snapshot"] == snap["file_sha256"],
                 "perp_panel_logical_sha256": json.loads((S.ORB_CACHE / "BTCUSDT_perp_1m.meta.json").read_text())["logical_sha256"]}
    out["Q1"]["pass"] = out["Q1"]["hourly_snapshot_matches_capture"]
    fires = S.load_fires()
    path = S.load_path()
    i = np.array([path.index(int(t) + 3540) for t in fires["bar_ts"]])
    rel = np.abs(path.close[i] - fires["entry"].to_numpy()) / fires["entry"].to_numpy()
    out["Q2"] = {"fires": int(len(fires)), "max_rel_diff": float(np.nanmax(rel)), "pass": bool(np.nanmax(rel) <= 1e-9)}
    m = S.sleeve_math()
    hourly = S.load_hourly_flush_bars()
    pos = {int(t): k for k, t in enumerate(hourly["ts"])}
    worst, kinds_equal = 0.0, 0
    for f in fires.itertuples(index=False):
        k = pos[int(f.bar_ts)]
        r = m.replay_bracket(hourly["high"], hourly["low"], hourly["close"], entry_idx=k, tif_bars=48, cost_bp=18.0)
        worst = max(worst, abs(r["r_outcome"] - float(f.r_outcome)))
        kinds_equal += r["exit_kind"] == f.exit_kind
    out["Q3"] = {"max_abs_diff_R": worst, "exit_kind_equal": int(kinds_equal), "pass": worst <= 1e-9 and kinds_equal == len(fires)}
    walks = S.walk_all(path, fires, hourly["flush_bar_ts"])
    gaps = np.diff(path.funding_s[(path.funding_s >= fires["entry_ts"].min() - 9 * 3600)
                                 & (path.funding_s <= walks["exit_s"].max() + 9 * 3600)])
    out["Q4"] = {"max_settlement_gap_s": int(gaps.max()), "missing_minutes_on_paths": int(walks["missing_minutes"].sum()),
                 "pass": int(gaps.max()) <= 8 * 3600 + 300}
    r = subprocess.run([sys.executable, "-m", "pytest", str(HERE / "tests" / "test_sqb_walk.py"), "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True)
    out["Q5"] = {"summary": r.stdout.strip().splitlines()[-1], "pass": r.returncode == 0}
    cut = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    n_cut = path.index(cut)
    short = S.Path1m(path.t0_ms, path.open[:n_cut], path.high[:n_cut], path.low[:n_cut], path.close[:n_cut],
                     path.funding_s[path.funding_s < cut], path.funding_rate[path.funding_s < cut])
    early = fires[fires["entry_ts"] < cut]
    a = S.walk_all(path, early, hourly["flush_bar_ts"])
    b = S.walk_all(short, early, hourly["flush_bar_ts"][hourly["flush_bar_ts"] + 3600 <= cut])
    done = (a["exit_s"] < cut).to_numpy()
    cols = ["kind", "exit_s", "exit_price", "net_R"]
    diffs = int((a.loc[done, cols].to_numpy() != b.loc[done, cols].to_numpy()).any(axis=1).sum())
    out["Q6"] = {"cut_utc": "2024-01-01T00:00:00+00:00", "exits_compared": int(done.sum()), "differences": diffs, "pass": diffs == 0}
    out["pass"] = all(out[q]["pass"] for q in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6"))
    S.write_json(S.RESULTS / "preconditions.json", out)
    return out


def compute_report(path: S.Path1m, fires: pd.DataFrame, flush_bar_ts: np.ndarray) -> tuple[pd.DataFrame, dict]:
    walks = S.walk_all(path, fires, flush_bar_ts)
    net = walks.pivot_table(index="bar_ts", columns="arm", values="net_R", sort=False).loc[fires["bar_ts"]]
    axis = S.day_axis(fires)
    idx = S.block_indices(len(axis))
    days = fires["entry_day"].reset_index(drop=True)
    by_arm = {}
    for arm, g in walks.groupby("arm", sort=False):
        by_arm[arm] = {"mean_net_R": float(g["net_R"].mean()), "mean_R_no_funding": float(g["R_price"].mean()),
                       "mean_funding_R": float(g["funding_R"].mean()), "win_rate": float((g["net_R"] > 0).mean()),
                       "median_hours_held": float(g["hours_held"].median()), "worst_R": float(g["net_R"].min()),
                       "exit_mix": g["kind"].value_counts().to_dict()}
    comparisons = {}
    for a in S.ARMS:
        if a.id in ("S0", "N0"):
            continue
        base = "S0" if a.family == "incumbent" else "N0"
        comparisons[f"{a.id}_minus_{base}"] = S.paired(days, (net[a.id] - net[base]).to_numpy(), axis, idx)
    comparisons["N0_minus_S0"] = S.paired(days, (net["N0"] - net["S0"]).to_numpy(), axis, idx)
    s0 = walks[walks["arm"] == "S0"].set_index("bar_ts")
    held = walks[walks["arm"] == "S_NOTIME"].set_index("bar_ts").loc[s0.index[s0["kind"] == "time"]]
    ex = S.excursions(path, fires)
    report = {
        "fires": int(len(fires)), "by_arm": by_arm, "paired": comparisons,
        "s0_time_stopped_held_without_time_stop": {
            "fires": int(len(held)), "S0_mean_net_R": float(s0.loc[held.index, "net_R"].mean()),
            "S_NOTIME_mean_net_R": float(held["net_R"].mean()), "S_NOTIME_exit_mix": held["kind"].value_counts().to_dict(),
            "median_hours_to_resolution": float(held["hours_held"].median())},
        "excursions": ex["summary"], "forward_moves_pct": S.forward_moves(path, fires, axis, idx),
        "single_open_sequence": {a.id: S.single_open_sequence(path, fires, walks, a.id) for a in S.ARMS},
    }
    return walks, report


def freeze0() -> Path:
    pre = json.loads((S.RESULTS / "preconditions.json").read_text())
    if not pre["pass"]:
        raise SystemExit("preconditions did not pass; nothing is frozen")
    target = S.RESULTS / "freeze_F0.json"
    if target.exists():
        raise SystemExit("freeze_F0.json exists; a freeze is never overwritten")
    files = [HERE / "PREREGISTRATION_SQUEEZE_BULL.md", HERE / "sqb_lib.py", HERE / "sqb_run.py", HERE / "tests" / "test_sqb_walk.py",
             S.RESULTS / "hourly_snapshot.json", S.RESULTS / "preconditions.json",
             S.ROOT / "bots" / "squeeze_bull" / "strategy" / "math.py", S.ROOT / "bots" / "squeeze_bull" / "strategy" / "config.py"]
    S.write_json(target, {"freeze": "F0", "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "note": "Report-only. Before any arm outcome on the perp path. Q6 walked every arm on pre-2024 fires "
                                  "in memory for an equality check only; no statistic was computed or shown.",
                          "inputs_sha256": input_hashes(),
                          "files": {f.resolve().relative_to(S.ROOT).as_posix(): S.sha256(f) for f in files}})
    return target


def outcomes() -> dict:
    f0 = json.loads((S.RESULTS / "freeze_F0.json").read_text())
    changed = [f for f, h in f0["files"].items() if S.sha256(S.ROOT / f) != h]
    if changed or input_hashes() != f0["inputs_sha256"]:
        raise SystemExit(f"changed since F0: {changed or 'inputs'}")
    if (S.RESULTS / "report.json").exists():
        raise SystemExit("report.json exists; the outcome run already happened")
    fires = S.load_fires()
    walks, report = compute_report(S.load_path(), fires, S.load_hourly_flush_bars()["flush_bar_ts"])
    walks.to_csv(S.RESULTS / "walks.csv.gz", index=False)
    S.write_json(S.RESULTS / "report.json", {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                            "f0_created_utc": f0["created_utc"], "report_only": True, **report,
                                            "walks_sha256": S.sha256(S.RESULTS / "walks.csv.gz")})
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["checks", "freeze0", "outcomes"])
    stage = ap.parse_args().stage
    res = {"checks": checks, "freeze0": freeze0, "outcomes": outcomes}[stage]()
    print(json.dumps(res if isinstance(res, dict) else str(res), indent=1, default=str)[:3000])
