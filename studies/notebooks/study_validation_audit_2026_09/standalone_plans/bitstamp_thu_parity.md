# Bitstamp Thursday-bear Pine parity — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `bitstamp_thu_bear_backtest.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

Cell 0 clearly distinguishes Pine's day-of-month event approximation and Friday exit from the service's actual calendar and exit time. The prior Wednesday EMA in cell 14 is a causal daily feature for the stated hourly-close rule.

## Findings and limits

1. Cells 13/14 use day-of-month NFP/CPI/OPEX proxies and Pine timing. These may be appropriate for parity but do not validate the actual event-filtered service. The distinction already documented in cell 0 must remain visible in any performance claim.
2. Cell 14 fills daily-gap stops at the stop price and leaves final open positions outside the closed-trade statistics. Cell 16's MaxDD is closed-trade drawdown, not marked account risk.
3. Cell 17 applies UTC+3 to a CSV comparison. The export's actual declared chart timezone must establish whether this is a fixed offset or a timezone with DST; the code does not establish it. Partial/missing exported rows and trade-number matches need explicit reconciliation.
4. __file__, argparse-oriented orchestration, and cache fetch/write paths under data/ prevent treating the converted notebook as a clean, isolated executed study.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_thu_parity_inputs.ipynb: freeze the Pine source/chart/export, timezone, calendar approximation, exact bar-close convention, costs and snapshot. Record the separate service specification without merging its rules into the parity target.
2. 01_thu_state_and_calendar.ipynb: test Thursday boundary entry, Friday exit, Wednesday indicator availability, event-date edge cases, timezone transitions where applicable, missing bars, stop gaps and terminal exposure. Compare complete trade identities and prices against the export.
3. 02_thu_reconciled_report.ipynb: show realized and marked returns, monetary fee/quantity reconciliation, unexplained mismatches and fresh-kernel execution. Keep all new cache/output files local to the study.
4. Only if service performance is the new question, register a separate actual-calendar replay with as-of announcement times, causal next fills, perpetual costs/funding, chronological validation and an unfiltered Thursday baseline. Count any chosen calendar/time variants in the historical search family.

## Frozen decisions and stopping rules

Accept exact parity only for the frozen Pine specification; it is not validation of the production event rules. Any missing chart metadata or unresolvable trade mismatch is inconclusive. No additional broad strategy sweep is needed for parity.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
