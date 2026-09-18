"""Assemble and execute the phase F review notebook (05_spot_perp_information.ipynb).

    C:/Python/Python313/python.exe studies/notebooks/exit_policy_2026_09/build_notebook_spotperp.py

System Python holds nbformat and nbclient; the cells run in the repo venv through a throwaway kernelspec, exactly as
`build_notebook.py` does for notebooks 01 to 04. The notebook recomputes every decision line from the saved
`events.csv.gz` and checks it against `report.json`, so it is a check on the run and not a second run.
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
# Exit-policy study, phase F: spot-led versus perp-led extremes

**Question (BACKLOG research item 2, row F of the exhaustion brainstorm):** does a new high carried by perpetual takers
and a rising premium, while Binance spot takers lag, tell us anything about what the rest of the trade is worth?

This notebook is a check on the frozen run, not a second run. It reloads `results/spot_perp/events.csv.gz` and
recomputes each decision line's mean, interval and halves with the same bootstrap, then asserts they match
`report.json`. Design: [PREREGISTRATION_SPOT_PERP.md](PREREGISTRATION_SPOT_PERP.md) v1.1 plus amendment A26. Verdict:
[findings_spot_perp.md](findings_spot_perp.md).
"""),
    ("code", """
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path.cwd(); sys.path.insert(0, str(HERE))
import micro_lib as M, spotperp_lib as L
R = HERE / "results" / "spot_perp"
report = json.loads((R / "report.json").read_text())
verdict = json.loads((R / "verdict.json").read_text())
f0 = json.loads((R / "freeze_F0.json").read_text())
pre = json.loads((R / "preconditions.json").read_text())
events = pd.read_csv(R / "events.csv.gz")
trades = pd.read_csv(R / "trades.csv.gz")   # the bootstrap day axis spans the whole population, as the run did
print("frozen", f0["created_utc"], "| outcome", report["created_utc"])
print("family:", report["family"])
print("verdict:", verdict["verdict"])
print("labels:", json.dumps(verdict["classifications"], indent=1))
"""),
    ("md", """
## 1. The preconditions, and what the family was fixed on

Eleven checks, all passing. Two are worth seeing: the new premium panel reproduces the top-anatomy stage's cache
exactly on their shared minutes, and the walker reproduces stage 1's saved walks on all 585 trades.
"""),
    ("code", """
print("preconditions:", {k: pre[k]["pass"] for k in pre if k.startswith("P")})
ov = pre["P7"]["panels"]["BTCUSDT_premium_1m_panel"]["premium_overlap"]
print("\\npremium panel vs the anatomy cache:", ov["minutes"], "minutes,",
      "identical" if ov["identical"] else "DIFFERENT", "| differing:", ov["differing_minutes"])
print("walker vs stage 1:", pre["P4"]["trades"], "trades, differences:", pre["P4"]["differences"])
rows = []
for pop in ("chento_BTC", "squeeze_bull", "chento_ETH", "short_squeeze"):
    for kind in ("F1_against", "F2_perp_led"):
        e = pre["P11"]["per_subpop"][pop][kind]
        rows.append({"population": pop, "kind": kind, "eligible": e["eligible_trades"],
                     "event trades": e["trades_with_event"],
                     "median elapsed (h)": round(e.get("median_elapsed_hours", float("nan")), 1),
                     "rung1": e.get("included_rung1"), "rung2": e.get("included_rung2"),
                     "rung3": e.get("included_rung3"), "decision rung": e["decision_rung"]})
pd.DataFrame(rows)
"""),
    ("md", """
## 2. The decision lines, recomputed from the saved events

`Δ` is the continuation value at the first qualifying minute minus the matched placebo, in each strategy's own R. The
assertion below is the point of this notebook: the numbers in `report.json` are reproduced from the saved per-event
rows.
"""),
    ("code", """
def recompute(subpop, kind):
    sub = events[(events["subpop"] == subpop) & (events["kind"] == kind)]
    sub = sub[np.isfinite(sub["placebo"])].sort_values("entry_ts", kind="mergesort")
    axis = M.day_axis(trades[trades["subpop"] == subpop]["entry_day"])   # same axis the outcome run used
    idx = M.block_indices(len(axis))
    return M.summarize(sub, "delta", axis, idx)

checked = []
for key in ["chento_BTC:F1_against", "squeeze_bull:F1_against", "chento_ETH:F1_against"]:
    pop, kind = key.split(":")
    t = report["tests"][key]; saved = t["lines"][t["decision_rung"]]
    mine = recompute(pop, kind)
    assert mine["n"] == saved["n"], (key, mine["n"], saved["n"])
    assert abs(mine["mean"] - saved["mean"]) < 1e-9, key
    assert max(abs(a - b) for a, b in zip(mine["ci95"], saved["ci95"])) < 1e-9, key
    checked.append({"test": key, "n": saved["n"], "Δ": round(saved["mean"], 3),
                    "95% low": round(saved["ci95"][0], 3), "95% high": round(saved["ci95"][1], 3),
                    "first half": round(saved["first_half"], 2), "second half": round(saved["second_half"], 2),
                    "holding after": round(saved["mean_cv_at_event"], 3),
                    "matched highs": round(saved["mean_placebo"], 3),
                    "Holm p": None if t.get("holm_p") is None else round(t["holm_p"], 3),
                    "label": t["classification"]})
print("recomputed from events.csv.gz and matched report.json exactly")
pd.DataFrame(checked)
"""),
    ("md", """
The Holm p values test whether `Δ` is **negative**, which is what an exit needs. Neither family test is close. On
chento BTC the interval lies above zero instead: holding after a perpetual-led high beat holding at matched highs by
about 1.07 R.

## 3. Every placebo, and the leg that separates

If the result were an artefact of one matching rule it would move when the rule changes. It does not.
"""),
    ("code", """
def lines_of(key):
    t = report["tests"][key]; rung = t["decision_rung"]
    order = ["rung1", "rung2", "rung3", "S1", "S2", "S3", "S4", "S5", f"rvol_{rung}", f"rsession_{rung}",
             f"shared_{rung}"]
    return {n.replace(f"_{rung}", ""): (round(t["lines"][n]["mean"], 3) if t["lines"].get(n, {}).get("n") else None)
            for n in order if n in t["lines"]}
pd.DataFrame({k: lines_of(k) for k in ["chento_BTC:F1_against", "squeeze_bull:F1_against", "chento_ETH:F1_against"]})
"""),
    ("code", """
dec = []
for pop in ("chento_BTC", "squeeze_bull"):
    for kind in ("F1_against", "F1_spot_confirmed", "F1_flow_only", "F1_flow_only_ctrl", "F1_prem_only",
                 "F1_prem_only_ctrl", "F1_mirror", "F1_oi_up", "F1_oi_down", "F1_against_usdt_fdusd",
                 "F2_perp_extreme", "F3"):
        t = report["tests"].get(f"{pop}:{kind}")
        line = t["lines"].get(t["decision_rung"] or "rung1", {}) if t else {}
        if line.get("n"):
            dec.append({"population": pop, "kind": kind, "n": line["n"], "Δ": round(line["mean"], 3),
                        "95% low": round(line["ci95"][0], 3), "95% high": round(line["ci95"][1], 3)})
pd.DataFrame(dec)
"""),
    ("md", """
The venue leg is the one that separates, and it separates the wrong way for an exit: on chento BTC, highs where spot
lagged ran on (`F1_flow_only` +1.05) while highs where spot confirmed did not (`F1_flow_only_ctrl` −1.06). The premium
leg alone carries nothing. The site-derived parameterisation (`F3`) and the USDT-plus-FDUSD composition line point the
same way, and squeeze_bull shows none of it.

## 4. The two tables computed after the verdict

The target-minute channel was the main structural worry: the event sits at a new running extreme, which is the kind of
minute that fills a target, so a trade whose first such minute is its exit minute would be dropped into the control
pool. It does not happen.
"""),
    ("code", """
ex = json.loads((R / "exploratory_reported_tables.json").read_text())
ch = {p: {"pattern at the exit minute": v["pattern_holds_at_exit_minute"]["F1_against"],
          "and it was the trade's first": v["first_pattern_minute_is_exit"]["F1_against"]}
      for p, v in ex["target_minute_channel"].items()}
display(pd.DataFrame(ch).T)
bal = {k: {"volume ratio (event/control)": f'{v["median_vr"]["event"]:.2f} / {v["median_vr"]["control"]:.2f}',
           "ordinal (event/control)": f'{v["ordinal_of_extreme"]["event"][1]:.0f} / {v["ordinal_of_extreme"]["control"][1]:.0f}',
           "weekend (event/control)": f'{v["weekend_share"]["event"]:.2f} / {v["weekend_share"]["control"]:.2f}'}
       for k, v in ex["covariate_balance"].items()}
pd.DataFrame(bal).T
"""),
    ("md", """
## 5. Amendment A26: the alignment check, corrected after it failed

The precondition originally correlated the premium **level** against the perpetual-over-spot **level**. Two
autocorrelated level series correlate about 0.92 at every lag from −3 to +3, so the winning lag is noise. The cell
below shows that directly, beside the returns half of the same check, which is decisive.
"""),
    ("code", """
lags = pre["P5"]["lags"]
show = {}
for key in ["BTC:2020:returns", "BTC:2020:premium_levels_reported", "BTC:2020:premium", "BTC:2024:premium"]:
    if key in lags:
        show[key] = {f"lag {k}": (None if v is None else round(v, 3)) for k, v in lags[key]["table"].items()}
        show[key]["peak"] = lags[key]["peak_lag"]; show[key]["decided"] = lags[key]["decided"]
display(pd.DataFrame(show).T)
off = {k: v["peak_lag"] for k, v in lags.items() if v["decided"] and v["peak_lag"] != 0}
print("decided lines peaking off lag 0:", off or "none")
print("2020 is reported, never decided: the earliest minute any population reads is 2021-04-16 23:15 UTC")
"""),
    ("md", """
## 6. Verdict

**NONE PROMOTED.** Neither family test is INFORMATIVE, so no stage 2 exit arm may be written on these events, and
nothing in production changes. Item F is closed as an exit signal.

What survives is a hypothesis pointing the other way and only on chento: highs where Binance spot takers lag ran on,
while highs where spot confirmed did not. That is an entry-side question, it is not protected by the family
correction, it is absent on squeeze_bull, and it would need its own pre-registration.
"""),
    ("code", """
print(json.dumps(verdict, indent=1))
"""),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="spotperp_kernel_")
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
    out = HERE / "05_spot_perp_information.ipynb"
    nbformat.write(nb, out)
    print("wrote", out)


if __name__ == "__main__":
    main()
