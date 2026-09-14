# Paladin harvester: validation review and conditional revisit

Review date: 2026-09-14. Static script/result review only; no backtest, database query or download.
This retrospective addendum preserves the frozen twelve-cell experiment and its rejection.

## Verdict and priority

**P2 — retain the archived KILL/no-build decision.**
All reported OOS variants are negative, and the strongest in-sample result misses the stated economic hurdle.
Do not retune the round tolerance, exits or stop geometry merely to rescue this historical idea.

## Artifacts reviewed

- `findings.md`, `harvester.py`, and their saved grid/trade result references.
- No notebook is present in this directory; future review/testing should use a new notebook here.
- The related Paladin wick rule and its historical derivation were reviewed separately.

## What is already usefully tested

- The grid is explicit: two ATR stop multipliers × trend gate on/off × both/long/short = twelve cells.
- `harvester.py:56` lags hourly ATR before using it for next-bar entries.
- The detector uses completed 15-minute bounce/rejection information and enters at the subsequent open.
- Replay uses stop-first ordering and deducts the specified 18 bp round-trip charge.
- Findings retain the full adverse result: best IS approximately +0.033R, with all OOS means below zero.
- Failure of mean/MAR gates makes a conditional archived status proportionate; a new optimization is unnecessary.

## Specific limitations

1. The 24-hour per-side cooldown in `harvester.py:100` is shorter than the maximum 72-hour hold.
   Trades can overlap; summed event R does not establish an affordable or unlevered account path.
2. `harvester.py:127` computes entry-ordered cumulative R drawdown and a total-return ratio.
   Without calendar annualization, initial capital and concurrent marks, this is not conventional portfolio MAR.
3. Exact stop/target prices through gaps and bar-close wick exits omit spread/latency/queue details.
   The 18 bp charge does not establish executable fills or capacity at every volatility state.
4. Funding is not booked despite potentially multi-day perp exposure; position financing and collateral are unspecified.
   A gross/net event comparison cannot establish margin survival when opposite-direction positions coexist.
5. The final dataset edge can truncate nominal 72-hour holds; full completion versus administrative censoring needs explicit labels.
   Delisting/data gaps and incomplete feature windows should be separated from ordinary timeouts.
6. The rule comes from previously examined Paladin behavior/exit studies, and broader project research had already inspected 2025+ prices.
   This is a disclosed historical split, not proof that the later segment was untouched across all related research.
7. The failed finite grid does not prove that every round-number strategy is impossible.
   Conversely, the separate Paladin 1-minute sensitivity cannot rescue this strategy without its own correct net accounting.

## Conditional forward plan

### P2.1 — if the rejection is cited or implementation reused

- Retain all twelve cells, original thresholds, native units and the KILL decision in a new review notebook.
- Record source/input/result hashes and inherited Paladin hypothesis history.
- Check event count, timestamp availability, incomplete-end cases and cost units without expanding the strategy family.
- Describe the old MAR as its exact implemented ratio and avoid treating it as account-level risk.
- Acceptance: every claim states the finite tested rule, exposure assumptions and already-inspected sample chronology.

### P1 only if a materially new hypothesis justifies revival

- Hypothesis: a specifically predeclared round-level reversal mechanism adds net return beyond a matched generic reversal control.
- State what new evidence distinguishes this question from the rejected twelve-cell search before observing new outcomes.
- Fix grid scale, tolerance, side, ATR window, stop, exit, cooldown, concurrency and maximum notional ex ante.
- Build causal fixtures for prior-hour ATR, next-open entry, gap stops, close-trigger latency and endpoint censoring.
- Require observed-at timestamps and prefix invariance: future rows must not change earlier detector states or order prices.
- Data: one declared venue/instrument with complete timestamped bars and, where needed, quotes/trades for fill order.
- Account for per-fill fees, spread, slippage, signed realized funding, mark-to-market and any hedged gross margin.
- Controls: cash, direction/regime-matched generic reversal and exposure-matched passive BTC; identical dates and capital.
- Preserve twelve prior cells plus inherited exit-study choices in the trial ledger; freeze one new primary and limited controls.
- Use a new chronological forward segment, with at least maximum holding-period purge at fold boundaries.
- Report daily account NAV and effective exposure rather than event R alone; include concentrated trend and gap periods.
- Use common calendar-block intervals preserving overlapping 72-hour positions and cooldown dependence.
- Keep the original mean-R and MAR gates as historical comparisons using clearly consistent definitions.
- A forward pass additionally requires lower 90% paired net excess-return bound above zero and no frozen capital/tail-risk-budget breach.
- If quote ambiguity or sample size prevents that decision, mark INCONCLUSIVE and retain no-build without automatic tuning.

## Planned notebook outputs

- New `validation_review.ipynb`: original twelve-cell evidence, chronology and completed/censored-event counts.
- Only if revived: causal fixtures, cash-flow/collateral ledger, all trial/control outcomes and calendar NAV.
- Paired dependent uncertainty, cost/latency sensitivity and literal-original-versus-forward decision table.

## Not applicable / not verified

Options bid/ask, quarterly delivery and external trader execution reconstruction are not part of this standalone rule.
Actual fill capacity, funding cost, full source coverage and corrected portfolio performance were not verified.
The no-build conclusion requires no automatic retest; no existing study or production file was changed.
