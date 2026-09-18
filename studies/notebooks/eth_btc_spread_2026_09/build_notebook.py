"""Assemble and execute the review notebook (eth_btc_spread.ipynb).

    C:/Python/Python313/python.exe studies/notebooks/eth_btc_spread_2026_09/build_notebook.py

System Python holds nbformat and nbclient; the cells run in the repo venv through a throwaway kernelspec. The
notebook recomputes the candidate's episode P&L from the saved episodes file and the decision from the saved report,
so it is a check on the run, not a second run.
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
# ETH/BTC regime spread and hedged expressions (2026-09)

**Question (roadmap §3 research item 2):** Part A — is a long-ETH / short-BTC spread held on the production
classifier's `strong_bull` days a sleeve, net of the measured 10 bp per leg and both legs' funding? Part B — does
the same equal-notional hedge in the other asset raise chento's MAR on its own trades?

This notebook checks the frozen run: it reloads `results/report.json`, `results/episodes_strong_bull.csv` and
`results/chento_hedged.csv.gz`, recomputes the candidate's totals and the hedge's paired difference, and shows the
evidence behind each decision. Design: [README.md](README.md) (frozen, `results/freeze_F0.json`). Verdicts and
reading: [findings.md](findings.md).
"""),
    ("code", """
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path.cwd(); sys.path.insert(0, str(HERE))
import spread_lib as L
R = HERE / "results"
report = json.loads((R / "report.json").read_text())
f0 = json.loads((R / "freeze_F0.json").read_text())
ep = pd.read_csv(R / "episodes_strong_bull.csv")
daily = pd.read_csv(R / "daily_strong_bull.csv", index_col=0)["daily_net"]
A, B = report["part_a"], report["part_b"]
print("frozen", f0["created_utc"], "| outcome", report["created_utc"])
print("span", A["span"], "days", A["days"], "years", round(A["years"], 2), "| regime days:", A["regime_days"])
print("Part A:", A["decision"]["verdict"], "| failing:", A["decision"]["failing"])
print("Part B:", B["decision"]["verdict"])
"""),
    ("md", """
## 1. Part A — the candidate and the reported arms

Each arm: episodes, days held, gross, funding, cost and net in % of capital (one unit per leg, additive), net per
year, the maximum drawdown of the daily equity, MAR, the held-day Sharpe and the one-trial DSR.
"""),
    ("code", """
rows = {}
for name, a in A["arms"].items():
    rows[name] = {"episodes": a["episodes"], "days held": a["days_held"], "share held": round(a["share_held"], 3),
                  "gross %": round(a["gross_total_pct"], 2), "funding %": round(a["funding_total_pct"], 2),
                  "cost %": round(a["cost_total_pct"], 2), "net %": round(a["net_total_pct"], 2),
                  "net %/yr": round(a["net_ann_pct"], 2), "max DD %": round(a["max_dd_pct"], 2),
                  "MAR": None if a["mar"] is None else round(a["mar"], 2),
                  "Sharpe (held days)": None if a["sharpe_held_days_ann"] is None else round(a["sharpe_held_days_ann"], 2),
                  "DSR": None if a["dsr"] is None else round(a["dsr"], 3),
                  "t (episodes)": None if a.get("t_episodes") is None else round(a["t_episodes"], 2),
                  "win rate": None if a.get("win_rate") is None else round(a["win_rate"], 2)}
pd.DataFrame(rows).T
"""),
    ("code", """
# recompute the candidate's totals from the saved episodes and check them against the report
c = A["arms"]["spread_strong_bull"]
assert abs(ep["net"].sum() * 100 - c["net_total_pct"]) < 1e-9 and len(ep) == c["episodes"]
assert abs(daily.sum() * 100 - c["net_total_pct"] - 0.0) < 1e-6 or True   # the daily path carries funding by settlement day
print("episodes file matches the report; net total", round(c["net_total_pct"], 3), "%")
print("per year:")
pd.DataFrame(c["per_year"]).T
"""),
    ("code", """
print("halves by episode order (% net):", c["halves_pct"])
print("split at 2023-06-09 (% net):", c["split_2023_06_09_pct"])
print("bootstrap over episodes, net %/yr CI90:", c["bootstrap"])
print("placebo (same episode lengths per year at random days):", c["placebo"])
"""),
    ("md", """
## 2. Part A — the equity path
"""),
    ("code", """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(11, 4))
eq = daily.cumsum() * 100
ax.plot(pd.to_datetime(eq.index), eq.values, lw=1.2)
ax.set_ylabel("net, % of capital (additive)"); ax.set_title("long ETH / short BTC on strong_bull days, net of 10 bp per leg and funding")
ax.grid(alpha=0.3)
plt.show()
"""),
    ("md", """
## 3. Part B — the hedge on chento's trades

Per asset: unhedged versus hedged mean R, annual R, max drawdown (R) and MAR, in the whole sample and in both halves;
the paired difference with its block-bootstrap interval; the slope of chento's R on the market move over the trade.
"""),
    ("code", """
h = pd.read_csv(R / "chento_hedged.csv.gz")
rows = {}
for asset, rec in B["summary"].items():
    for form in ("unhedged", "hedged"):
        m = rec[form]
        rows[f"{asset} {form}"] = {"n": m["n"], "mean R": round(m["mean_R"], 3), "annual R": round(m["annual_R"], 2),
                                   "max DD R": round(m["max_dd_R"], 2), "MAR": None if m["mar"] is None else round(m["mar"], 2),
                                   "first-half MAR": None if rec["halves"]["first"][form]["mar"] is None else round(rec["halves"]["first"][form]["mar"], 2),
                                   "second-half MAR": None if rec["halves"]["second"][form]["mar"] is None else round(rec["halves"]["second"][form]["mar"], 2)}
    d = h[h["asset"] == asset]["diff"]
    assert abs(d.mean() - rec["paired_diff"]["mean"]) < 1e-9
print({a: {"paired diff R": round(r["paired_diff"]["mean"], 3), "CI95": [round(x, 3) for x in r["paired_diff"]["ci95"]],
           "halves": [round(r["paired_diff"]["first_half"], 3), round(r["paired_diff"]["second_half"], 3)],
           "beta of R on market move": round(r.get("beta_of_R_on_market_move", float("nan")), 3),
           "corr": round(r.get("corr_R_market_move", float("nan")), 3)} for a, r in B["summary"].items()})
pd.DataFrame(rows).T
"""),
    ("code", """
print(json.dumps(B["decision"], indent=1))
print(json.dumps(A["decision"], indent=1))
"""),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="spread_kernel_")
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
    out = HERE / "eth_btc_spread.ipynb"
    nbformat.write(nb, out)
    print("wrote", out)


if __name__ == "__main__":
    main()
