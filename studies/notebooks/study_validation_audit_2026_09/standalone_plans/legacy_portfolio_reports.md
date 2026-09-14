# Legacy portfolio and replay reports — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P0.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `backtest_report.ipynb`
- `full_portfolio_report.ipynb`
- `portfolio_performance.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

These reports distinguish sleeve attribution and, in full_portfolio_report, explicitly reconstruct marked fixed-capital P&L additively and calculate Sharpe using opening NAV. Those corrections are useful; they do not make the three reports mutually equivalent.

## Findings and limits

1. backtest_report cell 6 compounds the output of trades_daily_returns even though the helper's fixed-capital P&L convention is additive. Its exit-date-only series also excludes unrealized risk. Cell 9 starts its drawdown peak after the first reported return, so an initial loss can be missed.
2. full_portfolio_report cell 11 constructs consecutive simple buy-and-hold returns, then passes them to equity_metrics (cell 9), which sums fixed-capital P&L percentages. The benchmark violates that function's input contract. Analytic example: prices 100 → 110 → 99 produce returns 0%, +10%, −10%; summing reports 0% while fixed-quantity buy-and-hold loses 1%.
3. portfolio_performance cells 1/3 query whole-variant closed trades while displaying a fixed date window, and its calendar curves book P&L at exit. Cell 3's saved output has an empty tactical book and a zero combined portfolio beside nonzero analytic sleeves; this is not evidence of a validated zero-return portfolio.
4. backtest_report and full_portfolio_report cell 2 depend on __file__; they are script conversions with commented main calls. Reproduction needs a real notebook entry point, explicit source snapshot and selected variant, not silent fallback capital or a mutable current ledger.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_report_contract.ipynb: freeze variant, as-of date, capital, contribution units, start/end boundaries and whether open positions belong in the report. Fail on an absent variant, empty required ledger or missing marks; reconcile the historical saved output before labeling it superseded.
2. 01_accounting_reconciliation.ipynb: use independent fixed-quantity and fixed-capital examples, initial-day loss, cross-year holdings, open positions, deposits, funding, partial exits and concurrent sleeves. Reconcile ending NAV to starting cash plus cashflows and marked inventory to declared monetary tolerance.
3. 02_report_comparison.ipynb: feed the same snapshot and date interval into corrected study-only versions of all three reports. Separate realized cashflow DD, daily marked DD and intraday bounds. Use direct quantity × price for buy-and-hold; align first investment time, idle capital and fees.
4. 03_report_decision.ipynb: export per-trade and per-day reconciliation differences plus before/after metric tables. Attribute every difference to semantics, source, dates or a defect; do not tune a strategy during this exercise.

## Frozen decisions and stopping rules

Complete when arithmetic fixtures and ledger/NAV/benchmark reconciliations pass, missing data fails explicitly, and the notebook reproduces from a fresh kernel. These are reporting tools: DSR, broad parameter searches and an alpha holdout are not prerequisites for reporting correctness. Any new portfolio superiority claim needs the separate portfolio plans.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
