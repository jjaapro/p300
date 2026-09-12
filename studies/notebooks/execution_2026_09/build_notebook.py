#!/usr/bin/env python3
"""Assemble + execute execution_study.ipynb.

Run with the system Python (C:/Python/Python313/python.exe has nbformat/nbclient; the repo venv does not).
The cells execute in the repo venv through a temporary kernelspec on JUPYTER_PATH, so the numbers are the
venv's — identical to running the seven scripts from this directory with venv\\Scripts\\python.

    C:/Python/Python313/python.exe studies/notebooks/execution_2026_09/build_notebook.py
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
NB_PATH = HERE / "execution_study.ipynb"


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix="ex_kernel_")
    kd = Path(tmp) / "kernels" / "p300venv"
    kd.mkdir(parents=True)
    (kd / "kernel.json").write_text(json.dumps({
        "argv": [str(VENV_PY), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "p300 venv", "language": "python"}), encoding="utf-8")
    prev = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = tmp + (os.pathsep + prev if prev else "")
    return "p300venv"


HEADER = """\
# Execution study 2026-09 — what execution costs each running sleeve (AUDIT + policy decision)

Pre-registration: [README.md](README.md) (frozen before any run). Write-up: [findings.md](findings.md).

| id | question |
|---|---|
| E0 | facts (what `btc_1m` is, spot-vs-perp basis) and parity gates on the sleeves' own validated numbers |
| E1 | market-order cost: realised spread and decision-to-fill drift |
| E2 | passive (limit) entry: fill rate, improvement, fill-weighted expectancy, no survivorship |
| E3 | take-profit fills: touch vs trade-through; market-at-touch cost |
| E4 | stop-market slippage under stop_path semantics |
| E5 | conditional adverse selection at signal times vs random times (5 s) |
| E6 | re-cost every sleeve under the measured models; apply the pre-registered rules |

Re-run order: `run_e0_facts.py` → `run_e1_market_cost.py` → `run_e2_passive_entry.py` → `run_e3_tp_fills.py`
→ `run_e4_stop_slippage.py` → `run_e5_conditional_as.py` → `run_e6_recost.py`. All read-only against prod.db.
"""

SHOW_DECISIONS = """\
import json, pandas as pd
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
E6 = json.load(open('results/e6_recost.json', encoding='utf-8'))
for sl, d in E6['decisions'].items():
    print(sl)
    print('  rule 1 cost constant :', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d['rule1'].items()})
    print('  rule 2 maker entry   :', d['rule2_entry'])
    print('  rule 3 resting TP    :', d['rule3_tp'])
    print('  rule 4 viability     :', {k: (round(v, 3) if isinstance(v, float) else v) for k, v in d['rule4'].items()})
print('ADX  :', E6['ADX']); print('CARRY:', E6['CARRY'])
"""

SHOW_E6 = """\
for sl, d in E6['sleeves'].items():
    print(f"\\n{sl}: n={d['n']} exits={d['exit_kinds']} gross {d['gross']['mean_r']:+.3f} R  drift {d['drift_bp']:+.2f} bp  stop slip {d['stop_slip_bp']:.2f} bp")
    display(pd.DataFrame(d['models']).T[['mean_bp_per_trade', 'mean_cost_r', 'net_mean_r', 'cost_share_of_gross', 'net_mar', 'net_first_half', 'net_second_half', 'fill_rate']].round(3))
"""

SHOW_E0_E1 = """\
E0 = json.load(open('results/e0_facts.json', encoding='utf-8'))
print('btc_1m identity:', E0['btc_1m_identity']); print('basis bp:', json.dumps({k: v for k, v in E0['basis_bp'].items() if k != 'by_year'}, indent=1))
print('gates:', json.dumps(E0['gates'], indent=1, default=str)[:3000])
E1 = json.load(open('results/e1_market_cost.json', encoding='utf-8'))
print('spreads:', json.dumps({k: {kk: vv for kk, vv in v.items() if not isinstance(vv, dict)} for k, v in E1['spreads'].items()}, indent=1))
rows = []
for sl, d in E1['entry_drift'].items():
    for res, dd in d.items():
        for k, v in dd.items():
            if isinstance(v, dict) and 'mean' in v:
                rows.append(dict(sleeve=sl, res=res, measure=k, n=dd.get('n'), mean=v['mean'], ci90=v.get('ci90'), median=v.get('median')))
display(pd.DataFrame(rows).round(2))
"""

SHOW_E2 = """\
E2 = json.load(open('results/e2_passive_entry.json', encoding='utf-8'))
for sl, d in E2['sleeves'].items():
    s = d['1m']; m = s['market']
    print(f"\\n{sl}: market n={m['n']} mean {m['mean_r']:+.3f} R (halves {m['first_half_mean']:+.3f}/{m['second_half_mean']:+.3f})")
    display(pd.DataFrame({c: dict(T=v['T'], rule=v['rule'], fb=v['fallback'], fill=v['fill_rate'], impr_bp=v['mean_impr_bp_filled'], mean_r=v['mean_r'],
                                  d_vs_mkt=v['delta_vs_market'], d_h1=v['delta_first_half'], d_h2=v['delta_second_half']) for c, v in s.items() if c != 'market'}).T.round(3))
    print('decision:', E2['decision'][sl])
print('ADX:', E2['adx'])
"""

SHOW_E3_E4_E5 = """\
E3 = json.load(open('results/e3_tp_fills.json', encoding='utf-8'))
for sl, d in E3['sleeves'].items():
    print(sl, {res: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in dd.items() if k not in ('unfilled_alt_kinds',)} for res, dd in d.items()})
E4 = json.load(open('results/e4_stop_slippage.json', encoding='utf-8'))
for sl, d in E4['sleeves'].items():
    for res, dd in d.items():
        print(sl, res, 'n', dd.get('n_stops'), 'gap-through', dd.get('share_gap_through'), 'median stop bp', round(dd.get('median_stop_dist_bp', float('nan')), 1),
              'pathsem', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in dd.get('slip_pathsem_bp', {}).items()},
              'poll', {k: (round(v, 2) if isinstance(v, float) else v) for k, v in dd.get('slip_poll_bp', {}).items()})
print('ADX SL:', {k: v for k, v in E4['adx'].items() if k != 'rows'})
E5 = json.load(open('results/e5_conditional_as.json', encoding='utf-8'))
for name, d in E5['sets'].items():
    print(name, {k: v for k, v in d.items() if not isinstance(v, dict)})
    for k in (60, 300, 900, 3600):
        m = d.get(f'mo_{k}')
        if m: print(f"   +{k}s: cond {m['cond_mean']:+.2f} {[round(x, 2) for x in m['cond_ci90']]} | random {m['rand_mean']:+.2f} | diff {m['diff']:+.2f} {[round(x, 2) for x in m['diff_ci90']]}")
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell("## E0 — facts and parity gates"), nbf.v4.new_code_cell("%run run_e0_facts.py"),
        nbf.v4.new_markdown_cell("## E1 — market-order cost"), nbf.v4.new_code_cell("%run run_e1_market_cost.py"),
        nbf.v4.new_markdown_cell("## E2 — passive entry"), nbf.v4.new_code_cell("%run run_e2_passive_entry.py"),
        nbf.v4.new_markdown_cell("## E3 — take-profit fills"), nbf.v4.new_code_cell("%run run_e3_tp_fills.py"),
        nbf.v4.new_markdown_cell("## E4 — stop slippage"), nbf.v4.new_code_cell("%run run_e4_stop_slippage.py"),
        nbf.v4.new_markdown_cell("## E5 — conditional adverse selection"), nbf.v4.new_code_cell("%run run_e5_conditional_as.py"),
        nbf.v4.new_markdown_cell("## E6 — re-cost and decide"), nbf.v4.new_code_cell("%run run_e6_recost.py"),
        nbf.v4.new_markdown_cell("## Decisions (from results/e6_recost.json)"), nbf.v4.new_code_cell(SHOW_DECISIONS),
        nbf.v4.new_markdown_cell("## E6 tables"), nbf.v4.new_code_cell(SHOW_E6),
        nbf.v4.new_markdown_cell("## E0 / E1 tables"), nbf.v4.new_code_cell(SHOW_E0_E1),
        nbf.v4.new_markdown_cell("## E2 tables"), nbf.v4.new_code_cell(SHOW_E2),
        nbf.v4.new_markdown_cell("## E3 / E4 / E5 tables"), nbf.v4.new_code_cell(SHOW_E3_E4_E5),
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
