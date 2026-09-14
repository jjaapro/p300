# Validation review plan: short_squeeze_sessions

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **EXPLORATORY_EDGE_REQUIRES_CAUSAL_EXECUTION_REVALIDATION**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [discovery.ipynb](discovery.ipynb): all 25 cells inventoried; decision review focused on session aggregation and retrospective day classification in cells 1, 8, 10, 12, 14, and 25.
- [strategy_backtest.ipynb](strategy_backtest.ipynb): all 51 cells inventoried; detailed source review of cells 1, 6, 8, 10, 12, 14, 20, 26, 32–34, 44, and 46–51.
- Both notebooks have null execution counts and no saved output evidence. There is no accompanying findings file in this directory.

## What is useful in the existing work

- The percentile builder uses prior observations, and the notebook explicitly identifies spot-only execution and limited-sample issues.
- Discovery labels and later context bins are useful descriptive work when kept separate from tradeable triggers; cooldown and session rules are explicitly encoded.

## Claim-specific assessment

### 1. Discovery mechanism — DESCRIPTIVE ONLY

Discovery uses the day's eventual extremes, fade, and CVD at the ultimate sweep to classify completed days. These are retrospective labels, not information available to an intraday entry.

### 2. Headline long edge — UNVERIFIED NET CLAIM

Strategy cell 1 reports roughly 70 longs, +0.40R and PF 1.65, but there are no saved execution outputs. Cell 14 enters at perp close and replays spot minutes, with only two basis points per leg; fees, actual funding, and basis effects are not a full net execution model.

### 3. Six-hour prior low — IMPLEMENTATION MISMATCH

Cell 6 removes non-session bars before rolling, then uses LOOKBACK_BARS+1 observations followed by a shift. With the stated setting this is 25 retained bars, and missing 21:00–24:00 bars make elapsed history differ from a continuous six-hour low.

### 4. Trailing-stop comparisons — INVALID PATH ASSUMPTION

Cell 26 updates the high watermark/1R activation from the current minute high before checking that minute's low. OHLC does not establish that sequence. A fixed-target comparator does not repair the trailing arm's within-bar information problem.

### 5. Selected thresholds and OOS — NOT FRESH CONFIRMATION

Cells 32–34 acknowledge inspection and search lookbacks/thresholds; later cells add exits and context. The nested check in cell 44 concerns an absolute variant, not every later percentile/filter choice. The full search must be counted.

### 6. Portfolio and timing parity — NOT VERIFIED

Cell 20 compounds per-trigger R without a complete overlapping-position capital model. Cell 10's Asia funding summary needs publication-time review and parity against the current signal's funding rule; incomplete minute paths are skipped or closed at their available tail.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Freeze the actual hypothesis

Specify one long reversal rule with its session clock, range history, percentile warm-up, trigger close, funding summary, cooldown, and one-position guard. Record fixed UTC sessions as fixed UTC; do not silently substitute daylight-saving local sessions.

### Step 2: Resolve range and availability

Keep the literal historical rule as a reference and separately register any continuous six-hour correction. Build features on full elapsed-time bars before session masking. Prove completed-bar availability for OI, CVD, and Asia aggregates with future-mutation fixtures.

### Step 3: Replace the execution evidence

Use matched perp minute or finer data for the same venue/contract and verified coverage. Record the September BTC spot-minute repair and cache lineage where spot sensitivities remain. Begin entry only after the signal exists; distinguish next-open execution from an assumed close fill.

### Step 4: Specify realistic fills

Include fees, spread, slippage, settled funding, gaps, stop/target ambiguity, and missing-path failures. For trailing stops, use a conservative event order or actual finer path and report unresolved bars; never activate a trail with a high that may follow the stop.

### Step 5: Control the comparison

Compare with a session-matched random-entry baseline, a sweep without the flow/funding filters, and simple reversal controls. Freeze each ablation and compare on common eligible opportunities as well as portfolio outcomes; keep retrospective discovery labels out of online gating.

### Step 6: Lock the chronological test

Ledger every threshold, session, exit, and context variant already inspected. Reconstruct development windows and use a fresh forward cohort for the selected percentile rule. Purge overlapping six-hour labels and fit percentile state only on available history.

### Step 7: Require an honest verdict

Report net R, daily mark-to-market equity, simultaneous risk, drawdown from initial capital, episode-block intervals, and concentration. No BUILD from the current headline alone; mark inadequate fine data or too few independent events INCONCLUSIVE. A current paper/live decision also requires signal-to-fill parity.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_session_feature_audit.ipynb: causal time semantics, literal-versus-corrected rolling range, data completeness.
- 01_matched_execution_replay.ipynb: fixed-target and path-valid trailing comparisons with fill audit rows.
- 02_frozen_forward_validation.ipynb: full trial history, current-policy parity, chronological uncertainty, and decision.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No historical study was rerun and no signal or live position was inspected. Reported performance is notebook narrative, not saved executed output. This review does not establish current deployed parity or economically correct fills.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
