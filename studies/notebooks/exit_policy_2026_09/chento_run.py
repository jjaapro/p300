"""Chento exit-policy study stages: freeze0, then outcomes (verdict written last).

    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\chento_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\exit_policy_2026_09\\chento_run.py outcomes

`compute_outcomes` is a pure function of (math module, markets, trades, k); the stage functions only verify the
freeze, load inputs and write files, so the whole decision path can be exercised on synthetic data
(tests/test_chento_outcomes_smoke.py) before anything real is computed.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chento_lib as C  # noqa: E402

from studies.lib.validation.dsr_pbo import dsr_from_returns  # noqa: E402
from studies.lib.validation.gates import walk_forward_folds  # noqa: E402

HORIZONS_H = (6, 12, 24, 48, 72, 120, 168, 336, 720)
CAPITAL, RISK_PCT, NOTIONAL_MAX_X = 10_000.0, 2.0, 3.0
COOLDOWN_H, NO_TILT_H = 6, 48


# --- the decision path (sections 6-7) --------------------------------------------------------------

def candidate_stats(net: pd.DataFrame, trades: pd.DataFrame, idx: np.ndarray) -> dict:
    out = {}
    first_half = (trades["entry_day"] < C.HALF_SPLIT_DAY).to_numpy()
    for c in C.CANDIDATES:
        d = (net[c] - net["A0"]).to_numpy()
        sums, counts = C.day_sums(trades["entry_day"], d)
        boot = C.boot_mean(sums, counts, idx)
        point = float(d.mean())
        years = pd.Series(d).groupby(trades["entry_day"].str[:4].to_numpy()).mean()
        out[c] = {
            "mean_d": point,
            "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.5)), float(np.quantile(boot, 0.975))],
            "p_one_sided": C.p_one_sided(point, boot),
            "mean_d_BTC": float(d[(trades["asset"] == "BTC").to_numpy()].mean()),
            "mean_d_ETH": float(d[(trades["asset"] == "ETH").to_numpy()].mean()),
            "mean_d_first_half": float(d[first_half].mean()),
            "mean_d_second_half": float(d[~first_half].mean()),
            "trades_changed": int((np.abs(d) > 1e-12).sum()),
            "by_year": {k: float(v) for k, v in years.items()},
        }
    return out


def walk_forward(net: pd.DataFrame, trades: pd.DataFrame) -> dict:
    order = [a.id for a in C.ARMS]                      # tie order: A0 first, then section 4
    folds = walk_forward_folds(list(trades["entry_day"]), 730, 365, 365)
    picks, oos_d = [], []
    for f in folds:
        fit = net.iloc[f["fit_idx"]][order].mean()
        best = max(order, key=lambda a: (round(float(fit[a]), 12), -order.index(a)))
        oos = net.iloc[f["oos_idx"]]
        oos_d.extend((oos[best] - oos["A0"]).tolist())
        picks.append({"oos_start": f["oos_start"], "oos_end": f["oos_end"], "selected": best,
                      "fit_mean_net_R": {a: float(fit[a]) for a in order}, "oos_trades": len(f["oos_idx"])})
    return {"folds": picks, "n_folds": len(folds),
            "stitched_oos_mean_d": float(np.mean(oos_d)) if oos_d else float("nan"),
            "selection_counts": {a: sum(p["selected"] == a for p in picks) for a in order}}


def decide(stats: dict, wf: dict) -> dict:
    adj = C.holm_adjust({c: stats[c]["p_one_sided"] for c in C.CANDIDATES})
    clauses, passing = {}, []
    for c in C.CANDIDATES:
        s = stats[c]
        cl = {"D1_effect": s["mean_d"] >= C.EFFECT_R,
              "D2_holm": adj[c] <= C.ALPHA,
              "D3_stability": all(s[k] > 0 for k in ("mean_d_BTC", "mean_d_ETH", "mean_d_first_half", "mean_d_second_half")),
              "D4_walk_forward": (wf["stitched_oos_mean_d"] > 0) and wf["n_folds"] > 0
                                 and wf["selection_counts"].get(c, 0) >= wf["n_folds"] / 2}
        clauses[c] = {**cl, "holm_p": adj[c], "passes": all(cl.values())}
        if clauses[c]["passes"]:
            passing.append(c)
    if passing:
        best = max(stats[c]["mean_d"] for c in passing)
        near = [c for c in passing if stats[c]["mean_d"] >= best - 0.02]
        chosen = min(near, key=C.PREFERENCE.index)
        verdict = f"CHANGE_SUPPORTED({chosen})"
    elif all(stats[c]["ci95"][2] < C.EFFECT_R for c in C.CANDIDATES):
        chosen, verdict = None, "KEEP_72H"
    else:
        chosen, verdict = None, "INCONCLUSIVE"
    a1 = stats["A1"]
    if clauses["A1"]["passes"]:
        hypothesis = "supported"
    elif a1["ci95"][2] < 0:
        hypothesis = "contradicted"
    else:
        hypothesis = "not settled"
    return {"verdict": verdict, "chosen": chosen, "passing": passing, "clauses": clauses,
            "hypothesis_time_stop_harmful": hypothesis}


# --- report-only (section 9) -----------------------------------------------------------------------

def forward_profile(markets: dict, trades: pd.DataFrame, idx: np.ndarray) -> dict:
    out = {}
    for h in HORIZONS_H:
        moves, days = [], []
        for tr in trades.itertuples(index=False):
            bars = markets[tr.asset].bars
            p = bars.first_ge(int(tr.t) + h * 3600)
            if p < 0:
                continue
            sign = 1 if tr.direction == "long" else -1
            moves.append(sign * (float(bars.close[p]) - float(tr.entry)) / float(tr.risk))
            days.append(tr.entry_day)
        m = np.asarray(moves)
        sums, counts = C.day_sums(pd.Series(days), m)
        boot = C.boot_mean(sums, counts, idx)
        out[str(h)] = {"mean_R": float(m.mean()), "ci95": [float(np.nanquantile(boot, 0.025)), float(np.nanquantile(boot, 0.975))],
                       "trades": int(len(m))}
    means = {int(h): v["mean_R"] for h, v in out.items()}
    peak_h = max(means, key=means.get)
    half = next((h for h in sorted(means) if means[peak_h] > 0 and means[h] >= 0.5 * means[peak_h]), None)
    return {"by_horizon_h": out, "peak_horizon_h": peak_h, "first_horizon_at_half_peak_h": half}


def sequence(markets: dict, trades: pd.DataFrame, walks: pd.DataFrame, arm: str, asset: str) -> dict:
    w = walks[(walks["arm"] == arm) & (walks["asset"] == asset)].set_index(["t", "direction"])
    tr = trades[trades["asset"] == asset].sort_values("t", kind="mergesort")
    taken, last_entry = [], -10**12
    for row in tr.itertuples(index=False):
        now = int(row.t) + C.BAR_S
        if now - last_entry < COOLDOWN_H * 3600:
            continue
        e = w.loc[(int(row.t), row.direction)]
        done = [x for x in taken if x["close_ts"] <= now]
        if asset == "BTC" and any(x["kind"] == "stop" and x["R_price"] < 0 and now - x["close_ts"] < NO_TILT_H * 3600 for x in done):
            continue
        scale = 1.0
        if asset == "ETH" and done:
            last = max(done, key=lambda x: x["close_ts"])
            scale = 0.5 if last["R_price"] < 0 else 1.0
        risk_usd = min(CAPITAL * RISK_PCT / 100 * scale, NOTIONAL_MAX_X * CAPITAL * float(row.risk) / float(row.entry))
        close_ts = int(e["exit_bar_ts"]) + C.BAR_S
        taken.append({"t": int(row.t), "direction": row.direction, "entry": float(row.entry), "risk": float(row.risk),
                      "entry_ts": now, "close_ts": close_ts, "kind": e["kind"], "R_price": float(e["R_price"]),
                      "net_R": float(e["net_R"]), "risk_usd": risk_usd})
        last_entry = now
    if not taken:
        return {"trades": 0}
    bars = markets[asset].bars
    events = sorted([(x["entry_ts"], 1, x["risk_usd"]) for x in taken] + [(x["close_ts"], -1, -x["risk_usd"]) for x in taken],
                    key=lambda e: (e[0], e[1]))
    open_n = open_risk = max_n = max_risk = 0
    for _, dn, dr in events:
        open_n += dn
        open_risk += dr
        max_n, max_risk = max(max_n, open_n), max(max_risk, open_risk)
    day0 = min(x["entry_ts"] for x in taken) // 86400 * 86400 + 86400
    day1 = max(x["close_ts"] for x in taken) // 86400 * 86400 + 86400
    equity, peak, mdd = [], -1e18, 0.0
    for D in range(day0, day1 + 1, 86400):
        p = bars.last_le(D - C.BAR_S)
        mark = float(bars.close[p])
        realised = sum(x["net_R"] * x["risk_usd"] for x in taken if x["close_ts"] <= D)
        unreal = sum((1 if x["direction"] == "long" else -1) * (mark - x["entry"]) / x["risk"] * x["risk_usd"]
                     for x in taken if x["entry_ts"] <= D < x["close_ts"])
        v = CAPITAL + realised + unreal
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    total = sum(x["net_R"] * x["risk_usd"] for x in taken)
    return {"trades": len(taken), "total_net_R_unscaled": float(sum(x["net_R"] for x in taken)),
            "total_return_pct": total / CAPITAL * 100, "mtm_max_drawdown_pct": mdd * 100,
            "max_concurrent": max_n, "peak_open_risk_pct": max_risk / CAPITAL * 100}


def compute_outcomes(ctm, markets: dict, trades: pd.DataFrame, k: float) -> tuple[pd.DataFrame, dict, dict]:
    walks = C.walk_all(ctm, markets, trades, k, cost_bp=C.COST_BP, with_funding=True)
    walks["R18_price"] = walks["R_price"] - walks["cost_R"] * (C.COST_BP_SECONDARY / C.COST_BP - 1)
    key = ["asset", "t", "direction"]
    net = walks.pivot_table(index=key, columns="arm", values="net_R", sort=False)
    net = net.loc[list(zip(trades["asset"], trades["t"], trades["direction"]))].reset_index(drop=True)
    idx = C.block_indices()
    stats = candidate_stats(net, trades, idx)
    wf = walk_forward(net, trades)
    decision = decide(stats, wf)

    by_arm = {}
    for arm, g in walks.groupby("arm", sort=False):
        by_arm[arm] = {"mean_net_R": float(g["net_R"].mean()), "mean_R_price_no_funding": float(g["R_price"].mean()),
                       "mean_R_18bp_no_funding": float(g["R18_price"].mean()), "mean_funding_R": float(g["funding_R"].mean()),
                       "win_rate_net": float((g["net_R"] > 0).mean()), "mean_hours_held": float(g["hours_held"].mean()),
                       "median_hours_held": float(g["hours_held"].median()), "exit_mix": g["kind"].value_counts().to_dict(),
                       "mean_net_R_BTC": float(g.loc[g["asset"] == "BTC", "net_R"].mean()),
                       "mean_net_R_ETH": float(g.loc[g["asset"] == "ETH", "net_R"].mean())}
    a0 = walks[walks["arm"] == "A0"].set_index(key)
    a1 = walks[walks["arm"] == "A1"].set_index(key)
    tif = a0.index[a0["kind"] == "time"]
    held = a1.loc[tif]
    tif_follow = {"trades": int(len(tif)), "A0_mean_net_R": float(a0.loc[tif, "net_R"].mean()),
                  "A1_mean_net_R": float(held["net_R"].mean()), "A1_exit_mix": held["kind"].value_counts().to_dict(),
                  "A1_median_hours_to_resolution": float(held["hours_held"].median())}
    daily = {}
    for arm in (a.id for a in C.ARMS):
        s, _ = C.day_sums(trades["entry_day"], net[arm].to_numpy())
        daily[arm] = s
    best_arm = max(by_arm, key=lambda a: by_arm[a]["mean_net_R"])
    dsr = {arm: {str(n): dsr_from_returns(daily[arm], n, 365) for n in (21, 40)} for arm in {best_arm, "A0"}}
    seq = {arm: {asset: sequence(markets, trades, walks, arm, asset) for asset in C.ASSETS} for arm in (a.id for a in C.ARMS)}
    report = {"k": k, "trades": int(len(trades)), "by_arm": by_arm, "candidates": stats, "walk_forward": wf,
              "a0_time_stop_trades_held_under_A1": tif_follow, "forward_profile": forward_profile(markets, trades, idx),
              "sequence": seq, "best_arm_by_mean_net_R": best_arm, "dsr": dsr}
    return walks, report, decision


# --- stages -----------------------------------------------------------------------------------------

def frozen_files() -> list[Path]:
    files = [HERE / "PREREGISTRATION_CHENTO.md", HERE / "chento_lib.py", HERE / "chento_checks.py", HERE / "chento_run.py"]
    files += sorted((HERE / "tests").glob("test_*.py"))
    files += [C.RESULTS / "step0.json", C.RESULTS / "preconditions.json"]
    files += [C.ROOT / "bots" / "chento_v3" / "strategy" / "math.py", C.ROOT / "bots" / "chento_v3" / "strategy" / "config.py",
              C.OKX_DIR / "okxlib.py"]
    files += [C.ORB_CACHE / f"{s}_funding.npz" for s in ("BTCUSDT", "ETHUSDT")]
    return files


def _rel(p: Path) -> str:
    return p.resolve().relative_to(C.ROOT).as_posix()


def freeze0() -> Path:
    pre = json.loads((C.RESULTS / "preconditions.json").read_text())
    C.L.require(pre["pass"], "preconditions did not pass; nothing is frozen")
    path = C.RESULTS / "freeze_F0.json"
    C.L.require(not path.exists(), "freeze_F0.json exists; a freeze is never overwritten")
    step = json.loads((C.RESULTS / "step0.json").read_text())
    C.write_json(path, {
        "freeze": "F0", "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Before any exit-arm outcome other than the OKX study's already-disclosed A0. P7 walked every arm on "
                "pre-2023-06 trades in memory for an equality check only; no statistic was computed or shown.",
        "k": step["k"], "snapshot_sha256": C.SNAPSHOT_SHA256, "okx_inputs_sha256": C.INPUT_SHA256,
        "files": {_rel(f): C.sha256(f) for f in frozen_files()}})
    return path


def verify_f0() -> dict:
    f0 = json.loads((C.RESULTS / "freeze_F0.json").read_text())
    changed = [f for f, h in f0["files"].items() if C.sha256(C.ROOT / f) != h]
    C.L.require(not changed, f"files changed since F0: {changed}")
    return f0


def stage_outcomes() -> dict:
    f0 = verify_f0()
    C.L.require(not (C.RESULTS / "verdict.json").exists(), "verdict.json exists; the outcome run already happened")
    C.L.require(C.verify_inputs()["pass"], "inputs changed since F0")
    signal, ctm, clock = C.open_process("BTC")
    trades = C.load_trades()
    con = C.L.ro_connect(C.SNAPSHOT)
    try:
        markets = {a: C.load_market(con, a, ctm) for a in C.ASSETS}
    finally:
        con.close()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    walks, report, decision = compute_outcomes(ctm, markets, trades, float(f0["k"]))
    walks.to_csv(C.RESULTS / "walks.csv.gz", index=False)
    C.write_json(C.RESULTS / "report.json", {"started_utc": started, **report})
    C.write_json(C.RESULTS / "verdict.json", {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             "f0_created_utc": f0["created_utc"], **decision,
                                             "walks_sha256": C.sha256(C.RESULTS / "walks.csv.gz"),
                                             "report_sha256": C.sha256(C.RESULTS / "report.json")})
    return decision


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "outcomes"])
    stage = ap.parse_args().stage
    res = freeze0() if stage == "freeze0" else stage_outcomes()
    print(json.dumps(res if isinstance(res, dict) else str(res), indent=1, default=str)[:6000])
