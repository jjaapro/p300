# Execution cost and fill-policy study — validation review and future plan

Date: 2026-09-14. Static review of current source, notebook text/saved outputs and supporting reports. No study executed; historical results were not independently reproduced. Notebook cell references are zero-based.

**Priority: P1.** Scope: retrospective review plus proposed follow-up; this is not the original preregistration. Preserve existing source, frozen decisions and saved results. Priority describes prerequisites for reusing evidence, not an instruction to change production.

## Coverage

- `execution_2026_09/execution_study.ipynb`

Paths above are relative to `studies/notebooks/`.

## What was done well

README and findings document E0 parity, spot/perpetual identity, 1-minute and 5-second sensitivity, fill-through alternatives, block intervals, small stress samples and an unrun E7 probe. The recorded decisions and subsequent authorized cost changes are historical facts; this audit does not reverse them.

## Findings and limits

1. findings.md calls the modeled taker cost measured and concludes execution is the fee and almost nothing else. exec_lib.py fixes fees, spread/adverse-selection assumptions and zero impact; bars do not measure account fees, order-book queue, attainable maker status or actual fills. The caveats are more defensible than that headline.
2. run_e4_stop_slippage.py:21–55 computes gap/stop slippage from the same OHLC stop walker. Zero modeled gaps means no opening-gap slippage in those sampled bars; it does not establish zero intrabar stop-market slippage or exact live fills. Polling tails and sparse stressed samples are material.
3. E2/E3 touch/through models cannot establish that a resting order actually filled at a maker fee; marketable orders may take or be rejected under post-only. The narrow SHORT_SQUEEZE stop is particularly exposed to the documented spot/perpetual basis mismatch and sparse 5-second event sample.
4. Entry lists are inherited from upstream studies. Parity with those lists validates reproduction, not their causal selection or engine correctness. The ADX stop-list check cannot detect omitted stop events absent from its source ledger. exec_lib.py:130–138 reuses existing caches unless force is set, without checking source hashes; cache and result lineage must be frozen for a current replay.

## Proposed notebook work

The names below are future artifacts, not completed tests. For top-level legacy notebooks, use `studies/notebooks/study_validation_audit_2026_09/followups/<family>/`; for a named study directory, use a new `validation_followup/` beneath that directory. Keep all caches, manifests and results alongside those future notebooks.

1. 00_execution_lineage_review.ipynb: map each sleeve's source ledger, code revision, asset/venue path, cache hash and cost convention; reconcile gross versus net R and distinguish original runs from later cost adoption. Resolve upstream engine issues before using a ledger as truth.
2. 01_execution_fill_bounds.ipynb: test causal order arrival, entry-bar stops, stop/target collisions, gaps, timeout, unfilled limits, maker rejection and cancellation races. Compare touch, through and conservative partial/no-fill bounds; retain all intended signals and skipped opportunity cost.
3. 02_execution_venue_costs.ipynb: separately evaluate true perpetual paths where available and matched spot-proxy sensitivity. Freeze dated account fee tiers when supplied, per-leg charges, spread, latency and stressed impact ranges. Report cost distributions and tail losses, not only mean drift near zero.
4. 03_execution_policy_validation.ipynb: use paired same-signal and time-block comparisons, both historical halves and a later frozen observation period. Require confidence/precision for net incremental policy benefit and trade retention, with actual uncertainty from the small stress cohorts.
5. 04_execution_observation_design.ipynb: specify passive quote/trade telemetry and, only as a separate later authorized task, any real-order probe. No orders, keys, purchases or live probes are part of this review. Paper price paths cannot settle queue or subsecond fill questions.

## Frozen decisions and stopping rules

The existing results can support modeled cost sensitivity within their data resolution. A claim about actual execution requires relevant observed fills/quotes and calibrated uncertainty. If venue/queue evidence is absent, retain explicit bounds and an inconclusive actual-fill verdict instead of declaring zero slippage.

Before the first follow-up run, freeze its exact primary question, data cutoff, inputs, costs, comparator, numerical tolerances/economic effect and any evaluation windows. Previously inspected results remain development evidence. Stop when a prerequisite fails; do not tune around it or rewrite historical clauses.

## Required evidence and applicability

Apply the [shared validation protocol](../study_validation_audit_2026_09/VALIDATION_PROTOCOL.md) where relevant. It specifies lineage, event-time causality, funding/fee semantics, accounting, selection correction, uncertainty, stress and decision labels. The tailored scope above takes precedence over a generic demand to run every statistical test.

- Save a source/data/environment manifest and complete fresh-kernel execution record for each actual follow-up.
- Save claim-to-result and before/after reconciliation tables, exclusions, unresolved issues and deviations.
- For a trading result, expose signal/trade/cashflow/calendar-NAV ledgers and actual parameter history; for a descriptive/parity/report result, expose its corresponding matched records and calibration checks.
- Mark unavailable data, unresolved ordering and inadequate precision as unevaluable/inconclusive. Keep hypothetical outcomes distinct from observed fills and original saved results.
- No empirical pass, repaired strategy, new trading performance or production change is claimed by this review.
