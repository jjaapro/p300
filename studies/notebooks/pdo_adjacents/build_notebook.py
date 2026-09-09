"""Write the viewer notebook for the pdo_adjacents study.

Run with the SYSTEM python (the venv has no nbformat):

    C:/Python/Python313/python.exe studies/notebooks/pdo_adjacents/build_notebook.py

The notebook only *reads* ./results/, so it can be executed with the venv
kernel afterwards without touching prod.db.
"""
from __future__ import annotations

from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
NB_PATH = HERE / "pdo_adjacents.ipynb"

md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

cells = [
    md("# S-PDO-adjacents — viewer\n"
       "\n"
       "Two pre-registered questions about the neighbourhood of the live PDO sleeve:\n"
       "\n"
       "- **(a)** regime threshold **−10 %** (live) vs **−7 %** (predecessor) → **KEEP −10 %**\n"
       "- **(b)** the **CDO retouch** variant → **KILL**\n"
       "\n"
       "Pre-registration: `README.md`. Verdicts and honest discussion: `findings.md`.\n"
       "This notebook only reads `results/` — it never opens prod.db."),

    code("import json\n"
         "from pathlib import Path\n"
         "import pandas as pd\n"
         "import numpy as np\n"
         "import matplotlib.pyplot as plt\n"
         "\n"
         "R = Path.cwd() / 'results'\n"
         "if not R.exists():\n"
         "    R = Path(__file__).resolve().parent / 'results' if '__file__' in dir() else R\n"
         "pd.set_option('display.width', 200)\n"
         "pd.set_option('display.max_columns', 50)\n"
         "print(sorted(p.name for p in R.glob('*')))"),

    md("## 0. Parity gate\n"
       "Study features vs the live sleeve's own functions under a frozen clock, "
       "**exact float equality**. This must pass or nothing below is reportable."),
    code("parity = json.loads((R / 'parity.json').read_text())\n"
         "print('passed:', parity['passed'], ' total checks:', parity['total_checks'])\n"
         "display(pd.DataFrame(parity['assets']).T)"),

    md("## (a) Regime threshold — clause table"),
    code("qa = json.loads((R / 'qa_clauses.json').read_text())\n"
         "cl = qa['clauses']\n"
         "rows = [\n"
         "    ('A1 BTC OOS delta bp', cl['A1_oos_delta_btc_bp'], '>= +5.0', cl['A1_fired']),\n"
         "    ('A1 ETH OOS delta bp', cl['A1_oos_delta_eth_bp'], '>= +5.0', cl['A1_fired']),\n"
         "    ('A2 BTC IS delta bp',  cl['A2_is_delta_btc_bp'],  '>= 0.0',  cl['A2_fired']),\n"
         "    ('A2 ETH IS delta bp',  cl['A2_is_delta_eth_bp'],  '>= 0.0',  cl['A2_fired']),\n"
         "]\n"
         "display(pd.DataFrame(rows, columns=['clause', 'measured', 'required', 'fired']))\n"
         "print('VERDICT:', qa['verdict'])\n"
         "print('discriminating trades  total:', qa['band_trades_total'], ' OOS:', qa['band_trades_oos'])"),

    md("### Mean per-trade outcome by asset × period × threshold (18 bp round trip)"),
    code("s = pd.read_csv(R / 'qa_summary.csv')\n"
         "piv = s.pivot_table(index=['asset', 'period'], columns='config',\n"
         "                    values=['n', 'mean_bp_18'])\n"
         "piv[('delta_bp', '')] = piv[('mean_bp_18', 'm7')] - piv[('mean_bp_18', 'm10')]\n"
         "display(piv.round(2))"),

    md("### Why the OOS clause measured exactly zero\n"
       "Every out-of-sample entry sat far above both thresholds, and the OOS window "
       "spent almost no time in the discriminating [−10 %, −7 %) band."),
    code("display(pd.read_csv(R / 'qa_oos_trade_list.csv')[\n"
         "    ['asset', 'config', 'entry_iso', 'gap_pct', 'btc_30d_pct', 'net_bp', 'in_band']])\n"
         "display(pd.read_csv(R / 'qa_regime_occupancy.csv').round(4))"),

    md("### The 15 discriminating trades (taken at −10 %, blocked at −7 %)"),
    code("band = pd.read_csv(R / 'qa_band_trade_list.csv')\n"
         "display(band[['asset', 'entry_iso', 'gap_pct', 'btc_30d_pct', 'net_bp']])\n"
         "display(pd.read_csv(R / 'qa_band_trades.csv').round(2))"),

    md("### Substitution: −7 % is not a subset of −10 %\n"
       "Blocking a setup frees the one-trade-per-day slot, so a later touch bar the "
       "same day can fire instead — usually at a worse price."),
    code("t = pd.read_csv(R / 'qa_trades.csv')\n"
         "for a in ['BTC', 'ETH']:\n"
         "    s10 = set(t[(t.asset == a) & (t.config == 'm10')].entry_ts)\n"
         "    s7 = set(t[(t.asset == a) & (t.config == 'm7')].entry_ts)\n"
         "    only7 = t[(t.asset == a) & (t.config == 'm7') & (t.entry_ts.isin(s7 - s10))]\n"
         "    print(f'== {a}: removed {len(s10 - s7)}, added {len(s7 - s10)}')\n"
         "    if len(only7):\n"
         "        display(only7[['entry_iso', 'btc_30d_pct', 'net_bp']])"),

    md("### Equity curves, both thresholds"),
    code("fig, axes = plt.subplots(1, 2, figsize=(13, 4))\n"
         "for ax, a in zip(axes, ['BTC', 'ETH']):\n"
         "    for cfg, lbl in [('m10', 'threshold -10%'), ('m7', 'threshold -7%')]:\n"
         "        d = t[(t.asset == a) & (t.config == cfg)].sort_values('entry_ts')\n"
         "        ax.plot(pd.to_datetime(d.entry_iso), d.net_bp.cumsum(), label=lbl)\n"
         "    ax.axhline(0, lw=0.8, color='0.6')\n"
         "    ax.set_title(f'{a} PDO cumulative net bp (18bp RT)')\n"
         "    ax.legend()\n"
         "fig.tight_layout()"),

    md("## (b) CDO retouch — clause table"),
    code("qb = json.loads((R / 'qb_clauses.json').read_text())\n"
         "c = qb['clauses']\n"
         "rows = [\n"
         "    ('B1  bootstrap p05 of mean R', c['B1_mean_R_p05'], '> 0', c['B1_fired']),\n"
         "    ('B2  positive complete folds', f\"{c['B2_positive_folds']} of {c['B2_complete_folds']}\", '>= 3 of 4', c['B2_fired']),\n"
         "    ('B3  DSR @ N_TRIALS=2', c['B3_dsr'], '> 0.95', c['B3_fired']),\n"
         "]\n"
         "display(pd.DataFrame(rows, columns=['clause', 'measured', 'required', 'fired']))\n"
         "print('VERDICT:', qb['verdict'])\n"
         "print('n trades:', qb['n_trades'], ' mean R:', round(qb['mean_R'], 4),\n"
         "      ' sum R:', round(qb['sum_R'], 1), ' maxDD R:', round(qb['max_drawdown_R'], 1))\n"
         "print(json.dumps(qb['robustness_bounds'], indent=1))"),

    md("### Walk-forward folds"),
    code("display(pd.read_csv(R / 'qb_folds.csv').round(4))"),

    md("### Every slice is negative"),
    code("qs = pd.read_csv(R / 'qb_summary.csv')\n"
         "display(qs.round(4))"),

    md("### The mechanism: target hit rate below break-even\n"
       "2R target, 1R stop, 18 bp = 0.18 R cost → break-even target rate 39.3 %."),
    code("tb = pd.read_csv(R / 'qb_trades.csv')\n"
         "mix = tb.exit_type.value_counts(normalize=True).rename('share')\n"
         "display(mix.to_frame().round(4))\n"
         "be = qb['robustness_bounds']['breakeven_target_hit_rate_at_18bp']\n"
         "print(f\"target hit rate {qb['robustness_bounds']['target_hit_rate']:.4f} \"\n"
         "      f\"vs break-even {be:.4f}\")\n"
         "print(f\"mean R at zero cost: {qb['robustness_bounds']['mean_R_at_zero_cost']:+.4f}\")"),

    code("fig, ax = plt.subplots(figsize=(11, 4))\n"
         "for a in ['BTC', 'ETH']:\n"
         "    d = tb[tb.asset == a].sort_values('entry_ts')\n"
         "    ax.plot(pd.to_datetime(d.entry_iso), d.R.cumsum(), label=a)\n"
         "d = tb.sort_values('entry_ts')\n"
         "ax.plot(pd.to_datetime(d.entry_iso), d.R.cumsum(), label='pooled', lw=2, color='k')\n"
         "ax.axhline(0, lw=0.8, color='0.6')\n"
         "ax.set_title('CDO retouch cumulative R (18bp RT, 1% = 1R)')\n"
         "ax.legend()\n"
         "fig.tight_layout()"),

    md("---\n"
       "**Verdicts**\n"
       "\n"
       "- (a) `KEEP -10%` — A1 measured +0.00 bp (no discriminating OOS trades at all), "
       "A2 measured −2.01 bp (BTC) and −4.00 bp (ETH), both below their bars. "
       "No change to `config.py` is recommended.\n"
       "- (b) `KILL` — 0 of 3 clauses fired; mean −0.164 R over 1,855 trades, "
       "0 of 4 folds positive, DSR 7.2e-09. At zero cost the mean is +0.016 R, "
       "i.e. the signal is a coin flip and the cost is what makes it a loser.\n"
       "\n"
       "See `findings.md` for the full clause tables, the substitution finding, and "
       "the limitations that bound both answers."),
]

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python",
                             "name": "python3"}
nb.metadata["language_info"] = {"name": "python"}
NB_PATH.write_text(nbf.writes(nb), encoding="utf-8")
print(f"wrote {NB_PATH} ({len(cells)} cells)")
