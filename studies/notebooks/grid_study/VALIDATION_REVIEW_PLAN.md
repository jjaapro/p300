# Grid study: validation review and conditional revisit

Review date: 2026-09-14. Static source/notebook/result review only; no replay or database access.
This is a retrospective review and forward addendum, preserving the frozen README and existing findings.

## Verdict and priority

**P2 — retain the archived no-build decision.**
The existing implementation is not a valid executable grid reference; its advertised upper-bound interpretation must not be reused.
A corrected grid study is warranted only for a separately justified research question, not to rescue a rejected cell.

## Artifacts reviewed

- `grid_study.ipynb`, all zero-based cells 0–8; viewer code cells contain no saved outputs.
- `README.md`, `findings.md`, `grid_harness.py`, and notebook references to the four-cell result tables.
- The findings' implementation correction and drawdown-era correction were included in this review.

## Evidence that remains useful

- The preregistration limits the primary search to two spacings and two fee assumptions.
- All four outcomes are reported; none meets the frozen MAR ≥0.5 plus positive-half-Sharpe gate.
- The best reported maker/0.5% cell has roughly 61.9% additive drawdown and MAR 0.30.
- `findings.md:64` discloses that the ladder buys above the market without valid resting-order direction.
- The historical rejection and disclosure are sound reasons to keep the study archived.
- The findings correctly identify the principal drawdown as COVID 2020; notebook cell 5 still says 2022.

## Specific limitations

1. `grid_harness.py:69` initializes empty levels on both sides and line 81 buys any level contained in a bar.
   Above-market buy limits would execute immediately or require different order types; a touch-only fill is not that market behavior.
2. Sale eligibility uses the bar high and can fill a stale target after a gap without proving an executable market/limit price.
   The `elif` branch prevents a same-bar buy/sell, but this is not a complete conservative intrabar model.
3. The level range contains 21 prices while each position receives capital/20 (`grid_harness.py:69`).
   All-filled notional can reach 105% before considering fees; the advertised 1× capital constraint is not strictly enforced.
4. Recentering creates another ladder using the original capital; solvency and available cash do not govern order creation.
   Positions may continue after additive losses exhaust initial capital; this is not an investable NAV process.
5. `grid_harness.py:149` sums realized returns and current unrealized P&L on initial capital.
   Drawdown lacks the initial zero point and is not percentage drawdown from a changing NAV peak.
6. A seeded 50% independent touch-fill assumption does not model queue position, adverse selection or capacity.
   Correcting order direction changes inventory paths, fees and future opportunity; profitability need not move monotonically downward.
7. Spot bars, futures-style fee assumptions, omitted financing/funding and an unspecified venue leave instrument economics ambiguous.
   The implemented rejection cannot prove that every valid grid, maker process or neutral inventory strategy is unprofitable.

## Conditional future work

### P2.1 — documentation reconciliation if results are cited

- Keep the four-cell result as an implementation-specific archived screen.
- In a new notebook, identify the invalid ladder mechanics and stale notebook drawdown text beside the old table.
- Do not relabel existing numbers as corrected, executable, worst-case or best-case bounds.
- Record source/result hashes and the exact old fee, fill-seed and recenter assumptions.
- Acceptance: each quoted statistic identifies its additive denominator and the defective execution model.

### P2.2 — only if a new executable grid hypothesis is approved for study

- Hypothesis: a frozen spot inventory grid improves net risk-adjusted return over matched spot exposure and cash.
- Define one venue/instrument, grid inventory at inception, buy/sell order types, cash reserve, recenter trigger and shutdown rule.
- Build hand-checkable rising, falling, gap, oscillating and simultaneous-touch fixtures before using market history.
- Require conservation of cash plus marked inventory and prohibit orders beyond available collateral.
- Freeze timestamps as bar open/close and submit only after the observation creating an order is known.
- Prefer quotes/trades for queue-aware fills; otherwise report pessimistic and optimistic OHLC ordering bounds and the ambiguous fraction.
- Include maker/taker fees, spread, cancellations, minimum size, slippage and financing; include settlement funding only for a declared perp version.
- Controls: cash, buy-and-hold with equal average/gross exposure, and periodic inventory rebalancing on the same calendar.
- Preserve the original four trials in the research ledger; freeze a maximum new grid before inspection, including fill seeds/sensitivities.
- Use chronological development/validation windows and untouched forward data; the original 2020–2026 span is already inspected.
- Evaluate actual daily NAV, turnover, inventory concentration, underwater duration and ruin before Sharpe/MAR comparisons.
- Use calendar-block uncertainty preserving inventory/recenter cycles; independent individual fills are not the sampling units.
- Keep original MAR ≥0.5 and positive-half-Sharpe thresholds as archival comparisons.
- Forward advancement additionally requires a positive lower 90% paired net advantage over the exposure-matched control and no modeled insolvency.
- Failure or inconclusive execution bounds ends that frozen hypothesis; it does not trigger an automatic finer spacing search.

## Planned notebook outputs

- New `validation_review.ipynb`: claim/implementation map, defective-versus-valid order-state examples and capital reconciliation.
- If conditionally revived: full trial table, realized/unrealized/funding/fee bridge, daily NAV and benchmark comparison.
- Fill-ambiguity bounds, inventory stress episodes, calendar-block intervals and literal-versus-forward gate table.

## Not applicable / not verified

Options quotes and discretionary signal provenance are not applicable.
Historical queue position, executable capacity, corrected grid performance and live venue fees were not verified.
No existing notebook or result was regenerated, and no new test is required merely to maintain the no-build decision.
