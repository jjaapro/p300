# Coinbase premium: validation review and conditional remediation plan

Review date: 2026-09-14. Status: **P0 — execution and artifact provenance do not support reuse of positive edge/latency claims; retain the documented KILL outcome.**

This is a retrospective audit/addendum. It does not replace either historical run or assert that reconstructed plans were preregistered.
No code was imported or executed, database accessed, data fetched, or existing notebook/result modified.

## Reviewed evidence

- [coinbase_premium.ipynb](coinbase_premium.ipynb), all zero-based cells **0–17**.
- [coinbase_premium_run_A_2026_09_08.ipynb](coinbase_premium_run_A_2026_09_08.ipynb), all zero-based cells **0–23**.
- Both notebooks' source/markdown and available textual outputs; they principally display saved results.
- [README.md](README.md), [findings.md](findings.md), [findings_run_A_2026_09_08.md](findings_run_A_2026_09_08.md), full text.
- Key panel/feature/execution/statistical/output code in [analysis.py](analysis.py), [premium_study.py](premium_study.py), and the documented descriptive addendum.
- Saved [results/report.json](results/report.json), [results/clauses.json](results/clauses.json), headline/provenance/era/sensitivity JSON, and referenced descriptive tables.
- Raw cross-venue bars and full individual trade paths were not independently inspected.

## Supported findings

Both retained runs record a KILL decision, and the findings explicitly preserve the fact that they differ.
The fixed rule and the predecessor's broader BTC search are described separately.
ETH transfer, post-ETF/2025+ checks, 48-hour drift comparisons, and zero-cost/entry sensitivity are useful questions to ask.
Trailing z-score means and standard deviations exclude the firing observation (`analysis.py:116–129`).
The descriptive premium/forward-return association is worth retaining as an observation, without interpreting it as a deployable causal effect.
BTC's recent negative point estimate justifies the original conservative decision clause; an interval spanning zero does not prove a negative population mean.

## Material problems

1. **Historical provenance is incomplete.** README lines 204–234 and both findings documents disclose overwritten artifacts and differences between runs A/B.
   Preserve that disclosure: a later freeze cannot erase exposure to the earlier outcomes.
   Contrary to the no-collision wording, `analysis.py:430` and `premium_study.py:526` both write `results/premium_by_year.csv`.
   Artifact viewers therefore need run-specific manifests; the current directory alone does not prove each table belongs to one coherent run.
2. **Incomplete-hour policy changes the ETH test.** `analysis.py:81–93` retains first/last available minute aggregations.
   `premium_study.py:105–108` requires a :59 last minute but does not require all 60 minutes or a :00 open.
   Eight dropped ETH hours can cascade through thresholds/cooldowns; the two saved ETH means and confidence bounds differ.
   Agreement on final KILL is not evidence that the data-policy discrepancy is immaterial to a positive claim.
3. **Signal and execution time are conflated.** `analysis.py:151–174` observes both venues' completed closes and enters at the same Binance close.
   Lines 198–199 label entry with the bar-open timestamp, even though its close is used.
   The close can only be acted on after both source observations arrive; a next available executable price is required for a tradability claim.
4. **Next-open sensitivity misses entry-hour stops.** `premium_study.py:199–218` enters at the next hour's open but scans lows starting at `e_idx+1`.
   The entire entry hour's low is skipped. This sensitivity cannot validate the latency/execution concern as implemented.
5. **Inner joining signal and execution data changes elapsed time and omits risk.** `analysis.py:107` and `premium_study.py:114` inner join Coinbase and Binance.
   Lookback, hold, cooldown, and stop loops then count surviving rows, not necessarily elapsed hours.
   A missing Coinbase hour can delete a Binance stop path while a trade is open.
   Signal availability must be separate from the complete execution grid.
6. **Price-level stop fills are optimistic.** Hourly lows do not establish a fill at the stop after a gap or during a missing interval.
   The same rule's 48-hour no-stop outcome and stop outcome are different hypotheses; a positive diagnostic is not confirmation of the original strategy.
7. **Statistical independence and mechanism are overstated.** Iid per-trade intervals and a single `::48` sample (`analysis.py:344–346`) do not eliminate regime dependence.
   BTC and ETH premium largely share venue/quote effects; they are not independent asset replications.
   USD-versus-USDT basis, broad momentum, volatility, and common flows can explain association without a Coinbase-specific predictive mechanism.

## Disposition and bounded correction

Retain the two historical KILL outcomes and their distinct rule/data definitions.
Do not pool run A/B tables or replace their original confidence intervals with a preferred version.
First create `artifact_reconciliation.ipynb`: inventory every result's producer, schema, timestamp/hash, and shared-name collision.
Document what cannot be recovered; mark unmatched outputs as provenance-uncertain rather than fabricate a historical run identity.
Reconcile ETH aggregation and eligible signal differences by exact timestamps, including downstream cooldown changes.
Reconcile the entry-clock and next-open stop-loop defects before citing the saved sensitivity as evidence of executable edge.
Use deterministic fixtures for missing Coinbase hours, missing :00/:59 Binance minutes, a stop in the entry hour, a gap through the stop, and a truncated final trade.
None of this requires searching new thresholds or rerunning an archived hypothesis to rescue it.

## Conditional future test if the strategy is revived

Freeze one rule, one aggregation/completeness policy, signal receipt cutoff, execution venue/instrument, and actual elapsed-hour horizon.
Store Coinbase features on a causal signal grid but walk positions on the full Binance execution grid, including outages of the signal venue.
Entry follows availability of both completed bars plus a specified latency; use actual minute/finer paths where needed.
Specify gap fills, fees/spread/slippage, terminal exposure, and missing-data abstention before viewing returns.
Funding is **N/A for an unleveraged spot-long claim**; include it only for a separately defined perp implementation, with matching execution prices.
Model USD/USDT conversion if interpreting a cross-venue premium as an economic arbitrage or flow measure.

Compare with same-time unconditional drift and volatility/momentum-matched controls at equal exposure.
Add a predeclared USD/USDT-basis control to test whether information is specific to the venue premium.
Preserve all historical predecessor trials, both implementations, stops/no-stop diagnostics, and sensitivities in the trial ledger.
Count additional choices before results; local N=1 ETH DSR does not erase BTC development or selection across assets.
Use common calendar blocks across BTC/ETH and paired policy/control returns; report 7/14/30-day sensitivity given 48-hour holds.
If decile plots use overlapping outcomes, show all calendar offsets with dependence-aware intervals rather than treating thousands of overlapping rows as independent.
Measure a useful net effect and report inconclusive intervals honestly; bootstrap sign frequency is not a posterior probability that an edge exists.
Only observations after a new protocol freeze provide prospective confirmation; the inspected 2020–2026 eras remain historical replication.

## Planned notebook outputs and limits

- Run-specific artifact manifest and A/B timestamp/data-policy reconciliation, with unrecoverable provenance explicitly recorded.
- Event-clock and entry-hour stop fixtures; one fixed candidate versus controls with coverage, full execution ledger, and cost bounds.
- Trial lineage, paired block intervals, era diagnostics, and a three-way decision: reject, inconclusive, or seek independent confirmation.

No parameter sweep is needed to preserve KILL, and this review does not recommend building the strategy.
ORB opening sessions, equity corporate actions, and passive-order queue priority are **N/A** to the original hourly market-entry rule.
Source ingestion latency, historical code hashes, full data completeness, actual fills, and corrected numerical performance were **not verified**.
