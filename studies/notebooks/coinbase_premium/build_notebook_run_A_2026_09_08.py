"""Build the viewer notebook (system Python: needs nbformat; the venv has none).
Run: C:/Python/Python313/python.exe studies/notebooks/coinbase_premium/build_notebook.py
"""
from pathlib import Path

import nbformat as nbf

here = Path(__file__).resolve().parent
nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell

nb.cells = [
    md("# S-Coinbase-premium — viewer (2026-09-08)\n\n"
       "**STATUS: CONCLUDED KILL per pre-registration.**\n\n"
       "Frozen rule: premium = (Coinbase close − Binance close) / Binance close on the hourly "
       "grid; 14-day (336-bar) z-score; fire at z ≥ +2.0; long at the firing bar's close; hold "
       "48h; stop at 2×ATR14 below entry; 18 bp round trip; one fire per 48 hours.\n\n"
       "Pre-registration in [README.md](README.md); verdict and discussion in "
       "[findings.md](findings.md). All artefacts under `results/` are produced by "
       "`premium_study.py` (every decision number) and `descriptive_addendum.py` "
       "(post-primary descriptives, non-decision-bearing). Both are read-only on `prod.db`.\n\n"
       "**Structure that matters:** BTC is a carry-over — this exact cell "
       "`(thr 2.0, hold 48h, lookback 336h)` was the training-set argmax of a 12-cell grid "
       "already run on BTC in the predecessor repo. **ETH was never tested on this rule "
       "anywhere, so ETH is the genuine out-of-sample test** and carries the verdict."),

    code("import json\n"
         "import pandas as pd\n"
         "import matplotlib.pyplot as plt\n"
         "pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)\n"
         "R = 'results/'\n"
         "CL = json.load(open(R + 'clauses.json'))\n"
         "HL = json.load(open(R + 'headline.json'))\n"
         "ERA = json.load(open(R + 'era_split.json'))\n"
         "CORR = json.load(open(R + 'correlation.json'))\n"
         "SENS = json.load(open(R + 'sensitivities.json'))\n"
         "PROV = json.load(open(R + 'provenance.json'))\n"
         "ADD = json.load(open(R + 'descriptive_addendum.json'))\n"
         "PY = pd.read_csv(R + 'premium_by_year.csv')\n"
         "TY = pd.read_csv(R + 'trades_by_year.csv')\n"
         "DEC = pd.read_csv(R + 'z_decile_table.csv')\n"
         "BUC = pd.read_csv(R + 'z_bucket_table.csv')\n"
         "TR = {a: pd.read_csv(R + f'trades_{a}.csv') for a in ('BTC', 'ETH')}\n"
         "print(CL['verdict'])"),

    md("## 1. The clause table — the whole decision\n\n"
       "`C1` is the genuine out-of-sample clause (ETH). `C2` is the BTC modern-era check."),

    code("rows = []\n"
         "for k in ('C0', 'C1', 'C2'):\n"
         "    c = CL[k]\n"
         "    rows.append({'clause': k, 'text': c['clause'], 'fired': c['fired'],\n"
         "                 'detail': {kk: vv for kk, vv in c.items() if kk not in ('clause', 'fired')}})\n"
         "for r in rows:\n"
         "    print(f\"{r['clause']}  fired={r['fired']}\\n    {r['text']}\\n    {r['detail']}\\n\")\n"
         "print('VERDICT:', CL['verdict'])"),

    md("### C1 fired by 0.005 R — read this before anything else\n\n"
       "The ETH 95% CI lower bound is −0.0052. Had the pre-registration named a **90%** "
       "interval, ETH's CI would have been [+0.028, +0.417] and C1 would not have fired. "
       "The verdict does not actually rest on that hair: ETH's whole positive mean is "
       "pre-2024, so C2 would have killed it regardless (see cell 4)."),

    code("b = HL['ETH']['bootstrap_mean_R']\n"
         "print('ETH mean R      ', round(b['mean_point'], 4))\n"
         "print('  CI95          [%.4f, %.4f]  -> C1 fires on ci_lo <= 0' % (b['ci95_lo'], b['ci95_hi']))\n"
         "print('  CI90          [%.4f, %.4f]  -> would NOT have fired' % (b['ci90_lo'], b['ci90_hi']))\n"
         "print('  P(mean R > 0) ', b['p_gt_0'])\n"
         "print('  DSR n_trials=1', round(HL['ETH']['dsr_by_n_trials']['1']['dsr'], 4))"),

    md("## 2. Headline, both assets"),

    code("keys = ['n', 'trades_per_year', 'mean_R', 'median_R', 'sd_R', 'total_R', 'max_dd_R',\n"
         "        'win_rate', 'sl_rate', 'mean_net_ret_pct', 'mean_risk_frac_pct']\n"
         "h = pd.DataFrame({a: {k: HL[a][k] for k in keys} for a in ('BTC', 'ETH')}).T\n"
         "for a in ('BTC', 'ETH'):\n"
         "    b = HL[a]['bootstrap_mean_R']; s = HL[a]['bootstrap_sharpe_R']\n"
         "    h.loc[a, 'meanR_ci95_lo'] = b['ci95_lo']; h.loc[a, 'meanR_ci95_hi'] = b['ci95_hi']\n"
         "    h.loc[a, 'P(meanR>0)'] = b['p_gt_0']\n"
         "    h.loc[a, 'sharpe_pertrade'] = s['sr_point']; h.loc[a, 'sharpe_ann'] = s['sr_point_ann']\n"
         "display(h.round(4))\n"
         "print('\\nDeflated Sharpe by trial count (BTC primary = 12; ETH = 1):')\n"
         "display(pd.DataFrame({a: {k: v['dsr'] for k, v in HL[a]['dsr_by_n_trials'].items()}\n"
         "                      for a in ('BTC', 'ETH')}).round(4))"),

    md("## 3. The premium itself — mean and sd by year (basis points)\n\n"
       "Two decays: dispersion collapses ~3.5× (16.6 bp sd in 2020 → 4.7 bp in 2025) and the "
       "level flips negative (−6.0 bp in 2026). A z-score is scale-free, so the rule keeps "
       "firing ~45×/year on progressively smaller real dislocations while the 18 bp cost "
       "does not shrink."),

    code("p = PY.pivot(index='year', columns='asset', values=['mean_bp', 'sd_bp'])\n"
         "display(p.round(2))\n"
         "fig, ax = plt.subplots(1, 2, figsize=(12, 4))\n"
         "for a in ('BTC', 'ETH'):\n"
         "    d = PY[PY.asset == a]\n"
         "    ax[0].plot(d.year, d.mean_bp, marker='o', label=a)\n"
         "    ax[1].plot(d.year, d.sd_bp, marker='o', label=a)\n"
         "ax[0].axhline(0, color='k', lw=0.5); ax[0].set_title('Coinbase premium: mean by year (bp)')\n"
         "ax[1].set_title('Coinbase premium: sd by year (bp)'); ax[1].set_ylim(bottom=0)\n"
         "for x in ax: x.legend(); x.grid(alpha=0.3)\n"
         "plt.tight_layout(); plt.show()"),

    md("## 4. Decay: era split and per-year trade R\n\n"
       "ETH's entire positive mean is pre-2024. This — not the 0.005 R knife edge — is why "
       "the rule dies."),

    code("rows = []\n"
         "for a in ('BTC', 'ETH'):\n"
         "    e = ERA[a]\n"
         "    for era in ('pre_etf', 'post_etf'):\n"
         "        rows.append({'asset': a, 'cutoff': e['cutoff'], 'era': era,\n"
         "                     'premium_mean_bp': e['premium'][era]['mean_bp'],\n"
         "                     'premium_sd_bp': e['premium'][era]['sd_bp'],\n"
         "                     'n_trades': e['trade_R'][era]['n'],\n"
         "                     'mean_R': e['trade_R'][era]['mean_R'],\n"
         "                     'total_R': e['trade_R'][era]['total_R'],\n"
         "                     'ci95_lo': e['trade_R'][era]['ci95_lo'],\n"
         "                     'ci95_hi': e['trade_R'][era]['ci95_hi']})\n"
         "display(pd.DataFrame(rows).round(4))\n"
         "print('\\nCalendar cuts (descriptive addendum):')\n"
         "display(pd.DataFrame({a: {k: v['mean_R'] for k, v in ADD[a]['trades_calendar_cuts'].items()}\n"
         "                      for a in ('BTC', 'ETH')}).round(4))"),

    code("display(TY.pivot(index='year', columns='asset', values=['n', 'mean_R', 'total_R']).round(3))\n"
         "fig, ax = plt.subplots(figsize=(11, 4))\n"
         "w = 0.38\n"
         "yrs = sorted(TY.year.unique())\n"
         "for k, a in enumerate(('BTC', 'ETH')):\n"
         "    d = TY[TY.asset == a].set_index('year').reindex(yrs)\n"
         "    ax.bar([y + (k - 0.5) * w for y in range(len(yrs))], d.mean_R.values, width=w, label=a)\n"
         "ax.set_xticks(range(len(yrs))); ax.set_xticklabels(yrs)\n"
         "ax.axhline(0, color='k', lw=0.8); ax.set_ylabel('mean R per trade'); ax.legend()\n"
         "ax.set_title('Mean R per trade by year — positive 2020-2023, negative after')\n"
         "ax.grid(alpha=0.3, axis='y'); plt.show()"),

    md("## 5. Equity in R — where the money was made"),

    code("fig, ax = plt.subplots(figsize=(11, 5))\n"
         "for a in ('BTC', 'ETH'):\n"
         "    t = TR[a]\n"
         "    ax.plot(pd.to_datetime(t.entry_dt), t.R.cumsum().values, label=f'{a} (n={len(t)})')\n"
         "for d, lab in (('2024-01-11', 'BTC spot ETF'), ('2024-07-23', 'ETH spot ETF')):\n"
         "    ax.axvline(pd.Timestamp(d), color='k', ls='--', lw=0.8)\n"
         "    ax.text(pd.Timestamp(d), 2, ' ' + lab, rotation=90, va='bottom', fontsize=8)\n"
         "ax.axhline(0, color='k', lw=0.5); ax.set_ylabel('cumulative R'); ax.legend()\n"
         "ax.set_title('Coinbase premium z>=+2.0, 48h hold, 2xATR stop, 18bp RT')\n"
         "ax.grid(alpha=0.3); plt.show()"),

    md("## 6. No red flag: the z / forward-48h relationship is monotone\n\n"
       "The pre-registration warned in advance that a signal working only at the +2 cut with "
       "no monotone gradient beneath it would be a red flag. It is monotone on both assets, "
       "in both eras. The signal carries weak but real information; the **trade construction** "
       "is what loses."),

    code("display(DEC.pivot(index='decile', columns='asset',\n"
         "                 values=['z_mid', 'n', 'mean_fwd48_bp', 'win_rate']).round(2))\n"
         "fig, ax = plt.subplots(1, 2, figsize=(12, 4))\n"
         "for a in ('BTC', 'ETH'):\n"
         "    d = DEC[DEC.asset == a]\n"
         "    ax[0].plot(d.decile, d.mean_fwd48_bp, marker='o', label=a)\n"
         "    b = BUC[BUC.asset == a]\n"
         "    ax[1].plot(range(len(b)), b.mean_fwd48_bp, marker='s', label=a)\n"
         "ax[1].set_xticks(range(len(BUC[BUC.asset == 'BTC'])))\n"
         "ax[1].set_xticklabels(BUC[BUC.asset == 'BTC'].bucket, rotation=45)\n"
         "ax[0].set_xlabel('z decile'); ax[0].set_ylabel('mean forward 48h return (bp)')\n"
         "ax[0].set_title('Monotone in z — no threshold applied'); ax[1].set_title('By z bucket')\n"
         "for x in ax: x.axhline(0, color='k', lw=0.5); x.legend(); x.grid(alpha=0.3)\n"
         "plt.tight_layout(); plt.show()\n"
         "print('correlations (no threshold):')\n"
         "display(pd.DataFrame(CORR).T.round(4))"),

    md("### 6b. But the *excess over drift* collapsed, and the stop eats the rest\n\n"
       "The decile table sits on a bull-market drift, so the number that matters is the "
       "excess of the z ≥ 2 bucket over the unconditional forward return."),

    code("rows = []\n"
         "for a in ('BTC', 'ETH'):\n"
         "    u = ADD[a]['uncond_fwd48_bp']; r = ADD[a]['raw_fwd48_when_z_ge_2_bp']\n"
         "    for era in ('pre_etf', 'post_etf'):\n"
         "        rows.append({'asset': a, 'era': era, 'uncond_bp': u[era],\n"
         "                     'z>=2_bp': r[era]['mean_bp'], 'n_bars': r[era]['n'],\n"
         "                     'excess_bp': r[era]['mean_bp'] - u[era]})\n"
         "display(pd.DataFrame(rows).round(1))\n"
         "print('\\nWhat the frozen 2xATR stop costs (same fires, stop removed):')\n"
         "display(pd.DataFrame({a: ADD[a]['no_stop_same_fires'] for a in ('BTC', 'ETH')}).T)\n"
         "print('\\nNOTE: an observation, NOT a recommendation. Removing the stop is an')\n"
         "print('unregistered post-hoc change to a rule already selected on BTC; it would')\n"
         "print('need its own pre-registration and its own untouched asset.')"),

    md("## 7. Sensitivities and data provenance\n\n"
       "Computed after the primary numbers were fixed; none was used to choose anything."),

    code("display(pd.DataFrame({(a, k): v for a in ('BTC', 'ETH')\n"
         "                      for k, v in SENS[a].items()}).T.round(4))\n"
         "print('\\nprovenance (verified before the pre-registration was written):')\n"
         "display(pd.DataFrame(PROV).T)\n"
         "print('\\nETH hourly completeness:', ADD['ETH'].get('hourly_completeness'))"),

    md("## 8. Re-run cells\n\n"
       "Read-only on `prod.db`. The primary script takes ~1 min (it loads 3.5M `eth_1m` rows "
       "to build the Binance ETH spot hourly leg)."),

    code("# !cd ../../.. && python studies/notebooks/coinbase_premium/premium_study.py"),
    code("# !cd ../../.. && PYTHONPATH=.;studies/notebooks/coinbase_premium python studies/notebooks/coinbase_premium/descriptive_addendum.py"),
]

out = here / "coinbase_premium.ipynb"
nbf.write(nb, str(out))
print("wrote", out)
