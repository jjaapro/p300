# Validation review plan: screener

Review date: 2026-09-14. This is a static evidence review and a plan for future work, not a new experiment result.
Priority: **P0**. Status: **STARTER_SETUPS_FAIL_HARNESS_TIMING_INVALID_BEFORE_REUSE**.

## Review boundary

- No backtests, study scripts, data downloads, or database queries were run for this review.
- Existing notebooks, results, plans, preregistrations, and concurrent edits are preserved. This new file does not change a live strategy or authorize a promotion.
- Notebook cell references are 1-based and count Markdown cells. Cell/source inventory and selected decision-path review do not establish runtime correctness.
- P0 means affected evidence must be repaired before reuse; P1 means a decision-bearing gap; P2 means archived/descriptive work with conditional revival.

## Artifacts and evidence examined

- [SCREENER_PLAN.md](SCREENER_PLAN.md), [run_starter_setups.py](run_starter_setups.py), [../../lib/screener_runner.py](../../lib/screener_runner.py), and [../../lib/screener_setups.py](../../lib/screener_setups.py): setup timestamps, outcome windows, scoring gates, universe loading, and execution interfaces.
- [../../material/screener_results/starter_setups_summary.json](../../material/screener_results/starter_setups_summary.json): all four saved setup summaries and reasons. No notebook exists.

## What is useful in the existing work

- The plan asks for baseline improvement, stability, frequency, neighborhood robustness, and asset diversity; the starter output records failures rather than promoting a winning horizon.
- The funding-flush proxy is explicitly named as a proxy in the setup code, which preserves an important distinction from a true funding/OI signal.

## Claim-specific assessment

### 1. Starter disposition — ALL FOUR FAIL

The saved S1, S2, S4-proxy, and S10 summaries are all FAIL. The record does not justify a sleeve or the plan's expectation that a small batch will necessarily find an edge.

### 2. Daily-close entry timing — INVALID R EVIDENCE

[../../lib/screener_setups.py](../../lib/screener_setups.py) uses completed daily OHLC/volume and daily-close entry while retaining the day label as ts. The runner's R replay starts hourly rows at ts, potentially using price movement earlier on the signal day before that close-based entry can exist.

### 3. Incomplete forward horizons — INVALID LABEL HANDLING

Forward-return measurement uses searchsorted(..., right)-1 without requiring the target horizon to exist; a target beyond the data can use the last known close. Empty R paths return an entry-price/zero outcome. These are unresolved observations, not mature labels.

### 4. Gate implementation — DOES NOT IMPLEMENT FULL PLAN

The runner selects the best forward-horizon Sharpe, splits by row count, and can bypass stability when unavailable. It does not implement the plan's full baseline-excess or parameter-neighborhood conditions, so a future PASS string would not itself establish the planned gate.

### 5. Funding mechanism and costs — NOT MEASURED

S4 uses price/volume capitulation because per-coin funding/OI is absent from its inputs. R outcomes omit complete fees, slippage, and actual funding; forward drift and gross per-trigger Sharpe are not deployable trade expectancy.

### 6. Universe and causal harness — NOT VERIFIED

Universe selection uses a stored current liquidity ranking. Setup functions receive full frames; the setup module's comment that the runner enforces causal sub-slicing is not established by the reviewed caller. Each setup needs availability invariants, not trust in that comment.

## Tailored future validation sequence

The following is proposed future work. It does not convert previously inspected data into unseen data or retrospectively preregister earlier choices.

### Step 1: Keep starter failures closed

Do not expand the idea catalog automatically. First repair reusable measurement semantics; any revived setup needs a finite hypothesis and explicit reason beyond an attractive selected forward horizon.

### Step 2: Make timestamps executable

Represent candle-open, candle-close/available, signal, and earliest order times separately. Replay daily-close setups from the next available execution point; fixture-test same-day pre-entry extrema and future mutation.

### Step 3: Fix label maturity and missing data

Require full requested horizon or record right-censored/unresolved status. Audit empty and truncated paths, duplicates, outages, and close availability; exclude unresolved observations transparently from gates instead of converting them to zero.

### Step 4: Freeze universe, mechanism, and controls

Build point-in-time liquid eligibility and identify spot/perp instruments. Register S4 as a price-volume proxy unless actual funding/OI is supplied. Compare with matched universe/calendar baseline and simple component controls.

### Step 5: Implement the exact economic gate

Specify actual net fees/slippage/funding, gaps, barriers, exposure, and capital before measuring. Encode baseline uplift, minimum sample, stability, robustness, and diversification explicitly; missing required evidence means INCONCLUSIVE rather than bypass.

### Step 6: Separate selection from testing

Ledger every setup, parameter, target, and 1/3/7/14-day horizon. Use calendar splits, nested chronological selection where applicable, event-interval purge, and an untouched final/forward cohort; row halves and best-horizon selection are not independent validation.

### Step 7: Report correct uncertainty and verdict

Use day/cross-asset-episode blocks, matched net differences, concentration, marked drawdown, and capacity. Gross forward return, per-trigger Sharpe, and executable R receive separate verdicts. Retain FAIL unless a new frozen study meets all of its conditions.

## Data and reusable-helper constraints

- A future run must freeze inputs, source and configuration hashes, instrument/venue, bar-label and availability semantics, calendar cutoffs, missingness, warm-up, and label maturity. Store per-event reasons for inclusion and exclusion.
- If BTC minute-derived inputs or caches are used, reconcile the documented September 7 repair before reusing older results: [data repair audit](../../../docs/strategy_issue_validation_2026_09_07.md). The repair does not prove that this study's old artifacts were regenerated.
- For perp economics, distinguish predictive funding features from actual settlement cash flows; eight-hour boundary sampling alone cannot establish historical settlement provenance. See [funding source implementation](../../../data/sources/venue_funding.py).
- [Validation helpers](../../lib/validation) are optional building blocks, not a certificate. Calendar folds do not fit models or prevent event overlap automatically; event-interval purging must reflect the actual holding horizon.
- Use [block bootstrap](../../lib/validation/bootstrap.py) with meaningful daily/episode units. [DSR/PBO helpers](../../lib/validation/dsr_pbo.py) require appropriate return units and a complete trial history; do not substitute a selected final trial count for the research family.
- [Metrics](../../lib/validation/metrics.py) on additive returns and [triple-barrier helpers](../../lib/validation/triple_barrier.py) do not replace an intraday fill engine or a compounded, marked portfolio ledger. Reuse only after checking the caller's units and assumptions.

## Proposed future notebook artifacts

- 00_screener_measurement_audit.ipynb: daily availability, mature-label, and censoring fixtures.
- 01_frozen_setup_controls.ipynb: only after the harness is repaired, with point-in-time universe and all trials.
- 02_net_chronological_verdict.ipynb: exact gate coverage, independent uncertainty, and explicit missing-evidence status.
- Each future notebook should run from a fresh kernel against an explicit research snapshot, surface missing prerequisites, and save readable outputs plus machine-readable event/fill/decision ledgers. A viewer of saved JSON must identify that role.
- Record every attempted trial and failed/excluded run before selecting results; preserve old outputs and append versioned corrections. No new artifact listed here has been generated by this review.

## Not applicable, not verified, and stopping rules

No runner, setup, backtest, or DB query was executed. The four FAILs are saved historical results; current universe, corrected data lineage, actual funding, runtime imports, and execution economics remain unverified.

- A descriptive or archived negative study does not need a new trading backtest merely to remain archived. Funding, capacity, and order-fill work is conditional on a claim about a tradeable implementation; it is not proof of a descriptive mechanism.
- Clean syntax and saved execution counts are weaker than successful fresh-kernel execution. Runtime dependencies, external paths, numerical reproducibility, and production parity remain unverified unless explicitly evidenced above.
- Use separate verdicts for data integrity, causal feature construction, execution validity, statistical support, and economic viability. Missing required evidence means INCONCLUSIVE; it must not silently become PASS.
- No current capital allocation or deployment decision follows from this plan. Preserve existing no-build/kill/not-due decisions until the specifically applicable future conditions are met.
