# PDO TradingView parity and CSV reconciliation — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P0.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `pdo_tv_csv_parse.ipynb`
- `pdo_tv_validate.ipynb`
- `pdo_tv_validate_dump.ipynb`
- `pdo_tv_validate_sweep.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The notebooks state the intended Pine parity question, disclose filters absent from that comparison, and provide per-trade dumps and bounded sensitivity sweeps. Reproducing another engine is a legitimate objective distinct from proving a tradable edge.

## Findings and limits

1. pdo_tv_csv_parse cell 4 excludes Margin call slices and calls DayEnd/HoldLimit rows the real trades. Those cashflows are economically real parts of the exported position history. Removing them cannot establish reconciled net profitability or account drawdown; percent-return sums/compounding also require the actual sizing and partial-exit denominator.
2. pdo_tv_validate cell 3 aggregates available minutes without enforcing complete hours; cell 6 uses the first observed hour as day open and previous observed day as PDO. Missing days/hours can change the intended calendar rule and make HOLD_BARS count observations rather than elapsed hours.
3. The validation engines' end-open position is not marked, and drawdown follows closed-trade equity. Header slippage assumptions and actual fills must be reconciled. A static printed TradingView reference is not a matched, versioned export.
4. The sweep's gap/tolerance/hold loops contain 20 evaluations and 18 unique parameter combinations on the same sample; that is parity sensitivity, not out-of-sample evidence for a selected winner. __file__, legacy database paths and hard-coded export locations obstruct fresh-kernel reproduction; the dump writes outside the preferred study output tree.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_pdo_parity_contract.ipynb: freeze Pine source/version, chart exchange, interval, chart timezone, bar magnifier/order processing, margin, commission, slippage, quantity and date range. Copy authorized inputs into a study-local manifest; never infer the timezone from machine locale.
2. 01_csv_cashflow_reconciliation.ipynb: preserve every export row and group entries, partial exits, margin calls, fees and open quantities by position/order identity. Reconcile quantities, realized dollars and account equity to the complete export; malformed/unmatched records must produce explicit failures.
3. 02_pdo_bar_and_engine_parity.ipynb: require complete calendar bars or a frozen exclusion rule; test midnight, missing prior day, 24-hour timeout, boundary touch, long/short arithmetic, gap, same-bar collision and open final positions. Compare exact signal, entry, exit and quantity records with declared tick/time tolerances.
4. 03_pdo_parity_verdict.ipynb: retain all 18 unique sweep configurations as diagnostic history, freeze one target configuration and explain unmatched trades. Save a corrected reconciliation table without deleting the original exports. Economic testing, if desired, belongs to a distinct future plan linked to pdo_adjacents/VALIDATION_REVIEW_PLAN.md.

## Frozen decisions and stopping rules

Pass only when the full export and study ledger reconcile, including margin calls/partial exits, with all exceptions explained and stable notebook execution. Matching a TradingView run with poor margin settings does not validate profitability. A parity-only exercise needs no DSR or arbitrary holdout.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
