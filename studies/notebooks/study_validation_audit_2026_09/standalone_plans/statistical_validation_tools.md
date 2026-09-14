# Paper-ledger statistical report — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `tools_statistical_validation.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The source uses zero-filled calendar dates, seeded bootstrap sampling, explicit annualization and additive fixed-capital CAGR/drawdown. It provides useful descriptive stability and correlation diagnostics with no intended database writes.

## Findings and limits

1. Cells 4/5 use first-to-last closed-trade exit dates and realized P&L. Long holdings and open trades disappear from the risk path. A precise bootstrap of that cashflow series is not a confidence interval for marked trading risk.
2. Cell 15 resamples individual days independently; BCa bias correction does not preserve holding-period or volatility dependence. It also has unhandled degenerate cases when the bias proportion reaches 0 or 1, and no explicit empty-input guard. Calibration should precede reuse as a general validator.
3. Cell 21 labels correlation as daily log returns although cell 19 converts BTC to simple returns. Correlation thresholds are used to narrate BTC-beta dependence, but correlation alone is not beta, explained return or independence. Rolling windows overlap and are descriptive, not independent tests.
4. The notebook is a converted script with __file__ and commented main, no saved execution record, and no family search correction or untouched evaluation. Those omissions are acceptable for a labeled ledger report but not a selected-strategy validation claim.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_statistical_input_contract.ipynb: freeze variant, snapshot, full observation window and fixed-capital versus opening-NAV returns. Show realized and marked paths separately, reconcile funding/fees/open inventory, and explicitly fail missing or nonfinite input.
2. 01_statistical_calibration.ipynb: test zero/constant series, one observation, isolated losses, skewed iid and dependent synthetic series, bootstrap edge probabilities and known equity curves. Measure empirical interval coverage across simulated datasets, not just agreement with the implementation.
3. 02_paper_uncertainty.ipynb: use paired time-block/stationary resampling with justified block lengths and an actual declared confidence level; show interval sensitivity, effective time coverage and nonpositive-equity handling. Estimate BTC beta and residual uncertainty if that is the question; keep rolling/year charts descriptive.
4. For a selected strategy's edge claim, attach the strategy's historical trial family and future validation design. Do not invent N_TRIALS=1 merely because this reporting notebook makes one call.

## Frozen decisions and stopping rules

Report correctness requires accounting and estimator calibration, fresh-kernel execution and accurately labeled uncertainty. Economic significance additionally requires a valid design for the strategy being reported. Small live samples remain inconclusive even if a descriptive Sharpe is high.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
