#!/usr/bin/env python3
"""Assemble + execute adx_robustness.ipynb (system Python with nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/adx_robustness_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "adx_robustness.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="ar_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# ADX robustness pack 2026-09 (AUDIT + one policy decision)

Pre-registration: [README.md](README.md) (frozen before any run). Write-up: [findings.md](findings.md).

| id | question |
|---|---|
| (a) | the shipped Tier-2 configuration under the harness fill model vs live semantics (1 m stops, next-day trail, 15 bp, funding) |
| (b)(c) | 24 day boundaries: MTM drawdown, sizing off it; phase ensembles k = 2, 3, 24 |
| (d) | late entry h hours after the boundary |
| (e) | funding paid by longs; overlap with CARRY's perp short |
"""

SHOW = """\
import json, pandas as pd
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
A = json.load(open('results/p1a_live_semantics.json', encoding='utf-8'))
print('parity:', A['parity']); print('port check:', A['port_check'])
print('harness fill:', json.dumps(A['harness_fill'], indent=1)); print('live:', json.dumps(A['live'], indent=1)); print('gap:', A['gap'])
B = json.load(open('results/p1bc_phases.json', encoding='utf-8'))
display(pd.read_csv('results/p1b_phases.csv').round(3)); print('ranges [min, median, max]:', B['phase_ranges']); print('sizing:', B['sizing'])
for k, v in B['ensembles'].items(): print(k, {kk: vv for kk, vv in v.items()})
D = json.load(open('results/p1d_late_entry.json', encoding='utf-8'))
display(pd.DataFrame(D['by_delay']).T.round(3)); display(pd.DataFrame(D['machine_with_delay']).T.round(3)); print(D['decision'])
E = json.load(open('results/p1e_funding.json', encoding='utf-8')); print(json.dumps(E, indent=1))
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## (a) live semantics"), nbf.v4.new_code_cell("%run run_p1a_live_semantics.py"),
        nbf.v4.new_markdown_cell("## (b)(c) phases, sizing, ensembles"), nbf.v4.new_code_cell("%run run_p1bc_phases.py"),
        nbf.v4.new_markdown_cell("## (d) late entry"), nbf.v4.new_code_cell("%run run_p1d_late_entry.py"),
        nbf.v4.new_markdown_cell("## (e) funding and venue"), nbf.v4.new_code_cell("%run run_p1e_funding.py"),
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
