#!/usr/bin/env python3
"""Assemble + execute squeeze_bull_revalidation.ipynb.

Run with the system Python (C:/Python/Python313/python.exe — it has
nbformat/nbclient; the repo venv does not). The notebook's cells execute in
the repo venv (venv/Scripts/python.exe, which has ipykernel) through a
temporary kernelspec placed on JUPYTER_PATH, so the numbers are the venv's —
identical to running the three scripts from the repo root with
venv\\Scripts\\python. Nothing outside this folder and a temp dir is written.

    C:/Python/Python313/python.exe studies/notebooks/squeeze_bull_revalidation/build_notebook.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
VENV_PY = ROOT / 'venv' / 'Scripts' / 'python.exe'
NB_PATH = HERE / 'squeeze_bull_revalidation.ipynb'


def register_temp_kernel() -> str:
    tmp = tempfile.mkdtemp(prefix='squeeze_bull_kernel_')
    kd = Path(tmp) / 'kernels' / 'p300venv'
    kd.mkdir(parents=True)
    (kd / 'kernel.json').write_text(json.dumps({
        'argv': [str(VENV_PY), '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
        'display_name': 'p300 venv', 'language': 'python'}), encoding='utf-8')
    prev = os.environ.get('JUPYTER_PATH')
    os.environ['JUPYTER_PATH'] = tmp + (os.pathsep + prev if prev else '')
    return 'p300venv'


HEADER = """\
# S-SqueezeBull — out-of-sample re-validation (AUDIT, N_TRIALS = 1)

Pre-registration: [README.md](README.md) (written before any data run). Write-up: [findings.md](findings.md).

The two June-2026 rules — **OI flush long** (−2 % OI in 4 rows + price down, stop 2 % / target 3 % / TIF 48 h,
bull-gated on BTC 30d > +10 %) and **funding+CVD divergence long** (14d funding z < −2 and CVD z > +0.5 for
4 bars, 5×ATR / 6R / 72 h) — are imported from `studies/notebooks/oi_flush/` and
`studies/notebooks/funding_cvd_divergence/` unchanged. Only the window is new:
**2026-04-14 → the last full UTC day**.

Re-run order (each cell re-runs the script; all read-only against prod.db):
`run_parity.py` → `run_oos.py` → `run_combined.py` → `run_fragility.py`. The last cells
render `results/`.

`run_fragility.py` is post-hoc: it measures how thin the pre-registered verdict's margin is
(leave-k-out, the regime-gate look-ahead audit, the OI-seam split, bootstrap CIs and the
deflated Sharpe at N_TRIALS = 1). It changes no threshold and no verdict label.
"""

SHOW_SUMMARY = """\
import json, pandas as pd
from IPython.display import Image, display
pd.set_option('display.width', 220); pd.set_option('display.max_columns', 40)
S = json.load(open('results/summary.json', encoding='utf-8'))
P = json.load(open('results/parity.json', encoding='utf-8'))
print('VERDICT:', S['verdict'], '—', S['reason'])
print('parity:', S['parity'])
print()
print('clause map (pre-registered decision rule -> measured):')
for k, v in S['clause_map'].items():
    print(f'  {k:<32s} {v}')
print()
print('projection:', json.dumps(S['projection'], indent=2))
"""

SHOW_PARITY = """\
rows = []
for pid in ('P1', 'P2', 'P3', 'P4'):
    p = P[pid]
    if pid == 'P1':
        rows.append({'check': 'P1 phase3a bull (<=2026-02-06)', 'got': p['got'], 'ref': p['ref'], 'pass': p['pass']})
    elif pid == 'P2':
        rows.append({'check': 'P2 ablation fixed_020 bull', 'got': p['got_bull'], 'ref': p['ref_bull'], 'pass': p['check_bull']['pass']})
        rows.append({'check': 'P2 ablation fixed_020 pool', 'got': p['got_pool'], 'ref': p['ref_pool'], 'pass': p['check_pool']['pass']})
    elif pid == 'P3':
        rows.append({'check': 'P3 caller window bull (<=2026-04-13)', 'got': p['got'], 'ref': p['ref'],
                     'pass': p['pass'], 'gap_fires': len(p['fires_after_2026_02_06_in_window'])})
    else:
        rows.append({'check': 'P4 fCVD June ledger', 'got': {'n': p['got_n'], 'meanR': p['got_meanR'], 'matched': p['matched'],
                     'max_abs_dR': p['max_abs_dR']}, 'ref': {'n': p['ref_n'], 'meanR': p['ref_meanR']}, 'pass': p['pass']})
for r in rows:
    print(r['check'], '| pass =', r['pass'])
    print('   got:', {k: r['got'].get(k) for k in ('n', 'mean_R', 'meanR', 'MAR', 'maxDD', 'annual_R', 'OOS_n', 'OOS_meanR', 'matched', 'max_abs_dR') if k in r['got']})
    print('   ref:', r['ref'])
print()
print('data-migration caveats:')
print(json.dumps({k: P['data_quality'][k] for k in ('oi_seam', 'funding_cadence')}, indent=2)[:3000])
print('OI OOS continuity:', {k: P['data_quality']['oi_continuity_oos'][k] for k in ('rows', 'missing', 'off_grid_rows', 'duplicates')})
"""

SHOW_OOS = """\
O = json.load(open('results/oos.json', encoding='utf-8'))
print('OOS window:', O['oos_window'], '| last full day', O['last_full_day'])
print('OI flush bull-gated (resolved):', O['oi_flush']['bull_gated_resolved'])
print('OI flush pooled (resolved):    ', O['oi_flush']['pooled_resolved'])
print('by regime:', json.dumps(O['oi_flush']['by_regime_resolved'], indent=1))
print('regime distribution (June construction):', json.dumps(O['regime']['june_construction'], indent=1))
print('bull gate never open in OOS:', O['regime']['bull_gate_never_open'])
print()
print('OOS OI-flush ledger (pooled):')
display(pd.read_csv('results/oos_oi_flush_ledger.csv'))
print('OOS funding+CVD ledger (A = as stored, B = 8h-consistent):')
display(pd.read_csv('results/oos_funding_cvd_ledger.csv'))
"""

SHOW_TABLES = """\
print('per-year (full sample):'); display(pd.read_csv('results/per_year.csv'))
print('pre-ETF vs post-ETF (2024-01-11):'); display(pd.read_csv('results/etf_split.csv'))
C = S['combined']
for h in ('A', 'B'):
    print(f'combined portfolio, fCVD handling {h}:')
    display(pd.DataFrame([C[h]['strict']['OI_bull'], C[h]['strict']['fCVD'], C[h]['strict']['combined']]))
    print(f"   June formula: {C[h]['june_formula']['combined']} | monthly Pearson r = {C[h]['monthly_pearson']} over {C[h]['active_months']} months")
display(Image('results/fig_cum_r.png'))
"""

SHOW_FRAGILITY = """\
F = json.load(open('results/fragility.json', encoding='utf-8'))
print('verdict under the frozen rule (unchanged by anything below):', F['headline_verdict_unchanged'])
print('margins over the pre-registered thresholds:', F['margins'])
print()
print('F1 leave-k-out on the OOS bull-gated set')
for k in ('drop_best_1', 'drop_best_2'):
    d = F['F1_leave_k_out'][k]
    print(f"  {k}: n={d['n']} meanR={d['mean_R']:+.4f} sumR={d['sum_R']:+.3f} -> {d['verdict']} ({d['why']})")
print('  jackknife:', F['F1_leave_k_out']['jackknife_leave_one_out'])
print('  all leave-two-out pairs:', F['F1_leave_k_out']['leave_two_out_all_pairs'])
print('  concentration:', F['F1_leave_k_out']['concentration'])
print()
R = F['F2_regime_gate_lookahead']
print('F2 regime-gate look-ahead: '
      f"{R['fires_whose_gate_source_close_is_at_or_after_the_fire']}/{R['oos_fires']} OOS fires read a daily "
      f"close stamped at or after the fire; range [{R['min_lookahead_hours']}, {R['max_lookahead_hours']}] h")
print('  backward-only construction is strictly causal:', R['backonly_is_implementable'])
print('  gate flips:', json.dumps(R['gate_flips'], indent=1))
print(f"  June bull set     n={R['june_bull_set']['n']} meanR={R['june_bull_set']['mean_R']:+.4f} (DECISION-BEARING)")
print(f"  backonly bull set n={R['backonly_bull_set']['n']} meanR={R['backonly_bull_set']['mean_R']:+.4f} (sensitivity)")
display(pd.read_csv('results/fragility_regime_audit.csv'))
print()
S3 = F['F3_oi_seam']
print('F3 OI source seam:', S3['seam'])
print(f"  bull-gated pre-seam  n={S3['bull_gated_pre_seam']['n']} meanR={S3['bull_gated_pre_seam']['mean_R']}")
print(f"  bull-gated post-seam n={S3['bull_gated_post_seam']['n']} meanR={S3['bull_gated_post_seam']['mean_R']}")
print('  post-seam subset alone ->', S3['post_seam_verdict_if_it_were_the_whole_sample'])
print()
U = F['F4_uncertainty']
print('F4 uncertainty on the OOS mean R')
for k in ('iid_bootstrap_mean_R', 'block_bootstrap_mean_R_block3', 'episode_cluster_bootstrap_mean_R',
          'dsr_oos_bull_gated', 'dsr_full_sample_bull_gated_context',
          'n_trials_breakeven_full_sample', 'clustering'):
    print(f'  {k}:', json.dumps(U[k], indent=1))
print('  N_TRIALS justification:', U['n_trials_justification'])
print()
print('F5 clause (b) decomposition:')
display(pd.DataFrame([{'set': k, **F['F5_clause_b'][k]} for k in
                      ('combined_full_sample', 'oi_bull_only_full_sample', 'fcd_only_full_sample',
                       'combined_pre_OOS_only', 'oi_bull_pre_OOS_only', 'fcd_pre_OOS_only')]))
print('  fCVD share of combined sum R:', F['F5_clause_b']['fcd_share_of_combined_sum_R'],
      '| OOS contribution to combined sum R:', F['F5_clause_b']['oos_contribution_to_combined_sum_R'])
"""


def main() -> None:
    import nbformat as nbf
    from nbclient import NotebookClient

    kernel = register_temp_kernel()
    nb = nbf.v4.new_notebook()
    nb.metadata['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nb.cells = [
        nbf.v4.new_markdown_cell(HEADER),
        nbf.v4.new_markdown_cell('## Step 2 — parity with the June numbers + data-migration caveats'),
        nbf.v4.new_code_cell('%run run_parity.py'),
        nbf.v4.new_markdown_cell('## Step 3 — OOS window 2026-04-14 → last full day, informational tables'),
        nbf.v4.new_code_cell('%run run_oos.py'),
        nbf.v4.new_markdown_cell('## Step 4 — full-sample combined portfolio and the pre-registered verdict'),
        nbf.v4.new_code_cell('%run run_combined.py'),
        nbf.v4.new_markdown_cell('## Step 5 — fragility of the verdict (post-hoc; changes no threshold)'),
        nbf.v4.new_code_cell('%run run_fragility.py'),
        nbf.v4.new_markdown_cell('## Verdict and clause map'),
        nbf.v4.new_code_cell(SHOW_SUMMARY),
        nbf.v4.new_markdown_cell('## Parity table'),
        nbf.v4.new_code_cell(SHOW_PARITY),
        nbf.v4.new_markdown_cell('## OOS ledgers and regime'),
        nbf.v4.new_code_cell(SHOW_OOS),
        nbf.v4.new_markdown_cell('## Per-year, ETF split, combined portfolio, cumulative R'),
        nbf.v4.new_code_cell(SHOW_TABLES),
        nbf.v4.new_markdown_cell(
            '## Fragility of the BUILD\n\n'
            'Post-hoc measurement of an already-frozen verdict — leave-k-out, the regime-gate\n'
            'look-ahead audit, the OI-seam split, bootstrap CIs and the deflated Sharpe at\n'
            'N_TRIALS = 1. Nothing here changes a threshold or the verdict label; the discussion\n'
            'is in [findings.md](findings.md).'),
        nbf.v4.new_code_cell(SHOW_FRAGILITY),
    ]
    nbf.write(nb, NB_PATH)
    client = NotebookClient(nb, timeout=1800, kernel_name=kernel,
                            resources={'metadata': {'path': str(HERE)}})
    client.execute()
    nb.metadata['kernelspec'] = {'name': 'python3', 'display_name': 'Python 3', 'language': 'python'}
    nbf.write(nb, NB_PATH)
    print('executed and wrote', NB_PATH)


if __name__ == '__main__':
    main()
