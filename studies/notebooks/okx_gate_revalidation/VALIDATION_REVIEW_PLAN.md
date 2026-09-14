# OKX gate causal revalidation — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- Script/report study; no existing notebook in this directory.

Paths above are relative to `studies/notebooks/`.

## What was done well

The detailed README/addenda and findings preserve frozen source commits, a hashed external snapshot, causal and same-hour controls, parity gates, power/precision limits, a final RETIRE clause and explicit separation of discrimination from portfolio drawdown. This is a substantially specified study; a new generic search plan would weaken its discipline.

## Findings and limits

1. findings.md:36–40 explicitly says the same-hour control also fails, so the final verdict does not isolate the look-ahead premise. Lines 84–108 clarify that RETIRE means discrimination was not demonstrated while profitable trades were blocked; it is not proof the gate is useless or that chento is statistically validated.
2. The study is script/report based; no .ipynb exists here. Its external snapshot and result links are documented but this static review did not independently rerun or re-hash that snapshot. Preserve those limitations rather than asserting an independent reproduction.
3. The report-only all-trades drawdown and 2.25× fire-rate increase imply a distinct sizing/concurrency question. Positive kept-minus-blocked R and row counts alone do not establish the executable portfolio impact of disabling the gate.
4. findings.md:163–174 already records that the report-only production-sequence approximation used losing predecessors before they closed and differed from the bot's 48-hour rule. Those particular portfolio figures must not be reused; the final gate verdict did not depend on them. Lines 152–161 also disclose cross-checkout hash/line-ending issues and omitted funding.
5. findings.md lists downstream studies using same-hour OKX values and forbids treating the causal re-test as their automatic recut. Each dependent overlay/attribution/filter requires its own as-of replay; new replacement gates inherit the documented search family.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_okx_verdict_reproduction.ipynb: create a report notebook that reads a pinned copy/export of the original snapshot and saved outputs, displays hashes and original clauses, and reproduces clause arithmetic without reopening the final decision or rewriting its preregistration.
2. 01_okx_dependency_review.ipynb: verify causal feature availability and compare R1/R2/same-hour controls using truncation tests. Enumerate all descendants and link each to its own review. Confirm any truncated/partial folds and actual information exposure in reports.
3. 02_okx_portfolio_impact.ipynb: for a separately frozen operational sizing question, replay OFF/gated arms jointly with actual sequencing, concurrent positions, capital caps, marked risk and event-time previous-outcome availability. Freeze risk limits before measuring; use paired calendar comparisons and tail stress.
4. Keep the historical RETIRE outcome intact. Any replacement z threshold, venue or sizing gate is a new preregistered family with inherited trials and a new evaluation window, not an extension that can silently reverse the old verdict.

## Frozen decisions and stopping rules

Research reproduction is complete only with a verified manifest and exact clause table. Portfolio feasibility is a separate pass/inconclusive decision based on its frozen risk budget. No flag, bot, snapshot location or production schedule is changed by this audit.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
