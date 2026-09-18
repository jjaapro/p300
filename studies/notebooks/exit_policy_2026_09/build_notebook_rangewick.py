"""Assemble and execute the stage R review notebook (07_range_wick_information.ipynb).

    C:/Python/Python313/python.exe studies/notebooks/exit_policy_2026_09/build_notebook_rangewick.py

System Python holds nbformat and nbclient; the cells run in the repo venv through a throwaway kernelspec, exactly as
`build_notebook_spotperp.py` does. The notebook recomputes every decision line from the saved `events.csv.gz` and
checks it against `report.json`, so it is a check on the run and not a second run.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parents[2] / "venv" / "Scripts" / "python.exe"

CELLS = [
    ("md", """
# Exit-policy study, stage R: move vs implied range (I) and the rejection-wick exit (J)

**Question (roadmap step 4, rows I and J of the exhaustion brainstorm):** inside a trade, does the first minute at
which the favourable move exceeds one option-implied daily move, or the first 15-minute bar that rejects from a
causal level while the trade is ≥ 0.3 R in profit, say anything about what the rest of the trade is worth?

This notebook is a check on the frozen run, not a second run. It reloads `results/range_wick/events.csv.gz` and
recomputes each decision line's mean, interval and halves with the same block bootstrap, then asserts they match
`report.json`. Design: [PREREGISTRATION_RANGE_WICK.md](PREREGISTRATION_RANGE_WICK.md) v1.0 with amendment A1.
Verdict and reading: [findings_range_wick.md](findings_range_wick.md).
"""),
    ("code", """
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path.cwd(); sys.path.insert(0, str(HERE))
import micro_lib as M, rangewick_lib as L
R = HERE / "results" / "range_wick"
report = json.loads((R / "report.json").read_text())
verdict = json.loads((R / "verdict.json").read_text())
f0 = json.loads((R / "freeze_F0.json").read_text())
pre = json.loads((R / "preconditions.json").read_text())
events = pd.read_csv(R / "events.csv.gz")
trades = pd.read_csv(R / "trades.csv.gz")
print("frozen", f0["created_utc"], "| outcome", report["created_utc"])
print("family:", report["family"])
print("decision rungs:", f0["decision_rungs"])
print("verdict:", verdict["verdict"])
print("labels:", json.dumps(verdict["classifications"], indent=1))
"""),
    ("md", """
## 1. The preconditions and the family

Eight checks, all passing after amendment A1 (the squeeze_bull population is the corrected-open-interest re-cut;
the imported libraries are recorded as they stand). The family was fixed on counts alone: three tests reached 30
included event trades under a decision rung; squeeze_bull's implied-range test did not (15 events, 7 of them at
the target minute) and is descriptive.
"""),
    ("code", """
print("preconditions:", {k: pre[k]["pass"] for k in pre if k.startswith("P")})
print("populations:", pre["P2"]["counts"], "| I-eligible:", pre["P2"]["i_eligible"])
print("squeeze_bull vs stage 1:", {k: v for k, v in pre["P2"]["squeeze_bull_vs_stage1"].items()})
print("walker identity:", pre["P3"]["differences"], "compared", pre["P3"]["compared"])
print("DVOL:", {a: (v["first_day"], v["last_day"], v["rows"], v["missing_days_inside_span"]) for a, v in pre["P4"]["facts"].items()})
rows = []
for name, per in pre["P8"]["per_subpop"].items():
    for kind in ("I1_implied", "I1_realised", "I2_day", "J_wick", "J_nolevel", "J_accept", "J_shape", "J_wick_1m"):
        e = per[kind]; inc = e["included"]
        rows.append({"subpop": name, "kind": kind, "eligible": e["eligible_trades"], "events": e["trades_with_event"],
                     "median h": e.get("median_elapsed_hours"), "rung1": inc.get("rung1"), "rung2": inc.get("rung2"),
                     "rung3": inc.get("rung3"), "S1": inc.get("S1"), "decision": e["decision_rung"]})
pd.DataFrame(rows)
"""),
    ("md", """
## 2. The decision lines, recomputed from the saved events

`Δ` is the continuation value at the first event minus the placebo mean of matched control minutes (same
subpopulation, direction, ±365 days unless rung 3, same 0.25 R profit bin, fresh extreme, no prior event, status
known). Negative means the rest of the trade was worth less after the event than at matched moments. The Holm p
tests `Δ < 0`.
"""),
    ("code", """
def recompute(subpop, kind):
    sub = events[(events["subpop"] == subpop) & (events["kind"] == kind)]
    sub = sub[np.isfinite(sub["placebo"])].sort_values("entry_ts", kind="mergesort")
    axis = M.day_axis(trades[trades["subpop"] == subpop]["entry_day"])
    idx = M.block_indices(len(axis))
    return M.summarize(sub, "delta", axis, idx)

keys = ["chento_BTC:I1_implied", "chento_BTC:J_wick", "squeeze_bull:J_wick", "chento_ETH:I1_implied",
        "chento_ETH:J_wick", "squeeze_bull:I1_implied", "chento_BTC:I1_realised", "chento_BTC:J_nolevel",
        "squeeze_bull:J_nolevel", "chento_ETH:I1_realised", "chento_ETH:J_nolevel"]
checked = []
for key in keys:
    pop, kind = key.split(":")
    t = report["tests"][key]; saved = t["lines"][t["decision_rung"]]
    if not saved.get("n"):
        checked.append({"test": key, "n": 0}); continue
    mine = recompute(pop, kind)
    assert mine["n"] == saved["n"], (key, mine["n"], saved["n"])
    assert abs(mine["mean"] - saved["mean"]) < 1e-9, key
    assert max(abs(a - b) for a, b in zip(mine["ci95"], saved["ci95"])) < 1e-9, key
    checked.append({"test": key, "rung": t["decision_rung"], "n": saved["n"], "Δ": round(saved["mean"], 3),
                    "95% low": round(saved["ci95"][0], 3), "95% high": round(saved["ci95"][1], 3),
                    "p(Δ<0)": round(saved["p_one_sided_less"], 3),
                    "first half": round(saved["first_half"], 2), "second half": round(saved["second_half"], 2),
                    "holding after": round(saved["mean_cv_at_event"], 3), "matched": round(saved["mean_placebo"], 3),
                    "Holm p": None if t.get("holm_p") is None else round(t["holm_p"], 3),
                    "label": t["classification"]})
print("recomputed from events.csv.gz and matched report.json exactly")
pd.DataFrame(checked)
"""),
    ("md", """
## 3. Every placebo, the controls and the shared-pool contrast

If a result were an artefact of one matching rule it would move when the rule changes. The lines: rungs 1–3
(fresh / strict extreme / all years), S1–S2 (no freshness match), S3 (no bin), S4 (no-time-exit walk), R-vol and
R-session (the robustness constraints), and the shared pool used for the event-versus-control contrast.
"""),
    ("code", """
def lines_of(key):
    t = report["tests"][key]; rung = t["decision_rung"]
    order = ["rung1", "rung2", "rung3", "S1", "S2", "S3", "S4", f"rvol_{rung}", f"rsession_{rung}", f"shared_{rung}"]
    return {n.replace(f"_{rung}", ""): (f"{t['lines'][n]['mean']:+.3f} (n {t['lines'][n]['n']})"
                                        if t["lines"].get(n, {}).get("n") else None) for n in order if n in t["lines"]}
pd.DataFrame({k: lines_of(k) for k in ["chento_BTC:I1_implied", "chento_BTC:I1_realised", "chento_BTC:J_wick",
                                        "chento_BTC:J_nolevel", "squeeze_bull:J_wick", "squeeze_bull:J_nolevel",
                                        "chento_ETH:I1_implied", "chento_ETH:J_wick"]})
"""),
    ("code", """
diff = {k: report["tests"][k].get("shared_difference") for k in report["family"]}
pd.DataFrame({k: {"n event": v["n_a"], "n control": v["n_b"], "Δ event − Δ control": round(v["mean"], 3),
                  "95% low": round(v["ci95"][0], 3), "95% high": round(v["ci95"][1], 3)}
              for k, v in diff.items() if v and "mean" in v})
"""),
    ("md", """
## 4. The reported kinds, the ETH replication and the exit-arm overlay

The overlay is the arm the brainstorm and the Paladin study phrased: exit at the close of the first event minute,
otherwise the shipped exit, paired per trade against the shipped exit over the whole subpopulation (non-event trades
contribute zero). It decides nothing; a positive overlay with a null `Δ` is the drift of the matched placebo.
"""),
    ("code", """
rep = []
for key, t in report["tests"].items():
    pop, kind = key.split(":")
    if kind in ("I1_implied", "I1_realised", "J_wick", "J_nolevel"):
        continue
    line = t["lines"].get(t["decision_rung"], {})
    if line.get("n"):
        rep.append({"test": key, "rung": t["decision_rung"], "n": line["n"], "Δ": round(line["mean"], 3),
                    "95% low": round(line["ci95"][0], 3), "95% high": round(line["ci95"][1], 3),
                    "halves": f"{line['first_half']:+.2f} / {line['second_half']:+.2f}"})
pd.DataFrame(rep)
"""),
    ("code", """
print("ETH non-overlap subsets:", json.dumps(report["eth_non_overlap"], indent=1))
ov = report["overlay"]
pd.DataFrame({k: {"n": v["n"], "events": v["n_event"], "mean Δ R (overlay − shipped)": round(v["mean_diff_R"], 3),
                  "95% low": round(v["ci95"][0], 3), "95% high": round(v["ci95"][1], 3),
                  "shipped R": round(v["mean_shipped_R"], 3), "overlay R": round(v["mean_arm_R"], 3),
                  "event trades that stopped out": v["event_trades_shipped_stop"],
                  "event trades that hit target": v["event_trades_shipped_target"]}
              for k, v in ov.items() if v.get("n")}).T
"""),
    ("md", """
## 5. Base rates, the exit-minute channel and covariate balance

The channel: a trade whose first pattern minute is its exit minute has no event and serves as a control. For I1 on
squeeze_bull the target (+3 %) sits about one implied day above the flush, so the crossing and the target fill
coincide on 7 of 15 event candidates; that is why squeeze_bull's I1 is descriptive.
"""),
    ("code", """
print("exit-minute channel:", json.dumps({n: {k: v for k, v in c.items() if k in ("I1_implied", "J_wick")}
                                          for n, c in pre["P8"]["exit_minute_channel"].items()}, indent=1))
pd.DataFrame({n: per["_base_rates"] for n, per in pre["P8"]["per_subpop"].items()}).T
"""),
    ("code", """
bal = {}
for name, per in pre["P8"]["per_subpop"].items():
    for kind in ("I1_implied", "J_wick"):
        b = per[kind].get("balance")
        if b and b.get("n") and "event" in b:
            bal[f"{name}:{kind}"] = {"n": b["n"], "VR event": round(b["event"]["vr_median"], 2),
                                     "VR controls": round(b["controls"]["vr_median"], 2),
                                     "mark event": round(b["event"]["mark_median"], 2),
                                     "mark controls": round(b["controls"]["mark_median"], 2),
                                     "ord event q": b["event"]["ord_q"], "ord controls q": b["controls"]["ord_q"],
                                     "weekend event": round(b["event"]["weekend_share"], 2),
                                     "weekend controls": round(b["controls"]["weekend_share"], 2)}
pd.DataFrame(bal).T
"""),
    ("md", """
## 6. Verdict

Section 8's rule, applied by `rangewick_run.stage_verdict`: a kind is promoted only with an INFORMATIVE family test
and an agreeing chento-ETH line. The reading is in [findings_range_wick.md](findings_range_wick.md).
"""),
    ("code", """
print(json.dumps(verdict, indent=1))
"""),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="rangewick_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps(
        {"argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
         "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    os.environ["JUPYTER_PATH"] = tmp + os.pathsep + os.environ.get("JUPYTER_PATH", "")
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "p300venv", "display_name": "p300 venv", "language": "python"}
    for kind, text in CELLS:
        text = text.strip("\n")
        nb.cells.append(nbformat.v4.new_markdown_cell(text) if kind == "md" else nbformat.v4.new_code_cell(text))
    NotebookClient(nb, timeout=1800, kernel_name="p300venv", resources={"metadata": {"path": str(HERE)}}).execute()
    out = HERE / "07_range_wick_information.ipynb"
    nbformat.write(nb, out)
    print("wrote", out)


if __name__ == "__main__":
    main()
