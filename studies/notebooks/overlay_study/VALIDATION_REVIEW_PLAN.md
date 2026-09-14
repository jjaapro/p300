# Overlay study: validation review and forward addendum

Review date: 2026-09-14. Static review of notebook, scripts and findings only.
No signal generation, replay, database query or download was run.
This retrospective addendum preserves the original and backward-only results and all later corrections.

## Verdict and priority

**P0 for reuse of tilt, wick-exit or combined-book improvement as validated evidence.**
The existing paper/no-production-change boundary should remain.
The backward-only trigger correction is valuable, but does not remove later policy-level lookahead or establish account drawdown.

## Artifacts reviewed

- `overlay_study.ipynb`, all zero-based cells 0–3, including saved text tables.
- `findings.md`, `gen_trades.py`, `gen_trades_backonly.py`, `run_overlays.py` and referenced original/backward-only results.
- Notebook cell 1 selects best MAR rows; cell 3 displays entry-ordered additive R curves.
- Related round-level implementation in `../paladin_study/exit_wick_study.py` was inspected.

## Useful existing checks

- Original trigger intersection lookahead is explicitly documented instead of concealed.
- A backward-only trigger pool was generated and later findings discuss changed conclusions after ETH data updates.
- The grid includes fixed exit, tilt and tagging alternatives; several unfavorable results are retained.
- Findings acknowledge gross-cost conventions, limited history and paper-only use.
- These corrections demonstrate research traceability; they do not independently validate a selected overlay.

## Decision-bearing defects

1. `run_overlays.py:125` lets tilt inspect `rs[:i]`, final outcomes of every earlier-entry trade.
   No exit timestamps are supplied; overlapping trades may not have resolved when the next trade is sized or skipped.
   This contaminates two-loss and daily-P&L gates, and skipped trades can still affect hypothetical state.
2. `run_overlays.py:80` builds round levels from the entire future holding window.
   The grid step depends on the future maximum's order of magnitude; changing future prices can change a past exit decision.
3. Gross R replay at `run_overlays.py:71` omits fill-specific costs and funding.
   Costs in R vary with stop distance; skipping trades or changing holding duration invalidates a blanket unchanged-ranking argument.
4. `run_overlays.py:142` cumulatively adds final trade R in entry order, without an initial zero point.
   This is not daily marked account equity, annualized MAR, allocated diversification or concurrent margin demand.
5. Pairing BTC and ETH outcomes without a common capital budget cannot establish drawdown reduction from diversification.
   Exposure, signal frequency, common market shocks and differing calendar coverage must be aligned.
6. The search spans five exits × four tilt policies × two tags across three scopes: 120 rows per result pool.
   Multiple trigger pools, source revisions and already-inspected 2025+ outcomes enlarge the research history beyond one winner table.
7. Tag availability, macro-event publication timestamps and bar-close/entry timing need exact as-of provenance.
   A backward-only trigger intersection does not automatically validate every downstream feature and fill.

## Prioritized forward plan

### P0.1 — establish a causal policy engine before reusing improvements

- Hypothesis: policy choices are invariant to future observations and depend only on events known at the decision time.
- Freeze source/result/dependency hashes and preserve old table values in a discrepancy bridge.
- Create an event ledger with signal observed-at, decision, fill, exit, settlement and P&L-recognition timestamps.
- Size/skip from the selected policy's own realized state, or explicitly predeclare a hypothetical reference-state policy.
- Freeze round-grid scale from known entry prices or prior data; do not derive it from future extrema.
- Add fixtures with unresolved earlier trades, skipped losses, simultaneous entries, same-day closes and a future price crossing a decimal boundary.
- Require each earlier decision/fill to remain unchanged when all later rows are perturbed.
- Reconcile R, fixed-unit dollar P&L, gross/net exposure and fees/funding for every accepted and skipped event.
- Acceptance: no future outcome enters tilt state, all same-bar ambiguities are explicit, and daily equity reconciles to the final ledger.

### P1.2 — retest only the actual policy question

- Freeze one primary exit/tilt policy and fixed controls before new results; do not restart the full grid as a selection exercise.
- Controls: original causal strategy, equal average exposure, simple fixed down-sizing, and the combined book at equal total capital.
- Preserve the known 120-row family per pool plus all later source/cost/policy variants in the trial registry.
- Use venue-consistent timestamped quotes or next-bar fills with latency, spread, slippage, gaps, fees and signed funding.
- Separate the paired same-entry exit experiment from the full policy experiment where earlier exits permit later entries.
- Model overlapping BTC/ETH positions, idle cash and the same leverage/margin cap in every comparison.
- Build daily NAV, drawdown duration, turnover and tail losses rather than ranking entry-order cumulative R alone.
- Treat all inspected original/backward-only history as development data; reserve a new forward window before inspection.
- Use calendar-block paired uncertainty across assets; purge at least the maximum holding period at validation boundaries.
- Advancement requires a lower 90% paired net improvement bound above zero and no breach of predeclared drawdown/gross-exposure limits.
- Require the chosen rule to survive predefined cost/latency bounds; inconclusive results retain the original policy.
- Changes prompted by failed forward evidence form a new preregistered study rather than another pass on the same holdout.

## Planned notebook outputs

- New `validation_review.ipynb`: lookahead fixtures, event-state ledger and original-to-causal result bridge.
- Paired gross/cost/funding attribution and equal-capital BTC/ETH portfolio NAV with concurrent exposure.
- Full selection history, block confidence intervals and original/forward decision tables with clear data cutoffs.

## Not applicable / not verified

Options quote execution is not applicable. Macro release-time accuracy, real fills and corrected causal profitability were not verified.
Saved notebook outputs were read; they were not regenerated and no production setting was changed.
All proposed work remains under `studies/notebooks/overlay_study`.
