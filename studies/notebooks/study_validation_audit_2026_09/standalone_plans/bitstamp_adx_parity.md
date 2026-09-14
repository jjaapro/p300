# Bitstamp ADX Pine and service parity — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `bitstamp_adx_backtest.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

Cell 0 explicitly limits the notebook to signal-level parity and discloses spot data, costs and omitted funding. It compares current, TradingView-cross, stateful and service-style machines, which is useful for diagnosing semantic differences.

## Findings and limits

1. Cell 11 opens at the daily close and then uses that same day's low/high in its worst-point MTM calculation. Those extrema predate the position. It tracks peaks only at those worst-point marks, so the reported MTM drawdown is not a complete NAV-path drawdown either. Do not reuse this risk metric without correction.
2. Stops fill exactly at their daily level without a gap-through rule. The final position is appended as still_open with a cost-adjusted mark, then included beside completed trades; realized and marked statistics need separate contracts. Cell 13 entry-year cohorts are not calendar marked annual returns.
3. Cell 2 requires __file__; cell 7's cache loader may fetch/write under data/. Service parity imports mutable current production implementations. The notebook has no saved execution counts establishing a fresh-kernel run.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_adx_parity_contract.ipynb: freeze price snapshot, Pine export, indicator warmup, process_orders_on_close setting, state reset/reversal rules and production dependency revision. Preserve intentionally buggy legacy variants as labeled diagnostic controls.
2. 01_adx_state_fixtures.ipynb: use independent short synthetic sequences for threshold equality, entry/reversal/exit, daily gap stop, same-bar collision, warmup and open terminal positions. Restrict risk marks to periods after entry and include initial capital.
3. 02_adx_parity_report.ipynb: reconcile signals and trade timestamps/prices at declared precision; separate executed trade metrics, open marks, entry cohorts and daily NAV. Write caches/results inside a new study-local run directory with no implicit download.
4. If the question becomes economic robustness, reuse the current adx_robustness_2026_09 and adx_eth_2026_09 review plans after their engine blockers are resolved; do not use this spot parity notebook as a shortcut to perpetual leverage or funding conclusions.

## Frozen decisions and stopping rules

Signal parity can pass with exact frozen-engine agreement and correctly labeled scope. Economic or risk claims remain unsupported until execution and accounting are corrected. Out-of-sample strategy selection is not needed merely to verify Pine semantics.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
