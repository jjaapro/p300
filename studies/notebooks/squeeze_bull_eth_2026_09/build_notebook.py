"""Assemble and execute the review notebook (squeeze_bull_eth.ipynb).

    C:/Python/Python313/python.exe studies/notebooks/squeeze_bull_eth_2026_09/build_notebook.py

System Python holds nbformat and nbclient; the cells run in the repo venv through a throwaway kernelspec. The
notebook recomputes the ETH bull-gated statistics from the saved ledger and checks them against the report; it is
a check on the run, not a second run.
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
# SQUEEZE_BULL on ETH (2026-09)

**Question (roadmap §3 research item 1, second assets):** does the shipped SQUEEZE_BULL rule — a −2 % 4-hour
open-interest drop with price down, bought at the bar's close in a bull regime, stop −2 %, target +3 %, 48-hour
time stop — earn on ETH, with the frozen June engine run unchanged on ETH hourly bars and close-of-hour open
interest built from on-disk panels?

This notebook checks the frozen run: it reloads `results/report.json` and `results/ledger_ETH.csv`, recomputes the
bull-gated statistics from the ledger and asserts they match the report. Design: [README.md](README.md) (frozen,
`results/freeze_F0.json`). Verdict and reading: [findings.md](findings.md).
"""),
    ("code", """
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path.cwd(); sys.path.insert(0, str(HERE))
import sqb_eth_lib as L
R = HERE / "results"
report = json.loads((R / "report.json").read_text())
f0 = json.loads((R / "freeze_F0.json").read_text())
print("frozen", f0["created_utc"], "| outcome", report["created_utc"])
print("P1 fidelity:", {k: v for k, v in report["P1"].items() if k not in ("only_panel", "only_ref")})
print("DECISION:", json.dumps(report["decision"], indent=1))
"""),
    ("md", """
## 1. The ledgers side by side

Bull-gated by the shipped causal regime (`ret_30d_backonly`), resolved fires, at the June 18 bp inside `r_outcome`
and re-costed at 10 bp. `BTC_reference` is the revalidation's corrected-table ledger read the same way;
`BTC_panel` is the same engine on the panel-built BTC tables (the fidelity run); `ETH` is the question.
"""),
    ("code", """
rows = {}
for label, key in (("BTC reference (prod tables)", "BTC_reference"), ("BTC panel-built", "BTC_panel"), ("ETH panel-built", "ETH")):
    rec = report[key]
    for form, col in (("full, 18 bp", "full_bull"), ("full, 10 bp", "full_bull_10bp"), ("OOS 2026-04-14 →, 18 bp", "oos_bull")):
        s = rec.get(col)
        if not s or not s.get("n"):
            rows[f"{label} · {form}"] = {"n": 0 if s else "—"}; continue
        rows[f"{label} · {form}"] = {"n": s["n"], "mean R": round(s["mean_R"], 3), "win": round(s["WR"], 2),
                                     "cum R": round(s["cum_R"], 1), "max DD R": round(s["maxDD"], 2),
                                     "annual R": round(s["annual_R"], 1),
                                     "MAR": None if s["MAR"] is None else round(s["MAR"], 2),
                                     "halves": f"{s['first_half_mean_R']:+.2f} / {s['second_half_mean_R']:+.2f}" if s.get("second_half_mean_R") is not None else None,
                                     "DSR": None if s.get("dsr") is None else round(s["dsr"], 3),
                                     "exits": s["exit_mix"], "span": s["first_fire"][:10] + " → " + s["last_fire"][:10]}
pd.DataFrame(rows).T
"""),
    ("code", """
led = pd.read_csv(R / "ledger_ETH.csv", parse_dates=["ts"])
bull = L.bull_gated(led)
mine = L.stats(bull)
saved = report["ETH"]["full_bull"]
assert mine["n"] == saved["n"] and abs(mine["mean_R"] - saved["mean_R"]) < 1e-9 and abs(mine["maxDD"] - saved["maxDD"]) < 1e-9
print("recomputed the ETH bull-gated statistics from ledger_ETH.csv and matched the report")
print("ETH fires by regime:", report["ETH"]["by_regime"], "| frame:", report["ETH"]["frame"])
print("per year (bull, 18 bp):", json.dumps(saved["per_year"], indent=1))
"""),
    ("md", """
## 2. The ETH equity path against BTC's
"""),
    ("code", """
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(11, 4))
btc = pd.read_csv(R / "ledger_BTC_panel.csv", parse_dates=["ts"])
for label, frame, color in (("BTC panel-built (bull-gated)", btc, "tab:blue"), ("ETH panel-built (bull-gated)", led, "tab:orange")):
    b = L.bull_gated(frame).sort_values("ts")
    ax.plot(b["ts"], b["r_outcome"].cumsum(), lw=1.2, label=f"{label}, {len(b)} fires", color=color)
ax.set_ylabel("cumulative R (18 bp inside)"); ax.legend(); ax.grid(alpha=0.3)
ax.set_title("SQUEEZE_BULL: the frozen June engine on BTC and on ETH, from on-disk panels")
plt.show()
"""),
    ("md", """
## 3. The fidelity run

The same builder and engine on the BTC panels against the corrected-table reference: the bull-gated fire sets and
the replay on the shared fires. The open-interest series differ by source (archive vs CoinDesk / native Binance),
which moves fires across the −2 % threshold; the hourly bars were assumed identical and are not on every hour.
"""),
    ("code", """
f = report["P1"]
print({k: v for k, v in f.items() if k not in ("only_panel", "only_ref")})
print("only in the panel run:", f["only_panel"][:10]); print("only in the reference:", f["only_ref"][:10])
"""),
    ("code", """
print(json.dumps(report["decision"], indent=1))
"""),
]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="sqbeth_kernel_")
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
    out = HERE / "squeeze_bull_eth.ipynb"
    nbformat.write(nb, out)
    print("wrote", out)


if __name__ == "__main__":
    main()
