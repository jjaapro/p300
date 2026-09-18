"""Assemble and execute the review notebook (short_squeeze_eth.ipynb).

    C:/Python/Python313/python.exe studies/notebooks/short_squeeze_eth_2026_09/build_notebook.py

System Python holds nbformat and nbclient; the cells run in the repo venv through a throwaway kernelspec. The
notebook recomputes the ETH statistics from the saved trades file and checks them against the report; it is a check
on the run, not a second run.
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
# SHORT_SQUEEZE on ETH (2026-09)

**Question (roadmap §3 research item 1, second assets):** does the shipped SHORT_SQUEEZE rule — the sweep of the
prior 24-bar low with perp-CVD selling and spot-vs-perp divergence on a short-macro day, London/NY, long only —
earn on ETH at the measured 10 bp round trip, and does the twin-table builder reproduce the BTC run first?

This notebook checks the frozen run: it reloads `results/report.json` and `results/trades_*.csv`, recomputes the
ETH statistics from the trades and asserts they match the report. Design: [README.md](README.md) (frozen,
`results/freeze_F0.json`). Verdict and reading: [findings.md](findings.md).
"""),
    ("code", """
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path.cwd(); sys.path.insert(0, str(HERE))
import ss_eth_lib as L
R = HERE / "results"
report = json.loads((R / "report.json").read_text())
f0 = json.loads((R / "freeze_F0.json").read_text())
print("frozen", f0["created_utc"], "| outcome", report["created_utc"])
print("P0 (engine == port, anchor):", {k: v for k, v in report["P0"].items() if k != "anchor_expected"})
print("P1 (builder fidelity on BTC):", {k: v for k, v in report["P1"].items() if k not in ("only_a", "only_b")})
print("DECISION:", report["decision"])
"""),
    ("md", """
## 1. The three runs side by side

`BTC_prod`: the engine on prod's tables (the anchor). `BTC_panel`: the same engine on tables built from the on-disk
panels — the fidelity run. `ETH_panel`: the same builder and engine on ETH. Gross is 0 bp per leg; net charges the
10 bp round trip against each trade's own risk distance.
"""),
    ("code", """
rows = {}
for label in ("BTC_prod", "BTC_panel", "ETH"):
    rec = report[label]
    for form in ("gross", "net"):
        d = rec[form]
        if not d.get("n"):
            rows[f"{label} {form}"] = {"n": 0}; continue
        rows[f"{label} {form}"] = {"n": d["n"], "mean R": round(d["mean_R"], 3), "win": round(d["win_rate"], 3),
                                   "PF": None if d["pf"] is None else round(d["pf"], 2),
                                   "halves": f"{d['first_half_mean_R']:+.2f} / {d['second_half_mean_R']:+.2f}",
                                   "annual R": round(d["annual_R"], 1), "max DD R": round(d["max_dd_R"], 1),
                                   "MAR": None if d["mar"] is None else round(d["mar"], 2),
                                   "DSR": None if d.get("dsr") is None else round(d["dsr"], 3),
                                   "median risk %": round(d["median_risk_pct"], 2), "exits": d["exit_mix"],
                                   "span": rec["frame_span"][0][:10] + " → " + rec["frame_span"][1][:10]}
    rows[f"{label} net"]["CI90 mean"] = [round(x, 3) for x in rec["boot_net"]["ci90"]] if rec["boot_net"]["ci90"][0] is not None else None
    rows[f"{label} net"]["cost share of gross"] = None if rec["cost_share_of_gross"] is None else round(rec["cost_share_of_gross"], 2)
pd.DataFrame(rows).T
"""),
    ("code", """
eth = pd.read_csv(R / "trades_ETH_panel.csv")
mine = L.describe(L.with_net(eth[["trigger_ts", "entry", "stop", "target", "exit_price", "exit_reason", "pnl_R", "risk_pct"]]), "net_R")
saved = report["ETH"]["net"]
if saved.get("n"):
    assert mine["n"] == saved["n"] and abs(mine["mean_R"] - saved["mean_R"]) < 1e-9 and abs(mine["max_dd_R"] - saved["max_dd_R"]) < 1e-9
    print("recomputed the ETH net statistics from trades_ETH_panel.csv and matched the report")
print("ETH per year (net):", json.dumps(saved.get("per_year", {}), indent=1))
"""),
    ("md", """
## 2. The ETH equity path against BTC's
"""),
    ("code", """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(11, 4))
for label, color in (("BTC_prod", "tab:gray"), ("BTC_panel", "tab:blue"), ("ETH_panel", "tab:orange")):
    t = pd.read_csv(R / f"trades_{label}.csv")
    if len(t):
        t["trigger_ts"] = pd.to_datetime(t["trigger_ts"], utc=True)
        ax.plot(t["trigger_ts"], t["net_R"].cumsum(), lw=1.2, label=f"{label} ({len(t)} trades)", color=color)
ax.set_ylabel("cumulative net R (10 bp round trip)"); ax.legend(); ax.grid(alpha=0.3)
ax.set_title("SHORT_SQUEEZE: the same engine on BTC (prod tables, panel tables) and on ETH")
plt.show()
"""),
    ("code", """
print(json.dumps(report["decision"], indent=1))
"""),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="sseth_kernel_")
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
    out = HERE / "short_squeeze_eth.ipynb"
    nbformat.write(nb, out)
    print("wrote", out)


if __name__ == "__main__":
    main()
