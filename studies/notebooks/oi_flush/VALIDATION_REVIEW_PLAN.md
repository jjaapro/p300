# Validation review plan: oi_flush

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **HISTORICAL_SEARCH_CAUSAL_EVIDENCE_INVALID_BEFORE_REUSE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [research.py](research.py), [phase2_backtest.py](phase2_backtest.py), [threshold_ablation.py](threshold_ablation.py), [phase3a_correlation_with_funding_cvd.py](phase3a_correlation_with_funding_cvd.py), and [overlap_vs_triple_v3.py](overlap_vs_triple_v3.py): source, hypotheses, imported baseline, and selection logic.
- Saved phase-two and threshold-ablation JSON summaries under [../../material/chento/validation](../../material/chento/validation): historical selected metrics, grids, and sample sizes. No notebook or standalone findings/README existed here before this review.

## What is useful in the existing work

- The initial study includes random-bar baselines and multiple horizons, and later work reports pooled as well as bullish-regime outcomes.
- Negative pooled performance remains visible. The later squeeze-bull revalidation documents important inherited limitations, rather than allowing the original sweep to be the only evidence.

## Claim-specific assessment

### 1. Pooled OI-flush edge — NOT ESTABLISHED

Saved phase-two results show roughly 221 events and pooled mean near +0.005R, with a selected bullish subset driving the stronger narrative. The result does not establish a broad unconditional net edge.

### 2. Bull-regime timing — INVALID ORIGINAL EVIDENCE

[phase2_backtest.py](phase2_backtest.py) builds an unshifted daily return gate using the final daily close at intraday signal timestamps. Bullish-subset attribution therefore needs a new causal replay before reuse.

### 3. Threshold and exit selection — EXPLORATORY

Phase two selects among four stops, five targets, and four TIFs (80), then inspects regimes. [threshold_ablation.py](threshold_ablation.py) searches 30 threshold definitions; selecting -2% after the earlier -3% rule is another trial, not fresh OOS confirmation.

### 4. Elapsed-time and price semantics — NOT VERIFIED

The OI/price join uses row-based changes and entry-close replay. Missing hourly rows can change the elapsed meaning of a four-row flush, and observation stamps do not establish release time.

### 5. Execution and portfolio evidence — LIMITED

The replay charges a fixed 18 bp, uses exact barrier fills without a full gap/open model, and does not establish actual funding or shared capital. Phase-three monthly signal-assigned R and a correlation threshold alone cannot establish portfolio diversification.

### 6. Descendant evidence — DO NOT DOUBLE COUNT

The later [squeeze-bull review](../squeeze_bull_revalidation/VALIDATION_REVIEW_PLAN.md) is a descendant of this selected family. Its old OOS and this study's inspected data cannot be treated as independent confirmations.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Set a conditional revival rule

Do not rerun the old search automatically. Reuse only to evaluate an explicitly frozen current causal descendant or a separately justified new mechanism; keep pooled and rejected variants in the record.

### Step 2: Freeze the event definition

Specify elapsed OI horizon, drop threshold, completed bullish gate, signal availability, cooldown, and one exit policy. Define source/contract units and stale-data limits before outcomes.

### Step 3: Repair input timing

Require continuous elapsed-time OI observations or explicit missing-bar exclusion; prove daily regime and OI release availability. Map migrations, revisions, and the funding source seam in an immutable manifest.

### Step 4: Use appropriate controls

Compare matched random times, price drops without an OI flush, OI flushes without the gate, and the gate alone. Match regime, volatility, session, asset, and exposure; avoid choosing the best control after inspection.

### Step 5: Audit execution

Replay matched perp data after signal availability with fees, spread/slippage, actual funding, gaps, barrier ambiguity, and unresolved tails. Produce position and capital ledgers instead of assuming trade-R concatenation is a portfolio.

### Step 6: Use all trials and real chronology

Carry at least the visible 80 exit and 30 threshold searches, plus regimes/overlaps, in the ledger. Treat old OOS as known development data; lock a new calendar or forward period, purging event overlap and refitting only in training if needed.

### Step 7: Decide with independent episodes

Report net paired effects, daily marked equity, episode-block intervals, tail removal, and concentration. Insufficient independent bull episodes means INCONCLUSIVE; a negative frozen effect means ARCHIVE, without another compensating grid search.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_oi_event_and_gate_audit.ipynb: immutable definitions, availability tests, and corrected eligibility diffs.
- 01_oi_frozen_controls.ipynb: matched control cohorts and realistic replay audit.
- 02_oi_forward_verdict.ipynb: complete search lineage, chronology, uncertainty, and descendant linkage.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

Historical JSON counts and scores were inspected, not replicated. Database continuity, release latency, source revisions, and actual execution remain unverified; there is no executed notebook evidence in this directory.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
