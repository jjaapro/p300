# Validation review plan: funding_cvd_divergence

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **FAILED_DISCOVERY_GATE_MECHANISM_AND_PORTFOLIO_CLAIMS_UNSUPPORTED**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [research.py](research.py), [phase2_robustness.py](phase2_robustness.py), and [combined_portfolio_sim.py](combined_portfolio_sim.py): mechanism, data SQL, feature construction, selection, regime labels, and portfolio scoring.
- Saved funding-CVD phase-one and phase-two JSON summaries under [../../material/chento/validation](../../material/chento/validation). No notebook or narrative README existed here before this review.

## What is useful in the existing work

- Phase one specifies an explicit n/expectancy/stability gate and records that no candidate passed.
- The later robustness output exposes the small 21-trade selected sample and recent losing observations; the separate bull revalidation provides additional, weak descendant evidence.

## Claim-specific assessment

### 1. Spot absorption mechanism — MISIDENTIFIED INPUT

[research.py](research.py) describes spot buyers and spot CVD, but its SQL reads Binance perp cd_futures_15m. The feature is rolling-standardized per-bar quote-volume delta, not a spot cumulative-volume-delta series. The strategy may test a perp-flow hypothesis, but it cannot establish the stated spot mechanism.

### 2. Phase-one acceptance — FAIL RETAINED

The saved cost-18-bp phase-one result has gate_pass=false and n_candidates=0. Its recorded result family must remain a failed discovery screen.

### 3. Phase-two winning configuration — EXPLORATORY AFTER FAILED GATE

[phase2_robustness.py](phase2_robustness.py) hard-codes a selected WINNING configuration with only 21 trades, below phase one's 100-trade floor. Continuing exploration is not itself prohibited, but it is not a passed validation or independent robustness test.

### 4. Regime and diversification — UNSUPPORTED

[combined_portfolio_sim.py](combined_portfolio_sim.py) uses unshifted final-day 30-day returns. Phase-two correlation_proxy infers diversification from regime labels rather than measured portfolio covariance; tiny regime subsets cannot establish that claim.

### 5. Combined portfolio superiority — NOT ESTABLISHED

Concatenated trade R and selected Pareto comparisons do not establish shared-capital net marked performance, simultaneous drawdown, or capacity.

### 6. Funding provenance and descendant sample — LIMITED

Historical predicted funding and later settlement observations are different quantities. The later bull revalidation has only two OOS funding-CVD observations and does not supply a robust positive confirmation.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Retain the failed discovery result

No automatic revival or shipping follows the selected 21-trade summary. Require an explicit new economic hypothesis before further tests; record why the original n≥100 gate was not met.

### Step 2: Name the signal truthfully

Freeze a perp delta/funding hypothesis using its actual table and units. A true spot-versus-perp divergence is a separate trial requiring actual spot flow, aligned contracts, and independent justification.

### Step 3: Audit feature availability

Specify whether funding is a predictive feature or an accrued cash flow, with publication and settlement timestamps by era. Verify causal trailing z-scores, stale fills, normalization, completed daily regime inputs, and each warm-up exclusion.

### Step 4: Create mechanism controls

Compare funding alone, perp delta alone, their interaction, and volatility/session/regime-matched random opportunities. Predefine direction and normalization; do not label a price-volume proxy as observed spot accumulation.

### Step 5: Model implementable outcomes

Use contract-matched execution, fees, slippage, gap and ambiguous-bar rules, actual settled funding, and open-position capital. Calculate aligned daily marked P&L for real covariance and portfolio comparisons.

### Step 6: Record and separate selection

Ledger every funding threshold, flow threshold, horizon, direction, regime, exit, and chosen winner. Keep failed screens and inspected halves as development; reserve chronological independent data and purge overlapping holding periods.

### Step 7: Apply a new frozen verdict

Require the prospectively stated sample/episode floor, net control uplift, selection-aware uncertainty, and stable capital-aware risk. Missing funding or too few events is INCONCLUSIVE; failure remains ARCHIVE without relabeling an attractive subgroup as a pass.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_funding_delta_semantics.ipynb: table-to-hypothesis mapping, units, provenance, and release timestamps.
- 01_preregistered_interaction_controls.ipynb: eligible-event and execution ledgers with all trials.
- 02_forward_portfolio_validation.ipynb: only if revival is justified and data meet the new sample floor.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No data or accounts were queried and no study was rerun. Saved results are historical; true spot flow, funding settlement provenance, contract fills, and portfolio covariance were not independently verified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
