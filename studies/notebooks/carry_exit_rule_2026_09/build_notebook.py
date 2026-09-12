#!/usr/bin/env python3
"""Assemble + execute carry_exit_rule.ipynb (system Python with nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/carry_exit_rule_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "carry_exit_rule.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="ce_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# CARRY exit rule 2026-09 (AUDIT + one policy decision)

Pre-registration: [README.md](README.md) (frozen before any run). Write-up: [findings.md](findings.md).
Five exit rules for S-078 on the full Binance settlement history, net of 0.24 % per toggle, with the C2 rule
simulator imported unchanged. Re-run: `run_p2_exit_rules.py`.
"""

SHOW = """\
import json, pandas as pd
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
R = json.load(open('results/p2_exit_rules.json', encoding='utf-8'))
print('parity:', R['parity'])
display(pd.DataFrame(R['rules']).T.round(3))
display(pd.read_csv('results/p2_per_year.csv').round(2))
print('era:', json.dumps(R['era'], indent=1)); print('tail stress:', json.dumps(R['tail_stress'], indent=1))
print('bootstrap vs LIVE:', json.dumps(R['bootstrap_vs_live'], indent=1)); print('decision:', json.dumps(R['decision'], indent=1))
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## Rules, per-year, era, tail stress, bootstrap, decision"), nbf.v4.new_code_cell("%run run_p2_exit_rules.py"),
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
