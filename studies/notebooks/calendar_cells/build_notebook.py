"""Build the viewer notebook for the calendar_cells study.

Run with the SYSTEM python (the venv has no nbformat):
    C:/Python/Python313/python.exe studies/notebooks/calendar_cells/build_notebook.py

Produces calendar_cells.ipynb next to this file. The notebook is a viewer: it
reads results/ produced by run_cells.py, it does not recompute anything and does
not touch prod.db.
"""
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent


def md(t):
    return nbf.v4.new_markdown_cell(t)


def code(t):
    return nbf.v4.new_code_cell(t)


cells = [
    md("# S-calendar-cells — viewer\n"
       "\n"
       "**STATUS: CONCLUDED KILL (all five cells) per pre-registration.**\n"
       "\n"
       "Five pre-registered calendar cells on BTC/ETH, tested once, `N_TRIALS = 5`.\n"
       "Pass rule (all three required): `t_pre >= 2.5`, `t_post >= 2.5`,\n"
       "`DSR(n_trials=5) >= 0.95`. Era split 2024-01-11. Cost 18 bp round trip.\n"
       "\n"
       "This notebook only *reads* `results/`. The pre-registration is `README.md`,\n"
       "the analysis is `run_cells.py`, the verdict and discussion are `findings.md`.\n"
       "Re-generate the artefacts with `python run_cells.py` (venv python)."),

    code("import json\n"
         "from pathlib import Path\n"
         "import pandas as pd\n"
         "\n"
         "HERE = Path.cwd()\n"
         "R = HERE / 'results'\n"
         "if not R.exists():\n"
         "    R = HERE / 'studies' / 'notebooks' / 'calendar_cells' / 'results'\n"
         "assert R.exists(), f'results/ not found from {HERE}; run run_cells.py first'\n"
         "\n"
         "summary = pd.read_csv(R / 'cells_summary.csv')\n"
         "meta = json.loads((R / 'cells_summary.json').read_text(encoding='utf-8'))\n"
         "sens = json.loads((R / 'nfp_sensitivity.json').read_text(encoding='utf-8'))\n"
         "pbo = json.loads((R / 'pbo.json').read_text(encoding='utf-8'))\n"
         "post_hoc = json.loads((R / 'post_hoc_notes.json').read_text(encoding='utf-8'))\n"
         "print('frozen parameters:', json.dumps(meta['frozen'], indent=2))"),

    md("## 1. The clause table\n"
       "\n"
       "The five decision cells. `PASS` requires C1 **and** C2 **and** C3."),

    code("pct = ['mean', 'mean_gross', 'mean_pre', 'mean_post']\n"
         "d = summary.copy()\n"
         "for c in pct:\n"
         "    d[c] = (d[c] * 100).round(3)\n"
         "cols = ['cell', 'n', 'mean', 'mean_gross', 't', 'sharpe', 'dsr',\n"
         "        'n_pre', 'mean_pre', 't_pre', 'n_post', 'mean_post', 't_post',\n"
         "        'C1_t_pre_ge_2p5', 'C2_t_post_ge_2p5', 'C3_dsr_ge_0p95', 'PASS']\n"
         "view = d[cols].round(4)\n"
         "view.columns = ['cell', 'n', 'mean%', 'gross%', 't', 'SR/trade', 'DSR',\n"
         "                'n_pre', 'mean%_pre', 't_pre', 'n_post', 'mean%_post', 't_post',\n"
         "                'C1', 'C2', 'C3', 'PASS']\n"
         "view"),

    code("decision = summary[summary.cell.isin(\n"
         "    ['QTR_END', 'NFP_INCREMENTAL', 'WKND_FADE', 'HIGH52', 'EMA_ETH'])]\n"
         "print('cells passing all three clauses:', int(decision['PASS'].sum()), '/ 5')\n"
         "print('clauses fired in total:',\n"
         "      int(decision[['C1_t_pre_ge_2p5', 'C2_t_post_ge_2p5',\n"
         "                    'C3_dsr_ge_0p95']].sum().sum()), '/ 15')\n"
         "print('best DSR seen:', round(decision['dsr'].max(), 4), '(bar = 0.95)')\n"
         "print('best era t seen:',\n"
         "      round(max(decision['t_pre'].max(), decision['t_post'].max()), 3),\n"
         "      '(bar = 2.5)')"),

    md("## 2. Power — what each cell would have needed\n"
       "\n"
       "Descriptive, not a decision clause. `sr_needed_for_dsr95` is the per-trade\n"
       "Sharpe required to clear C3 at that cell's realised `n`; the mean columns are\n"
       "the per-trade mean required to clear C1/C2 at that era's realised `n` and sd.\n"
       "\n"
       "This is what separates a *well-powered* KILL (`WKND_FADE`) from a *low-power*\n"
       "one (`QTR_END`, `HIGH52`, `EMA_ETH` — positive point estimates, far too few\n"
       "observations for the bar to be reachable)."),

    code("p = summary[['cell', 'n', 'sharpe', 'sr_needed_for_dsr95',\n"
         "             'mean_needed_pre_for_t2p5', 'mean_pre',\n"
         "             'mean_needed_post_for_t2p5', 'mean_post']].copy()\n"
         "for c in ['mean_needed_pre_for_t2p5', 'mean_pre',\n"
         "          'mean_needed_post_for_t2p5', 'mean_post']:\n"
         "    p[c] = (p[c] * 100).round(3)\n"
         "p.columns = ['cell', 'n', 'SR realised', 'SR needed (C3)',\n"
         "             'mean% needed pre', 'mean% got pre',\n"
         "             'mean% needed post', 'mean% got post']\n"
         "p.round(3)"),

    md("## 3. NFP — the incremental-to-R4 question\n"
       "\n"
       "Every NFP date is a first Friday, so every one has day-of-month <= 7 and is\n"
       "already inside the live `JPLUS_R4_*_V2` Friday window (Fri 04:00→14:00 UTC,\n"
       "dom <= 14). The only slice R4 does not already own is **14:00→16:00**, and\n"
       "that is the decision-bearing cell."),

    code("print('NFP dates in sample:', sens['nfp_dates_in_sample'])\n"
         "print('all Fridays:', sens['nfp_all_fridays'])\n"
         "print('all day-of-month <= 14:', sens['nfp_all_dom_le_14'])\n"
         "print('=> every NFP date already an R4 V2 Friday fire:',\n"
         "      sens['nfp_all_covered_by_r4_v2_friday'])\n"
         "\n"
         "nfp = summary[summary.cell.str.startswith('NFP')][\n"
         "    ['cell', 'n', 'mean_gross', 'mean', 't', 'dsr']].copy()\n"
         "nfp['mean_gross'] = (nfp['mean_gross'] * 100).round(4)\n"
         "nfp['mean'] = (nfp['mean'] * 100).round(4)\n"
         "nfp.columns = ['window', 'n', 'gross%/fire', 'net%/fire', 't', 'DSR']\n"
         "nfp.round(4)"),

    code("rows = []\n"
         "for k in ['incremental_shutdown_excluded', 'standalone_shutdown_excluded',\n"
         "          'incremental_dsr_n_trials_6', 'standalone_dsr_n_trials_6']:\n"
         "    r = sens[k]\n"
         "    rows.append(dict(sensitivity=k, n=r['n'],\n"
         "                     mean_pct=round((r['mean'] or 0) * 100, 4),\n"
         "                     t=round(r['t'], 3), dsr=round(r['dsr'], 4)))\n"
         "print('shutdown dates dropped:', sens['dropped_dates'],\n"
         "      '->', sens['n_dropped_incremental'], 'observations removed')\n"
         "pd.DataFrame(rows)"),

    md("## 4. Weekend fade — the one well-powered result\n"
       "\n"
       "n = 349, net −0.590%/weekend, t = −2.92, and **gross** −0.410%, so cost is not\n"
       "what kills it. Both eras agree on the sign. The weekend move *persists* into\n"
       "Monday rather than reverting."),

    code("w = pd.read_csv(R / 'trades_wknd_fade.csv')\n"
         "w['gap'] = w['note'].str.replace('gap=', '', regex=False).astype(float)\n"
         "eq = w['net'].cumsum()\n"
         "ax = eq.plot(figsize=(11, 4), title='WKND_FADE cumulative net return '\n"
         "                                    '(sum of per-trade returns, 18 bp charged)')\n"
         "ax.axhline(0, color='k', lw=0.8)\n"
         "ax.set_xlabel('weekend #'); ax.set_ylabel('cumulative net (decimal)')\n"
         "print('n =', len(w), ' net mean %:', round(w['net'].mean() * 100, 4),\n"
         "      ' gross mean %:', round(w['gross'].mean() * 100, 4),\n"
         "      ' final cum %:', round(eq.iloc[-1] * 100, 1))"),

    code("import numpy as np\n"
         "b = pd.cut(w['gap'].abs(), [0, 0.005, 0.01, 0.02, 0.05, 1.0])\n"
         "g = w.groupby(b, observed=True)['net'].agg(['count', 'mean'])\n"
         "g['mean%'] = (g['mean'] * 100).round(3)\n"
         "print('fade net return by |weekend gap| bucket '\n"
         "      '(descriptive only — no threshold was pre-registered, and none is proposed)')\n"
         "g[['count', 'mean%']]"),

    md("## 5. Post-hoc direction flips — NOT part of the pre-registration\n"
       "\n"
       "Directions were frozen in `README.md` before running. These rows exist only so\n"
       "the magnitude of the implied opposite direction is visible, and are generated\n"
       "uniformly for all five cells rather than cherry-picked. **They change no\n"
       "verdict.** Note the flip is not a sign change of the net return — the 18 bp is\n"
       "paid whichever way you face, so `net_flip = -gross - cost`."),

    code("print(post_hoc['warning'])\n"
         "rows = []\n"
         "for k, r in post_hoc['flipped'].items():\n"
         "    rows.append(dict(flipped_cell=k, n=r['n'],\n"
         "                     mean_pct=round((r['mean'] or 0) * 100, 4),\n"
         "                     t=round(r['t'], 3),\n"
         "                     t_pre=round(r['t_pre'], 3), t_post=round(r['t_post'], 3),\n"
         "                     dsr=round(r['dsr'], 4), PASS=r['PASS']))\n"
         "pd.DataFrame(rows)"),

    md("## 6. PBO and bootstrap\n"
       "\n"
       "CSCV PBO across the five cells on a common monthly net-return grid. With only\n"
       "5 columns the statistic is coarse; a mid-range value across five nulls is what\n"
       "you would expect and is not itself evidence of overfitting — there was no\n"
       "search to overfit."),

    code("print('PBO = %.3f over %d combinations (s=%d, %d months, cells=%s)'\n"
         "      % (pbo['pbo'], pbo['n_combos'], pbo['s'], len(pbo['months']),\n"
         "         ', '.join(pbo['cells'])))\n"
         "\n"
         "boot = summary[['cell', 'n', 'sharpe', 'boot_sr_p05',\n"
         "                'boot_sr_p50', 'boot_sr_p95']].round(4)\n"
         "boot.columns = ['cell', 'n', 'SR point', 'SR p05', 'SR p50', 'SR p95']\n"
         "boot['CI excludes 0'] = (summary['boot_sr_p05'] > 0) | (summary['boot_sr_p95'] < 0)\n"
         "boot"),

    code("grid = pd.read_csv(R / 'monthly_grid.csv', index_col='month')\n"
         "ax = grid.cumsum().plot(figsize=(11, 5),\n"
         "                        title='Cumulative monthly net return by cell '\n"
         "                              '(flat months = 0, 18 bp charged)')\n"
         "ax.axhline(0, color='k', lw=0.8)\n"
         "ax.set_ylabel('cumulative net (decimal)')\n"
         "grid.cumsum().tail(1).round(4)"),

    md("## 7. Sample diagnostics\n"
       "\n"
       "Zero observations were skipped for missing bars in any cell, so no result is a\n"
       "data-availability artefact."),

    code("print('skipped observations:', json.dumps(meta['skipped_observations'], indent=2))\n"
         "print()\n"
         "print('HIGH52:', json.dumps(meta['high52_info'], indent=2))\n"
         "print()\n"
         "print('EMA_ETH:', json.dumps(meta['ema_eth_info'], indent=2))"),

    code("pd.read_csv(R / 'trades_ema_eth.csv')[\n"
         "    ['entry_date', 'exit_ts', 'side', 'p_entry', 'p_exit', 'net']].round(4)"),

    md("## 8. Verdict\n"
       "\n"
       "**All five cells KILL.** 0 of 15 clauses fired.\n"
       "\n"
       "* `WKND_FADE` is a **well-powered** KILL — significantly the wrong sign, gross\n"
       "  as well as net. The weekend-gap-fade idea should be considered closed.\n"
       "* `NFP_INCREMENTAL` is dead **before costs** (gross −1.2 bp over 81 events).\n"
       "  NFP adds nothing to R4, and every NFP date is already an R4 V2 Friday fire.\n"
       "* `QTR_END`, `HIGH52`, `EMA_ETH` are **low-power** KILLs: positive point\n"
       "  estimates, but 26 / 38 / 11 observations against a double-era t >= 2.5 bar\n"
       "  that was close to unpassable by construction. \"Not demonstrated\", not\n"
       "  \"demonstrated absent\".\n"
       "\n"
       "**Build nothing.** See `findings.md` for the full discussion, the declared\n"
       "deviation, and the data limitations that bound each number."),
]

nb = nbf.v4.new_notebook(cells=cells)
nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python",
                             "name": "python3"}
out = HERE / "calendar_cells.ipynb"
nbf.write(nb, str(out))
print("wrote", out, "with", len(cells), "cells")
