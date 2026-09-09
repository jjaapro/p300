"""Build the viewer notebook (system Python: needs nbformat; the venv has none).
Run: C:/Python/Python313/python.exe studies/notebooks/grid_study/build_notebook.py
"""
from pathlib import Path
import nbformat as nbf

here = Path(__file__).resolve().parent
nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb.cells = [
    md("# Grid trading (S-080) — viewer (2026-09-07)\n\n"
       "Pre-registered in [README.md](README.md); verdict in [findings.md](findings.md). "
       "The four cells (0.2% / 0.5% spacing × maker / taker costs) were run once on "
       "`btc_1m` 2020-01-01 → 2026-09-06 (`results/run_1m.log`). Re-running takes ≈ 25 min "
       "on 3.5M bars: `python grid_harness.py --bars 1m`; the 5m variant is a quick check."),
    md("## Summary table (as produced)"),
    code("import pandas as pd\n"
         "pd.set_option('display.width', 200)\n"
         "s = pd.read_csv('results/grid_summary.csv').set_index('cell')\n"
         "display(s.round(2))"),
    md("## Equity curves\n\nCumulative % of capital (additive, unlevered), per cell."),
    code("import matplotlib.pyplot as plt\n"
         "fig, ax = plt.subplots(figsize=(11, 5))\n"
         "for cell in s.index:\n"
         "    d = pd.read_csv(f'results/grid_daily_{cell}.csv', index_col=0, parse_dates=True)\n"
         "    ax.plot(d.index, d['equity'] * 100, label=cell)\n"
         "ax.axhline(0, color='k', lw=0.5); ax.legend(); ax.set_ylabel('% of capital')\n"
         "ax.set_title('Grid P&L, realized + mark-to-market of open lots')\n"
         "plt.show()"),
    md("## Per-year returns\n\nThe only maker cell with positive Sharpe in both halves "
       "(A_maker_0.5) is positive in every year except 2022, where the long inventory "
       "accumulated into the decline cost −39% and sets the 62% max drawdown."),
    code("rows = {}\n"
         "for cell in s.index:\n"
         "    d = pd.read_csv(f'results/grid_daily_{cell}.csv', index_col=0, parse_dates=True)\n"
         "    rows[cell] = d['ret'].groupby(d.index.year).sum() * 100\n"
         "display(pd.DataFrame(rows).round(1))"),
    md("## Re-run (optional)\n\nUncomment to recompute everything read-only against `prod.db`."),
    code("# %run grid_harness.py --bars 5m"),
]
nbf.write(nb, here / "grid_study.ipynb")
print("wrote", here / "grid_study.ipynb")
