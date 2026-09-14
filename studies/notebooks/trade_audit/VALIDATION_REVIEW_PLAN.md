# Historical behavior compliance and paper/sim audit — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P2.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- Script/report study; no existing notebook in this directory.

Paths above are relative to `studies/notebooks/`.

## What was done well

findings.md separates historical paper and replay variants, identifies inconsistent P&L field units, explains configuration cutovers and reports missing sleeve coverage and tiny sample sizes. It flags specific late cold fills instead of treating the entire book as validated.

## Findings and limits

1. The claim that 203/203 trades obey timing/asset/direction checks establishes those checks, not full strategy-logic compliance. It does not test indicator reconstruction, missing eligible signals, sizing, costs, duplicate orders, exit paths or idempotent restart behavior.
2. The scripts contain hard-coded variant/database assumptions and direct sqlite3.connect(DB), without mode=ro enforcement. The June 7 enabled-variant convention and counts are an as-of snapshot; they are not a reliable current classifier for paper, simulated and real execution histories.
3. Mean return / win-rate differences from different historical periods and n≤6 paper trades cannot establish matched paper-versus-simulator agreement, improvement or drift. Configuration history needs precise effective timestamps, and replay P&L percent fields have different denominators across sleeves.
4. No notebook exists in this folder. Static findings do not show a reusable fresh-kernel reconciliation workflow, complete event coverage or a verified root cause for every anomaly.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_trade_audit_snapshot.ipynb: freeze ledger and configuration history at an explicit cutoff, use read-only access and select runs by stable identity/execution metadata. Normalize units and preserve open trades, partial fills and missing fields.
2. 01_behavior_event_replay.ipynb: reconstruct all eligible signals, including no-trade decisions, and compare decisions/fills/exits by event identity against the configuration active then. Test restart/cold start, delayed poll, duplicate event, missed window and changed weekday rules using deterministic fixtures.
3. 02_paper_sim_reconciliation.ipynb: run the same historical paper observation window through the frozen simulator, compare expected versus actual signal and fill times, prices, quantities, costs and reasons, and explain every unmatched event. Separate model error from operational deviation and deliberate configuration changes.
4. 03_trade_audit_closure.ipynb: publish anomaly IDs, expected/observed behavior, cause or unresolved status and evidence. A future performance-monitoring plan needs a predetermined observation window and drift/precision rules; never infer validation from six winners.

## Frozen decisions and stopping rules

Historical behavior findings may stay archived. A renewed audit passes only its explicitly checked behaviors with complete event reconciliation; unresolved/missing data is inconclusive. No alpha search or multiple-testing correction is needed merely to prove schedule/idempotency correctness.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
