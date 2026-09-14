# Validation review plan: swing_base_limit_bid

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **EXPLORATORY_NOTEBOOK_TIMING_AND_FILL_EVIDENCE_INVALID**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [discovery.ipynb](discovery.ipynb): all 35 cells inventoried, with detailed review of data loaders (4, 6), base confirmation (8), volume profile (12), MTF signature (16), replay (18), selected signatures (22), mirrored short logic (32), and concluding roadmap (35).
- All 17 code cells have null execution counts and no saved outputs. The notebook is the principal artifact; no independent findings document exists.

## What is useful in the existing work

- The notebook states structural hypotheses and exposes detector parameters, unfilled limits, and mirrored direction exploration.
- It explicitly leaves chronological validation for future work rather than providing a complete independent OOS study.

## Claim-specific assessment

### 1. MTF confluence — INVALID ORIGINAL AVAILABILITY

Cell 16 claims last-closed higher-timeframe state but uses tf.index.asof(ts) against resampled bars without an explicit completed-bar availability shift. Left-labeled hour/4h/day final bars can leak their future close; weekly/monthly labels also require explicit semantics.

### 2. Confirmation and limit activation — INVALID POSSIBLE PRE-CONFIRMATION FILL

Cell 8 timestamps a base's confirmed expansion at the hit bar's label, though that bar's high must first be observed. Cell 18 scans for a limit fill beginning at that timestamp, including the confirmation bar, so a touch before confirmation can become a fill.

### 3. Intrabar and exit diagnostics — UNRELIABLE

Cell 18 updates high-water state before checking the same bar's low; OHLC cannot prove that path. Some excursion diagnostics inspect the remaining path after an exit, so they are not necessarily excursions while the position was open.

### 4. Volume profile — APPROXIMATION

Cell 12 places each 15-minute bar's full volume into a close-price bin. This may be a useful feature proxy, but it is not a measured traded-volume profile or evidence of a true HVN/LVN at that level.

### 5. Backtest performance — UNVERIFIED / NON-NET

There are no saved executed outputs; replay lacks complete fees, slippage, funding, and capital constraints. The narrative's minute coverage beginning in 2017 is not supported by the currently documented BTC minute inventory beginning in 2020.

### 6. Signature selection and tail handling — EXPLORATORY

Cell 22 selects MTF signatures with small n thresholds. The detector's full-forward-horizon loop excludes late candidates even when confirmation could occur earlier; chronological cuts and censoring must be made explicit before inference.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Freeze a minimal structural hypothesis

Revive only if the causal base-and-expansion idea remains independently motivated. Freeze base width, expansion, confirmation, limit offset, stop, TIF, and direction before retesting; MTF and profile add-ons are separate trials.

### Step 2: Establish exact availability

Store formation, confirmation-observed, signal, order-active, and fill timestamps separately. Map all MTF states to completed bars and fixture-test that future changes cannot alter earlier signatures.

### Step 3: Repair limit mechanics

Activate orders only after the expansion is known, with a clear cancel clock. Specify partial fills, queue assumptions, same-bar ambiguity, gap stops, trailing state order, and excursions truncated at actual exit. Missing future coverage is unresolved, not a completed zero.

### Step 4: Match data and economics

Choose spot or perp explicitly, including borrow assumptions for spot shorts or actual funding for perps. Use corrected minute lineage where relevant, same-instrument fine data, fees/slippage, and realistic leverage/capital limits; disclose volume-profile approximation.

### Step 5: Use structural controls

Compare matched bases without expansion, expansion without the base, and the frozen base rule without each add-on. Compare the same eligible opportunities and keep unfilled limits in opportunity/capital reporting.

### Step 6: Control selection chronologically

Ledger every detector, MTF signature, side, target, and exit inspected. Use known history for development, a fresh chronological test, and event-interval purging; keep the final period genuinely untouched.

### Step 7: Require independent net evidence

Report fill rate, net expectancy, day/episode uncertainty, tail dependence, capital-marked drawdown, and asset replication under the same rule. A corrected engine plus sparse selected cells is still INCONCLUSIVE; BUILD requires a new explicit frozen gate.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_base_confirmation_and_mtf_audit.ipynb: availability timeline and adversarial future-mutation fixtures.
- 01_causal_limit_replay.ipynb: order/fill lifecycle, cost decomposition, and control cohorts.
- 02_frozen_structural_validation.ipynb: trial ledger, chronological evidence, uncertainty, and verdict.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No notebook was executed, imports resolved at runtime, or market data queried. The forward scan is not itself proof of leakage if confirmation is delayed correctly; the identified problem is the actual availability/fill timestamp treatment.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
