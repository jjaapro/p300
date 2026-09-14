# Validation review plan: squeeze_bull_revalidation

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **HISTORICAL_BUILD_PRESERVED_CURRENT_VALIDATION_INCOMPLETE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [README.md](README.md), [findings.md](findings.md), [squeeze_bull_lib.py](squeeze_bull_lib.py), [run_parity.py](run_parity.py), [run_oos.py](run_oos.py), [run_combined.py](run_combined.py), [run_fragility.py](run_fragility.py), and notebook-builder interfaces.
- [squeeze_bull_revalidation.ipynb](squeeze_bull_revalidation.ipynb): all 19 cells inventoried, including saved text outputs; nine code cells have execution counts. Main reported verdict and fragility outputs were compared with the findings addenda.
- Source review emphasized data loading, ledger construction, daily regime availability, concatenated portfolio scoring, and the independent correction section in findings.

## What is useful in the existing work

- The study has a frozen protocol, parity reconstruction, explicit unresolved-trade treatment, funding-source A/B diagnostics, and unusually candid post-hoc fragility corrections.
- The findings preserve the historical gate verdict while documenting small samples, the three-episode structure, broader search multiplicity, and a corrected overlap calculation.

## Claim-specific assessment

### 1. Frozen BUILD — RETAIN AS HISTORICAL RULE RESULT

The original gate passed with ten OOS trades, mean about +0.2021R, and combined MAR about 1.60. This is a result under the frozen historical clause, not a current-policy deployment validation.

### 2. Regime gate — INVALID ORIGINAL TIMING

The original unshifted daily 30-day return uses the current day's final close. Findings report all 20 inspected eligible fires affected, by 1–21 hours. Previous-completed-day and intraday causal alternatives are different gates, not interchangeable repairs.

### 3. Search-adjusted confidence — WEAK

Findings' independent correction counts the prior 30 threshold and 80 exit searches; DSR deteriorates sharply beyond N=1. The notebook's saved N=1 fragility view must be read alongside that correction, not as complete family-wide evidence.

### 4. Sample independence — INSUFFICIENT PRECISION

The ten OOS events cluster into only three episodes (8/1/1), with episode-bootstrap uncertainty crossing zero. The tenth fire predates the preregistration date; this alone does not prove prior inspection, but it cannot establish a newly accrued unseen sample.

### 5. Combined portfolio — LIMITED PROXY

[run_combined.py](run_combined.py) concatenates trade R by signal time. This does not establish daily marked equity, actual capital usage, drawdown, or interaction of overlapping holdings. Findings corrected an earlier overlap bug; do not revive that superseded error.

### 6. Funding and gate details — PARTIAL / HISTORICAL

Selecting pre-cutover rows at eight-hour boundaries does not turn predicted funding into documented settlements. The gate compares rounded metrics in source, though the recorded raw MAR remains above the current threshold; future gates should use full precision.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Preserve the freeze

Add a new versioned current-policy protocol; do not rewrite the old preregistration or erase its BUILD. Name the current regime gate, stop/no-stop variant, contract, funding treatment, and one-open guard exactly.

### Step 2: Close timing gaps

Implement and fixture-test completed-day or explicitly intraday causal gating, selected before new scores. Require each input's event and availability timestamps, funding provenance by era, and a full old-versus-corrected eligibility diff.

### Step 3: Reconcile current twins

Link to the separate [squeeze_recut review](../squeeze_recut/VALIDATION_REVIEW_PLAN.md) for paired current-policy evidence. Do not use unmatched old pooled R to justify current stop removal or promotion.

### Step 4: Audit same-instrument economics

Use actual contract execution, fees, spread/slippage, gap rules, funding settlements, missing-path handling, and capital limits. Produce daily marked P&L for the combined book with a documented initial equity peak.

### Step 5: Register the complete search

Carry forward the threshold and exit families plus any causal-gate alternatives; N=1 is a frozen-record label, not a universal multiplicity count. Record choices and dates without retroactive claims of prospective intent.

### Step 6: Test chronologically

Use old OOS as known evidence after inspection and preserve a genuinely new forward cohort. Set sample/episode floors and review dates without peeking between them; report clustered uncertainty and causal-gate sensitivity as separate trials.

### Step 7: Decide on current evidence

Use full-precision net criteria, episode-block intervals, tail concentration, and daily capital-aware risk. Retain INCONCLUSIVE when independence is inadequate. The funding-CVD addition has only two negative OOS observations here and receives no automatic revival.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_historical_to_current_parity.ipynb: original record, correction addenda, causal gate and funding lineage.
- 01_current_policy_net_validation.ipynb: portfolio marks, episode uncertainty, complete trial accounting.
- A new current-policy verdict report linked to the preserved historical notebook and squeeze recut gate.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

Saved notebook outputs and findings were read, not recomputed. Source-seam semantics, runtime parity, current positions, actual fills, and immutable preregistration exposure history were not independently verified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
