#!/usr/bin/env python3
"""Assemble + execute scanner_study.ipynb from results/ (run after the studies)."""
from __future__ import annotations

import os

import nbformat as nbf
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))

SETUP = """\
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
pd.set_option('display.width', 200)
v1 = pd.read_csv('results/variant_summary.csv')
v2 = pd.read_csv('results/variant_summary_v2.csv')
print(f"v1: {v1.variant.nunique()} variants   v2: {v2.variant.nunique()} variants")
"""

TABLES = """\
cols = ['variant','split','n','net_exp_r','gross_exp_r','wr_pct','total_r','max_dd_r','trades_per_day','med_cost_r']
print('=== v1 A (sweep-fade short): best by OOS net expectancy ===')
a = v1[v1.variant.str.startswith('A_') & (v1.split=='OOS')].sort_values('net_exp_r', ascending=False)
display(a[cols].head(8).round(3))
print('=== v1 B (RS-dump long): all decisively negative ===')
display(v1[v1.variant.str.startswith('B_')][cols].round(3))
print('=== v2 A2 (pre-registered refinement): gross edge did NOT concentrate ===')
display(v2[v2.split=='OOS'].sort_values('net_exp_r', ascending=False)[cols].head(8).round(3))
"""

CURVE = """\
t = pd.read_csv('results/trades_A_sweepfade_1_2.5_1.5_skip_up30d.csv', parse_dates=['time']).sort_values('time')
fig, ax = plt.subplots(figsize=(9,3.5))
ax.plot(t.time, t.gross_r.cumsum(), label='gross', color='#4878cf')
ax.plot(t.time, t.net_r.cumsum(), label='net of 18bp round-trip', color='#d1494e')
ax.axhline(0, color='gray', lw=.5); ax.legend()
ax.set_ylabel('cumulative R'); ax.set_title('Best v1 variant: costs consume the entire edge')
plt.tight_layout()
byyear = t.assign(year=t.time.dt.year).groupby('year').agg(
    n=('net_r','size'), gross=('gross_r','mean'), net=('net_r','mean')).round(3)
display(byyear)
"""

def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(
            '# Scanner study — Paladin-derived cross-sectional templates\n\n'
            '**Concluded NEGATIVE 2026-08-23** — see `findings.md` for the full write-up.\n\n'
            '171-symbol Binance futures universe, 15m, 2024-01→2026-08. '
            'IS/OOS split at 2025-09. 18bp round-trip taker costs, next-bar-open fills, '
            'conservative stop-first bar ambiguity.'),
        nbf.v4.new_code_cell(SETUP),
        nbf.v4.new_markdown_cell('## Variant tables'),
        nbf.v4.new_code_cell(TABLES),
        nbf.v4.new_markdown_cell('## The story in one chart: gross vs net'),
        nbf.v4.new_code_cell(CURVE),
    ]
    path = os.path.join(HERE, 'scanner_study.ipynb')
    nbf.write(nb, path)
    client = NotebookClient(nb, timeout=600, kernel_name='python3',
                            resources={'metadata': {'path': HERE}})
    client.execute()
    nbf.write(nb, path)
    print('executed and wrote', path)


if __name__ == '__main__':
    main()
