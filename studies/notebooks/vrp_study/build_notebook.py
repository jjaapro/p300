"""Build the viewer notebook (system Python: needs nbformat; the venv has none).
Run: C:/Python/Python313/python.exe studies/notebooks/vrp_study/build_notebook.py
"""
from pathlib import Path
import nbformat as nbf

here = Path(__file__).resolve().parent
nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb.cells = [
    md("# VRP harvest (short ±10% BTC strangle, 7-day hedge) — viewer (2026-09-07)\n\n"
       "Pre-registered in [README.md](README.md); verdict in [findings.md](findings.md). "
       "Two runs of `run_vrp.py` are on disk: the pre-registered mark-settled cell "
       "(`results/vrp_*.{csv,json}`) and the spec-faithful intrinsic-settlement sensitivity "
       "(`results/*_intrinsic.*`). Re-run cells are at the bottom (read-only on `prod.db`, ≈ 1 min each)."),
    code("import json, pandas as pd, matplotlib.pyplot as plt\n"
         "pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)\n"
         "S = json.load(open('results/vrp_summary.json')); SI = json.load(open('results/vrp_summary_intrinsic.json'))\n"
         "M = pd.read_csv('results/vrp_expiries.csv'); M = M[M.status == 'ok']\n"
         "I = pd.read_csv('results/vrp_expiries_intrinsic.csv'); I = I[I.status == 'ok']\n"
         "T = pd.read_csv('results/trader_canonical_expiries.csv'); T = T[T.status == 'ok']"),
    md("## Headline: pre-registered mechanics vs spec-faithful mechanics"),
    code("rows = []\n"
         "for label, s in (('mark (pre-registered)', S), ('intrinsic @08:00 (sensitivity)', SI)):\n"
         "    for split in ('TRAIN', 'TEST', 'TRAIN+TEST'):\n"
         "        st = s['stats'][split]\n"
         "        rows.append({'mode': label, 'split': split, **{k: st.get(k) for k in ('n', 'mean_pct', 'sd_pct', 'sharpe_ann', 'win_pct', 'worst_pct')}})\n"
         "display(pd.DataFrame(rows).round(3))\n"
         "print('verdicts:', S['verdict'], '|', SI['verdict'])"),
    md("## Deflated Sharpe by trial count"),
    code("d = pd.DataFrame(S['dsr']).set_index('n_trials')[['dsr']].rename(columns={'dsr': 'mark'})\n"
         "d['intrinsic'] = pd.DataFrame(SI['dsr']).set_index('n_trials')['dsr']\n"
         "display(d.round(3))"),
    md("## Parity against the trader's own run (its DB, its code)\n\n"
       "Expiry-by-expiry difference; only three TEST expiries differ (USDC-linear picks upstream)."),
    code("j = T.set_index('expiry')[['split', 'strike_call', 'strike_put', 'pnl_pct']].join(\n"
         "    M.set_index('expiry')[['strike_call', 'strike_put', 'pnl_pct']], lsuffix='_trader', rsuffix='_p300')\n"
         "j['diff'] = j.pnl_pct_p300 - j.pnl_pct_trader\n"
         "print('expiries compared:', len(j), ' max |diff| on identical picks:', j[j['diff'].abs() < 0.01]['diff'].abs().max())\n"
         "display(j[j['diff'].abs() >= 0.01].round(3))"),
    md("## Cumulative P&L per expiry (both modes)"),
    code("fig, ax = plt.subplots(figsize=(11, 5))\n"
         "for df, label in ((M, 'mark (trader mechanics), n=%d' % len(M)), (I, 'intrinsic @08:00 (spec strikes), n=%d' % len(I))):\n"
         "    x = pd.to_datetime(df.expiry); ax.plot(x, df.pnl_pct.cumsum().values, marker='.', label=label)\n"
         "ax.axhline(0, color='k', lw=0.5); ax.set_ylabel('cumulative % of spot'); ax.legend(); ax.set_title('Short strangle, T-21, 7d hedge')\n"
         "plt.show()"),
    md("## Strike drift: what the mark-mode picker actually sold\n\n"
       "Call offset = K_call / spot − 1, put offset = 1 − K_put / spot at entry."),
    code("fig, ax = plt.subplots(1, 2, figsize=(11, 4))\n"
         "for k, (df, label) in enumerate(((M, 'mark mode'), (I, 'spec strikes'))):\n"
         "    ax[k].hist(df.strike_call / df.spot_entry - 1, bins=20, alpha=0.6, label='call offset')\n"
         "    ax[k].hist(1 - df.strike_put / df.spot_entry, bins=20, alpha=0.6, label='put offset')\n"
         "    ax[k].axvline(0.10, color='k', ls='--'); ax[k].set_title(label); ax[k].legend()\n"
         "plt.show()\n"
         "extra = I[~I.expiry.isin(M.expiry)]\n"
         "print('expiries absent from the mark-mode sample (no expiry-day rows at all):')\n"
         "display(extra[['expiry', 'split', 'spot_entry', 'strike_call', 'strike_put', 'raw_premium_pct', 'terminal', 'pnl_pct']].round(2))"),
    md("## DVOL − forward 30-day realised vol (is the premium still there?)"),
    code("display(pd.DataFrame(S['dvol_premium']['yearly']).T.round(2))\n"
         "print('latest', S['dvol_premium']['latest_date'], 'DVOL', S['dvol_premium']['latest_dvol'], 'trailing RV30', round(S['dvol_premium']['trailing_rv30'], 1))\n"
         "p = pd.read_csv('results/vrp_dvol_premium.csv'); p['month'] = pd.to_datetime(p.month)\n"
         "ax = p.plot(x='month', y='dvol_minus_fwd_rv30', figsize=(11, 3.5), legend=False, title='monthly mean DVOL − forward RV30 (vol points)')\n"
         "ax.axhline(0, color='k', lw=0.5); plt.show()"),
    md("## OOS accrual status"),
    code("print(S['oos'])"),
    md("## Re-run (optional)"),
    code("# %run run_vrp.py\n# %run run_vrp.py --terminal intrinsic"),
]
nbf.write(nb, here / "vrp_study.ipynb")
print("wrote", here / "vrp_study.ipynb")
