# Earlier project validation audit — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `validation_audit_2026_09/validation_audit.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The notebook identifies itself as report-only, loads saved result files, reports that nothing clears the stated DSR standard, reconciles zero-cost/long-hold and costed/short-hold claims, and discloses partial PBO coverage and sparse paper evidence. Those disclosures remain valuable.

## Findings and limits

1. Reproducing a loader's published point estimate (cells 8/10) does not establish its engine or source labels are correct. The current review finds upstream causal, funding, first-holding-day and MTM problems that can propagate into this audit's input vectors.
2. Cells 13/14 disclose PBO only for the saved four tilt alternatives, omitting other searched branches whose matrices were unavailable. That is a partial-family diagnostic, not a complete search correction. Daily closed-trade or signal-return inputs must not be interpreted as a reconciled live account NAV.
3. The closing narrative in cell 19 treats point estimates as real and breadth as the binding limitation. A cautious restatement is that saved point estimates reproduced under the tested definitions; data/engine validity and actual economic edge remain separate unresolved questions.
4. Paper counts and deployment status in cell 5 are an as-of historical snapshot. They should not be displayed as current facts without a new explicit observation cutoff. A report-only notebook does not record execution of the source audit scripts in its own cells.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_audit_dependency_graph.ipynb: map every reported number to source study, input return definition, dependency revision, data hash and original validation clause. Mark affected descendants when a causal/data/accounting source is corrected.
2. 01_validation_helper_contracts.ipynb: verify actual helper behavior and units for DSR, PBO selection statistic/remainder, CPCV refitting/purge, bootstrap confidence levels and net equity. Use analytic or independent simulation checks appropriate to each metric before recalculation.
3. 02_audit_reconciliation.ipynb: preserve historical values, regenerate only affected inputs after source corrections, and show before/after plus full/partial family coverage. Missing search branches are an explicit uncertainty/lower bound, not silently N_TRIALS=1.
4. 03_audit_verdicts.ipynb: separate reproduction, validity, statistical precision, selection correction, portfolio impact and current paper evidence. Freeze any genuinely new evaluation period and observation rules before inspecting it.

## Frozen decisions and stopping rules

Complete when each claim has a traceable valid input and a conclusion no broader than its check. An old low DSR remains insufficient evidence, while a corrected point estimate does not itself establish an edge. No need to rerun unaffected rejected studies.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
