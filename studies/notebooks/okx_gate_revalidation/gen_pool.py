"""§9 step 2 — the backward-only research trigger pool and its P1 parity check (§2.1).

Re-states the steps of overlay_study/gen_trades.py::gen (:52-80) inline, with
intersect_triggers replaced by intersect_backward as gen_trades_backonly.py:22 does. It does
NOT import gen_trades.py (its gen() writes files). Only (t, direction) leaves this script.

P1 continues the pipeline through research replay_one and the no_resist_OB filter and compares
(ts, direction, entry, stop, target) with overlay_study/results_backonly/trades_{asset}.csv for
ts <= 2026-07-20 23:59:59. Only those five columns are read from the CSV; only those five and
dist_resist_OB_R are copied out of each replay_one record, so its r_outcome and exit_kind never
survive the call.

Outputs: results/pool_{BTC,ETH}.csv (t, direction) and results/pool_parity.json.

  python studies/notebooks/okx_gate_revalidation/gen_pool.py
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import importlib  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

import okxlib as L  # noqa: E402

CJ = "studies.notebooks.chento_journal."
CSV_COLS = ["ts", "direction", "entry", "stop", "target"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot", type=Path, default=L.DEFAULT_SNAPSHOT)
    ap.add_argument("--results-dir", type=Path, default=L.RESULTS)
    ap.add_argument("--csv-dir", type=Path,
                    default=L.ROOT / "studies" / "notebooks" / "overlay_study" / "results_backonly")
    args = ap.parse_args(argv)
    snapshot = args.snapshot.resolve()
    scratch = Path(tempfile.mkdtemp(prefix="okx_gen_pool_"))
    try:
        meta = L.verify_snapshot(snapshot, args.results_dir)
        L.set_process_env(None, scratch)
        L.assert_no_sleeve_yet()
    except L.Refusal as e:
        print(f"REFUSED: {e}")
        return 2
    L.install_connect_guard(allowed=[snapshot])
    L.repoint_support_db(snapshot)

    import pandas as pd

    ma = importlib.import_module(CJ + "validation_multi_asset")
    B1 = importlib.import_module(CJ + "validation_B1_moneyflow_divergence")
    B5 = importlib.import_module(CJ + "validation_B5_lsr_extremes")
    B7 = importlib.import_module(CJ + "validation_B7_multitf_cvd")
    C5 = importlib.import_module(CJ + "validation_C5_smc_features")
    ib = importlib.import_module(CJ + "validation_group_A_tuning_backonly").intersect_backward
    L.repoint_research(snapshot)

    parity = {}
    pools = {}
    for asset in L.ASSETS:
        # gen_trades.py:52-60, intersect_triggers -> intersect_backward
        df_15m = ma.load_perp_15m(asset)
        df_b1 = B1.compute_moneyflow_signal(df_15m)
        df_b1["atr"] = B1.compute_atr(df_b1, period=14)
        b1 = B1.b1_triggers(df_b1, cvd_threshold=0.5, velocity_max=1.0)
        lsr_z = B5.compute_lsr_extremes(ma.load_lsr_asset(ma.ASSET_CONFIG[asset]["lsr_asset"]))
        b5 = B5.b5_triggers(df_15m, lsr_z)
        b7 = B7.b7_alignment_triggers(B7.compute_multitf_cvd(df_15m), z_threshold=2.0)
        triple = ib(ib(b1, b5), b7)

        # gen_trades.py:63-80 — P1 only
        df_p = C5.compute_pivots(df_15m, n=5)
        df_smc = C5.compute_smc_state(df_p, n=5)
        obs = C5.compute_order_blocks(df_smc)
        fvgs = C5.compute_fvgs(df_smc)
        df_atr = df_smc.copy()
        df_atr["atr"] = B1.compute_atr(df_atr, period=14)
        rows = []
        for _, trig in triple.iterrows():
            rec = C5.replay_one(trig, df_smc, df_atr, fvgs, obs, atr_mult=5.0, target_r=6.0,
                                tp_mode="fixed")
            if rec is not None:
                rows.append({k: rec[k] for k in ("ts", "direction", "entry", "stop", "target",
                                                 "dist_resist_OB_R")})
            del rec
        rep = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
        rep = rep[(rep["dist_resist_OB_R"] > 2.0) | rep["dist_resist_OB_R"].isna()].copy()
        rep["ts"] = [L.parse_ts(x) for x in rep["ts"]]
        research = rep[rep["ts"] <= L.P1_CUTOFF][CSV_COLS]

        csv = pd.read_csv(args.csv_dir / f"trades_{asset}.csv", usecols=CSV_COLS,
                          float_precision="round_trip")
        csv["ts"] = [L.parse_ts(x) for x in csv["ts"]]
        csv = csv[csv["ts"] <= L.P1_CUTOFF][CSV_COLS]
        parity[asset] = L.p1_compare(research, csv)

        pool = triple[["ts", "direction"]].copy()
        pool["t"] = [L.parse_ts(x) for x in pool["ts"]]
        pool = pool[(pool["t"] >= L.WINDOW_START) & (pool["t"] < L.WINDOW_END)]
        pool = pool.sort_values("t", kind="mergesort")
        L.require(not pool.duplicated(["t", "direction"]).any(), "duplicate pool trigger")
        pools[asset] = pd.DataFrame({"t": [L.iso(x) for x in pool["t"]],
                                     "direction": pool["direction"].to_numpy()})
        parity[asset]["n_pool_in_window"] = int(len(pools[asset]))
        parity[asset]["n_triple_all"] = int(len(triple))
        print(f"{asset}: triple {len(triple)}, pool in window {len(pools[asset])}, "
              f"P1 {'pass' if parity[asset]['pass'] else 'FAIL'}")

    L.assert_p0d_after(snapshot, scratch)
    L.require("bots.chento_v3.strategy" not in sys.modules, "sleeve imported in gen_pool")
    # Nothing is written until both assets and the hygiene check completed.
    args.results_dir.mkdir(parents=True, exist_ok=True)
    for asset in L.ASSETS:
        pools[asset].to_csv(args.results_dir / f"pool_{asset}.csv", index=False, lineterminator="\n")
    csvs = {f"reference_trades_{a}.csv": args.csv_dir / f"trades_{a}.csv" for a in L.ASSETS}
    L.write_json_atomic(args.results_dir / "pool_parity.json",
                        {"pass": all(parity[a]["pass"] for a in L.ASSETS), "assets": parity,
                         "p1_cutoff_utc": L.iso(L.P1_CUTOFF),
                         "provenance": L.provenance(meta["file_sha256"], Path(__file__), csvs),
                         "outputs_sha256": {f"pool_{a}.csv": L.file_sha256(args.results_dir / f"pool_{a}.csv")
                                            for a in L.ASSETS}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
