# MACD post-ATH bottom description — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P2.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `macd_2w_post_ath_bottom.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

The notebook distinguishes cross proximity to a historical low from strict/ultimate subsequent lows and retains an open-cycle caveat. Those are useful descriptive questions about a small number of market cycles, not an executable exit rule.

## Findings and limits

1. The title and narrative say 2W, but cell 1 sets BAR_DAYS=7 and the saved output in cell 2 confirms 7d bars. Any claim specifically about a tested two-week signal is unsupported by this saved run.
2. Cell 2 reports interior gap bars and retains them. Cell 1's root search still expects jplus/. Dataset lineage, bucket completeness and the difference between displayed bar start and actionable bar close must be resolved before reproduction.
3. Cells 5/6 examine proximity bands on the same eleven cycles. Cells 7/8 use future lows, next ATH/bear boundaries and future peak closes. These are retrospective labels; they cannot be entry features or realized exit profits. Shared/overlapping cycles and one unresolved cycle limit inferential precision.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_macd_description_reproduction.ipynb: freeze weekly versus biweekly intent explicitly, anchor, warmup, gap exclusion and bar-close timestamps. Reproduce the saved weekly result as historical description first; treat a biweekly version as an additional previously inspected choice.
2. 01_macd_cycle_uncertainty.ipynb: retain every qualifying cross and cycle, show censored labels, sensitivity to cycle definitions and the full band table, and report counts rather than headline percentages alone. Compare all bullish MACD crosses and simple time-matched descriptive controls without optimizing the band.
3. If there is a new trading question, 02_macd_causal_policy.ipynb must freeze an attainable entry and non-clairvoyant exit, costs and sizing. Use chronological cycle-level validation and honest uncertainty about the few independent eras; BTC/ETH transfer is supporting evidence, not many independent cycles.

## Frozen decisions and stopping rules

Keep as a descriptive/archived study unless a new use case is chosen. Correct timeframe metadata and label timing before citation. A high share of retrospectively identified bottoms or a future-peak return is not an investable win rate; no profitability or leverage claim follows.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
