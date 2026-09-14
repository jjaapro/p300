# Validation review plan: chento_journal

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **HISTORICAL_CLAIMS_REQUIRE_REVALIDATION**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [README.md](README.md), [findings.md](findings.md), [strategy.md](strategy.md), [strategy_spec.md](strategy_spec.md), and the existing [VALIDATION_PLAN.md](VALIDATION_PLAN.md): source/claim and dependency review; existing plans remain historical.
- [final_calibration_report.md](final_calibration_report.md), [NOTEBOOK_1_SUMMARY.md](NOTEBOOK_1_SUMMARY.md), and [NOTEBOOK_2_SUMMARY.md](NOTEBOOK_2_SUMMARY.md): results, caveats, and promotion claims.
- Notebook inventory: bootstrap cells 1–21; v1 cells 1–14; v3 cells 1–13; comparison cells 1–18 (1-based, including Markdown). Detailed source review emphasized bootstrap 1, 4–5, 15–16, 20–21; v1 2, 4, 6, 8, 13; v3 1–6; comparison journal-to-bot assumptions.
- Key computation paths: [validation_B_composite.py](validation_B_composite.py), [validation_group_A_redo.py](validation_group_A_redo.py), [validation_liquidation_and_C6.py](validation_liquidation_and_C6.py), [validation_C4_cross_exchange_delta.py](validation_C4_cross_exchange_delta.py), [validation_group_A_tuning_backonly.py](validation_group_A_tuning_backonly.py), [validation_adaptive_hybrid_backonly.py](validation_adaptive_hybrid_backonly.py), and their imported trigger/replay interfaces.

## What is useful in the existing work

- The journal findings explicitly identify missing lifecycle information and skipped clusters; Notebook 2 distinguishes the derived system from the trader, with under 25% timestamp coverage.
- Historical plans specify trade ledgers and explicit gates; later backward-only scripts show that timing issues were recognized. Retain these as provenance, not proof that every caller was repaired.

## Claim-specific assessment

### 1. Trader profitability and bot equivalence — UNSUPPORTED

The selected image journal is not a complete account ledger. [findings.md](findings.md) lines 66 and 119–127 describe incomplete lifecycles and omitted material; [final_calibration_report.md](final_calibration_report.md) lines 9–12 call approximate +0.30R and +0.33R statistically identical without an equivalence test. Its two-day annualization (86–88) and projected returns (148–159) do not establish realized or expected performance.

### 2. Early composite timing — INVALID BEFORE REUSE

[validation_B_composite.py](validation_B_composite.py) lines 55–66 intersects events with an absolute ±24-hour distance. [validation_group_A_redo.py](validation_group_A_redo.py) and [validation_liquidation_and_C6.py](validation_liquidation_and_C6.py) retain callers. The backward-only alternatives patch selected module bindings in their main paths; their existence does not repair other imports.

### 3. Outcome-dependent gates and sizing — NOT VERIFIED CAUSAL

Prior-row final R, e.g. [validation_liquidation_and_C6.py](validation_liquidation_and_C6.py) lines 236–239, is not necessarily known when the next signal fires. Replay must use completed exits for state updates and identify the precise gate/tilt version.

### 4. Multi-asset replication — NOT EQUIVALENT

The existing plan's progress log notes that 86% of v2 entries bypassed the intended confluence path. v3 cell 1 also describes unequal BTC/ETH/OP confluence. Different missing-data fallbacks are different strategies, not independent replication of one rule.

### 5. Notebook reproducibility — NOT EXECUTED / BROKEN IMPORT

All four notebooks have null execution counts and no saved outputs. v1/v3 import strategies.sleeves.chento_limit_bid, which is absent; archived source exists elsewhere. A valid Python syntax inventory is not a fresh-kernel run.

### 6. Current strategy relevance — HISTORICAL ONLY

The reports span retired sleeves, changed gates, selected filters, and data later repaired. An old SHIP or calibrated label cannot validate the current gate-off or paper-trading configuration.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Separate three hypotheses

Treat journal reconstruction, a mechanical composite edge, and execution/sizing improvements as separate claims. Freeze one exact current strategy and a causal baseline before any new scoring; do not use reconstructed trader success as an alpha prior.

### Step 2: Reconstruct provenance first

Create a missingness/selection ledger for all journal clusters, including losses and unresolved positions. Require account-level fills, fees, cash flows, and equity to estimate trader returns; otherwise retain a descriptive case study. Record source/config hashes for every reported bot figure.

### Step 3: Audit timing and data

Prove every conjunction is backward-only; event timestamps must mean availability, not candle open. Test that altering future observations cannot change earlier signals, and that unresolved trades cannot update tilt. Use instrument-matched series and documented repaired minute inputs; invalidate affected caches explicitly.

### Step 4: Build an executable research entry point

Restore notebook imports to a versioned research adapter without importing write-capable production startup code. Add small synthetic timing/fill fixtures, then a future clean-kernel run with pinned dependencies and captured output hashes. Preserve the original notebook as historical evidence.

### Step 5: Reconcile execution economics

Specify limit placement, activation, cancel/replace, partial fills, gap stops, same-bar ordering, fees, slippage, actual funding, leverage, liquidation, and aggregate capital. Use matched perp execution where the hypothesis trades perps; spot replay is a labeled sensitivity only.

### Step 6: Account for selection and chronology

Inventory all A/B/C, composite, gate, exit, asset, and sizing trials, including killed ideas. Reconstruct earlier development cuts honestly. Use nested chronological fitting only where rules fit, purge overlapping event intervals, and reserve a genuinely unseen final period or forward paper cohort.

### Step 7: Compare and decide

Compare the frozen composite with its components and regime-matched baseline, then compare execution variants on identical eligible signals. Report paired net differences, day/episode-block uncertainty, tail concentration, effective sample size, and capital-aware drawdown. Current promotion requires a new explicit decision; unsupported trader equivalence and annualized marketing projections remain withdrawn.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_provenance_and_claims.ipynb: journal completeness, report-to-code map, and stale claim register.
- 01_causal_current_baseline.ipynb: point-in-time invariants, repaired-data manifest, fills and reconciliation.
- 02_frozen_validation.ipynb: all-trial ledger, chronological net comparisons, uncertainty, and a dated verdict addendum.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

All four notebook cell inventories and key source paths were reviewed statically; the large historical validation-script family was not exhaustively audited line by line. Journal images/account records, database contents, production behavior, and runtime imports were not independently verified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
