# Opening Range Breakout (ORB) study

**Status: CONCLUDED 2026-09-15.** The pre-registered campaign ended at validation: both frozen candidates failed
every performance clause, the lockbox was not opened for confirmation, and the verdict is
**INCONCLUSIVE + PRICE_SIGNAL_ONLY** with negative point estimates (BTC P0 −6.8 bp per trade net in 2023–24).
Read [findings.md](findings.md) first. No production code, bot or paper trading was touched.

Scope: Binance BTCUSDT and ETHUSDT linear perpetuals, one-minute bars, 2020-01-01 → 2026-09-13. Everything the study
downloads, builds and writes stays in this directory; prod.db is only ever opened read-only.

## Documents

| File | What it is |
|---|---|
| [findings.md](findings.md) | Verdict, development and validation numbers, why ORB fails, exit events, limitations |
| [PREREGISTRATION.md](PREREGISTRATION.md) | Frozen rules v1.0: data gate (with Amendment A1), P0, the 46-policy family, controls, costs, chronology, selection, continuation rule, verdicts |
| [TEST_PLAN.md](TEST_PLAN.md), [RESEARCH.md](RESEARCH.md) | The 2026-09-14 draft plan and literature review the pre-registration resolved (kept unchanged) |
| `trial_ledger.csv` | Every policy evaluation, its block, use and outcome-access time |
| `results/freeze_F0.json` … `freeze_verdict.json` | SHA-256 freeze manifests with UTC timestamps, in stage order |

## Notebooks (executed; each recomputes from the panels and compares with the frozen results)

| Notebook | Question |
|---|---|
| [00_research_and_plan.ipynb](00_research_and_plan.ipynb) | The draft plan as a notebook (Markdown only) |
| [01_data_and_calendars.ipynb](01_data_and_calendars.ipynb) | Is the data good enough? Archive vs REST vs trades, outages, funding, tick size, calendars |
| [02_reference_engine.ipynb](02_reference_engine.ipynb) | Does the engine do what the rules say? Fixtures, two-implementation parity, truncation, sessions by hand |
| [03_development.ipynb](03_development.ipynb) | BTC 2020–22: P0, anchors, controls, exits, selection, multiple testing, power |
| [04_validation_and_verdict.ipynb](04_validation_and_verdict.ipynb) | BTC/ETH 2023–24: the continuation rule and the frozen verdict |
| [05_exploratory_exits_and_mechanism.ipynb](05_exploratory_exits_and_mechanism.ipynb) | Post-verdict, all blocks and assets: decay, random controls, anchors, exit events, excursions |

## Code

| Module | Role |
|---|---|
| `orb_data.py` | Download and checksum the Binance archive; build the dense minute panels and funding arrays |
| `orb_calendars.py`, `configs/` | Session anchors from frozen NYSE/LSE calendars (IANA time zones, DST-exact) |
| `orb_engine.py` | Reference engine: one session, one bar at a time |
| `orb_signals.py` | Independent vectorized implementation used only for parity and the randomized controls |
| `orb_policies.py` | The policy registry (46-policy family, controls, diagnostics) |
| `orb_metrics.py`, `orb_controls.py` | Costs, calendar returns, block bootstrap, Holm; randomized and buy-and-hold controls |
| `orb_checks.py`, `orb_parity.py` | Data gate and engine checks written before F0 |
| `orb_run.py` | The stages: `freeze0`, `development`, `validation`, `lockbox`; each refuses to rerun past its freeze |
| `orb_explore.py` | Post-verdict exploratory tables (refuses to run before the verdict) |
| `tests/` | Synthetic fixtures, calendar checks, accounting checks; `test_run_smoke.py` is opt-in (`ORB_SMOKE=1`) |
| `build_notebooks.py`, `notebook_cells.py`, `review_plots.py` | Assemble and execute notebooks 01–05 |

## Reproduce (from the repository root)

```powershell
venv\Scripts\python.exe studies\notebooks\orb_study\orb_data.py download   # ~290 MB into data/raw (gitignored)
venv\Scripts\python.exe studies\notebooks\orb_study\orb_data.py build      # ~181 MB into cache (gitignored)
venv\Scripts\python.exe -m pytest studies\notebooks\orb_study\tests -q
C:/Python/Python313/python.exe studies/notebooks/orb_study/build_notebooks.py
```

The raw zips were deleted on 2026-09-15 to relieve a full C: drive and re-downloaded the same day once space
was freed; all 346 matched the SHA-256 values in `data/raw/binance_um/manifest.json`.

The stage runner will not redo a stage whose freeze exists. To audit a freeze, compare the SHA-256 values in
`results/freeze_F0.json` with the files, or run notebook 03, which calls `orb_run.verify_f0()`.
