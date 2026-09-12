#!/usr/bin/env python3
"""Assemble + execute adx_eth.ipynb (system Python with nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/adx_eth_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "adx_eth.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="ae_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# ADX Tier-2 machine on ETH (new-sleeve candidate test, N_TRIALS = 1)

Pre-registration: [README.md](README.md) (frozen before any run). Write-up: [findings.md](findings.md).
The shipped BTC configuration applied to ETH unchanged, live semantics, 24 day boundaries. Re-run: `run_n1_eth.py`.
"""

SHOW = """\
import json, pandas as pd
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
R = json.load(open('results/n1_eth.json', encoding='utf-8'))
display(pd.read_csv('results/n1_phases.csv').round(3)); print('ranges [min, median, max]:', R['phase_ranges'])
print('live phase:', json.dumps(R['live_phase'], indent=1)); print('BTC/ETH:', json.dumps(R['btc_eth'], indent=1)); print('decision:', R['decision'])
display(pd.read_csv('results/n1_eth_ledger_phase0.csv').round(2))
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## 24 phases, live phase, BTC correlation, decision"), nbf.v4.new_code_cell("%run run_n1_eth.py"),
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
