# Validation review plan: fvg_magnet

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P2**. Status: **NEGATIVE_DESCRIPTIVE_AND_TRADE_RESULTS_CONDITIONAL_REVIVAL**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [research.py](research.py), [lvn_research.py](lvn_research.py), [lvn_phase3_directionality.py](lvn_phase3_directionality.py), [lvn_phase3b_regime.py](lvn_phase3b_regime.py), and [lvn_phase4_backtest.py](lvn_phase4_backtest.py): feature, controls, regime and replay paths.
- Saved FVG/LVN phase summaries under [../../material/chento/validation](../../material/chento/validation): main uplift, node transit, regime exploration, and negative phase-four outcomes. No notebook or README existed.

## What is useful in the existing work

- The study attempts explicit comparison levels and reports the failed overall magnet threshold rather than promoting one attractive bucket.
- Phase four tests a concrete trade and retains a negative result with uncertainty; descriptive touching or transit behavior was not sufficient for a positive trade verdict.

## Claim-specific assessment

### 1. FVG magnet effect — NOT ESTABLISHED

The saved broad sample of about 43,623 FVGs has roughly 1.00–1.03 uplift, below the 1.5 criterion. The selected 2–5% distance bucket is exploratory and does not reverse that broad result.

### 2. Control interpretation — MISDESCRIBED / CONFOUNDED

[research.py](research.py) describes a same-distance same-direction random control, while the reviewed implementation uses an opposite-side mirrored level. This compares directional tendencies as well as level type; it is not an isolated FVG-specific attraction test.

### 3. LVN price structure — PROXY ONLY

[lvn_research.py](lvn_research.py) allocates bar volume via typical price and compares zones with point-like opposite controls. That approximation is not observed traded volume at each price; width and initial depth can explain transit differences.

### 4. Regime evidence — INVALID BEFORE REUSE

[lvn_phase3b_regime.py](lvn_phase3b_regime.py) and [lvn_phase4_backtest.py](lvn_phase4_backtest.py) apply final daily returns at intraday timestamps without a completed-bar shift. Affected regime claims need a causal reconstruction.

### 5. Trade viability — NEGATIVE / SMALL OOS

The phase-four saved result has 27 trades, mean near -0.369R, 26 IS and one OOS. This supports rejecting that tested configuration, not a precise universal claim about all FVG/LVN trades.

### 6. Fill and risk realism — LIMITED

Phase-four uses entry close, exact barrier prices, and fixed costs without complete gaps/funding/capital treatment. Drawdown uses a trade-R path and span conventions that do not establish marked portfolio risk.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Keep negative decisions closed

Do not launch another search automatically. Require an independently motivated new mechanism or a specific correction before revival; retain every failed magnet and trading configuration.

### Step 2: Define the estimand

Separate attraction probability, time to traverse, direction prediction, and trade expectancy. Predefine horizon, censoring, distance, width, and trigger availability for each; one descriptive effect does not imply another.

### Step 3: Construct valid controls

For FVG specificity, compare direction/distance/time/regime-matched non-FVG levels. For LVNs, match zone width and starting depth; distinguish sampled real levels from mirrored mathematical controls. Freeze sampling seeds and eligible populations.

### Step 4: Audit causal data

Use only completed bars and profiles available before the event, with contract identity, gaps, and volume-proxy limitations explicit. Repair the daily regime timing and test future-mutation invariance.

### Step 5: Make any trade model executable

Specify entry activation after confirmation, limit/market fills, spread, fees, funding, gaps, same-bar ambiguity, and portfolio exposure. Use finer matched data where barrier order matters; do not substitute spot paths without labeling the basis sensitivity.

### Step 6: Control selection and chronology

Ledger all FVG buckets, LVN definitions, directions, regimes, and phase-four tuning. Reserve fresh dates for any new frozen rule, purge overlapping horizons, and use date/episode blocks with correct right-censor handling.

### Step 7: Use claim-specific verdicts

Report control-adjusted probabilities and intervals separately from net R and marked risk. Require adequate independent OOS events before BUILD; otherwise INCONCLUSIVE or ARCHIVE. A one-trade OOS cannot validate the selected strategy.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_fvg_lvn_control_audit.ipynb: observed/proxy data mapping and matched-level estimands.
- 01_conditional_revival.ipynb: only for a newly justified frozen hypothesis with causal controls.
- 02_trade_validation.ipynb: only after descriptive qualification, with all trials, execution, uncertainty, and separate verdicts.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

All five script roles and core comparison/replay paths were reviewed statically, not rerun. Saved metrics are historical; true volume-at-price, complete raw event sets, data gaps, and fills remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
