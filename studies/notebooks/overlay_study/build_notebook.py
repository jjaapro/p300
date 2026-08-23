#!/usr/bin/env python3
"""Assemble + execute overlay_study.ipynb from results/."""
from __future__ import annotations

import os

import nbformat as nbf
from nbclient import NotebookClient

HERE = os.path.dirname(os.path.abspath(__file__))

SETUP = """\
import pandas as pd, numpy as np
import matplotlib.pyplot as plt
pd.set_option('display.width', 200)
res = pd.read_csv('results/overlay_summary.csv')
cols = ['variant','n','traded_n','mean_r','total_r','max_dd_r','mar_like','wr_pct','is_total_r','oos_total_r']
for scope in ('BTC','ETH','COMBINED'):
    sub = res[res.scope==scope]
    base = sub[sub.variant=='base|tilt=none|H=off'][cols].round(2)
    top = sub.sort_values('mar_like', ascending=False)[cols].head(8).round(2)
    print(f'=== {scope} baseline ==='); display(base)
    print(f'=== {scope} top by MAR ==='); display(top)
"""

CURVES = """\
bt = pd.read_csv('results/trades_BTC.csv', parse_dates=['ts'])
et = pd.read_csv('results/trades_ETH.csv', parse_dates=['ts'])
for t in (bt, et):
    t['aligned'] = (((t.direction=='long') & (t.okx_delta_z>=0)) | ((t.direction=='short') & (t.okx_delta_z<=0)))
bt, et = bt[bt.aligned], et[et.aligned]
end = min(bt.ts.max(), et.ts.max())
comb = pd.concat([bt[bt.ts<=end], et[et.ts<=end]]).sort_values('ts')
fig, ax = plt.subplots(figsize=(9,3.5))
ax.plot(bt.ts, bt.r_outcome.cumsum(), label=f'BTC (n={len(bt)})', color='#4878cf')
ax.plot(et.ts, et.r_outcome.cumsum(), label=f'ETH (n={len(et)})', color='#e1812c')
ax.plot(comb.ts, comb.r_outcome.cumsum(), label='combined', color='#333', lw=1.8)
ax.legend(); ax.set_ylabel('cumulative R (research replay, no overlays)')
ax.set_title('Multi-asset chento composite: BTC + ETH')
plt.tight_layout()
"""

def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(
            '# Overlay study — multi-asset chento, wick-exit, half-risk tag, post-loss throttle\n\n'
            'See `findings.md` for the write-up. Verdicts: multi-asset BTC+ETH **yes** '
            '(diversification real); wick-exit **no** (cuts the 6R tail); half-risk tag '
            '**no as specified** (tags 54% of trades); post-loss: shipped skip-after-loss '
            'best MAR, half-after-loss best income/DD compromise (and better on ETH).\n\n'
            '*Absolute Rs inherit the research-pool lookahead; relative comparisons are the finding.*'),
        nbf.v4.new_code_cell(SETUP),
        nbf.v4.new_markdown_cell('## Cumulative R per asset (base exits, no overlays)'),
        nbf.v4.new_code_cell(CURVES),
    ]
    path = os.path.join(HERE, 'overlay_study.ipynb')
    nbf.write(nb, path)
    client = NotebookClient(nb, timeout=600, kernel_name='python3',
                            resources={'metadata': {'path': HERE}})
    client.execute()
    nbf.write(nb, path)
    print('executed and wrote', path)


if __name__ == '__main__':
    main()
