# Hedge tests: validation review and conditional revisit

Review date: 2026-09-14. Static review only; no replay, database query or download.
This addendum preserves the historical nine-cell reactive experiment and rejected range experiment.

## Verdict and priority

**P2 — retain the no-change/no-build conclusions.**
The saved tests are useful negative screens, not calibrated evidence that hedging never helps or that the quoted MAR is investable.
Correct the reusable execution/accounting model only if a concrete hedge question is reopened.

## Artifacts reviewed

- `hedge_experiments.ipynb`, all zero-based cells 0–6; no saved code outputs, with conclusions embedded in cell 6.
- `reactive_hedge.py`, `range_hedge.py`, saved result text and notebook result references.
- Shared range detector and the imported Chento optimized/replay context were inspected as source.

## What the existing evidence supports

- The reactive experiment reports all nine trigger/unwind combinations rather than only the best one.
- Saved baseline has approximately 103 events; changes are small enough that no deployment change was recommended.
- Range hedging reports roughly 380 events, mean −1.34R and only one both-legs-win event.
- Range replay checks stops first; costs are explicitly represented in R rather than ignored outright.
- The notebook acknowledges sample size, limited differentiation and the range experiment's strongly negative screen.
- Keeping both concepts unpromoted is proportionate to this evidence.

## Specific gaps

1. `reactive_hedge.py:68` checks a favorable target before an adverse hedge trigger on the same bar.
   Later logic updates an extreme and can unwind using the opposite extreme of that bar; actual order is unknown.
2. At `reactive_hedge.py:104`, a hedge still present at time expiry subtracts one cycle cost from the frozen result.
   A main position and independently opened/closed hedge require a leg-by-leg fee reconciliation; the second cycle appears uncharged.
3. Outcome-dependent filters are applied separately after each replay, yielding about 98–109 events versus 103 baseline.
   Mean and MAR changes are not purely paired same-entry effects; policy path and entry eligibility need explicit separation.
4. `reactive_hedge.py:116` and `range_hedge.py:135` form entry-ordered cumulative R and divide total R by its drawdown.
   This quantity is neither annualized MAR nor simultaneous account NAV drawdown, and omits the initial zero peak.
5. `range_hedge.py:52` treats two opposing legs as R outcomes without a joint collateral account.
   Gross notional, asymmetric time open, residual inventory, funding and hedge margin cannot be inferred from the sum.
6. Range boundaries include the current detector bar (`studies/lib/range_detector.py:63`); execution must start after that bar closes.
   Exact level fills through gaps, queue position, spread and stop slippage are not established by OHLC touches.
7. The imported optimized Chento pool is already selected research with its own chronology and costs.
   The nine-cell test does not create a fresh independent validation sample for that upstream selection.

## Conditional future plan

### P2.1 — before a reusable hedging claim is quoted

- Identify the saved result as a negative screen under the stated bar model and approximate R accounting.
- Preserve all cells and the original event counts; do not describe differing cohorts as a perfectly paired comparison.
- Save source/dependency/result hashes and an inherited-search manifest in a new viewer notebook.
- Acceptance: reported R statistics have correct units and are not labeled portfolio MAR or liquidation evidence.

### P2.2 — only if hedging is reconsidered for a named risk

- Hypothesis: a frozen reactive hedge reduces a named tail-loss measure enough to compensate for extra trading/funding cost.
- Define whether hedge mode means two gross positions, net position reduction or close-and-reopen; these have different fees and margin.
- Build small path fixtures for adverse-first, favorable-first, simultaneous touch, gap, still-hedged expiry and repeated unwind.
- Require identical original entry IDs for the paired policy experiment; separately test a causal closed-trade entry gate if desired.
- Use timestamped trades/quotes for order sequence, or publish both feasible OHLC ordering bounds and the ambiguous fraction.
- Book each main/hedge fill, fee, spread, slippage and signed funding settlement; enforce net and gross exposure limits.
- Controls: no hedge, simple size reduction, immediate close and a frozen wider-stop alternative on equal capital.
- For range hedging, compare a single edge trade and cash with the same risk/collateral; define range ownership after the observed close.
- Preserve the nine original trials; predeclare at most one primary hedge and fixed controls before new results are inspected.
- Use chronological future data with a purge spanning the maximum seven-day hold; old Chento selection history remains development data.
- Resample contiguous calendar blocks across overlapping trades; report tail-event count and uncertainty rather than independent-event p-values alone.
- Primary decision: lower 90% paired net improvement bound above zero for the predeclared utility, with no increase beyond the frozen gross-margin limit.
- If the goal is tail reduction, predeclare the minimum reduction and permitted mean-return sacrifice before testing; do not select them after outcomes.
- Failed or inconclusive paired evidence keeps the no-change decision; no automatic expansion of hedge thresholds is planned.

## Planned notebook outputs

- New `validation_review.ipynb`: event cohort bridge, path-order fixtures and full main/hedge cash-flow ledger.
- Calendar NAV and gross/net/margin charts; paired differences under explicit execution bounds.
- Full trial registry, control comparison, dependent uncertainty and a conditional decision table.

## Not applicable / not verified

Option bid/ask and quarterly settlement are not applicable to these perp experiments.
Quote-level sequencing, live hedge mode, effective funding, collateral and corrected profitability were not verified.
No existing experiment was rerun and no retest is required merely to maintain its rejection.
