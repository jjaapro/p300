# R4 discovery and sizing: validation review and remediation plan

Review date: 2026-09-14. Status: **P0 — preserve as exploratory research; headline Sharpe, live-calendar equivalence, and independent validation claims require correction before reuse.**

This is a retrospective audit/addendum, not a preregistration for the earlier grid search or sizing choices.
No notebooks/backtests ran, database was accessed, data fetched, or existing files changed.

## Reviewed evidence

- [era_split.ipynb](era_split.ipynb), all zero-based cells **0–5**.
- [grid_search.ipynb](grid_search.ipynb), all zero-based cells **0–6**.
- [sizing_study.ipynb](sizing_study.ipynb), all zero-based cells **0–10**.
- [walk_forward.ipynb](walk_forward.ipynb), all zero-based cells **0–4**.
- [year_breakdown.ipynb](year_breakdown.ipynb), all zero-based cells **0–7**.
- [r4_lib.py](r4_lib.py), full source, and [findings.md](findings.md), full text.
- All notebook source/markdown was read; notebooks have no saved result outputs and their main invocations are commented out.
- Findings reference standalone analysis `.py` files that are not present here; reproducibility instructions need a new, explicit notebook entry point.
- Downstream [R4 bot preparation review](../r4_bot_prep/VALIDATION_REVIEW_PLAN.md) supplies the calendar comparison, not independent validation.

## Supported strengths

The grid is systematic and exposes alternative entry hours, holds, weekdays, and month filters.
Era and year breakdowns reveal concentration rather than reporting only a full-sample mean.
The sizing notebook explicitly recognizes that expectancy weights estimated on the post-ETF period and tested there are in-sample.
The ultimate choice to keep symmetric weights is conservative relative to adopting the fitted higher-return weights.
The evidence documents a route to candidate calendar windows; it does not establish their economic mechanism or independently confirm their profitability.

## Evidence gaps

1. **Large search history.** `grid_search.ipynb` enumerates 12 entry hours × 6 holds × 14 weekday sets × 8 month filters: 8,064 raw cells per asset before screening.
   It ranks on multiple views and filters by sample size; the full matrix and every tried choice matter, not only the top rows.
   Cross-asset and era follow-ups on these same dates are part of the selection process.
2. **“Walk-forward” is one historical split.** `walk_forward.ipynb` selects top training t-stat configurations from 2018–2022 and evaluates 2023–2026.
   The other notebooks also inspect that later history, so it is not an untouched confirmation of the final selected windows.
   It is not a repeated chronological fit/select/test portfolio with a stitched unseen return series.
3. **ETH calendar mismatch.** `r4_lib.py:106` applies the day-of-month filter to the entry date.
   The bot-prep `sl_sweep.py:57` applies it to the next day for Tuesday 20:00→Wednesday 20:00 ETH.
   This changes boundary dates, including entry on the 14th and month ends; `findings.md:65–76` should not imply exact live-rule parity from this result.
4. **Sizing Sharpe is annualized on sparse active dates.** `sizing_study.ipynb` cells 6–7 aggregate complete window P&L on entry dates and use a square-root-252 factor.
   The 221 post-ETF observations in `findings.md:112–117` are active dates, not a full daily calendar.
   The reported annual Sharpe 5.03/4.56 is therefore not a validated account Sharpe.
5. **Portfolio risk is incomplete.** The sizing notebook omits actual within-hold MTM, cash days, funding, and full production gates/quantity dynamics.
   Summed gross returns are not compounded capital returns; identical omitted gates need not preserve allocation rankings when weights interact with exposure.
6. **Data and costs are weakly specified.** `r4_lib.py` uses gross open-to-open returns, an older database path, and hourly aggregation from available ETH minutes.
   Exact endpoint availability does not guarantee a complete hourly bar or executable open.
   Fees, funding, latency, and spread can materially affect smaller cells.
7. **Mechanism/validation language is too strong.** `findings.md:74–83` calls Tuesday uniqueness real and cross-asset similarity evidence of real macro flow.
   Lines 122–125 say rankings validate the V2 split. Shared market drift and retrospective selection are competing explanations.
   A negative sizing overlay result cannot validate the earlier window discovery by itself.

## Bounded remediation before reusing headline results

Create `validation_reconciliation.ipynb` in this directory, leaving old notebooks and findings untouched.
Record the full search lineage, including which dates/results were seen before each named window and sizing choice.
Make a manifest of notebook/code versions and source data definitions; label unrecoverable execution outputs explicitly.
Freeze the existing four named windows and equal-weight versus historical expectancy-weight comparison; do not start another grid search.
Resolve the ETH entry-day/next-day predicate before comparing to the shipped rule, with a ledger of exact differing dates.
Use fixtures for month end, entry date 14, next date 1/15, leap day, and overlapping V1/V2 positions.
Require executable endpoint timestamps and completeness rules; distinguish spot-proxy and perp claims.

Build one marked daily capital ledger on a common full UTC calendar, including flat days, overnight holds, funding, costs, and initial NAV.
Carry positions causally through dates rather than placing the entire 24-hour return on entry day.
Simulate the intended static/fraction-of-current-NAV allocation and leverage/gates explicitly.
Report standard arithmetic daily excess-return Sharpe, expectancy per fire, cumulative net capital return, and marked drawdown separately.
Show corrected versus original labels and values so historical prose cannot silently retain invalid annualized statistics.
No numerical correction automatically establishes that another weighting scheme is better.

## Forward evidence if a new R4 change is proposed

Use weekday/time controls and equal-exposure broad-market controls to distinguish a calendar effect from ordinary crypto drift.
Pair BTC/ETH and all windows on the same calendar; overlapping Wednesday/Friday positions are not independent trades.
For inference, use jointly resampled calendar blocks and report sensitivity to loss clusters and major trend eras.
Start with 14/30-day blocks; disclose uncertainty from a small number of independent calendar cycles.
Estimate allocation weights only on prior data in a genuine chronological fitting experiment, with a fixed rebalance schedule and frozen fallback for weak estimates.
Any fold training must exclude outcomes extending across its boundary; preserve position-state rules explicitly.
Do not select a new window or weighting scheme on test folds and still call their return series out-of-sample.
Historical 2023–2026 results already seen here remain development/replication; prospective confirmation begins after a new protocol freeze.
If ranking thousands of cells again is ever justified, save the complete return matrix/trial ledger and use family-level selection correction plus a separate chronological confirmation stage.
DSR/PBO are diagnostics of selection assumptions, not substitutes for causal fills or unseen observations.

## Decision rules and planned outputs

- Calendar/data parity report, exact fixed candidate list, full-calendar capital ledger, and original-versus-corrected metric table.
- Paired equal-weight versus frozen alternative comparison with economic threshold, uncertainty, costs, and drawdown tolerance declared in advance.
- A chronology/trial manifest distinguishing discovery, historical checks, fitted sizing, and any genuinely future confirmation.

Keep the original no-reweighting disposition unless new evidence supports a separately reviewed change.
The evidence gate is a credible net incremental benefit at equal capital with acceptable risk; a higher fitted mean or top grid t-stat is insufficient.
Use inconclusive when intervals include the useful-effect threshold; do not call low power proof that no calendar effect exists.
ORB range controls, equity corporate actions, and passive queue modeling are **N/A** to the fixed-time crypto windows.
Current live strategy parity, the historical database content, original code execution, and actual account performance were **not verified**.
