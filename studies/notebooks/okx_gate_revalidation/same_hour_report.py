"""§9 step 6 — the same-hour (C4-style) control arm, written only after verdict.json exists.

Non-decision-bearing (§4.5). Same OFF arm, same trades and the same R as step 5 (read from
results/trades_{asset}.csv, never recomputed), gated by okx_aligned(z_SH, dir, 0.0) with z_SH
from results/features_sh_{asset}.csv. Computes POWER, Δ, D, boot_diff, boot_B and K1 at grid 53
for reference, the §4.5 required-statement flag and the §8 red flag (causal gap larger than the
same-hour gap). A crash or a non-finite control quantile writes report_post_failed.json (type and
code location only): one repair by addendum, then "control not evaluable (run failure)" (A5).

  python studies/notebooks/okx_gate_revalidation/same_hour_report.py
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


def control(tr, verdict: dict) -> dict:
    """Pure: `tr` is the ordered pooled OFF arm with R, in_on_R1 and z_SH columns."""
    import numpy as np
    tr = tr.copy()
    tr["in_on_SH"] = [L.okx_pass(z, d) for z, d in zip(tr["z_SH"].astype(float), tr["direction"])]
    counts = {a: {"n_off": int((tr["asset"] == a).sum()),
                  "n_K": int(tr.loc[tr["asset"] == a, "in_on_SH"].sum()),
                  "n_B": int((~tr.loc[tr["asset"] == a, "in_on_SH"].astype(bool)).sum())} for a in L.ASSETS}
    counts["pooled"] = {k: sum(counts[a][k] for a in L.ASSETS) for k in ("n_off", "n_K", "n_B")}
    power_ok = L.power_passes(counts)
    R = tr["R"].astype(float).to_numpy()
    kept = tr["in_on_SH"].astype(bool).to_numpy()
    d_pool = L.delta_KB(R, kept)
    d_asset = {a: L.delta_KB(R[(tr["asset"] == a).to_numpy()], kept[(tr["asset"] == a).to_numpy()])
               for a in L.ASSETS}
    D = L.direction_clause(d_pool, d_asset["BTC"], d_asset["ETH"])
    boot_diff, boot_B = L.bootstraps(list(tr["entry_day"]), R, kept)
    finite = L.ci_finite(boot_diff, boot_B)
    rc = L.retire_clauses(boot_diff, boot_B) if finite else None
    gm = L.gate_metrics_for(tr, kept_col="in_on_SH")
    meets_retire = bool(finite and power_ok and rc["R_a"] and rc["R_b"] and D)
    d_pool_R1 = L.delta_KB(R, tr["in_on_R1"].astype(bool).to_numpy())
    return {
        "control_run_failure": not finite,
        "power": counts, "power_ok": bool(power_ok),
        "delta_K_minus_B": {"pooled": d_pool, **d_asset}, "D": bool(D),
        "boot_diff": boot_diff, "boot_B": boot_B,
        "R_a": None if rc is None else rc["R_a"], "R_b": None if rc is None else rc["R_b"],
        "anti_discriminating": None if rc is None else rc["anti_discriminating"],
        "meets_full_retire_clause": meets_retire,
        "K1_reference": {"pass": bool(gm["promotion"]["pass"]), "criteria": gm["promotion"]["criteria"],
                         "n_folds": gm["metrics"]["n_folds"]},
        "required_statement": None if not finite else
        L.required_statement(verdict["outcome"], bool(power_ok), meets_retire),
        "red_flag_causal_gap_exceeds_same_hour": bool(np.isfinite(d_pool_R1) and np.isfinite(d_pool)
                                                      and d_pool_R1 > d_pool),
        "delta_pooled_R1": d_pool_R1,
        "sign_agreement_R1_vs_SH_membership": float((tr["in_on_R1"].astype(bool) == tr["in_on_SH"]).mean()),
    }


def p3_tif_exit_prices(snapshot: Path) -> list[dict]:
    """Addendum A1: the P3 TIF exit price is report-only and written only after verdict.json."""
    con = L.ro_connect(snapshot)
    try:
        bars = {a: L.load_bars(con, a) for a in L.ASSETS}
        rows = con.execute("SELECT id, strategy_variant, direction, exit_price, notes FROM p3_ledger ORDER BY id").fetchall()
    finally:
        con.close()
    out = []
    for rid, variant, direction, exit_px, notes in rows:
        obj, reason = L.parse_ledger_notes(notes)
        if L.EXIT_KIND.get(reason or "") != "tif":
            continue
        asset = "ETH" if variant == "bot_chento_v3_eth" else "BTC"
        w = L.walk_trade(bars[asset], L.parse_ts(obj["bar_ts"]), direction.lower(), float(obj["_entry_price"]),
                         float(obj["_risk"]), L.COST_BP, stop_price=float(obj["_stop_price"]),
                         target_price=float(obj["_target_price"]))
        out.append({"id": rid, "walker_exit_close": w.exit_price, "ledger_exit_price": exit_px,
                    "rel_diff": (w.exit_price - exit_px) / exit_px if exit_px else None})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--repair-addendum", metavar="COMMIT",
                    help="the committed repair addendum; required for the one rerun after a control failure")
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    res = args.results_dir
    scratch = Path(tempfile.mkdtemp(prefix="okx_same_hour_"))
    failed, failed_first = res / "report_post_failed.json", res / "report_post_failed_run1.json"
    try:
        L.require((res / "verdict.json").exists(), "verdict.json missing — the control runs after the verdict")
        L.verify_snapshot(snapshot, res)
        for n in [f"trades_{a}.csv" for a in L.ASSETS] + [f"features_sh_{a}.csv" for a in L.ASSETS]:
            L.require((res / n).exists(), f"results/{n} missing")
        L.require(not (res / "report_post.json").exists(), "report_post.json exists")
        L.require(not (failed.exists() and failed_first.exists()),
                  "the control failed twice — findings.md records 'control not evaluable (run failure)'")
        if failed.exists() or failed_first.exists():
            L.require(bool(args.repair_addendum), "a control failure is recorded — the one rerun needs --repair-addendum")
            L.git("merge-base", "--is-ancestor", args.repair_addendum, "HEAD")
        L.set_process_env("BTC", scratch)
        L.install_connect_guard(allowed=[snapshot])
        L.import_sleeve(snapshot, "BTC")
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2
    if failed.exists():
        failed.replace(failed_first)

    import pandas as pd
    verdict = L.read_json(res / "verdict.json")
    before = {n: L.file_sha256(res / n) for n in ["verdict.json"] + [f"trades_{a}.csv" for a in L.ASSETS]}
    try:
        parts = []
        for a in L.ASSETS:
            t = pd.read_csv(res / f"trades_{a}.csv", float_precision="round_trip")
            sh = pd.read_csv(res / f"features_sh_{a}.csv", float_precision="round_trip")
            for f in (t, sh):
                f["t"] = [L.parse_ts(x) for x in f["t"]]
            m = t.merge(sh[["t", "direction", "z_SH"]], on=["t", "direction"], how="left", validate="1:1")
            L.require(len(m) == len(t), "same-hour z join changed the OFF arm")
            parts.append(m)
        tr = L.order_trades(L.add_entry_fields(pd.concat(parts, ignore_index=True)))
        out = control(tr, verdict)
        out["verdict_outcome"] = verdict["outcome"]
        out["p3_tif_exit_prices_report_only"] = p3_tif_exit_prices(snapshot)
        if args.repair_addendum:
            out["repair_addendum"] = args.repair_addendum
        L.assert_p0d_after(snapshot, scratch)
        after = {n: L.file_sha256(res / n) for n in before}
        L.require(before == after, "step 5 outputs changed during the control run")
        if out["control_run_failure"]:
            L.write_json_atomic(failed, {"stage": "non_finite_control_quantile"})
            print("CONTROL RUN FAILURE")
            return 1
        L.write_json_atomic(res / "report_post.json", out)
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2
    except BaseException as e:  # noqa: BLE001
        try:
            L.write_json_atomic(failed, {"stage": "exception", **L.exception_record(e)})
            print("CONTROL RUN FAILURE")
        except BaseException:
            os._exit(3)
        return 1
    print(f"control written; required statement: {out['required_statement']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
