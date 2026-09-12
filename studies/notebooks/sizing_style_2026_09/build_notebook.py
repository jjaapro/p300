#!/usr/bin/env python3
"""Assemble + execute sizing_style.ipynb (system Python with nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/sizing_style_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "sizing_style.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="ss_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# Sizing style 2026-09 — account-level risk vs per-trade stops (AUDIT + policy decision)

Pre-registration: [README.md](README.md) (frozen before any run). Write-up: [findings.md](findings.md).

| id | question |
|---|---|
| S1 | exit policy on identical entries: shipped stop vs time-only vs target-only vs 3× catastrophe stop |
| S2 | sizing rule on the shipped trades (fixed-R on fixed capital / on equity / fixed notional) and the ruin table |
| S3 | minute-by-minute cross-margin liquidation walk per bot and pooled; pre-registered decision |

Re-run order: `run_s1_exit_policies.py` → `run_s2_sizing_rules.py` → `run_s3_liquidation.py`.
"""

SHOW = """\
import json, pandas as pd
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
S1 = json.load(open('results/s1_exit_policies.json', encoding='utf-8'))
for sl_, d in S1['sleeves'].items():
    print(sl_)
    display(pd.DataFrame({p: dict(n=r['n'], mean_r=r['mean_r'], win=r['win'], worst_r=r['worst_r'], h1_r=r['first_half_r'], h2_r=r['second_half_r'],
                                  mean_pct=r['mean_pnl_pct'], mtm_maxdd_pct=r['maxdd_pct'], pct_per_yr=r['pct_per_year'], mar=r['mar'], notional_x=r['mean_notional_x'])
                          for p, r in d.items()}).T.round(3))
display(pd.DataFrame(S1['adx']).T)
S2 = json.load(open('results/s2_sizing_rules.json', encoding='utf-8'))
for sl_, d in S2['sleeves'].items():
    print(sl_); display(pd.DataFrame(d).T.round(2))
print(json.dumps(S2['ruin'], indent=1))
S3 = json.load(open('results/s3_liquidation.json', encoding='utf-8'))
for pol, d in S3['per_bot'].items():
    print(pol); display(pd.DataFrame(d).T.round(4)); print('pooled:', S3['pooled'][pol])
print(json.dumps(S3['decision'], indent=1))
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## S1 — exit policies"), nbf.v4.new_code_cell("%run run_s1_exit_policies.py"),
        nbf.v4.new_markdown_cell("## S2 — sizing rules and ruin table"), nbf.v4.new_code_cell("%run run_s2_sizing_rules.py"),
        nbf.v4.new_markdown_cell("## S3 — liquidation walk and decision"), nbf.v4.new_code_cell("%run run_s3_liquidation.py"),
        nbf.v4.new_markdown_cell("## S3b — POST-HOC sensitivity (not pre-registered): SHORT_SQUEEZE single-open"), nbf.v4.new_code_cell("%run run_s3b_single_open.py"),
        nbf.v4.new_markdown_cell("## Tables"), nbf.v4.new_code_cell(SHOW),
    ]
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, NB_PATH)
    client = NotebookClient(nb, timeout=3600, kernel_name=kernel, resources={"metadata": {"path": str(HERE)}})
    client.execute()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, NB_PATH)
    print("executed and wrote", NB_PATH)


if __name__ == "__main__":
    main()
