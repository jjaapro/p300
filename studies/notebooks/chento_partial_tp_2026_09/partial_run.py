"""Chento partial take-profit study: freeze0, then outcomes (verdict written last).

    venv\\Scripts\\python.exe studies\\notebooks\\chento_partial_tp_2026_09\\partial_run.py freeze0
    venv\\Scripts\\python.exe studies\\notebooks\\chento_partial_tp_2026_09\\partial_run.py outcomes

freeze0 hashes README.md, the exit-policy inputs and the committed A0 reference before any ladder outcome exists.
outcomes refuses to run without a freeze, with a changed README, with changed inputs, or twice.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import partial_lib as P

C, L = P.C, P.L


def freeze0() -> dict:
    P.RESULTS.mkdir(exist_ok=True)
    path = P.RESULTS / "freeze_F0.json"
    L.require(not path.exists(), "freeze_F0.json exists; a freeze is never overwritten")
    inputs = C.verify_inputs()
    L.require(inputs["pass"], "the exit-policy inputs do not match their frozen hashes")
    f0 = {"freeze": "F0", "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "readme_sha256": C.sha256(P.HERE / "README.md"), "engine_sha256": C.sha256(P.HERE / "partial_lib.py"),
          "a0_reference_sha256": C.sha256(P.A0_REFERENCE), "inputs": inputs,
          "ladders": {k: list(map(list, v)) for k, v in P.LADDERS.items()},
          "placebo": {"draws": P.PLACEBO_DRAWS, "seed": P.PLACEBO_SEED, "range": list(P.PLACEBO_RANGE)},
          "decision": {"MAR_GAIN": P.MAR_GAIN, "MEAN_D_FLOOR": P.MEAN_D_FLOOR, "CI_LOW_FLOOR": P.CI_LOW_FLOOR},
          "note": "Before any partial-close outcome. The A0 reference is the exit-policy study's committed walks."}
    C.write_json(path, f0)
    return f0


def verify_f0() -> dict:
    f0 = json.loads((P.RESULTS / "freeze_F0.json").read_text(encoding="utf-8"))
    L.require(C.sha256(P.HERE / "README.md") == f0["readme_sha256"], "README.md changed since F0")
    L.require(C.sha256(P.HERE / "partial_lib.py") == f0["engine_sha256"], "partial_lib.py changed since F0")
    L.require(C.sha256(P.A0_REFERENCE) == f0["a0_reference_sha256"], "the A0 reference changed since F0")
    L.require(C.verify_inputs()["pass"], "inputs changed since F0")
    return f0


def stage_outcomes() -> dict:
    f0 = verify_f0()
    L.require(not (P.RESULTS / "verdict.json").exists(), "verdict.json exists; the outcome run already happened")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    signal, ctm, clock = C.open_process("BTC")
    trades = C.load_trades()
    con = L.ro_connect(C.SNAPSHOT)
    try:
        markets = {a: P.light_market(con, a) for a in C.ASSETS}
    finally:
        con.close()

    # G1: the shipped exit, reproduced and checked against the committed record.
    walks = P.a0_walks(ctm, markets, trades)
    g1 = P.check_a0_reference(walks)
    print("G1 A0 vs committed reference:", g1)
    L.require(g1["pass_"], "A0 reproduction does not match the exit-policy record")
    paths = P.build_paths(markets, trades, walks)
    a0_net = np.array([w for w in walks.set_index(["asset", "t", "direction"])
                       .loc[list(zip(trades["asset"], trades["t"], trades["direction"])), "net_R"]])

    # G2: the empty ladder is A0.
    empty = P.net_for(paths, ())
    g2 = {"max_abs_diff": float(np.abs(empty - a0_net).max()), "pass_": bool(np.abs(empty - a0_net).max() < 1e-9)}
    print("G2 empty ladder == A0:", g2)
    L.require(g2["pass_"], "the engine with no ladder does not reproduce A0")

    nets = {"A0": a0_net, **{c: P.net_for(paths, P.LADDERS[c]) for c in P.CANDIDATES}}
    seqs = {k: P.all_sequences(v, trades) for k, v in nets.items()}
    idx = C.block_indices()
    pairs = {c: P.paired(nets[c], a0_net, trades, idx) for c in P.CANDIDATES}
    fills = {c: P.fill_stats(paths, P.LADDERS[c]) for c in P.CANDIDATES}
    placebos = {c: P.placebo(paths, trades, tuple(f for _, f in P.LADDERS[c])) for c in P.CANDIDATES}
    for c in P.CANDIDATES:
        pm = seqs[c]["pooled"]["MAR_R"]
        placebos[c]["candidate_percentile"] = float(np.mean(np.array(placebos[c]["_mars"]) <= pm)) if pm is not None else None
    decision = P.decide(seqs, pairs, placebos)

    per_trade = pd.DataFrame({"asset": trades["asset"], "t": trades["t"], "direction": trades["direction"],
                              "entry_day": trades["entry_day"], "exit_kind_A0": [p.exit_kind for p in paths],
                              **{f"net_R_{k}": v for k, v in nets.items()}})
    per_trade.to_csv(P.RESULTS / "per_trade.csv.gz", index=False)
    report = {"started_utc": started, "trades": int(len(trades)), "gates": {"G1": g1, "G2": g2},
              "sequences": seqs, "paired_vs_A0": pairs, "fills": fills,
              "placebo": {c: {k: v for k, v in placebos[c].items() if not k.startswith("_")} for c in P.CANDIDATES},
              "a0_exit_kinds": {k: int(v) for k, v in walks["kind"].value_counts().items()}}
    C.write_json(P.RESULTS / "report.json", report)
    C.write_json(P.RESULTS / "placebo_draws.json", {c: placebos[c]["_mars"] for c in P.CANDIDATES})
    C.write_json(P.RESULTS / "verdict.json", {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              "f0_created_utc": f0["created_utc"], **decision,
                                              "report_sha256": C.sha256(P.RESULTS / "report.json")})
    return decision


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["freeze0", "outcomes"])
    stage = ap.parse_args().stage
    res = freeze0() if stage == "freeze0" else stage_outcomes()
    print(json.dumps(res, indent=1, default=str)[:4000])
