# Validation review plan: footprint_study

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P2**. Status: **TEST_A_KILLED_TEST_B_NOT_AUTHORIZED_BY_EVIDENCE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [README.md](README.md), [gen_events.py](gen_events.py), [fetch_footprints.py](fetch_footprints.py), and [score_test_a.py](score_test_a.py): preregistered flags, event generation, download/parser behavior, join/coverage handling, and Test A gates.
- Saved verdicts appended to README: six Test A combinations and explicit decision not to proceed to Test B. No notebook exists.

## What is useful in the existing work

- Test B depends explicitly on passing Test A, and the project respected the all-six-KILL outcome.
- The protocol separates gross from net outcomes, documents event coverage, and reuses a deterministic event engine instead of selecting individual chart examples.

## Claim-specific assessment

### 1. Test A verdict — KILL RETAINED

All six flag/target combinations fail. The strongest C3 gross result remains negative after costs and fails the second half; neither Test B nor alt continuation is established.

### 2. Footprint mechanism — LIMITED OPERATIONAL TEST

Top-quarter signed volume is a trade-flow summary, not direct proof of absorption or participant intent. A killed summary feature does not disprove every footprint hypothesis.

### 3. C3 semantics — REQUIRES EXPLICIT DEFINITION

[score_test_a.py](score_test_a.py) implements event-bar C1 AND prior-bar top delta<0. If 'combined' in the README means pooled two-bar signed volume, those are different rules. Preserve the tested AND rule and register any pooled alternative as a new trial.

### 4. Missingness and denominator — AUDIT NEEDED BEFORE REUSE

The scorer inner-joins events and footprints; the fetcher can mark insufficient-trade/zero-range rows done without storing an aggregate. Missing prior rows become a false C3 condition. Report eligible, requested, parsed, excluded, and matched counts separately.

### 5. Archive reproducibility — NOT VERIFIED

The fetch path reads daily archives and stores derived aggregates, but complete raw archive checksums, parse versions, and the full bin vectors were not independently verified. Reported 100% matched coverage alone is not end-to-end provenance.

### 6. Independence and selection — LIMITED

The 1,357 event rows include different targets and clustered symbol/date events, not necessarily 1,357 independent trials. The chronological half split is a discovery gate after all six comparisons, not untouched evidence for a newly chosen flag.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Respect the stop condition

Retain the Test A KILL and do not run Test B automatically. Revival requires a separately justified new flag or corrected semantics, recorded as a new experiment rather than a reinterpretation of the failed one.

### Step 2: Freeze event and flag definitions

Specify price-bin construction, boundary inclusion, signed-volume units, event availability, and whether C3 is a conjunction or pooled delta. Make the event/target deduplication key explicit.

### Step 3: Audit source coverage

Create an immutable archive/parse manifest and loss ledger, including failed requests, low-count bars, zero ranges, and missing prior bars. Fixture-test boolean maker-side parsing, time units, cross-midnight loading, and bin edges.

### Step 4: Use appropriate controls

Compare against the unchanged scanner event, overall bar delta/volume, and matched non-flag events. Match symbol, regime, volatility, session, and target; do not infer mechanism from a favorable selected subgroup.

### Step 5: Reconcile execution

Use the same contract for footprint and fills, post-close entry availability, venue fees/slippage, funding, gaps, and ambiguous bars. Keep each target a trial and distinguish net trade results from descriptive flow association.

### Step 6: Preserve chronology and full search

Ledger the six old comparisons, any new flag definitions, and any alternative continuation tests. Hold a new chronological cohort untouched; purge overlapping holding periods and cluster uncertainty by date and symbol episodes.

### Step 7: Decide conditionally

Require net control uplift, sample/episode sufficiency, and stability under the frozen rule before proceeding to downstream Test B. Missing coverage or semantics yields INCONCLUSIVE, and another failure remains ARCHIVE.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_footprint_coverage_and_semantics.ipynb: archive lineage, denominator reconciliation, and C1/C2/C3 fixtures.
- 01_new_flag_validation.ipynb: conditional revival only, with matched controls, execution, and all trials.
- A Test B eligibility record that explicitly cites a new qualifying Test A result if one ever exists.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No archives were fetched, parquet recomputed, scanner rerun, or Test B executed. README counts and verdicts are documented results; raw bin data, archive integrity, and actual execution remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
