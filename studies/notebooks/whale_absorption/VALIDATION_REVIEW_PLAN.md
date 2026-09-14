# Validation review plan: whale_absorption

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P2**. Status: **ARCHIVED_NEGATIVE_CONDITIONAL_REVIVAL_ONLY**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [research.py](research.py): all experiment stages inventoried; detailed review of cadence loaders, tier-flow aggregation, z-score timing, replay, grid, and gates.
- Six saved whale_absorption_phase1_results JSON files under [../../material/chento/validation](../../material/chento/validation): 1m/5m/15m, paid and zero-cost runs; result summaries and parameter grids were inspected.
- No notebook, README, or independent findings document existed here before this review.

## What is useful in the existing work

- The study tests fade and follow directions across three cadences, reports a cost-free sensitivity, and keeps every cadence's no-pass outcome.
- Futures flow is paired with futures price inputs, and trailing feature construction does not obviously use centered or future windows in the reviewed path.

## Claim-specific assessment

### 1. Discovery acceptance — FAIL RETAINED

All six saved result files record gate_pass=false and zero candidates. Retain the negative screen; do not reinterpret a selected positive mean as accepted alpha.

### 2. Cost sensitivity comparability — NOT FULLY PAIRED

The 15m paid result uses z windows 96/192/288, cooldowns 4/8, and TIFs 16/48; its zero-cost file uses time-scaled windows 4/16/32, cooldowns 1/2, and TIFs 4/16. That pair changes parameters as well as costs, so it is not a clean recost comparison.

### 3. Whale mechanism — PROXY ONLY

Notional trade-size buckets are not identified beneficial owners or proof of inventory absorption. The aggregate data can support a size-conditioned flow hypothesis, not observed whale intent.

### 4. Coverage and independence — LIMITED

Fine-cadence evidence spans roughly thirteen weeks in 2026. Many overlapping events and hundreds of parameter cells do not become independent confirmations across market regimes.

### 5. Execution evidence — LIMITED

Entry-close and exact-barrier replay with a fixed cost do not establish latency, gaps, funding, spread, or queue execution. Both halves are used in the discovery search, so the second half is a stability screen, not untouched OOS.

### 6. Reusable engine — AUDIT BEFORE REUSE

No positive decision currently depends on this engine. If reused, verify censored paths, data gaps, time-scaled windows, and identical parameter hashes before attributing changes to cost or resolution.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Revive only for a concrete reason

Keep the family archived unless a new independently justified feature, materially longer matched data, or a documented execution correction warrants a new hypothesis. Do not repeat the grid merely to search for a pass.

### Step 2: Freeze one estimand

Define absorption as a measurable conditional forward-price effect, with explicit flow bucket, side, horizon, and event spacing. Separate economic interpretation from unobservable participant identity.

### Step 3: Audit data by cadence

Freeze venue/contract, flow units, aggregation rules, coverage, gaps, and bar availability. Require overlapping same-period comparisons for resolution effects and disclose that available fine perp history is short.

### Step 4: Repair comparison design

Pair cost scenarios on identical events and exact parameter hashes; pair cadence comparisons on common dates and equivalent elapsed horizons. Include total-volume and ordinary-delta controls to isolate any size-bucket contribution.

### Step 5: Use implementable replay

Specify post-signal entry, fees, spread/slippage, funding, stop gaps, ambiguous bars, and open-position limits. Treat close-entry and zero-cost variants as diagnostic bounds, not deployable returns.

### Step 6: Freeze chronology and trial accounting

Record all cadence grids, directions, cost scenarios, and inspected halves. Fit only in development; use a genuinely new chronological cohort, event-interval purge, and day/episode blocks rather than IID trade resampling.

### Step 7: Set a conditional verdict

Predefine minimum independent episodes and net uplift versus controls. Failure stays ARCHIVED; insufficient new data is INCONCLUSIVE. Any positive effect must survive selection-aware uncertainty and a capital-aware execution check before a build proposal.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_archived_result_inventory.ipynb: six result manifests and exact paired/unpaired parameter comparison.
- 01_new_absorption_hypothesis.ipynb: created only after a justified revival, with controls and timing fixtures.
- 02_frozen_validation.ipynb: matched execution, all-trial ledger, block intervals, and verdict.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

The negative verdicts and parameter discrepancies are read from saved artifacts, not replicated. No tape ownership, DB completeness, latency, fills, or actual funding was verified. Fine-data coverage is documented context, not a fresh inventory.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
