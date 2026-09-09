#!/usr/bin/env python3
"""Assemble anchor_allocator.ipynb (viewer notebook with %run cells).

Run with the SYSTEM Python (the venv has no nbformat):
    C:/Python/Python313/python.exe studies/notebooks/anchor_allocator_study/build_notebook.py [--execute]

--execute runs the notebook through nbclient on the repo venv's kernel
(venv/Scripts/python -m ipykernel_launcher) without installing a kernelspec,
so the %run cells use the repo's own interpreter. Without the flag the
notebook is written unexecuted; open it with the venv kernel and run all.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"

INTRO = """\
# A1 — anchor / idle-capital allocator study

Pre-registration: [README.md](README.md) (hypothesis, KILL rules, fixed allocator rules,
priors — written before any computation). Write-up: [findings.md](findings.md).
Approximations: [results/panel_notes.md](results/panel_notes.md).

Pipeline (each cell re-runs a script; the panel build reads prod.db read-only and takes ~15 s):

1. `build_panel.py` → `results/daily_panel.csv` — one unit-exposure daily return column per sleeve.
2. `run.py` → `results/summary.csv`, `results/equity_*.csv`, `results/verdict.json` — variants,
   sensitivity cells, diagnostics, and the pre-registered verdict.
"""

RUN_PANEL = "%run build_panel.py"
RUN_SIM = "%run run.py"

SUMMARY = """\
import json
import pandas as pd
pd.set_option('display.width', 220)
v = json.load(open('results/verdict.json', encoding='utf-8'))
print('VERDICT:', v['verdict'], '| period', *v['period'], '| n_days', v['n_days'])
for k, c in v['clauses'].items():
    print(f"  {'FIRES' if c['fires'] else 'no   '}  {k}: measured {c['measured']:+.3f} vs {c['threshold']:+.2f}")
s = pd.read_csv('results/summary.csv')
cols = ['variant', 'group', 'sharpe', 'cagr', 'mdd', 'calmar', 'ret_2022', 'sharpe_post_etf',
        'mean_idle_pct', 'mean_anchor_pct', 'mean_gross', 'd_sharpe_vs_baseline', 'd_mdd_pp_vs_baseline']
display(s[cols].round(3))
"""

CURVES = """\
import matplotlib.pyplot as plt
import numpy as np
fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
for name, color in (('baseline', '#333333'), ('baseline_gold', '#c9a227'), ('overflow', '#d62728'),
                    ('overflow_no_gold', '#1f77b4'), ('overflow_cash_only', '#2ca02c')):
    e = pd.read_csv(f'results/equity_{name}.csv', parse_dates=['date'])
    axes[0].plot(e.date, e.nav, label=name, color=color, lw=1.3)
    peak = np.maximum.accumulate(e.nav.values)
    axes[1].plot(e.date, (e.nav.values / peak - 1) * 100, color=color, lw=1.0)
axes[0].set_yscale('log'); axes[0].set_ylabel('NAV (log)'); axes[0].legend(loc='upper left')
axes[0].set_title('Decomposition variants — compounded NAV and drawdown')
axes[1].set_ylabel('drawdown %')
plt.tight_layout()
"""

WEIGHTS = """\
e = pd.read_csv('results/equity_overflow.csv', parse_dates=['date'])
fig, ax = plt.subplots(figsize=(10, 3.5))
ax.stackplot(e.date, e.w_tactical * 100, e.w_ema * 100, e.w_gold * 100, e.w_cash * 100,
             labels=['tactical (active caps)', 'EMA 1W BTC', 'GOLD', 'cash'],
             colors=['#7f7f7f', '#d62728', '#c9a227', '#2ca02c'], alpha=0.85)
ax.plot(e.date, e.idle * 100, color='k', lw=0.8, label='idle before overflow')
ax.set_ylabel('% of NAV'); ax.legend(loc='upper left', ncol=5, fontsize=8)
ax.set_title('overflow variant — daily weights (idle tactical capital is routed to GOLD/EMA)')
plt.tight_layout()
"""

CONTRIB = """\
sc = pd.read_csv('results/sleeve_contrib.csv')
piv = sc.pivot(index='sleeve', columns='variant', values='sum_contrib_pct')
display(piv[['baseline', 'baseline_gold', 'overflow', 'overflow_no_gold', 'overflow_cash_only']].round(1))
py = pd.read_csv('results/per_year.csv', index_col=0)
display((py * 100).round(1))
bb = pd.read_csv('results/bootstrap_delta.csv')
display(bb[['a', 'b', 'point', 'p05', 'p50', 'p95', 'p_gt_0', 'p_ge_010', 'p_ge_020']].round(3))
"""


def build() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3 (p300 venv)", "language": "python"}
    nb.cells = [
        nbf.v4.new_markdown_cell(INTRO),
        nbf.v4.new_markdown_cell("## 1. Build the daily unit-exposure panel"),
        nbf.v4.new_code_cell(RUN_PANEL),
        nbf.v4.new_markdown_cell("## 2. Run the allocator variants + sensitivity + verdict"),
        nbf.v4.new_code_cell(RUN_SIM),
        nbf.v4.new_markdown_cell("## 3. Verdict and summary table"),
        nbf.v4.new_code_cell(SUMMARY),
        nbf.v4.new_markdown_cell("## 4. Equity curves and drawdowns"),
        nbf.v4.new_code_cell(CURVES),
        nbf.v4.new_markdown_cell("## 5. Where the capital sits (overflow variant)"),
        nbf.v4.new_code_cell(WEIGHTS),
        nbf.v4.new_markdown_cell("## 6. Attribution, per-year returns, paired bootstrap of ΔSharpe (diagnostic)"),
        nbf.v4.new_code_cell(CONTRIB),
    ]
    return nb


def execute(nb: nbf.NotebookNode) -> None:
    """Execute on the repo venv's ipykernel without touching the user's kernelspecs."""
    from jupyter_client import KernelManager
    from nbclient import NotebookClient
    km = KernelManager(kernel_name="python3")
    km.kernel_cmd = [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=km, timeout=1200, resources={"metadata": {"path": str(HERE)}})
    client.execute()


def main() -> int:
    nb = build()
    path = HERE / "anchor_allocator.ipynb"
    if "--execute" in sys.argv:
        os.chdir(HERE)
        execute(nb)
        print("executed on", VENV_PY)
    nbf.write(nb, str(path))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
