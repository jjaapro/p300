#!/usr/bin/env python3
"""Assemble basis_carry.ipynb (viewer notebook with %run cells).

Run with the SYSTEM Python (the repo venv has no nbformat):
    C:/Python/Python313/python.exe studies/notebooks/basis_carry/build_notebook.py [--execute]

--execute runs the notebook through nbclient on the repo venv's kernel
(venv/Scripts/python -m ipykernel_launcher) without installing a kernelspec, so
the %run cells use the repo's own interpreter. Without the flag the notebook is
written unexecuted; open it with the venv kernel and run all.
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
# S-DN(ii) — Cash-and-carry basis: Binance perp vs quarterly future

Pre-registration: [README.md](README.md) — hypothesis, frozen rules, decision clauses,
trial count and priors, written and saved **2026-09-08 before any outcome number
existed**. Write-up and clause table: [findings.md](findings.md).

**Headline.** The literal pre-registered verdict is *INCONCLUSIVE (data)* because gate
**D3** fired (15m→1h exact-match 98.80% against a 99.9% threshold). The economics were
nevertheless computed as a recorded deviation and are a **KILL**: net −7.14% annualised
on deployed capital, bootstrap 95% CI [−12.22%, −2.58%], DSR 2.0e−08 at 12 trials.
The convergence is real (+11.72% gross) — the perp funding you pay to hold the hedge
(−17.67%) is larger than it. Fees are only −1.19% of the gap.

Sibling studies S-DN(i) and S-DN(iii) live in
[`studies/notebooks/delta_neutral/`](../delta_neutral/) and are owned by another agent;
this folder is split off so two agents do not write the same directory.

**Execution reality:** p300 has no delivery-futures adapter. Acting on this trade would
be a new venue integration, not a new sleeve. No sleeve and no bot was created.

Pipeline (every cell is read-only on `prod.db`; the whole notebook is ~2 minutes):

1. `basis_carry.py` → the pre-registered run: gates D1/D2/D3, the 12-cell grid, the
   all-15m robustness pass, `results/report.json`.
2. `diagnose_d3.py` → why D3 failed (post-hoc diagnostic of a failed gate).
3. `staleness_check.py` → post-hoc data quality + an independent recomputation of four
   trades from raw SQL.
4. `funding_vs_basis.py` → post-hoc: the population carry identity that explains the
   result without reference to any entry rule.
"""

RUN_MAIN = "%run basis_carry.py"
RUN_D3 = "%run diagnose_d3.py"
RUN_STALE = "%run staleness_check.py"
RUN_CARRY = "%run funding_vs_basis.py"

CLAUSES = """\
import json
import pandas as pd
pd.set_option('display.width', 240)
pd.set_option('display.max_columns', 60)

rep = json.load(open('results/report.json', encoding='utf-8'))
print('LITERAL PRE-REGISTERED VERDICT :', rep['verdict_literal_prereg'])
print('ECONOMICS IF D3 IS SET ASIDE   :', rep['economics_verdict_ignoring_D3'])
print()
for name, c in rep['clauses'].items():
    m = c.get('measured', c.get('measured_p2_5'))
    m = f'{m:.6g}' if isinstance(m, float) else m
    print(f"  {'FIRES' if c['fired'] else 'no   '}  {name}: measured {m}  vs threshold {c['threshold']}")
"""

PRIMARY = """\
p = rep['primary']
print(f"pre-registered primary: 8% trigger, CURRENT_QUARTER, BTC+ETH pooled")
print(f"  n_trades          {p['n_trades']}")
print(f"  gross  (annualised on deployed capital)  {p['gross_ann_pooled']:+.4%}")
print(f"  fees                                     {p['fees_ann_pooled']:+.4%}")
print(f"  funding                                  {p['funding_ann_pooled']:+.4%}")
print(f"  ------------------------------------------------")
print(f"  NET  R_ann                               {p['R_ann']:+.4%}")
print()
b = p['bootstrap_R_ann']
print(f"  bootstrap 95% CI on R_ann  [{b['p2_5']:+.4%}, {b['p97_5']:+.4%}]"
      f"   P(R_ann>0)={b['p_above_zero']:.4f}   P(R_ann>=5%)={b['p_above_kill']:.4f}")
print(f"  DSR (n_trials=12)          {p['dsr']['dsr']:.4g}   SR/trade {p['dsr']['sr_per_obs']:+.4f}"
      f"   annualised {p['trade_sharpe_ann']:+.4f}")
print(f"  win rate {p['win_rate']:.1%}   mean hold {p['mean_hold_days']:.1f}d"
      f"   mean entry basis_ann {p['mean_entry_basis_ann']:+.4%}"
      f"   mean residual basis at exit {p['mean_exit_basis']:+.4%}")
print(f"  funding-incomplete trades: {p['n_funding_incomplete']}  (0 = funding fully charged)")
print()
rows = []
for key, lbl in (('primary', 'primary BTC+ETH'), ('primary_btc_only', 'BTC only'),
                 ('primary_eth_only', 'ETH only'),
                 ('primary_alt_all_15m', 'robustness: both perps from 15m')):
    r = rep[key]
    rows.append(dict(cell=lbl, n=r['n_trades'], R_ann=r['R_ann'],
                     gross=r['gross_ann_pooled'], fees=r['fees_ann_pooled'],
                     funding=r['funding_ann_pooled'],
                     boot_p2_5=r['bootstrap_R_ann']['p2_5'], dsr=r['dsr']['dsr']))
sp = pd.DataFrame(rows).set_index('cell')
for c in ('R_ann', 'gross', 'fees', 'funding', 'boot_p2_5'):
    sp[c] = sp[c].map(lambda v: f'{v:+.4%}')
sp['dsr'] = sp['dsr'].map(lambda v: f'{v:.3g}')
display(sp)
"""

GRID = """\
g = pd.read_csv('results/grid_12_cells.csv')
cols = ['source', 'asset', 'slot', 'trigger', 'cycles_available', 'cycles_closable',
        'n_trades', 'R_ann', 'gross_ann_pooled', 'fees_ann_pooled', 'funding_ann_pooled',
        'win_rate', 'mean_hold_days', 'n_funding_incomplete']
print(f"cells with R_ann >= +5% (the KILL line): {(g.R_ann >= 0.05).sum()} of {len(g)}")
print(f"cells with R_ann > 0:                    {(g.R_ann > 0).sum()} of {len(g)}")
display(g[cols].round(4))
"""

TRADES = """\
import numpy as np
t = pd.read_csv('results/primary_trades.csv')
liq = pd.read_csv('results/primary_fill_liquidity.csv')
t = t.merge(liq[['asset', 'entry_utc', 'entry_qtr_volume', 'zero_volume_fill']],
            on=['asset', 'entry_utc'], how='left')
show = ['asset', 'entry_utc', 'exit_utc', 'direction', 'hold_days', 'entry_basis_ann',
        'exit_basis', 'gross_ann', 'funding_ann', 'net_ann', 'net', 'entry_qtr_volume']
display(t[show].round(4))

fig_rows = []
t['entry_year'] = pd.to_datetime(t.entry_utc).dt.year
for y, d in t.groupby('entry_year'):
    fig_rows.append(dict(year=int(y), n=len(d),
                         R_ann=365 * d.net.sum() / d.hold_days.sum(),
                         gross=365 * d.gross.sum() / d.hold_days.sum(),
                         funding=365 * d.funding.sum() / d.hold_days.sum()))
fy = pd.DataFrame(fig_rows).set_index('year')
for c in ('R_ann', 'gross', 'funding'):
    fy[c] = fy[c].map(lambda v: f'{v:+.4%}')
display(fy)
print(f"trades clearing +5% annualised: {(t.net_ann >= 0.05).sum()} of {len(t)}")
print(f"R_ann excluding the 2 zero-volume-entry trades: "
      f"{365 * t[~t.zero_volume_fill.astype(bool)].net.sum() / t[~t.zero_volume_fill.astype(bool)].hold_days.sum():+.4%}")
"""

DECOMP = """\
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

# left: per-trade decomposition, sorted by entry
d = t.sort_values('entry_ts').reset_index(drop=True)
x = np.arange(len(d))
axes[0].bar(x, d.gross_ann * 100, color='#2ca02c', label='gross convergence')
axes[0].bar(x, d.funding_ann * 100, color='#d62728', label='funding paid')
axes[0].plot(x, d.net_ann * 100, 'k.-', lw=0.9, ms=4, label='net')
axes[0].axhline(5, color='#1f77b4', ls='--', lw=1, label='KILL line +5%')
axes[0].axhline(0, color='k', lw=0.6)
axes[0].set_ylabel('% annualised'); axes[0].set_xlabel('trade (chronological)')
axes[0].set_title('39 pre-registered trades: funding is bigger than the convergence')
axes[0].legend(fontsize=8)

# right: the two curves, population level
fb = pd.read_csv('results/funding_vs_basis_by_year.csv')
yr = fb[fb.year.astype(str).str.isdigit()].copy()
yr['year'] = yr.year.astype(int)
for asset, c in (('BTC', '#f7931a'), ('ETH', '#627eea')):
    s = yr[yr.asset == asset]
    axes[1].plot(s.year, s.basis_ann_mean * 100, 'o-', color=c, label=f'{asset} basis')
    axes[1].plot(s.year, s.funding_ann_mean * 100, 's--', color=c, alpha=0.6, label=f'{asset} funding')
axes[1].axhline(0, color='k', lw=0.6)
axes[1].set_ylabel('% annualised'); axes[1].set_xlabel('year')
axes[1].set_title('mean annualised basis vs mean annualised funding\\n(funding above basis = trade loses)')
axes[1].legend(fontsize=8)
plt.tight_layout()
"""

CARRY = """\
fb = pd.read_csv('results/funding_vs_basis_by_year.csv')
display(fb.round(4))
ci = pd.read_csv('results/funding_vs_basis_ci.csv')
print('\\ncarry = basis_ann - funding_ann, measured while the pre-registered 8% trigger is ON:')
display(ci.round(4))
"""

QUALITY = """\
print('quarterly print staleness — NEXT_QUARTER before 2024 is a repeated (zero-volume) print:')
display(pd.read_csv('results/staleness_by_year.csv').round(4))
print('\\nterm structure restricted to entry-eligible hours (dtd >= 7d) — use this one, not the'
      ' unrestricted file, whose sd is the 365/dtd term exploding near expiry:')
d7 = pd.read_csv('results/basis_term_structure_dtd7.csv')
display(d7[d7.slot == 'CURRENT_QUARTER'].round(4))
print('\\nindependent recomputation of 4 sample trades from raw SQL (no shared code):')
display(pd.read_csv('results/manual_trade_check.csv').round(10))
print('\\nD3 gate evidence — by-year exact-match fraction of the 15m->1h reconstruction:')
display(pd.read_csv('results/d3_diagnostic.csv').round(6))
print('D3 third-source tiebreak:', json.dumps(rep['D3']['tiebreak_third_source'], indent=1))
print('D3 impact on the primary :', json.dumps(rep['d3_impact_on_primary'], indent=1))
"""


def build() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3",
                                 "display_name": "Python 3 (p300 venv)",
                                 "language": "python"}
    nb.cells = [
        nbf.v4.new_markdown_cell(INTRO),
        nbf.v4.new_markdown_cell("## 1. The pre-registered run (gates, 12-cell grid, robustness)"),
        nbf.v4.new_code_cell(RUN_MAIN),
        nbf.v4.new_markdown_cell("## 2. Decision clauses, exactly as frozen in README.md §5"),
        nbf.v4.new_code_cell(CLAUSES),
        nbf.v4.new_markdown_cell("## 3. The primary result and its splits"),
        nbf.v4.new_code_cell(PRIMARY),
        nbf.v4.new_markdown_cell(
            "## 4. The full grid — context triggers (5%, 6%) and the NEXT_QUARTER slot\n\n"
            "Reported for completeness, **not** as a selection menu. The decision is the 8% "
            "CURRENT_QUARTER cell and nothing else. All 12 pre-registered cells and all 12 "
            "robustness cells are negative. Note that the NEXT_QUARTER rows nearest breakeven "
            "are also the least trustworthy — see section 7."),
        nbf.v4.new_code_cell(GRID),
        nbf.v4.new_markdown_cell("## 5. The 39 pre-registered trades, decomposed"),
        nbf.v4.new_code_cell(TRADES),
        nbf.v4.new_markdown_cell("## 6. Picture of the whole finding"),
        nbf.v4.new_code_cell(DECOMP),
        nbf.v4.new_markdown_cell(
            "## 7. Why — the carry identity, independent of any entry rule\n\n"
            "At every 8h settlement with at least 7 days to delivery, compare the annualised "
            "quarterly basis against the annualised realised perp funding. Their difference is "
            "the trade's gross carry before fees. Conditioning on the pre-registered entry state "
            "(`basis_ann >= 8%`) makes the carry **worse**, not better: the state that makes the "
            "quarterly rich is the state that makes funding expensive."),
        nbf.v4.new_code_cell(RUN_CARRY),
        nbf.v4.new_code_cell(CARRY),
        nbf.v4.new_markdown_cell(
            "## 8. Data quality, verification, and the failed D3 gate\n\n"
            "`diagnose_d3.py` and `staleness_check.py` are **post-hoc** diagnostics written after "
            "the pre-registered run. They change no rule and move no threshold."),
        nbf.v4.new_code_cell(RUN_D3),
        nbf.v4.new_code_cell(RUN_STALE),
        nbf.v4.new_code_cell(QUALITY),
    ]
    return nb


def execute(nb: nbf.NotebookNode) -> None:
    """Execute on the repo venv's ipykernel without touching the user's kernelspecs."""
    from jupyter_client import KernelManager
    from nbclient import NotebookClient
    km = KernelManager(kernel_name="python3")
    km.kernel_cmd = [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=km, timeout=2400,
                            resources={"metadata": {"path": str(HERE)}})
    client.execute()


def main() -> int:
    nb = build()
    path = HERE / "basis_carry.ipynb"
    if "--execute" in sys.argv:
        os.chdir(HERE)
        execute(nb)
        print("executed on", VENV_PY)
    nbf.write(nb, str(path))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
