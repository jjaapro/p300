# FOMC attribution, phase drilldown and leverage sensitivity — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `compare_with_fomc.ipynb`
- `fomc_backtest_drilldown.ipynb`
- `fomc_leverage_sensitivity.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The family separates event diagnostics, a with/without contribution view and leverage sensitivity. A seeded Monte Carlo and explicit cost convention improve repeatability. The source is useful exploratory scaffolding, not saved execution evidence in these notebooks.

## Findings and limits

1. compare_with_fomc cells 4/7 remove FOMC rows from the realized book and book remaining P&L at exit. This estimates contribution within that executed ledger; it does not recompute free capital, skipped trades, position conflicts or marked risk of an executable portfolio without FOMC.
2. fomc_backtest_drilldown cell 0 compares win rates with phase expectations, and cell 3 joins observer phase labels by FOMC date. The review cannot establish that historical phase labels/expectations were available before each trade or were independently calibrated. Latest labels are not an as-of signal history.
3. fomc_leverage_sensitivity cell 3 does not load direction, while cell 6 calculates exit/entry−1. A long-only ledger prerequisite must be verified, or shorts are scored incorrectly. Closed-trade compounding and iid event resampling omit intrahold margin, liquidation, funding and clustered event shocks.
4. The leverage table inspects eleven leverage settings. Its Monte Carlo uses the observed empirical tails and cannot establish safety at 10–30× or bound unobserved losses. These risk claims would be P0 before reuse. All three notebooks retain __file__ setup and commented main calls.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_fomc_lineage.ipynb: snapshot all scheduled announcements, changed dates/times, eligibility, skipped signals, historical phase/F&G inputs and their publication times. Freeze one existing rule and its full historical search lineage; record observed versus genuinely uninspected event periods.
2. 01_event_replay.ipynb: reproduce each eligible event at a causal fill time with direction, venue, fees, funding and intraday stop/exit handling. Reconcile logged signals and fills; show all missing/skipped/censored events rather than only closed winners and losers.
3. 02_event_validation.ipynb: use chronological event folds with training-only phase thresholds, event-level purging and joint BTC/ETH time clusters. Set a meaningful net effect and attainable precision before testing; use a simple same-time ungated event rule, exposure-matched random dates and cash as relevant controls. No fixed trade count is proof of power.
4. 03_fomc_portfolio_risk.ipynb: compare a full concurrent portfolio with/without FOMC using identical capital rules. Start with unit notional, then the existing intended leverage; treat additional leverage arms as sensitivity, not a winning leverage search. Replay mark-price margin, funding, spread/latency shocks and gap losses; compare cashflow attribution with executable incremental results.

## Frozen decisions and stopping rules

Attribution may be completed by reconciliation alone. An edge claim requires valid as-of data and engine plus a positive, sufficiently precise net incremental effect under the frozen rule. Insufficient events are inconclusive. No leverage or live-order decision follows from this retrospective plan.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
