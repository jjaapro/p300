#!/usr/bin/env python3
"""Assemble + execute brainstorm_validation.ipynb.

Run with the system Python (C:/Python/Python313/python.exe — it has
nbformat/nbclient; the repo venv does not).  The cells execute in the repo venv
(venv/Scripts/python.exe, which has ipykernel) through a temporary kernelspec on
JUPYTER_PATH, so the numbers are the venv's — identical to running the six
scripts from the repo root with venv\\Scripts\\python.

    C:/Python/Python313/python.exe studies/notebooks/brainstorm_validation_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "brainstorm_validation.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="bv_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# Brainstorm session 2026-09-11 — independent validation (AUDIT, N_TRIALS = 1)

Pre-registration: [README.md](README.md) (written before any run). Write-up: [findings.md](findings.md).

Five decision-bearing claims from `studies/material/brainstorming_session_sep11_2026/` are
(1) reproduced on the brainstorm's own cached data with an exact port of its statistics,
(2) re-derived from p300's database with an independent implementation, and (3) pushed through
data the original never saw and through p300's deflation / bootstrap bar.

| id | claim |
|---|---|
| C1 | big-bar daily momentum (+193.5 bp, t 3.04; "beats buy-and-hold") |
| C2 | funding-carry quartile timing (+7.7 % vs +3.4 %/yr) |
| C3 | absorption 5 m / 2 h (+4.8 bp, t 2.91) |
| C4 | ADX regime-machine claims (veto = stop-fill artefact; ±0.15 Sharpe bar-phase noise; short leg) |
| C5 | residual TA cells (Bollinger squeeze-down short; RSI enter-overbought long) |

Re-run order (each cell re-runs the script; all read-only against prod.db):
`run_c0_data_parity.py` → `run_c1_momentum.py` → `run_c2_carry_timing.py` → `run_c3_absorption.py`
→ `run_c4_adx_engine.py` → `run_c5_ta_residuals.py`. The last cells render `results/`.
"""

SHOW_DECISIONS = """\
import json, pandas as pd
from IPython.display import Image, display
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
for c in ('c1_summary', 'c2_summary', 'c3_summary', 'c4_summary', 'c5_summary'):
    S = json.load(open(f'results/{c}.json', encoding='utf-8'))
    print(c.upper(), '->', json.dumps(S['decision'], indent=1)[:1800])
    print()
"""

SHOW_C0 = """\
G = json.load(open('results/c0_data_parity.json', encoding='utf-8'))
print('gate:', G['gate'])
print('BTC daily cache vs p300:', json.dumps(G['btc_daily_cache_vs_p300']['stats'], indent=1))
print('5m cache -> 15m vs cd_spot_15m:', json.dumps(G['btc_5m_cache_vs_cd_spot_15m']['stats'], indent=1))
print('funding:', json.dumps(G['funding'], indent=1))
"""

SHOW_C1 = """\
S = json.load(open('results/c1_summary.json', encoding='utf-8'))
print('parity:', S['parity']); print('p300 primary:', S['p300_btc']['primary']); print('era:', S['p300_btc']['era'])
print('bitstamp OOS 2012-17:', S['bitstamp']['oos']); print('eras:', S['bitstamp']['eras'])
print('ETH:', S['eth']); print('alts pooled:', S['alts'])
print('DSR:', {k: round(v['dsr'], 3) for k, v in S['dsr'].items() if v})
display(pd.read_csv('results/c1_per_year.csv'))
g = pd.read_csv('results/c1_grid.csv'); display(g.pivot_table(index=['z', 'W'], columns='H', values='t').round(2))
display(pd.read_csv('results/c1_alts.csv').sort_values('t', ascending=False).head(15))
for lab, s in S['sizing']['sims'].items():
    print(lab, 'B&H', s['buy_and_hold'])
    for k, v in s['runs'].items():
        print('  ', k, {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items() if not isinstance(vv, dict)})
        for kk in ('bh_exposure_matched', 'strategy_curve_stats', 'boot_cagr_vs_bh_1x', 'boot_cagr_vs_bh_matched', 'boot_maxdd_vs_bh_1x', 'random_control'):
            if kk in v: print('      ', kk, v[kk])
display(Image('results/c1_equity.png'))
"""

SHOW_C2 = """\
S = json.load(open('results/c2_summary.json', encoding='utf-8'))
print('series:', S['series']); print('parity (last 1200 / 500 prints):', json.dumps(S['parity'], indent=1)[:2500])
display(pd.DataFrame(S['autocorr_by_year']).round(3))
display(pd.read_csv('results/c2_quartiles.csv').round(1))
display(pd.DataFrame(S['rules']['table']).round(3))
p = pd.read_csv('results/c2_per_year.csv'); display(p.pivot_table(index='rule', columns='year', values='net_ann_pct').round(2))
print('era:', json.dumps(S['era'], indent=1)); print('bootstrap:', json.dumps(S['bootstrap_vs_always_on'], indent=1))
display(Image('results/c2_curves.png'))
"""

SHOW_C3 = """\
S = json.load(open('results/c3_summary.json', encoding='utf-8'))
print('parity:', {k: v for k, v in S['parity'].items() if k != 'decay_z1'})
display(pd.read_csv('results/c3_decay_their_cache_z1.csv').round(2))
print('exact-resolution window:', S['exact_resolution']['window'], S['exact_resolution']['max_rel_diff'])
display(pd.DataFrame(S['exact_resolution']['p300']).round(2)); display(pd.DataFrame(S['exact_resolution']['their_cache_same_window']).round(2))
for n in ('spot_15m', 'perp_15m', 'eth_perp_15m'):
    print(n, S['panels_15m'][n]['span']); display(pd.read_csv(f'results/c3_decay_{n}_z1.csv').round(2))
print('primary spot 15m 2h:', S['primary_spot15m_2h']['summary'], S['primary_spot15m_2h']['era'],
      {k: round(v['dsr'], 3) for k, v in S['primary_spot15m_2h']['dsr'].items()})
display(pd.read_csv('results/c3_per_year_spot15m.csv').round(2))
display(Image('results/c3_decay.png'))
"""

SHOW_C4 = """\
S = json.load(open('results/c4_summary.json', encoding='utf-8'))
print('parity:', json.dumps({k: v for k, v in S['parity'].items() if k in ('study', 'live', 'buy_and_hold', 'veto_inverts_under_live_fill')}, indent=1))
display(pd.read_csv('results/c4_phase_s005.csv').round(3))
display(pd.read_csv('results/c4_phase_p300.csv').round(2)); print('ranges:', S['phase_p300']['ranges'])
print('stop fill:', json.dumps(S['stop_fill_p300'], indent=1))
display(pd.read_csv('results/c4_shortleg.csv').round(2))
print('short leg:', json.dumps({k: v for k, v in S['short_leg'].items() if k != 'weight_sweep'}, indent=1))
display(pd.DataFrame(S['short_leg']['weight_sweep']).round(3))
"""

SHOW_C5 = """\
S = json.load(open('results/c5_summary.json', encoding='utf-8'))
display(pd.read_csv('results/c5_cells.csv').round(2))
p = pd.read_csv('results/c5_rsi_plateau.csv'); display(p.pivot_table(index=['rsi_len', 'lvl', 'H'], columns='panel', values='t').round(2))
print(json.dumps(S['decision'], indent=1, default=str)[:4000])
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## C0 — data parity gate"),
        nbf.v4.new_code_cell("%run run_c0_data_parity.py"),
        nbf.v4.new_markdown_cell("## C1 — big-bar daily momentum"),
        nbf.v4.new_code_cell("%run run_c1_momentum.py"),
        nbf.v4.new_markdown_cell("## C2 — funding-carry quartile timing"),
        nbf.v4.new_code_cell("%run run_c2_carry_timing.py"),
        nbf.v4.new_markdown_cell("## C3 — absorption"),
        nbf.v4.new_code_cell("%run run_c3_absorption.py"),
        nbf.v4.new_markdown_cell("## C4 — ADX regime machine"),
        nbf.v4.new_code_cell("%run run_c4_adx_engine.py"),
        nbf.v4.new_markdown_cell("## C5 — residual TA cells"),
        nbf.v4.new_code_cell("%run run_c5_ta_residuals.py"),
        nbf.v4.new_markdown_cell("## Decisions (from results/*.json)"),
        nbf.v4.new_code_cell(SHOW_DECISIONS),
        nbf.v4.new_markdown_cell("## C0 tables"),
        nbf.v4.new_code_cell(SHOW_C0),
        nbf.v4.new_markdown_cell("## C1 tables and figure"),
        nbf.v4.new_code_cell(SHOW_C1),
        nbf.v4.new_markdown_cell("## C2 tables and figure"),
        nbf.v4.new_code_cell(SHOW_C2),
        nbf.v4.new_markdown_cell("## C3 tables and figure"),
        nbf.v4.new_code_cell(SHOW_C3),
        nbf.v4.new_markdown_cell("## C4 tables"),
        nbf.v4.new_code_cell(SHOW_C4),
        nbf.v4.new_markdown_cell("## C5 tables"),
        nbf.v4.new_code_cell(SHOW_C5),
    ]
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, NB_PATH)
    client = NotebookClient(nb, timeout=3600, kernel_name=kernel, resources={"metadata": {"path": str(HERE)}})
    client.execute()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, NB_PATH)
    print("executed and wrote", NB_PATH)


if __name__ == "__main__":
    main()
