# RSI divergence sketch — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P2.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `rsi_divergence_sketch.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

Cell 0 candidly records failure in W1–W3, favorable W4 results and a parked conclusion. That is appropriate negative evidence for the tested rule; the later diagnostic ideas are visibly exploratory.

## Findings and limits

1. Cell 1's window_mask includes each end date by adding a day, while the next window begins on the same date. Adjacent windows therefore overlap a boundary day. They are fixed-period comparisons, not a fitted walk-forward procedure.
2. Cell 4 uses same-close information and fills, without a latency model. Its 10× liquidation proxy is based on hourly closes and a 1/leverage threshold, omitting intrabar marks, maintenance margin and funding. That cannot support liquidation safety. The RSI calculation's zero-loss case becomes NaN and needs a declared flat/rising-market convention.
3. Cell 7 includes late crossings without a full future horizon in reach-probability denominators. Cell 8's suggested rolling gate arose after seeing W4 and future reach outcomes; future-gate decisions may use only already matured outcomes. The root finder and legacy database path also need modernization in a study copy.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_rsi_reproduction.ipynb: reproduce both failed directions with explicit data hash, RSI zero-loss convention, disjoint half-open windows and event counts. Correctly mark incomplete future reach labels and end-open trades; preserve the original parked decision.
2. 01_rsi_engine_bounds.ipynb, only if reused: compare causal next-open fills against the original same-close diagnostic, model actual long/short costs and perpetual funding, and bound intrahour stop/mark risk. Remove the close-only liquidation proxy from any economic conclusion.
3. A new rolling gate is a separate candidate family. Freeze its matured-outcome horizon and update schedule, compare against ungated divergence and plain RSI rules, train only on preceding data, purge overlapping labels and use a genuinely later evaluation window. Do not keep searching W4 until a gate passes.

## Frozen decisions and stopping rules

No rerun is required to keep the idea parked. Before reuse, reconcile the stated defects and assess a prespecified net effect with dependence-aware uncertainty. Failure of this rule does not disprove every RSI strategy; W4 alone does not rescue it.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
