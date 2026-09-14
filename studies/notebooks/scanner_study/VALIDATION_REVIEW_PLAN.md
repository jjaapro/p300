# Validation review plan: scanner_study

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **NO_SLEEVE_RETAINED_BTC_CONDITIONED_ENGINE_INVALID_BEFORE_REUSE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [findings.md](findings.md), [scanner_lib.py](scanner_lib.py), [run_study.py](run_study.py), [run_study_v2.py](run_study_v2.py), [fetch_universe.py](fetch_universe.py), and [build_notebook.py](build_notebook.py).
- [scanner_study.ipynb](scanner_study.ipynb): all six cells and meaningful saved text outputs reviewed; three executed code cells. The notebook primarily presents saved script outputs, rather than independently implementing every experiment.

## What is useful in the existing work

- The findings retain the broadly negative outcome: small gross A expectancy, negative wider-stop A2, and negative B. No sleeve is justified.
- The engine shifts ATR and uses next-bar-open entries; these are useful causal design choices to preserve in a repaired harness.

## Claim-specific assessment

### 1. Study disposition — NO SLEEVE RETAINED

Selected A OOS expectancy is tiny before full trading frictions; A2 and B are negative. The record supports no build, not a universal claim about all event scanners.

### 2. BTC-conditioned arms — INVALID FEATURE TIMING

[run_study.py](run_study.py) and v2 form final hourly BTC closes with left labels; [scanner_lib.py](scanner_lib.py) forward-fills them into 15-minute rows without a completed-hour shift. B/skip_up30d arms can see the unfinished hour's future close. Plain A without BTC gating is not tainted merely by having those columns present.

### 3. Universe relevance — SELECTION BIAS

The universe is a May-23 frozen current/liquid union with selected trader-watchlist names. Historical inclusion is not point-in-time listing/liquidity eligibility, and missing/dead assets need an explicit ledger.

### 4. OOS interpretation — DEVELOPMENT AFTER SELECTION

The September-2025 split is a single chronological split, not a full rolling walk-forward. Ranking OOS across the 28 first and 24 second-run configurations uses that period for selection.

### 5. Trade and capital replay — LIMITED

Stops fill at exact levels without a complete gap model. A 24-hour cooldown can permit overlapping 48-hour trades; no global capital/funding model proves portfolio feasibility. Preserve entry, exit, and holding timestamps explicitly.

### 6. Human comparison — UNSUPPORTED CAUSAL CLAIM

The finding that a trader's win rate is 'manufactured' is not established by this scanner's results. Different selected opportunities and risk management cannot be causally attributed from the available study.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Keep the no-build decision

Do not automatically restart A/B searches. Revival requires a newly justified rule or a material implementation/data correction, with the old negative results retained.

### Step 2: Repair the shared engine first

Map BTC hourly state to its completed-bar availability and fixture-test future invariance. Add gap-stop, same-bar, missing-path, event deduplication, cooldown/overlap, and exit timestamp tests before reusing the engine in another study.

### Step 3: Build a point-in-time universe

Record listing/delisting, quote-asset/contract changes, liquidity eligibility, data coverage, and missing symbols by date. Separate the watchlist case study from a historical investable universe.

### Step 4: Freeze one finite contrast

Specify breakout/reversal event, ATR timing, BTC gate, target, stop, cooldown, and capital guard. Compare against unconditioned events and volatility/session/regime-matched controls, with identical eligible opportunities.

### Step 5: Use venue-aware net execution

Charge fees, spread/slippage, actual perp funding, gap fills, and liquidity/capacity limits by asset; distinguish contract and spot sources. Build marked daily portfolio equity with correlated simultaneous positions and initial capital.

### Step 6: Use honest chronology and selection

Ledger both old grids and all gates/stops; treat their inspected OOS as development. Use nested chronological selection if tuning continues, event-interval purge, and one untouched outer or forward cohort.

### Step 7: Require robust evidence

Report net control uplift, date/symbol-episode clustered uncertainty, concentration, capacity, and capital-marked drawdown. A small selected gross mean is insufficient. PASS requires the new frozen criteria; otherwise INCONCLUSIVE or ARCHIVE.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_scanner_engine_and_universe_audit.ipynb: causal BTC mapping, invariant fixtures, and eligibility manifest.
- 01_frozen_scanner_controls.ipynb: new hypothesis only, with complete event/fill/cost ledgers.
- 02_chronological_net_verdict.ipynb: all trials, unseen cohort, clustered uncertainty, and no-build/build decision.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

Saved notebook outputs and findings were read, not regenerated. Universe counts, market-data gaps, current venue costs, and actual fills were not freshly verified; current/no-build production state was not queried.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
