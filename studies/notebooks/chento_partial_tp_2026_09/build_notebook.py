#!/usr/bin/env python3
"""Assemble + execute chento_partial_tp.ipynb (system Python with nbformat/nbclient; cells run in the repo venv).

    C:/Python/Python313/python.exe studies/notebooks/chento_partial_tp_2026_09/build_notebook.py

The cells render results/ written by partial_run.py; they do not recompute.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "chento_partial_tp.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="cptp_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# Chento partial take-profits 2026-09

Pre-registration: [README.md](README.md), frozen in `results/freeze_F0.json` before any ladder outcome. Write-up:
[findings.md](findings.md). Chento's own trim arithmetic (fractions of the running position at fixed R-levels)
on the exit-policy study's 392 entries, against the shipped single 6R target, with a random-level placebo.
Re-run: `partial_run.py freeze0`, then `partial_run.py outcomes`.
"""

LOAD = """\
import json, pandas as pd
from pathlib import Path
R = Path('results')
F0 = json.loads((R / 'freeze_F0.json').read_text(encoding='utf-8'))
J = json.loads((R / 'report.json').read_text(encoding='utf-8'))
V = json.loads((R / 'verdict.json').read_text(encoding='utf-8'))
print('freeze F0:', F0['created_utc'], '| outcomes started:', J['started_utc'])
print('gates:', json.dumps(J['gates'], indent=1))
print('A0 exit kinds:', J['a0_exit_kinds'])
"""

SEQ = """\
rows = []
for arm, s in J['sequences'].items():
    for scope in ('pooled', 'BTC', 'ETH', 'first_half', 'second_half'):
        x = s[scope]; rows.append(dict(arm=arm, scope=scope, trades=x['trades'], mean_net_R=x['mean_net_R'],
                                       cum_R=x['cum_R'], win_rate=x['win_rate'], max_dd_R=x['max_dd_R'], MAR_R=x['MAR_R']))
df = pd.DataFrame(rows)
for scope in ('pooled', 'BTC', 'ETH', 'first_half', 'second_half'):
    print(scope); display(df[df.scope == scope].set_index('arm').drop(columns='scope').round(3))
"""

PAIRS = """\
print('paired vs A0 (net R per trade):')
display(pd.DataFrame(J['paired_vs_A0']).T.round(3))
print('fills:')
display(pd.DataFrame(J['fills']).T)
print('placebo (same fractions, random levels):')
display(pd.DataFrame(J['placebo']).T)
"""

CURVES = """\
pt = pd.read_csv(R / 'per_trade.csv.gz')
pt['entry_day'] = pd.to_datetime(pt['entry_day'])
pt = pt.sort_values(['entry_day', 't'])
cum = pt.set_index('entry_day')[[c for c in pt.columns if c.startswith('net_R_')]].cumsum()
cum.columns = [c.replace('net_R_', '') for c in cum.columns]
ax = cum.plot(figsize=(10, 4), title='cumulative net R, pooled, in entry order')
ax.set_ylabel('R')
"""

DECISION = """\
print('VERDICT:', V['verdict'])
print(json.dumps(V['clauses'], indent=1))
"""


def main() -> None:
    import nbformat
    from nbclient import NotebookClient
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    kernel = register_temp_kernel()
    nb = new_notebook(metadata={"kernelspec": {"name": kernel, "display_name": "p300 venv", "language": "python"}})
    nb.cells = [new_markdown_cell(HEADER),
                new_markdown_cell("## Freeze and gates — A0 reproduced against the committed exit-policy record"),
                new_code_cell(LOAD),
                new_markdown_cell("## Sequences — mean R, cumulative R, drawdown, MAR by arm and scope"),
                new_code_cell(SEQ),
                new_markdown_cell("## Paired differences, fills and the placebo"),
                new_code_cell(PAIRS),
                new_markdown_cell("## Cumulative net R by arm"),
                new_code_cell(CURVES),
                new_markdown_cell("## Decision (rule fixed in README.md §6 before the run)"),
                new_code_cell(DECISION)]
    client = NotebookClient(nb, timeout=600, kernel_name=kernel, resources={"metadata": {"path": str(HERE)}})
    client.execute()
    nbformat.write(nb, NB_PATH)
    print("executed and wrote", NB_PATH)


if __name__ == "__main__":
    main()
