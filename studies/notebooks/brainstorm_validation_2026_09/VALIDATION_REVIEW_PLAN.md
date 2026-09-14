# Brainstorm claim replication — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `brainstorm_validation_2026_09/brainstorm_validation.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

This is one of the stronger rejection studies: frozen claim-specific clauses, exact external-cache replication, historical/asset transfer, fees and turnover, explicit failed reproduction, cluster-aware statistics, and prior search counts. C0 separately compares fetched realized funding against the mixed production table; C2 uses the fetched history rather than assuming mod8h forecasts are settlements.

## Findings and limits

1. The blanket N_TRIALS=1 label refers to the new replication tests, while selected hypotheses came from the larger documented search. Existing deflation calculations recognize that distinction; future summaries must not erase it or treat five claims plus all secondary analyses as a single unselected test.
2. The notebook runs C0 then later scripts, while run_c0_data_parity.py:149–156 writes and prints gate values. A future orchestrator needs explicit required-gate enforcement and hash linkage, not execution order alone. Identical copied cache data is replication, not independent data.
3. New historical years and correlated crypto assets test transportability; they are not automatically untouched chronological validation. C5's unreproduced H24 cell must remain unreproduced rather than being absorbed into a general claim that all arithmetic matched.
4. C4's bar-phase and fill-model sensitivity supports sensitivity under those engines. It does not validate all ADX implementations or exact live stops; the newer ADX robustness harness has separate first-day-stop/accounting issues. Keep engine-specific conclusions and dependency revisions explicit.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_claim_reproduction_manifest.ipynb: tie each C0–C5 result to the external cache hash, local input hash, dependency commit, original search family and the exact frozen clause. Fail downstream execution on any required parity/data gate; preserve recorded deviations.
2. 01_replication_closure.ipynb: maintain one row per claim, separating exact parity, transfer evidence, costs and final decision. Resolve H24 provenance only if needed for reuse; do not search for an alternative H24 definition that recreates the headline.
3. 02_engine_sensitivity_review.ipynb, if C4 infrastructure is reused: test causal phase boundaries, entry-day stops, long/short return units, open positions and MTM reconciliation with independent fixtures before replay. Compare phases jointly and retain the original chosen UTC convention rather than optimizing it.
4. Only new evidence or a changed economic question should reopen killed C1/C2/C3/C5 ideas. Register a new family with inherited trials, as-of data, net comparator and precision target; old failed tests remain in the record.

## Frozen decisions and stopping rules

Preserve the original rejection decisions and explicitly bounded C4 sensitivity findings. Completion is provenance/gate reconciliation and narrowly correct claims; rerunning broad searches to rescue killed ideas is unnecessary. Future adoption needs its own causal and economic validation.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
