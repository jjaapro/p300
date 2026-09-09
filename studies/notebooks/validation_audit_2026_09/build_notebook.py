"""Builds validation_audit.ipynb — the viewer notebook for the Track V4 audit.

The venv python has no nbformat, so run this with the system Python:

    C:/Python/Python313/python.exe studies/notebooks/validation_audit_2026_09/build_notebook.py

The notebook only READS results/*.csv|json — it recomputes nothing, so opening
it can never disagree with findings.md.
"""
from __future__ import annotations

from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent


def md(t: str):
    return nbf.v4.new_markdown_cell(t)


def code(t: str):
    return nbf.v4.new_code_cell(t)


CELLS = [
    md("""# Track V4 — validation audit of p300's own track record

**STATUS: CONCLUDED AUDIT per pre-registration** ([README.md](README.md) →
[findings.md](findings.md))

This is an **audit**, not an alpha search: `N_TRIALS = 1` for every paper
series, there are **no KILL rules**, and nothing was switched off as a result.
The deliverable is an honest-Sharpe table — what p300's numbers look like after
deflation, bootstrap uncertainty and multiple-testing correction.

This notebook only reads `results/`. It recomputes nothing."""),

    code("""import json
from pathlib import Path
import pandas as pd

HERE = Path.cwd() if (Path.cwd() / 'results').exists() else Path('studies/notebooks/validation_audit_2026_09')
R = HERE / 'results'
pd.set_option('display.width', 220)
pd.set_option('display.max_columns', 120)

a = pd.read_csv(R / 'part_a_paper_audit.csv')
b = pd.read_csv(R / 'part_b_backtest_audit.csv')
extras = json.loads((R / 'part_b_extras.json').read_text(encoding='utf-8'))
recon = json.loads((R / 'chento_cost_reconciliation.json').read_text(encoding='utf-8'))
c0 = json.loads((R / 'part_a_c0.json').read_text(encoding='utf-8'))
print(f'Part A rows: {len(a)}   Part B rows: {len(b)}')"""),

    md("""## 1. Headline — the honest-Sharpe table

Nothing in this study reaches DSR ≥ 0.95. The best number in the audit is
chento COMBINED at the conservative trial count: **0.876**."""),

    code("""cols_a = ['key', 'series', 'window', 'n_trades', 'n_daily_rows',
          'sharpe_daily_ann', 'boot_trade_sr_p05', 'boot_trade_sr_p95',
          'dsr_trade', 'haircut_holm_sharpe',
          'alpha_alpha_annual_pct', 'alpha_t_alpha_nw', 'alpha_r_squared',
          'verdict']
a[cols_a].round(4)"""),

    code("""cols_b = ['key', 'n_trades', 'n_daily_rows', 'unit', 'mean_per_trade',
          'sharpe_daily_ann', 'block_daily_sr_p05', 'block_daily_sr_p95',
          'dsr_cons', 'dsr_aggr', 'hc_cons_holm_sharpe', 'hc_aggr_holm_sharpe',
          'alpha_alpha_annual_pct', 'alpha_t_alpha_nw', 'alpha_r_squared',
          'cpcv_decay_ratio', 'verdict']
b[cols_b].round(4)"""),

    md("""## 2. Part A — the paper ledgers are not yet evidence

Six bots, **six closed trades between them** (all `bot_chento_v3_v1`, three days
in August). `bot_r4_v1` is declared in `bots/r4/config.py` but has never
registered in the `variants` table — the operator has not started the runner.

The 13-trade legacy R4 record is the only R4 paper record that exists, and it
lies **entirely** inside the pre-2026-05-16 window the repo's own memory marks
as not fully trustworthy."""),

    code("""print(json.dumps(c0, indent=2))
print()
print('trust-cutoff split (pre-2026-05-16 rows are not fully trustworthy):')
a[a.key.str.contains('post') | a.key.isin(['A7', 'A8'])][
    ['key', 'window', 'n_trades', 'total', 'mean_per_trade', 'verdict']].round(4)"""),

    md("""## 3. The chento headline expectancy is a zero-cost number

The published `+0.80 R/trade` comes from `run_overlays.py::replay` — TIF 72 h
and **no transaction cost**. The trade file it reads carries a *different*
series in `r_outcome` — TIF 24 h with 18 bp charged (`+0.225 R`).

`audit_chento_cost_check.py` reproduces the published figure to **+0.0000**,
then charges the source pool's own 18 bp model."""),

    code("""for asset, d in recon.items():
    print(f\"=== {asset}: n={d['n_okx_aligned']}  mean cost {d['cost_r_mean']:.4f} R/trade \"
          f\"(median {d['cost_r_median']:.4f}, max {d['cost_r_max']:.4f})\")
    if 'reproduced_mean_r_base_none' in d:
        print(f\"    reproduction of overlay_summary.csv: published \"
              f\"{d['published_mean_r_base_none']:.4f}  reproduced \"
              f\"{d['reproduced_mean_r_base_none']:.4f}  diff {d['reproduction_diff']:+.4f}\")
    display(pd.DataFrame(d['variants']).round(3))"""),

    code("""pd.DataFrame(extras['chento_cost_tif_dsr_sensitivity']).T[
    ['n', 'mean_r', 'sr_per_trade', 'dsr_cons', 'dsr_aggr']].round(4)"""),

    md("""## 4. ADX — reproduction, and the comparison that actually decides it

The audit reproduces the study's published table **exactly** at the study's own
end date, which validates the read-only candle loader.

`harness.py`'s `sharpe` field is `mean/pstdev × √n` — a **per-trade
t-statistic**, not an annualised Sharpe. The annualised daily Sharpe is 0.84
(baseline) / 1.12 (Tier-2).

The pre-registered R²-based beta clause does not fire, but that test is weak for
a strategy flat 64 % of days. The decision-relevant question is whether it beats
simply holding BTC — and buy-and-hold over 2018-2026 is itself significant
(DSR 0.982 at N=1)."""),

    code("""print(json.dumps(extras['adx_reproduction_check'], indent=2))"""),

    code("""e = pd.DataFrame(extras['adx_exposure_decomposition']).T
e[['time_in_market_pct', 'time_long_pct', 'time_short_pct',
   'strategy_daily_sharpe_ann', 'btc_buy_hold_daily_sharpe_ann',
   'btc_buy_hold_dsr_n1', 'excess_volmatched_daily_sharpe_ann',
   'sharpe_diff_point', 'sharpe_diff_p05', 'sharpe_diff_p95',
   'sharpe_diff_p_gt_0']].astype(float).round(4)"""),

    md("""`SR(strategy) − SR(buy-and-hold)` for Tier-2 is **+0.375** with a 5-95
interval of **[−0.400, +1.178]** and P(diff > 0) = 0.78. On 28 trades in
8.5 years you cannot establish that ADX beats levered buy-and-hold.

Note: the `alpha_long_days_only` regression in the JSON gives beta = 0.995,
R² = 0.9998. That is a **construction identity** (the mark-to-market series is
defined as direction × BTC return), not a finding."""),

    md("""## 5. Flat-max and PBO

PBO is **partial** — only the 4-variant tilt family is reconstructible from
files on disk. The 5 wick-exit variants need a bar-by-bar replay and the H-tag
needs the events table; re-running either would be the parameter sweep this
study forbids. ADX PBO is skipped entirely (`experiments.py` saves nothing)."""),

    code("""fm = {**extras['chento_flat_max'], 'adx_atr_mult': extras['adx_flat_max_atr_mult']}
rows = []
for k, v in fm.items():
    rows.append({'axis': k, 'chosen': v['chosen'], 'chosen_score': v['chosen_score'],
                 'peak': v['peak_param'], 'peak_score': v['peak_score'],
                 'flat_max_score': v['flat_max_score'],
                 'relative_drop': v['relative_drop'], 'verdict': v['verdict']})
display(pd.DataFrame(rows).round(3))
for k, v in fm.items():
    print(f\"\\n{k} ({v['metric']})\")
    for p, s in sorted(v['scores'].items(), key=lambda kv: float(kv[0])):
        mark = '  <- chosen' if float(p) == float(v['chosen']) else ''
        print(f'   {float(p):+6.2f}  {s:8.3f}{mark}')"""),

    code("""print(json.dumps(extras['chento_pbo_partial'], indent=2))
print()
print(json.dumps(extras['adx_pbo'], indent=2))"""),

    md("""## 6. Trial counts — where every N came from

The trial count matters more than any other input to a deflated Sharpe. Both a
conservative (documented, on disk) and an aggressive (stated assumption) figure
are computed so the reader sees the range."""),

    code("""for k, v in extras['trial_counts'].items():
    print(f'{k}:\\n    {v}\\n')"""),

    md("""## 7. Verdict

**Part A** — every single-strategy paper series fails the observation floor
(max 13 closed trades). The whole R4 paper record sits inside the untrustworthy
pre-2026-05-16 window; restricted to trustworthy rows it is **empty**.

**Part B** — neither backtest clears DSR 0.95 at any documented trial count.
The point estimates are real (every chento bootstrap lower bound is positive,
un-tilted alpha t-statistics clear 2); the binding constraint is **breadth, not
edge**. Multi-asset is the one lever that visibly improves the statistics.

Full discussion, deviations and prior-scoring: [findings.md](findings.md)."""),
]


def main() -> int:
    nb = nbf.v4.new_notebook(cells=CELLS)
    nb.metadata["kernelspec"] = {"display_name": "Python 3",
                                 "language": "python", "name": "python3"}
    out = HERE / "validation_audit.ipynb"
    nbf.write(nb, str(out))
    print(f"wrote {out}  ({len(CELLS)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
