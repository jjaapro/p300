# Hawkes event-clustering descriptive note — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P2.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- Script/report study; no existing notebook in this directory.

Paths above are relative to `studies/notebooks/`.

## What was done well

The parent hawkes_note.md explicitly defines descriptive gates and two distinct nulls, explains the stationarity/rate confound, records the binary-bin clause problem, labels the N3 addendum post hoc, and parks the idea. It correctly avoids Sharpe/PBO machinery for a non-return statistic.

## Findings and limits

1. The note's binary rising-edge streams cannot satisfy its original hourly over-dispersion condition; the note discloses this and explains why the verdict does not change. Preserve that literal original clause and its recorded deviation, rather than retrospectively replacing the preregistration.
2. addendum_slow_rate.py:9–13 uses a centered rate estimated from the same observations and calls residual clustering genuine self-excitation. The code/note can show a descriptive mismatch with that particular null; it cannot identify a unique excitation mechanism, a causal forecast or the benefit of a Hawkes model.
3. The full-sample percentile thresholds and centered smoother are legitimate descriptive choices but would leak future information in prediction. Two hundred null replications provide limited precision for 99th-percentile thresholds, and multiple streams/bin widths/lags need simultaneous inference if a new formal discovery claim is made.
4. The archived liquidity-feed span and funding-era restriction are historical inventory facts. The plan did not requery present coverage. A monotone ladder in this sample does not prove no future dataset or shorter kernel could ever yield a useful model.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_hawkes_description.ipynb: collect the existing note, result JSON and figures into a reproducible notebook with data/source hashes, exposure masks, event definitions, bin boundaries and original/deviated clauses. Preserve the PARK outcome.
2. 01_hawkes_null_calibration.ipynb, only if the statistic is reused: simulate homogeneous and inhomogeneous Poisson, renewal/refractory and known Hawkes processes, matching missing exposure and discretization. Verify false-positive rates, estimator bias, bin-edge sensitivity and coverage with enough replications for the declared tail level.
3. A future predictive use is a new question: freeze trailing-only rate estimation and thresholds, compare held-out point-process likelihood/calibration against a simple time-varying-rate model, and use chronological event windows. Economic evaluation is required only if that forecast is translated into an execution/risk policy.

## Frozen decisions and stopping rules

Descriptive completion needs calibrated statistics and limited interpretation, not profitable returns or a DSR. Revisit only for a specified new data/method/use-case gate. More model complexity or a large Fano factor is not evidence of trading value.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
