# Validation review plan: squeeze_recut

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **NOT_DUE_DECISION_ENGINE_REQUIRES_REVIEW**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [README.md](README.md), [recut_lib.py](recut_lib.py), [run_recut.py](run_recut.py), and saved results/union evidence: static rule-to-code and error-path review.
- Detailed source review: recut_lib pairing and decisions around lines 535–665, funding residual reconstruction around 474, and run_recut exception handling around 129–144.
- No notebook exists. The saved September 12 result records n_paired=0 and NOT_DUE; no threshold was calculated or live sample queried in this review.

## What is useful in the existing work

- The paired design uses same-process twins, includes a union ledger for guard disagreements, and forbids early threshold reviews.
- The current README documents September 14 cost and long-funding corrections, a BOTH-closed pairing rule, and incomplete reconstruction limitations. Those concurrent edits are preserved.

## Claim-specific assessment

### 1. Current experimental verdict — NOT DUE

The saved zero-pair result supports no performance decision. This review does not infer a current live sample count or request an early recut.

### 2. Reconstruction failure — MUST FAIL INCONCLUSIVE

[run_recut.py](run_recut.py) catches reconstruction/replay exceptions, substitutes empty collections, then still calls decide. Missing replay can make divergence checks unavailable while other decision branches continue; absence of evidence must not become a cleared check.

### 3. D4 action routing — RULE/IMPLEMENTATION AMBIGUITY

[recut_lib.py](recut_lib.py) combines residual magnitude across variants, then routes a threshold breach to DISABLE_NOSTOP. A breach caused only by the stop variant needs an explicit diagnostic/action rule; the current blanket no-stop action is not logically established by that evidence.

### 4. D6 risk conjunction — EXTERNAL CHECK REQUIRED

The decision branch checks the mean difference but the README also requires marked drawdown within five percentage points. Source prints a conditional promotion consideration, not actual promotion; the external DD evidence must be mandatory before an operator treats D6 as satisfied.

### 5. Drop-trade sensitivity — MISLABELED OPTIMISTIC CHECK

The sensitivity helper sorts ascending and averages x[k:], removing the smallest outcomes. This is not the adverse drop-best/worst-case sensitivity its label implies. Preserve the historical output but clarify the estimand before future decision use.

### 6. Pair/funding completeness — NOT VERIFIED

Pairing uses a pivot with aggfunc='first', which can conceal duplicate keys. Missing reconstructed funding may become NaN with zero used in a residual component. Require unique pair invariants and treat incomplete cost/funding decomposition as inconclusive.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Respect the experimental clock

Do not run early threshold calculations. Preserve the frozen 20/30-pair checkpoints and anytime operational safeguards, and keep this review separate from any performance verdict. A repair addendum must explain changes without retroactive preregistration.

### Step 2: Resolve action semantics first

Specify D4's action for each affected variant, D6's external marked-DD prerequisite, exact equality boundaries, and allowed checkpoint values. Do not silently relax or tighten historical gates while fixing implementation.

### Step 3: Fixture-test decision safety

Use small synthetic records for replay exceptions, absent funding, duplicate pairs, one-arm-only closure, stop-only divergence, and n=19/20/29/30. Test fail-inconclusive behavior and the distinction between operational halt and statistical rejection; no market-data run is needed for these tests.

### Step 4: Require a complete paired manifest

At an authorized checkpoint, freeze both twins' signal, guard, entry, exit, fee, funding, and status records. Reconcile paired and union populations without collapsing duplicates; record exclusions and reconstruction confidence per row.

### Step 5: Reconcile actual execution

Use booked fees and actual settled funding for each long position; align replay contract, quotes, gap behavior, and fill timestamps. Keep approximate short-side reconstruction out of direct evidence about the long twin comparison.

### Step 6: Report paired uncertainty and risk

Compare same-signal differences, union opportunity loss, calendar/episode-block intervals, and daily marked equity under shared capital. Calculate adverse drop-best sensitivity separately from an explicitly labeled remove-worst diagnostic. Use the full prior trial family, with 30 only the documented minimum.

### Step 7: Make the scheduled verdict reviewable

Emit NOT_DUE, INCONCLUSIVE_DATA, operational divergence, continuation, or the exact frozen decision with every conjunct shown at full precision. Missing D6 DD cannot support promotion. Preserve the original verdict and all repaired-versus-original audit rows.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_recut_protocol_and_fixtures.ipynb: reviewable synthetic decision cases and exact rule mapping.
- 01_scheduled_paired_review.ipynb: populated only at the authorized checkpoint, with pairs, union, costs, and marked risk.
- A versioned machine-readable decision trace and repair addendum; neither rewrites the existing README nor advances the sample clock.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No current DB query, recut run, live threshold peek, or twin intervention occurred. Saved results are historical; current n, actual fills/funding, and external D6 drawdown remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
