# Validation review plan: dwell_block

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **DO_NOT_INTEGRATE_STALE_POSITIVE_NOTEBOOK_INVALID_COMPARISON**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [findings.md](findings.md) and all 15 cells of [dwell_block_research.ipynb](dwell_block_research.ipynb), including its positive opening/concluding narrative.
- [dwellblock.py](dwellblock.py), [analyze_chento_bids.py](analyze_chento_bids.py), [experiment_abc.py](experiment_abc.py), [experiment_ladder.py](experiment_ladder.py), [prod_compare.py](prod_compare.py), and equity-comparison interfaces; detailed review of imported triggers and limit/fallback replay.
- Saved prod_compare_results.json arm summaries: market/shallow/fill-only populations and means. The notebook has null execution counts and no saved outputs.

## What is useful in the existing work

- The later findings explicitly reject integration, identify tight-stop artifacts, and distinguish detector agreement with selected charts from a deployable entry edge.
- The experiments include market, filled-limit, fallback, and ladder ideas, making the opportunity-loss question visible even though some implementation comparisons are invalid.

## Claim-specific assessment

### 1. Current conclusion — DO NOT INTEGRATE

[findings.md](findings.md) lines 7–12 says no deployable entry edge and rejects integration. Notebook cells 1 and 15 still describe a positive/wire-in interpretation; the newer negative conclusion governs, and the old narrative is stale.

### 2. Fallback comparison — INVALID COUNTERFACTUAL

[prod_compare.py](prod_compare.py) lines 71–81 first observes whether a limit fills over the next 24 hours; if not, it assigns the original market price and original start time. That fallback depends on future nonfill information and is not an executable policy.

### 3. Shared signal baseline — NOT CAUSAL AS IMPORTED

[experiment_abc.py](experiment_abc.py) imports build_optimized_triggers from the old redo path, while prod_compare imports the old composite builder. Their reviewed conjunction path admits future ±24-hour events unless explicitly patched; the standalone callers do not establish such a repair.

### 4. Paired comparison population — NOT IDENTICAL

The saved market and shallow-fallback arms have 103 and 105 trades. Filters/state are applied after arm-specific outcomes, so an asserted same-trade comparison requires a signal/eligibility reconciliation and exit-completion-aware state.

### 5. Fill-bar and instrument effects — LIMITED / BIASED

Filled-limit replay begins after the fill bar, omitting possible immediate adverse movement. Fine 5-second spot paths do not establish perp fills or funding; fixed research costs cannot resolve basis and execution differences.

### 6. Detector/metric claims — DESCRIPTIVE ONLY

A detector matched to three charts and a selected trader journal is not an independent edge test. The trade-R cumulative-return/drawdown ratio is not an annualized capital-marked MAR.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Retain the archive decision

Do not integrate or automatically retest the old shallow-entry winner. Only revive a separately justified detector/filter hypothesis after documenting why the negative result does not settle that new question.

### Step 2: Freeze an executable contrast

Compare market entry, limit-only with expiration, and a fallback that enters at the then-available price at the actual deadline. Specify order activation, cancellation, fill-bar risk, and identical eligibility before outcomes.

### Step 3: Repair causal signal lineage

Use an explicit backward-only trigger implementation with current gate configuration; do not rely on monkey-patches in another script's main block. Update trade-state filters only from completed exits and include future-mutation fixtures.

### Step 4: Version data and fills

Match perp signals to perp execution or label spot paths as a sensitivity. Capture corrected BTC minute lineage, finer-data coverage/gaps, gaps through stops, ambiguous paths, fees/slippage, actual funding, and open-risk constraints.

### Step 5: Test the proposed mechanism

Compare dwell detection with matched non-dwell levels, simple recent-range structure, and unchanged baseline entries. Keep missed fills and market moves during waiting in both opportunity and portfolio accounting.

### Step 6: Separate all prior searches

Ledger the chart-calibrated detector, A/B/C, ladder, shallow, and fallback variants. Keep inspected periods in development, reserve a genuinely new chronological cohort, and purge overlapping labels; do not promote the selected best historical arm.

### Step 7: Make uncertainty and disposition explicit

Use paired opportunity-level differences, episode/day-block intervals, fill rates, tail sensitivity, and daily marked risk. Preserve DO_NOT_INTEGRATE unless new frozen net evidence qualifies; insufficient data is INCONCLUSIVE. A future notebook must display the dated negative conclusion prominently.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_dwell_claim_and_engine_audit.ipynb: stale narrative map, causal imports, and executable fallback fixtures.
- 01_conditional_dwell_controls.ipynb: only after a justified revival; same eligible pool and instrument-matched fills.
- 02_frozen_net_validation.ipynb: all-trial history, chronological uncertainty, and an explicit integrate/archive verdict.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No source or existing notebook was modified and no experiment rerun. Saved arm counts are historical; current production parity, actual fine-data completeness, fills, and funding remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
