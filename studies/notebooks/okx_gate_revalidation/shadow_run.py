"""Development check before the study run: phase B, the report and the control arm on REAL
features and gate membership but SYNTHETIC prices.

Every 15m bar of both assets is replaced by a seeded random walk on the snapshot's own
timestamp grid, and each trigger's entry and risk are re-read from that walk. No real price
after any trigger is used, so no real R, split statistic or verdict exists anywhere in this
run. It exercises what an exception in phase B would otherwise discover during the one study
run (Addendum A4 makes that INVALID): dtypes and time zones, the 1,987-day axis, the real fold
and POWER counts, every §4.5 report item, JSON serialisation, the A4 write order and the
control arm. Output goes to a temporary directory that is deleted at the end; nothing is
written under results/.

  python studies/notebooks/okx_gate_revalidation/shadow_run.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import importlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402

SEED = 20260913


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    scratch = Path(tempfile.mkdtemp(prefix="okx_shadow_scratch_"))
    meta = L.verify_snapshot(snapshot, args.results_dir)
    L.set_process_env("BTC", scratch)
    L.install_connect_guard(allowed=[snapshot])
    L.import_sleeve(snapshot, "BTC")
    importlib.import_module("studies.notebooks.overlay_study.run_overlays")
    L.repoint_research(snapshot, extra=("studies.notebooks.overlay_study.run_overlays",))

    import numpy as np
    import pandas as pd
    import outcomes as O
    import same_hour_report as S

    inputs = O.load_inputs(args.results_dir, meta["file_sha256"])
    con = L.ro_connect(snapshot)
    try:
        grids = {a: np.array([r[0] for r in con.execute(
            f"SELECT timestamp FROM {L.PERP_15M[a]} ORDER BY timestamp")], dtype=np.int64) for a in L.ASSETS}
    finally:
        con.close()
    rng = np.random.default_rng(SEED)
    bars, feats = {}, {}
    for a in L.ASSETS:
        ts = grids[a]
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.003, len(ts))))
        spread = np.abs(rng.normal(0.0, 0.002, len(ts)))
        high, low = close * (1 + spread), close * (1 - spread)
        bars[a] = L.Bars(ts, high, low, close)
        atr = pd.Series(high - low).rolling(14).mean().to_numpy()
        f = inputs["features"][a].copy()
        pos = np.searchsorted(ts, f["t"].astype(np.int64).to_numpy())
        f["entry"] = close[pos]
        f["risk"] = L.ATR_STOP_MULT * atr[pos]
        feats[a] = f
    L.require(all(np.isfinite(feats[a].loc[feats[a]["in_off"], "risk"]).all() for a in L.ASSETS),
              "synthetic risk not finite")

    out_dir = Path(tempfile.mkdtemp(prefix="okx_shadow_results_"))
    try:
        dec, tr, report = O.run_phase_b(feats, bars, inputs["fidelity"])
        L.require(dec["decision"]["outcome"] != "INVALID",
                  f"shadow phase B hit INVALID checks: {dec['decision']['invalid_checks']}")
        verdict = O.verdict_record(dec, {"phase": "shadow", "synthetic_prices_seed": SEED})
        O.write_outcomes(out_dir, tr, report, verdict)
        names = sorted(p.name for p in out_dir.iterdir())
        L.require(names == sorted(O.OUTCOME_FILES), f"unexpected outcome files {names}")
        for n in ("report_pre.json", "verdict.json", "n_trials.json"):
            with open(out_dir / n, "r", encoding="utf-8") as fh:
                json.load(fh)
        parts = []
        for a in L.ASSETS:
            t = pd.read_csv(out_dir / f"trades_{a}.csv", float_precision="round_trip")
            sh = pd.read_csv(args.results_dir / f"features_sh_{a}.csv", float_precision="round_trip")
            for fr in (t, sh):
                fr["t"] = [L.parse_ts(x) for x in fr["t"]]
            parts.append(t.merge(sh[["t", "direction", "z_SH"]], on=["t", "direction"], how="left",
                                 validate="1:1"))
        tr_sh = L.order_trades(L.add_entry_fields(pd.concat(parts, ignore_index=True)))
        post = S.control(tr_sh, verdict)
        L.write_json_atomic(out_dir / "report_post.json", post)
        L.assert_p0d_after(snapshot, scratch)
        print(f"shadow run completed on synthetic prices: {len(tr)} OFF-arm trades, "
              f"{len(report)} report sections, folds {dec['gate_metrics']['metrics']['n_folds']}, "
              f"control written, all files re-read")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
