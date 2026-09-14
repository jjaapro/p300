# Validation review plan: lsr_b5_study

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P1**. Status: **KEEP_HISTORICAL_NO_CHANGE_REVALIDATE_BEFORE_REUSE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [README.md](README.md), [findings.md](findings.md), [results/test0.md](results/test0.md), and [results/parity_explained.md](results/parity_explained.md): protocol, stamp evidence, parity exceptions, and verdicts.
- [test0_stamp_semantics.py](test0_stamp_semantics.py), [gen_variant_pools.py](gen_variant_pools.py), [score_variants.py](score_variants.py), and attribution/figure script interfaces: timing, baseline construction, scoring, and output dependencies.
- No notebook exists in this directory; the documented notebook-style review is currently script/Markdown based.

## What is useful in the existing work

- The study freezes six variants, explicitly treats ambiguous stamps conservatively, and documents why seven extra ETH baseline trades appear after backfill.
- All five changes were killed. Low BTC short sample size and the limitations of gross attribution are stated rather than hidden.

## Claim-specific assessment

### 1. Frozen no-change verdict — RETAIN

[findings.md](findings.md) records five KILLs and no production change. This supports retaining that historical decision, not claiming LSR can never work.

### 2. Timestamp correctness across history — PARTIALLY VERIFIED

The stamp exercise compares a recent 20-day window and limited recent vendor rows. The statement that it cleared every LSR backtest exceeds that evidence; historical revisions, latency, and earlier endpoint semantics remain unverified.

### 3. Parity — DOCUMENTED WITH DATA DRIFT

BTC matches 204 reference trades; ETH has 182 plus seven reconstructed additions, explained in [results/parity_explained.md](results/parity_explained.md). This is useful snapshot accounting, but a changed data snapshot is not immutable reproduction.

### 4. Current baseline relevance — NOT VERIFIED

[score_variants.py](score_variants.py) lines 49–57 applies the historical OKX gate. Results are relative to that stack; they do not evaluate the current gate-off configuration. Imported tilt state also needs exit-completion timing review.

### 5. Short-side precision — INSUFFICIENT

[findings.md](findings.md) reports only 13 BTC V0 OOS trades, including three shorts. The two-asset and n≥20 gates prevent interpreting an attractive subgroup as a robust effect.

### 6. Absolute P&L and attribution — LIMITED

The findings themselves state inherited research-engine limitations and gross, cost-free attribution. That attribution cannot quantify deployable net uplift or justify an execution change.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Revival condition

Retain no change unless the current baseline has materially changed or independent data now meet the frozen sample requirement. Do not reopen the best rejected 365-day setting solely because its historical cell looks attractive.

### Step 2: Freeze the new contrast

Write an additive protocol for one current baseline versus the finite LSR variants. Preserve original V0–V5 rules and explain any new family as new research. Define both-asset and short-side minimum sample gates before viewing outcomes.

### Step 3: Version data and availability

Archive vendor/schema/version manifests, actual release semantics, timestamp shifts, stale-value ages, and missingness. Repeat bounded stamp checks across available historical eras or explicitly mark eras unprovable; period-start labels alone do not establish availability.

### Step 4: Rebuild causal parity

Map the current gate, assets, cooldowns, prior-trade state, and backfilled trades. Require a row-level old/new eligibility diff and state updates only on completed outcomes. Investigate every extra or missing trade before scoring.

### Step 5: Make economics comparable

Replay identical instruments with venue-specific fees, spread/slippage, funding settlements, gap stops, and portfolio constraints. Keep gross attribution separate from net gated expectancy and log any missing funding as unknown.

### Step 6: Use chronological controls

Compare V0 and variants on common eligible signals and actual kept/dropped opportunity sets; include unconditioned and regime-matched controls. Record prior LSR searches plus these variants in an all-trial ledger; keep earlier OOS as development after inspection and reserve fresh chronological data.

### Step 7: Report uncertainty and decision

Use paired differences and day/episode-block intervals across BTC and ETH; report n, exposure, tails, and effective independent episodes. Use full-precision gates and label insufficient subgroup evidence INCONCLUSIVE. PASS requires the new protocol's net criteria; otherwise KEEP_BASELINE, without altering old KILL records.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_lsr_provenance_and_parity.ipynb: timestamp evidence by era, immutable manifests, and baseline diffs.
- 01_lsr_current_stack_validation.ipynb: frozen variants, paired chronological comparisons, trial ledger, and uncertainty.
- A new dated verdict addendum linking the original no-change decision and any genuinely new cohort.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No notebook execution or database verification occurred. The recent stamp test and parity counts are documented results, not freshly replicated facts; historical vendor publication latency and imported tilt behavior remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
