# Range sanity and first-break fades — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P0.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `range_sanity_2026_09/range_sanity_2026_09.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The notebook explicitly calls these non-preregistered falsification/sanity checks and labels the maker arm as cost-only, with no fill simulation. Negative findings are useful for shelving the tested hypotheses without a larger search.

## Findings and limits

1. b_basis_funding_asia.py's first-break fade outcome calculation approximates gross R as 2×target_hit−stop_hit, giving timeout trades zero gross return instead of marking their actual exit. It starts the stop/target walk strictly after the first breach bar, so an entry/fill followed by an adverse move within that bar is omitted.
2. Session eligibility permits incomplete Asia/rest sessions rather than requiring the stated full windows. Quantify missing-time effects and whether the first observed breach was actually the first; do not silently replace unavailable bars with clean sessions.
3. The funding path samples the historical mixed funding table at eight-hour timestamps. Pre-cutover forecasts are not established realized cashflows. Earlier minute-based saved outputs also need lineage comparison against the September 7 BTC repair before being reused.
4. Cost-only maker discounts and exploratory LVN/profile alternatives do not demonstrate attainable fills or a fully defined tradable policy. Negative numbers reject the modeled variants, not every intraday range idea.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_range_reproduction.ipynb: preserve the old negative result and map every A/B/C arm, threshold, session, cost and source snapshot. Record actual session coverage, time labels and exclusions; distinguish spot features from any perpetual economics.
2. 01_range_path_fixtures.ipynb, before engine reuse: test breach-bar entry/stop/target ordering, gap fills, a timeout at a gain/loss, missing first breach, midnight and simultaneous alternatives. Mark actual timeout prices and report OHLC ambiguity bounds where ordering cannot be resolved.
3. 02_range_corrected_baseline.ipynb: run only the existing frozen arms after data/engine fixes, with realized settlement funding where applicable and consistent per-leg costs. Compare old/new trade lists and net R with paired time-block uncertainty. Do not add a grid to compensate for failure.
4. A new LVN/range strategy would require its own training-only profile construction, event-time feature availability, executable fill rule, unfiltered/simple-fade controls, trial ledger and later validation period under the shared protocol.

## Frozen decisions and stopping rules

Keep rejected ideas archived. P0 applies to reuse of the current timeout/entry-bar engine and exact economic claims, not a requirement to revive this study. A corrected negative result closes the question for that specification; wide uncertainty is inconclusive.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
