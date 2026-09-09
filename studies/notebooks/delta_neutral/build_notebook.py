"""Build the viewer notebook (system Python: needs nbformat; the venv has none).
Run: C:/Python/Python313/python.exe studies/notebooks/delta_neutral/build_notebook.py
"""
from pathlib import Path
import nbformat as nbf

here = Path(__file__).resolve().parent
nb = nbf.v4.new_notebook()
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell
nb.cells = [
    md("# Delta-neutral funding studies — viewer (2026-09-08)\n\n"
       "Two pre-registered studies sharing a funding data spine. Design frozen in "
       "[README.md](README.md) before any outcome number; verdicts in [findings.md](findings.md).\n\n"
       "* **(i) cross-venue funding dispersion** — `dispersion.py` → **KILL** on the powered "
       "2-venue arm, **INCONCLUSIVE on power** on the 3-venue arm.\n"
       "* **(iii) carry-crash sizing** — `carry_sizing.py` → **KILL** in all 18 grid cells.\n\n"
       "Re-run cells at the bottom. Both scripts are read-only on `prod.db`."),
    code("import json, pandas as pd, numpy as np, matplotlib.pyplot as plt\n"
         "pd.set_option('display.width', 220); pd.set_option('display.max_columns', 50)\n"
         "D = json.load(open('results/dispersion_summary.json'))\n"
         "C = json.load(open('results/carry_summary.json'))\n"
         "G = pd.read_csv('results/dispersion_strategy_grid.csv')\n"
         "CG = pd.read_csv('results/carry_grid.csv')\n"
         "print('(i) ', D['overall_verdict'])\n"
         "print('(iii)', C['verdict'])"),

    md("## (i) Clause tables\n\n"
       "Arm P = the rule exactly as specified (Binance + OKX + Bybit). Arm S = the same rule on "
       "the 2-venue set that has history. `fired=True` means the clause triggered."),
    code("rows = []\n"
         "for arm, cl in D['clauses'].items():\n"
         "    for k in ('I-0','I-1','I-2','I-3','I-4'):\n"
         "        c = cl[k]\n"
         "        rows.append({'arm': arm, 'clause': k, 'value': c['value'],\n"
         "                     'threshold': c['threshold'], 'fired': c['fired'], 'desc': c['desc']})\n"
         "display(pd.DataFrame(rows))"),

    md("## (i) The dispersion distribution\n\n"
       "The 3-venue arm never reaches the 3 bp threshold at all — its maximum spread over "
       "278 settlements is 1.56 bp."),
    code("dd = pd.DataFrame(D['dispersion']).T\n"
         "cols = ['n','first','last','mean_spread_bp','sd_spread_bp','p50','p90','p95','p99','max_spread_bp',\n"
         "        'n_gt_1bp','n_gt_2bp','n_gt_3bp','n_gt_5bp','n_gt_10bp',\n"
         "        'top5pct_days_share_of_3bp_exceedances','stress_concentrated']\n"
         "display(dd[cols])"),
    code("fig, ax = plt.subplots(1, 2, figsize=(12, 4))\n"
         "for k, arm in enumerate(('P', 'S')):\n"
         "    s = pd.read_csv(f'results/dispersion_series_{arm}.csv')\n"
         "    ax[k].hist(s.spread_bp.clip(upper=12), bins=60)\n"
         "    ax[k].axvline(3, color='r', ls='--', label='3 bp rule threshold')\n"
         "    ax[k].set_yscale('log'); ax[k].set_xlabel('spread (bp, clipped at 12)')\n"
         "    ax[k].set_title(f\"arm {arm}: n={len(s)}, max={s.spread_bp.max():.2f} bp\"); ax[k].legend()\n"
         "plt.tight_layout(); plt.show()"),

    md("## (i) The era split — why the historical dispersion is suspect\n\n"
       "Every one of the 497 exceedances above 3 bp in 6.5 years falls before the 2026-04-13 "
       "cadence cutover, i.e. in the era where the Binance leg is a predicted-rate hourly bar "
       "rather than a recorded settlement. In the all-realised 3-venue window no venue pair "
       "ever exceeds 1.6 bp."),
    code("display(pd.DataFrame(D['pairwise_venue_diff']).T)\n"
         "display(pd.DataFrame(D['era_split_arm_S']).T)"),
    code("s = pd.read_csv('results/dispersion_series_S.csv')\n"
         "s['t'] = pd.to_datetime(s.utc)\n"
         "fig, ax = plt.subplots(figsize=(12, 4))\n"
         "ax.plot(s.t, s.spread_bp, lw=0.4)\n"
         "ax.axhline(3, color='r', ls='--', label='3 bp'); ax.set_yscale('symlog')\n"
         "ax.axvline(pd.Timestamp('2026-04-13'), color='k', lw=1.2, label='cadence cutover')\n"
         "ax.set_ylabel('Binance−Bybit spread (bp)'); ax.legend(); plt.show()"),

    md("## (i) Strategy grid — causal vs contemporaneous, three cost models\n\n"
       "`causal=True` is the tradeable version (decide at t, collect t+1). Even at zero cost the "
       "causal 3 bp rule earns 2.73 %/yr, under both the 5 % bar and the Carry benchmark."),
    code("view = G[(G.arm == 'S')].pivot_table(index=['threshold_bp','causal'], columns='cost_mode',\n"
         "                                     values='ann_return_pct')\n"
         "display(view.round(2))\n"
         "print('\\nprimary cell, all arms:')\n"
         "display(G[(G.threshold_bp == 3) & (G.causal) & (G.cost_mode == 'pair_change')]\n"
         "        [['arm','n_on','on_rate','rotation_rate_on','mean_gross_on_bp','mean_net_bp',\n"
         "          'ann_return_pct','carry_bench_ann_pct']].round(4))"),

    md("## (i) Basis leakage — the 'delta-neutral' assumption, measured\n\n"
       "sd of the 8-hourly change in log(P_B / P_A). In the 3-venue window this price noise is "
       "2.3× the entire mean funding spread being harvested."),
    code("display(pd.DataFrame(D['basis_leakage']).T)"),

    md("---\n# (iii) Carry-crash sizing\n\n"
       "The study's daily funding series was asserted byte-equal to the live "
       "`strategies.support.funding.daily_sums_pct` before anything was scored."),
    code("print('byte-equivalence: %d days, max |diff| = %g' % (\n"
         "    C['byte_equivalence']['n_days'], C['byte_equivalence']['max_abs_diff_daily_pct']))\n"
         "display(pd.DataFrame([{'clause': k, **{kk: v[kk] for kk in ('value','threshold','fired')}, 'desc': v['desc']}\n"
         "                      for k, v in C['clauses'].items()]))"),
    code("display(pd.DataFrame({'baseline': C['baseline'], 'with_rule': C['with_rule'],\n"
         "                      'baseline_eligible': C['baseline_elig'],\n"
         "                      'with_rule_eligible': C['with_rule_elig']}).T.round(4))"),

    md("### Equity curves\n\n"
       "Both arms hold identical positions on identical days and differ only in size. The rule "
       "keys on the UPPER tail of funding; the drawdown comes from the LOWER tail, so max "
       "drawdown is unchanged to the basis point while the mean falls."),
    code("d = pd.read_csv('results/carry_daily.csv', parse_dates=['date'])\n"
         "fig, ax = plt.subplots(2, 1, figsize=(12, 7), sharex=True,\n"
         "                       gridspec_kw={'height_ratios': [3, 1]})\n"
         "ax[0].plot(d.date, d.ret_base_pct.cumsum(), label='baseline')\n"
         "ax[0].plot(d.date, d.ret_rule_pct.cumsum(), label='60d z > expanding p95 -> 50% size')\n"
         "ax[0].axvline(pd.Timestamp(C['window']['rule_eligible_from']), color='k', lw=0.8,\n"
         "              ls=':', label='rule eligible from')\n"
         "ax[0].set_ylabel('cumulative % of capital'); ax[0].legend(); ax[0].set_title(\n"
         "    'S-078 Carry replay: %d days, %d trades' % (C['window']['n_days'], C['n_trades_baseline']))\n"
         "ax[1].fill_between(d.date, d.size_mult, 1.0, step='mid', alpha=0.6)\n"
         "ax[1].set_ylabel('size mult'); ax[1].set_ylim(0.4, 1.05)\n"
         "plt.tight_layout(); plt.show()"),

    md("### The 18-cell grid — MAR is worse in every single cell"),
    code("display(CG[['window','pctile','resize_cost','n_fire_days','sharpe_uplift','mar','mar_base',\n"
         "            'ann_return_pct','ann_return_base_pct','max_dd_pct','max_dd_base_pct']].round(4))\n"
         "print('cells with MAR <= baseline: %d / %d' % ((CG.mar <= CG.mar_base).sum(), len(CG)))\n"
         "print('cells with negative Sharpe uplift: %d / %d' % ((CG.sharpe_uplift < 0).sum(), len(CG)))"),

    md("### Where the rule fires\n\n"
       "The z-score is the trailing 60-day z of daily funding; the threshold is its own expanding "
       "95th percentile (252 z-observations of warm-up). Firing days are the best-paid days."),
    code("z = pd.read_csv('results/carry_zscore.csv', parse_dates=['date'])\n"
         "fig, ax = plt.subplots(figsize=(12, 4))\n"
         "ax.plot(z.date, z.z60, lw=0.5, label='60d z of daily funding')\n"
         "ax.plot(z.date, z.expanding_p95, color='r', lw=1.0, label='expanding p95')\n"
         "fire = z[z.fires_next_day == 1]\n"
         "ax.scatter(fire.date, fire.z60, s=8, color='orange', zorder=3,\n"
         "           label='fires next day (n=%d)' % len(fire))\n"
         "ax.legend(); plt.show()\n"
         "print('mean daily funding on firing days   %.5f %%' % z[z.fires_next_day == 1].daily_funding_pct.mean())\n"
         "print('mean daily funding on all other days %.5f %%' % z[z.fires_next_day == 0].daily_funding_pct.mean())"),

    md("### Bootstrap and DSR"),
    code("print('paired block bootstrap of the Sharpe DIFFERENCE (with - without):')\n"
         "display(pd.DataFrame({'full window': C['bootstrap']['sharpe_diff_full'],\n"
         "                      'rule-eligible': C['bootstrap']['sharpe_diff_eligible']}).T)\n"
         "print('\\nblock bootstrap of each arm:')\n"
         "display(pd.DataFrame({'with_rule': C['bootstrap']['with_rule'],\n"
         "                      'baseline': C['bootstrap']['baseline']}).T)\n"
         "print('\\ndeflated Sharpe (uninformative here - the series is still carry):')\n"
         "display(pd.DataFrame({k: v for k, v in C['dsr'].items() if v}).T[['sr_ann','n_trials','dsr']])"),

    md("## Re-run (optional; read-only on prod.db, ~1 min each)"),
    code("# %run carry_sizing.py\n# %run dispersion.py\n# %run _write_findings.py"),
]
nbf.write(nb, here / "delta_neutral.ipynb")
print("wrote", here / "delta_neutral.ipynb")
