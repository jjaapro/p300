"""Build the viewer notebook (system Python: needs nbformat; the venv has none).
Run: C:/Python/Python313/python.exe studies/notebooks/r4_bot_prep/build_notebook.py
"""
from pathlib import Path
import nbformat as nbf

here = Path(__file__).resolve().parent
nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb.cells = [
    md("# R4 bot prep — stop-loss grid and late-entry curve (2026-09-06)\n\n"
       "Pre-registered in [README.md](README.md); verdicts in [findings.md](findings.md). "
       "The cell below re-runs the sweep read-only against `prod.db` (≈2 min) and prints "
       "both tables plus the rule-based decision."),
    code("%run sl_sweep.py"),
    md("## Results on disk\n\nThe same numbers as CSV, for other notebooks."),
    code("import pandas as pd\n"
         "pd.set_option('display.width', 200)\n"
         "display(pd.read_csv('results/sl_sweep.csv').round(1))\n"
         "display(pd.read_csv('results/late_entry.csv').round(1))"),
]
nbf.write(nb, here / "r4_bot_prep.ipynb")
print("wrote", here / "r4_bot_prep.ipynb")
