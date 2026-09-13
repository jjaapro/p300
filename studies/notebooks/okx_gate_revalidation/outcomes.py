"""§9 step 5 — preconditions, then outcomes and the verdict (§4.4 as fixed by Addendum 1).

Phase A (no outcome exists): P0 (parity.json), P1 (pool_parity.json), P2 (pooled fidelity),
POWER (from gate membership) and P3 (the §2.4 walker against the closed live ledger trades,
A1). Writes results/preconditions.json; on a failed check writes results/invalid.json and
stops. `--preconditions-only` stops here in every case — it cannot reach phase B.

Phase B (A2: the study run is the first phase B at the run commit): walks every OFF-arm trade,
evaluates INVALID -> KEEP -> RETIRE -> INCONCLUSIVE, computes every §4.5 report item in memory,
then writes trades_{asset}.csv, report_pre.json, n_trials.json and verdict.json LAST by atomic
rename (A4). Any exception, non-finite R, degenerate R2 arm, degenerate gate_metrics or
non-finite bootstrap quantile before that rename is INVALID: invalid.json holds check names or
an exception type and code locations only, and nothing but "INVALID" is printed.

Refusals (a missing artefact, a hash mismatch, a dirty run commit) write nothing and are not
INVALID (A3).

  python studies/notebooks/okx_gate_revalidation/outcomes.py --preconditions-only
  python studies/notebooks/okx_gate_revalidation/outcomes.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402

OUTCOME_FILES = ("trades_BTC.csv", "trades_ETH.csv", "report_pre.json", "n_trials.json", "verdict.json")
PHASE_B_MARKER = "phase_b_started.json"
MEMBERSHIP_COLS = ("atr_drop", "in_off", "in_on_R1", "in_on_R2")
STUDY_PATHS = ("studies/notebooks/okx_gate_revalidation", "tests/test_okx_gate_revalidation.py")


# ─── Inputs ────────────────────────────────────────────────────────────────────

def load_inputs(res: Path, snapshot_sha: str) -> dict:
    """Step 2-4 artefacts, refused (A3) if missing, stale or made from other code or data."""
    import pandas as pd
    names = ["parity.json", "pool_parity.json"] + [f"fidelity_{a}.json" for a in L.ASSETS] \
        + [f"features_{a}.csv" for a in L.ASSETS] + [f"pool_{a}.csv" for a in L.ASSETS]
    for n in names:
        L.require((res / n).exists(), f"results/{n} missing (run steps 2-4)")
    parity = L.read_json(res / "parity.json")
    pool_parity = L.read_json(res / "pool_parity.json")
    L.check_provenance(parity.get("provenance"), snapshot_sha, HERE / "parity_check.py", "parity.json")
    L.check_provenance(pool_parity.get("provenance"), snapshot_sha, HERE / "gen_pool.py", "pool_parity.json")
    feats = {}
    fid = {}
    for a in L.ASSETS:
        L.require(pool_parity["outputs_sha256"][f"pool_{a}.csv"] == L.file_sha256(res / f"pool_{a}.csv"),
                  f"pool_{a}.csv differs from the one gen_pool.py recorded")
        fid[a] = L.read_json(res / f"fidelity_{a}.json")
        L.check_provenance(fid[a].get("provenance"), snapshot_sha, HERE / "bot_features.py",
                           f"fidelity_{a}.json", {f"pool_{a}.csv": res / f"pool_{a}.csv"})
        L.require(fid[a]["outputs_sha256"][f"features_{a}.csv"] == L.file_sha256(res / f"features_{a}.csv"),
                  f"features_{a}.csv differs from the one bot_features.py recorded")
        f = pd.read_csv(res / f"features_{a}.csv", float_precision="round_trip")
        f["t"] = [L.parse_ts(x) for x in f["t"]]
        pool = pd.read_csv(res / f"pool_{a}.csv")
        L.require(list(zip(f["t"], f["direction"])) == list(zip([L.parse_ts(x) for x in pool["t"]], pool["direction"])),
                  f"features_{a}.csv rows differ from pool_{a}.csv")
        stored = {c: f[c].astype(bool).to_numpy() for c in MEMBERSHIP_COLS}
        f = L.membership(f.drop(columns=list(MEMBERSHIP_COLS)))
        for c in MEMBERSHIP_COLS:
            L.require(bool((f[c].astype(bool).to_numpy() == stored[c]).all()),
                      f"features_{a}.csv membership differs from its recomputation")
        feats[a] = f
    return {"parity": parity, "pool_parity": pool_parity, "features": feats, "fidelity": fid}


def phase_a(inputs: dict, snapshot: Path) -> dict:
    feats, fid = inputs["features"], inputs["fidelity"]
    counts = L.power_counts(feats)
    for a in L.ASSETS:
        L.require(fid[a]["power_R1"] == counts[a], f"fidelity_{a}.json POWER counts differ from features")
        L.require(fid[a]["p2_denominator"] == len(feats[a]) and fid[a]["p2_numerator"] == int(feats[a]["anchor"].sum()),
                  f"fidelity_{a}.json P2 counts differ from features")
    fidelity = L.pooled_fidelity(fid)

    con = L.ro_connect(snapshot)
    try:
        bars = {a: L.load_bars(con, a) for a in L.ASSETS}
        ledger = con.execute("SELECT id, strategy_variant, direction, actual_exit_time, exit_price, notes "
                             "FROM p3_ledger ORDER BY id").fetchall()
    finally:
        con.close()
    # NaN prices on any OFF trade's path would make the walker miss a stop silently (or give a
    # NaN TIF R). The frozen text does not say what to do with them, so they are a refusal to be
    # settled by addendum, never a silent outcome.
    nonfinite = {a: sum(L.path_nonfinite_prices(bars[a], int(t))
                        for t in feats[a].loc[feats[a]["in_off"].astype(bool), "t"]) for a in L.ASSETS}
    L.require(sum(nonfinite.values()) == 0, "non-finite prices on an OFF-arm walk path — needs an addendum")
    p3 = []
    for rid, variant, direction, act_exit, exit_px, notes in ledger:
        asset = "ETH" if variant == "bot_chento_v3_eth" else "BTC"
        p3.append(L.p3_check_trade(bars[asset], {"id": rid, "direction": direction, "notes": notes,
                                                 "actual_exit_time": act_exit, "exit_price": exit_px}))
    pre = {
        "P0": bool(inputs["parity"]["pass"]),
        "P1": bool(inputs["pool_parity"]["pass"]),
        "P2": bool(fidelity >= L.P2_MIN),
        "POWER": bool(L.power_passes(counts)),
        "P3": bool(sorted(r["id"] for r in p3) == sorted(L.P3_IDS) and all(r["pass"] for r in p3)),
        "diagnostics": {"pooled_fidelity": fidelity, "power_counts_R1": counts, "p3_trades": p3,
                        "p3_ids_expected": list(L.P3_IDS), "p2_min": L.P2_MIN, "p3_tolerance_s": L.P3_TOL_S,
                        "nonfinite_prices_on_off_paths": nonfinite},
    }
    return pre, bars


# ─── Phase B (pure given inputs; the shadow run and the tests call this) ───────

def walk_off_arm(feats: dict, bars: dict):
    import pandas as pd
    rows = []
    for a in L.ASSETS:
        f = feats[a][feats[a]["in_off"].astype(bool)]
        for rec in f.itertuples(index=False):
            t, d = int(rec.t), rec.direction
            w = L.walk_trade(bars[a], t, d, float(rec.entry), float(rec.risk), L.COST_BP)
            w18 = L.walk_trade(bars[a], t, d, float(rec.entry), float(rec.risk), L.COST_BP_SECONDARY)
            rows.append({"asset": a, "t": t, "direction": d, "entry": float(rec.entry), "risk": float(rec.risk),
                         "R": w.R, "R18": w18.R, "cost_R": w.cost_R, "kind": w.kind,
                         "exit_bar_ts": w.exit_bar_ts, "exit_price": w.exit_price, "missing": w.missing,
                         "max_consecutive_missing": w.max_consecutive_missing,
                         "in_on_R1": bool(rec.in_on_R1), "in_on_R2": bool(rec.in_on_R2),
                         "z_R1": float(rec.z_R1), "z_R2": float(rec.z_R2)})
    return L.order_trades(L.add_entry_fields(pd.DataFrame(rows)))


def build_report(tr, dec: dict, bars: dict, feats: dict, fid: dict) -> dict:
    import numpy as np
    import pandas as pd
    from studies.lib.validation import benchmark, dsr_pbo
    tilt_sizes = sys.modules["studies.notebooks.overlay_study.run_overlays"].tilt_sizes

    scopes = {"pooled": tr, "BTC": tr[tr["asset"] == "BTC"], "ETH": tr[tr["asset"] == "ETH"]}

    def arms(sub, kept_col="in_on_R1", R_col="R"):
        k = sub[kept_col].astype(bool)
        return {"OFF": L.arm_stats(sub, R_col), "K": L.arm_stats(sub[k], R_col), "B": L.arm_stats(sub[~k], R_col),
                "delta_K_minus_B": L.delta_KB(sub[R_col], k)}

    def dsr(x, n):
        out = dsr_pbo.dsr_from_returns(np.asarray(x, float), n)
        return None if out is None else {k: out[k] for k in ("dsr", "dsr_z", "sr_per_obs", "sr_expected", "n")}

    def mtm_dd(sub, sizes=None):
        recs = sub.to_dict("records")
        return benchmark.compounded_max_drawdown(L.mtm_daily_returns(recs, bars, sizes))

    rep: dict = {}
    rep["counts"] = {s: {"n_OFF": int(len(x)), "n_K": int(x["in_on_R1"].sum()),
                         "n_B": int((~x["in_on_R1"].astype(bool)).sum()),
                         "nan_z_R1_in_OFF": int((~np.isfinite(x["z_R1"].astype(float))).sum()),
                         "nan_z_R2_in_OFF": int((~np.isfinite(x["z_R2"].astype(float))).sum())}
                     for s, x in scopes.items()}
    for a in L.ASSETS:
        rep["counts"][a]["atr_drops"] = int(feats[a]["atr_drop"].sum())
        rep["counts"][a]["pool"] = int(len(feats[a]))
    rep["counts"]["pooled"]["atr_drops"] = sum(rep["counts"][a]["atr_drops"] for a in L.ASSETS)
    rep["counts"]["pooled"]["pool"] = sum(rep["counts"][a]["pool"] for a in L.ASSETS)
    rep["arms_R1_10bp"] = {s: arms(x) for s, x in scopes.items()}
    rep["X1_delta"] = dec["delta"]
    rep["boot_diff"], rep["boot_B"] = dec["boot_diff"], dec["boot_B"]
    rep["ci_half_width_boot_diff"] = (dec["boot_diff"]["ci"][2] - dec["boot_diff"]["ci"][0]) / 2
    rep["daily_sharpe_by_entry_day"] = {
        s: {"OFF": L.daily_sharpe_by_entry_day(x), "K": L.daily_sharpe_by_entry_day(x[x["in_on_R1"]]),
            "B": L.daily_sharpe_by_entry_day(x[~x["in_on_R1"].astype(bool)])} for s, x in scopes.items()}
    K = tr[tr["in_on_R1"]]
    B = tr[~tr["in_on_R1"].astype(bool)]
    rep["dsr_entry_order"] = {"K_N1": dsr(K["R"], 1), "K_N27": dsr(K["R"], 27), "K_N53": dsr(K["R"], 53),
                              "OFF_N1": dsr(tr["R"], 1)}
    rep["long_short"] = {s: {d: arms(x[x["direction"] == d]) for d in ("long", "short")}
                         for s, x in scopes.items()}
    rep["exit_mix"] = {s: {arm: {k: int(v) for k, v in sub["kind"].value_counts().items()}
                           for arm, sub in (("OFF", x), ("K", x[x["in_on_R1"]]),
                                            ("B", x[~x["in_on_R1"].astype(bool)]))}
                       for s, x in scopes.items()}
    periods = {"C4_IS": tr["t"] <= L.C4_IS_END,
               "C4_OOS": (tr["t"] > L.C4_IS_END) & (tr["t"] < L.C4_OOS_END),
               "HOLDOUT": tr["t"] >= L.HOLDOUT_START}
    rep["periods"] = {p: {s: arms(x[m.loc[x.index]]) for s, x in scopes.items()} for p, m in periods.items()}
    rep["R2"] = {"arms": {s: arms(x, kept_col="in_on_R2") for s, x in scopes.items()}, "K3": dec["K3_values"]}
    rep["cost_18bp"] = {s: arms(x, R_col="R18") for s, x in scopes.items()}
    rep["cap_binding_trades"] = {s: L.cap_binding(x) for s, x in scopes.items()}
    rep["mde_bonferroni_53_power_080"] = L.mde(K["R"], B["R"])
    rep["precision"] = {a: fid[a]["precision"] for a in L.ASSETS}
    rep["walker"] = {"missing_bars": int(tr["missing"].sum()),
                     "trades_over_4_consecutive_missing": int((tr["max_consecutive_missing"] > 4).sum())}
    rep["mtm_drawdown"] = {"OFF": mtm_dd(tr), "ON_R1": mtm_dd(K),
                           "trade_close_R": {"OFF": L.arm_stats(tr)["maxdd_R_trade_close"],
                                             "ON_R1": L.arm_stats(K)["maxdd_R_trade_close"]}}
    tilt = {}
    for arm, sub_all in (("OFF", tr), ("ON_R1", K)):
        sized_parts = []
        for a in L.ASSETS:
            sub = sub_all[sub_all["asset"] == a]
            sizes = tilt_sizes(sub["R"].astype(float).to_numpy(),
                               pd.DatetimeIndex(pd.to_datetime(sub["entry_ts"], unit="s", utc=True)),
                               L.TILT_POLICY[a])
            part = sub.assign(size=sizes, R_sized=sub["R"].astype(float).to_numpy() * sizes)
            sized_parts.append(part)
            tilt.setdefault(arm, {})[a] = _tilt_stats(part, lambda p: mtm_dd(p, p["size"].to_numpy()))
        pooled = L.order_trades(pd.concat(sized_parts, ignore_index=True))
        tilt[arm]["pooled"] = _tilt_stats(pooled, lambda p: mtm_dd(p, p["size"].to_numpy()))
    rep["tilt_portfolios_approximation"] = tilt
    rep["gate_metrics"] = dec["gate_metrics"]
    return rep


def _tilt_stats(part, mtm) -> dict:
    from studies.lib.validation import metrics
    import numpy as np
    total = float(part["R_sized"].sum())
    dd = metrics.max_drawdown(part["R_sized"].astype(float).to_numpy()[L.exit_order(part)])
    return {"n": int(len(part)), "n_skipped": int((part["size"] == 0).sum()),
            "n_halved": int((part["size"] == 0.5).sum()), "total_R": total, "maxdd_R_trade_close": dd,
            "mar_like": total / dd if dd > 0 else float("inf"), "mtm_drawdown": mtm(part)}


def run_phase_b(feats: dict, bars: dict, fid: dict) -> tuple[dict, object, dict | None]:
    tr = walk_off_arm(feats, bars)
    dec = L.decide(tr)
    if dec["decision"]["outcome"] == "INVALID":
        return dec, tr, None
    return dec, tr, build_report(tr, dec, bars, feats, fid)


def verdict_record(dec: dict, run: dict) -> dict:
    gm = dec["gate_metrics"]
    return {
        **dec["decision"],
        "clauses": dec["clauses"],
        "anti_discriminating": dec["anti_discriminating"],
        "values": {
            "K1_promotion_criteria": gm["promotion"]["criteria"],
            "K1_n_folds": gm["metrics"]["n_folds"],
            "delta_K_minus_B": dec["delta"],
            "K3": dec["K3_values"],
            "R_a_boot_B_ci": dec["boot_B"]["ci"],
            "R_b_boot_diff_ci": dec["boot_diff"]["ci"],
        },
        "frozen": {"N_TRIALS": L.N_TRIALS, "cost_bp": L.COST_BP, "block": L.BLOCK, "n_iter": L.N_ITER,
                   "seed": L.SEED, "window": [L.iso(L.WINDOW_START), L.iso(L.WINDOW_END)]},
        "run": run,
    }


def write_outcomes(res: Path, tr, report: dict, verdict: dict) -> None:
    """A4 write order: outcome files first, verdict.json LAST by atomic rename."""
    from studies.lib.validation import gates
    for a in L.ASSETS:
        out = tr[tr["asset"] == a].copy()
        out["t"] = [L.iso(x) for x in out["t"]]
        out["exit_bar_ts"] = [L.iso(int(x)) for x in out["exit_bar_ts"]]
        out.to_csv(res / f"trades_{a}.csv", index=False, lineterminator="\n")
    L.write_json_atomic(res / "report_pre.json", report)
    gates.n_trials_ledger(res / "n_trials.json").add("chento_okx_gate", 1, tag="okx_gate_revalidation")
    L.write_json_atomic(res / "verdict.json", verdict)


# ─── CLI ───────────────────────────────────────────────────────────────────────

def cleanup(res: Path) -> None:
    for n in OUTCOME_FILES:
        for p in (res / n, res / (n + ".tmp")):
            if p.exists():
                p.unlink()


def record_phase_b(res: Path, run: dict, compute) -> int:
    """Marks phase B as started, then computes and records it. Any exception — a KeyboardInterrupt
    included — before verdict.json's rename leaves invalid.json (type and code locations only) and
    no outcome file. `compute()` returns (decision, trades, report)."""
    L.write_json_atomic(res / PHASE_B_MARKER, {"run": run})
    try:
        dec, tr, report = compute()
        if dec["decision"]["outcome"] == "INVALID":
            cleanup(res)
            L.write_json_atomic(res / "invalid.json", {"stage": "phase_b_checks",
                                                       "failed_checks": dec["decision"]["invalid_checks"],
                                                       "run": run})
            print("INVALID")
            return 1
        verdict = verdict_record(dec, run)
        write_outcomes(res, tr, report, verdict)
    except BaseException as e:  # noqa: BLE001 — any exception before the rename is INVALID
        try:
            cleanup(res)
            L.write_json_atomic(res / "invalid.json", {"stage": "phase_b_exception",
                                                       **L.exception_record(e), "run": run})
            print("INVALID")
        except BaseException:  # never let the original message reach stderr
            os._exit(3)
        return 1
    print(f"VERDICT: {verdict['outcome']}")
    return 0


def assert_run_commit_clean() -> None:
    """A2: phase B only at a clean commit — the study files (untracked included) and every repo
    file this process has imported (tracked modifications)."""
    root = str(L.ROOT.resolve()).lower()
    files = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if not f:
            continue
        full = str(Path(f).resolve())
        if full.lower().startswith(root + os.sep) and f"{os.sep}venv{os.sep}" not in full.lower():
            files.add(os.path.relpath(full, L.ROOT).replace(os.sep, "/"))
    L.require(L.git("status", "--porcelain", "--", *STUDY_PATHS) == "",
              "phase B runs only at a clean run commit (A2): study files modified or untracked")
    L.require(L.git("status", "--porcelain", "--untracked-files=no", "--", *sorted(files)) == "",
              "phase B runs only at a clean run commit (A2): imported code modified")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--preconditions-only", action="store_true")
    ap.add_argument("--rerun-addendum", metavar="COMMIT",
                    help="the committed repair addendum; required for every run after one INVALID")
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    res = args.results_dir
    scratch = Path(tempfile.mkdtemp(prefix="okx_outcomes_"))
    invalid_path, first_invalid = res / "invalid.json", res / "invalid_run1.json"
    try:
        meta = L.verify_snapshot(snapshot, res)
        L.require(not (res / "verdict.json").exists(), "verdict.json exists — the verdict is final")
        L.require(not (first_invalid.exists() and invalid_path.exists()),
                  "a second INVALID is recorded — the study ended with no verdict")
        after_invalid = invalid_path.exists() or first_invalid.exists()
        if after_invalid:
            L.require(bool(args.rerun_addendum), "an INVALID is recorded — the one rerun needs --rerun-addendum <commit>")
            L.git("merge-base", "--is-ancestor", args.rerun_addendum, "HEAD")   # refuses if not committed
        L.require(not any((res / n).exists() for n in OUTCOME_FILES), "stale outcome files in results/")
        if not args.preconditions_only:
            L.require(res.resolve() == L.RESULTS.resolve(), "phase B writes only to the study's own results/ (A2)")
            L.require(not (res / PHASE_B_MARKER).exists() or invalid_path.exists(),
                      "phase B already started once with no verdict or INVALID record — record it by addendum")
        run = {"phase": "A" if args.preconditions_only else "B", "head": L.git("rev-parse", "HEAD"),
               "snapshot_sha256": meta["file_sha256"]}
        if args.rerun_addendum:
            run["rerun_addendum"] = args.rerun_addendum
        L.set_process_env("BTC", scratch)
        L.install_connect_guard(allowed=[snapshot])
        L.import_sleeve(snapshot, "BTC")
        import importlib
        for name in ("studies.notebooks.overlay_study.run_overlays", "studies.lib.validation.gates",
                     "studies.lib.validation.benchmark", "studies.lib.validation.metrics",
                     "studies.lib.validation.dsr_pbo", "same_hour_report"):
            importlib.import_module(name)
        L.repoint_research(snapshot, extra=("studies.notebooks.overlay_study.run_overlays",))
        if not args.preconditions_only:
            assert_run_commit_clean()
        inputs = load_inputs(res, meta["file_sha256"])
        pre, bars = phase_a(inputs, snapshot)
        L.assert_p0d_after(snapshot, scratch)
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2

    if invalid_path.exists():                    # the one rerun starts: archive the first record
        invalid_path.replace(first_invalid)
        marker = res / PHASE_B_MARKER
        if marker.exists():
            marker.replace(res / "phase_b_started_run1.json")
    pre["run"] = run
    L.write_json_atomic(res / "preconditions.json", pre)
    failed = [k for k in ("P0", "P1", "P2", "P3", "POWER") if not pre[k]]
    if failed:
        L.write_json_atomic(invalid_path, {"stage": "preconditions", "failed_checks": failed, "run": run})
        print("INVALID")
        return 1
    if args.preconditions_only:
        print("preconditions: all pass (P0, P1, P2, P3, POWER)")
        return 0

    def compute():
        out = run_phase_b(inputs["features"], bars, inputs["fidelity"])
        L.assert_p0d_after(snapshot, scratch)
        return out

    return record_phase_b(res, run, compute)


if __name__ == "__main__":
    sys.exit(main())
