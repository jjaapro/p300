# ADX ETH transfer: validation review and remediation plan

Review date: 2026-09-14. Status: **P0 — exact performance evidence invalid for reuse until the shared execution model is corrected; retain the historical KILL decision.**

This is a retrospective static audit and a proposed addendum, not a preregistration of completed work.
No notebook, backtest, database query, data download, or production change was performed for this review.
The saved results are evidence of what the study reported, not independently reproduced results.

## Reviewed evidence

- [adx_eth.ipynb](adx_eth.ipynb), all zero-based cells **0–4**, including saved textual execution and table outputs.
- [README.md](README.md), [findings.md](findings.md), and [run_n1_eth.py](run_n1_eth.py), full text.
- [results/n1_eth.json](results/n1_eth.json), including the 24 phase records, decision clauses, and BTC/ETH comparison.
- [results/n1_phases.csv](results/n1_phases.csv) and the phase-0 ledger as displayed by notebook cell 4.
- Shared [../adx_robustness_2026_09/adx_lib.py](../adx_robustness_2026_09/adx_lib.py), especially lines 117–168; the corresponding BTC audit is a dependency.
- Cross-study metric/helper behavior was inspected statically; no data provenance or saved artifact hashes were verified.

## What the evidence supports

The study transferred the selected BTC Tier 2 rule to ETH without an ETH parameter sweep.
It reported all 24 daily phase offsets rather than retaining the best one.
The historical decision is correctly recorded as KILL: phase-0 trade t is below 1.5 and only 19/24 phases clear Sharpe 0.5, below the frozen 80% requirement.
Phase 0 has only 19 closed trades; the study appropriately avoided declaring an attractive chart sufficient to build an ETH sleeve.
The BTC/ETH comparison asks a useful incremental portfolio question rather than treating a second positive curve as automatically additive.

## Material gaps and implications

1. **Shared entry-day stop omission.** `run_n1_eth.py:35` calls `al.live_walk`.
   In the shared `adx_lib.py:141`, `continue` runs inside the daily loop immediately after entry.
   It skips the whole first holding day's minute walk, despite the comment saying only the entry minute is skipped.
   Closed trades, subsequent signals, phase rankings, and all dependent metrics can therefore change.
2. **Funding timing and terminal exposure.** Shared `adx_lib.py:161–168` books cumulative funding as an exit cost.
   The walk returns closed trades, without a terminal open-position mark-to-market ledger.
   This is inadequate for daily drawdown, Sharpe, and cross-asset correlation at a common cutoff.
3. **Phase comparisons use different calendars.** `run_n1_eth.py:38` computes each phase's curve statistics before alignment.
   Saved `n1_eth.json` phase histories range from 1,933 to 2,271 days, despite a nominal common study period.
   A near-threshold 19/24 pass fraction should not be interpreted as a stable rejection boundary under this construction.
4. **Conditional correlation is mislabeled.** `run_n1_eth.py:67–74` unions dates, fills absent returns with zero, and selects days where either return is nonzero.
   The reported 0.473 is conditional on that filter; nonzero P&L is not an exposure flag.
   The phase-0 ETH standalone and combined tables also annualize over different spans.
5. **Report mismatch.** `findings.md` gives phase median CAGR/Sharpe of about 18.5%/0.60.
   Saved `n1_eth.json.phase_ranges` gives about 20.51%/0.634, also shown in notebook cell 2.
   A future addendum must identify the source of the discrepancy instead of silently replacing history.
   Independently, the shared MTM helper is also mathematically inconsistent with fixed-quantity accounting: `adx_lib.py:168` calls `sizing_style_2026_09/sizing_lib.py:109`, which applies original notional to successive percentage changes.
   Its daily P&L need not sum to the trade's fixed-quantity price P&L. Resolve this dependency in the [sizing-style review](../sizing_style_2026_09/VALIDATION_REVIEW_PLAN.md) before interpreting any ETH NAV statistic.
6. **One transfer is not one experiment in the project's history.** N_TRIALS=1 describes the local ETH rule count.
   The BTC selection and funding/exit research are inherited information; correlated BTC/ETH histories are not independent replication.
   DSR's default iid approximation does not settle uncertainty from 19 multiweek trades.

## Bounded forward work, only if these results will be reused

First complete the shared-engine remediation in [the BTC robustness review](../adx_robustness_2026_09/VALIDATION_REVIEW_PLAN.md).
Keep the original ETH parameters and all 24 phase offsets frozen; do not search for a replacement winning phase.
Record an immutable input manifest, code revision, venue/instrument, source timestamps, complete-data cutoff, and the exact original rule lineage.
Use an identical complete UTC calendar for every phase and for BTC/ETH comparison, including flat days and terminal open exposure.
Separate missing observations from genuine zero P&L; define a whole-session eligibility rule before examining outcomes.
Use executable perp prices for a perp claim, with funding cash flows at actual settlement timestamps and documented quantity conventions.
If only spot execution is available, label the result a spot-price proxy and avoid deployment-risk claims.

Create a small causal trace for entry, initial stop, first-day minute path, next daily trail update, signal exit, and final mark.
Require the sum of marked price P&L to match fixed quantity times signed exit-minus-entry price regardless of the number of interim marks.
Fixtures should cover an entry-day stop, a gap across the stop, simultaneous boundary events, missing minutes, and an open final trade.
Reconcile corrected-versus-original trades by entry identity, and decompose changed P&L into stop timing, funding timing, and calendar effects.
Evaluate the existing frozen decision clauses once on the corrected panel and preserve both old and new verdict tables.
If clauses disagree or uncertainty is wide, report **inconclusive**, not a newly confirmed edge.

For the diversification claim, compare a frozen BTC-only allocation with the fixed equal-weight pair on the same daily ledger.
Report all-calendar correlation, exposure-conditional correlation, joint drawdown episodes, and incremental return/risk at equal total capital.
Use paired calendar blocks jointly across both assets; use 30-day blocks as a starting diagnostic with 14/60-day sensitivity.
Sparse trend episodes may require episode-level uncertainty as well; blocks do not manufacture independent market regimes.
Report intervals for the pair-minus-BTC contrast and the minimum improvement worth adding operational complexity.
Do not infer a material improvement from MAR 0.755 versus 0.748 without uncertainty and execution costs.

Any future adoption proposal needs a newly frozen observation period after the proposal, or an explicitly labeled historical replication.
Reusing the already inspected 2020–2026 history cannot create an untouched holdout.
ETH-only retuning, additional vetoes, or selecting among phases starts a new trial family and requires a new plan.

## Planned notebook outputs and completion gates

- A new `validation_reconciliation.ipynb` in this directory: manifest, engine fixture traces, common-calendar coverage, and old/new ledger differences.
- One table containing all 24 corrected phase results, decision inputs, sample sizes, and common start/end dates.
- A BTC/ETH exposure panel and paired portfolio comparison with block intervals and stressed costs.
- A short disposition: historical KILL retained, corrected KILL, or inconclusive; any later build proposal remains a separate decision.

No new performance claim is ready until the entry-day path and cash-flow reconciliation pass.
No optimization is required to retain the archived no-build decision.
Opening-session, intraday breakout-direction, equity holiday, and order-book queue controls are **N/A** to this daily ADX transfer.
Perp funding, stop gaps, daily boundary timing, and correlated-asset uncertainty are applicable.
Live dispatch parity, current production configuration, exchange fills, data completeness, and future profitability were **not verified**.
