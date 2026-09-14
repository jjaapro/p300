# Variance risk premium: validation review and forward addendum

Review date: 2026-09-14. Static review of code, notebook and saved findings only.
No options backtest, database query, quote retrieval or data download was run.
This retrospective review preserves the original preregistration, parity results and later rejection.

## Verdict and priority

**P0 applies to reuse of the positive mark-based result or its options engine as validated evidence.**
Retain the existing decision not to advance the sleeve: the intrinsic sensitivity already undermines the positive result.
There is no requirement to run a new options study simply because this archived implementation has defects.

## Artifacts reviewed

- `vrp_study.ipynb`, all zero-based cells 0–17; viewer cells have no saved code outputs.
- `README.md`, `findings.md`, `run_vrp.py`, and referenced mark/intrinsic/parity result tables.
- Shared `studies/lib/options/chain.py` and `pnl_engine.py`, including selection, daily marks, hedge and expiry logic.

## Evidence that is already useful

- Notebook cells 3, 7 and 11 expose mark-versus-intrinsic behavior, old-engine parity and missing expiry/strike drift.
- The parity exercise reproduces the earlier 66-expiry calculation; it is implementation parity, not independent market validation.
- Findings explicitly disclose future-mark survival selection and approximately seven omitted crash weeks.
- The 73-expiry intrinsic sensitivity is much weaker, with negative TEST mean and DSR around 0.189.
- Refusing advancement despite formal mark-model gate passes is appropriate and should be preserved.
- The full conditional distinction is more informative than quoting the old Sharpe 1.91 or DSR 0.902 alone.

## Material defects and unresolved assumptions

1. `chain.py:98` selects the last quote/mark in the entry UTC day, while the entry label is midnight.
   Daily spot similarly uses the last hourly close; `pnl_engine.py` evaluates time-to-expiry on daily timestamps.
   Prices, availability and hedge clocks therefore require reconciliation before a causal interpretation.
2. Strike selection requires later terminal marks; missing future observations change which strikes/expiries enter the sample.
   This is outcome-dependent survival selection, not a tradable entry-time liquidity filter.
3. Terminal marks in `chain.py:113` can come from a window extending two days past expiry.
   Intrinsic-mode settlement uses an hourly proxy and can fall back to a nearby future date (`chain.py:195`; `pnl_engine.py:67`).
   Neither arbitrary nearby marks nor residual time value establish the contract's exact official settlement.
4. Marks with a 3% haircut are not executable option bid prices; no bid/ask, quote age, size or stale/crossed quote gate is established.
   Exit ask, expiry fees, inverse-contract cash flows, BTC collateral conversion and hedge funding are incompletely represented.
5. `run_vrp.py:171` annualizes expiry observations with sqrt(12), although the expiry list includes more than twelve dates per year.
   Overlapping approximately 21-day positions are not independent trades or a fully allocated portfolio.
6. Per-expiry additive P&L omits intrahold option marking and simultaneous option/hedge collateral constraints.
   Twenty percent of NAV per strangle and the observed worst return do not bound short-option loss or liquidation risk.
7. The mark and intrinsic variants, strike survival choices and post-result sensitivities all belong in selection history.
   The small OOS count in notebook cell 17 and DSR alone cannot validate a tail-selling strategy.

## Prioritized conditional plan

### P0.1 — quarantine positive evidence before engine reuse

- Hypothesis: entry-time-selected contracts with exact cash settlement can be accounted for without future-data dependence.
- Freeze original source/result hashes and preserve both original and corrected tables in a new notebook.
- Specify quote observation, availability, order submission, fill, hedge, expiry and cash-settlement timestamps explicitly.
- Select instruments from the entry-time listing universe; keep missing later observations as unresolved exposures, not dropped trades.
- Build exact fixtures for expiry payoff, inverse-versus-linear denomination, collateral conversion, hedge P&L and fees.
- Add future-data perturbation fixtures: later missing marks or prices must not change past instrument selection or orders.
- Require fixed-contract option cash flows plus fixed-quantity hedge flows to reconcile to daily equity and final cash.
- Acceptance: no future fallback or unknown-price fill is used in decision-bearing results, and every removed expiry has a documented ex-ante exclusion.

### P1.2 — only if an options research question remains worth funding

- Hypothesis: a frozen short-strangle rule earns positive net excess return versus a risk-matched alternative across volatility regimes.
- Required data: historical listings, timestamped bid/ask and size, official settlement/index history, funding, contract specifications and margin rules.
- Use entry bid and exit ask with conservative latency/slippage; parameterize capacity from displayed executable size.
- Compare expiry hold, defined-risk spread and delta-hedged exposure on the same allocated capital and calendar.
- Report cash, BTC collateral beta and a matched hedge-only control; do not attribute all earned premium to variance risk premium.
- Freeze delta/maturity/rebalance choices and a total search budget before inspection; log old 66/73-expiry variants and all subsequent changes.
- Walk forward with a purge at least equal to maximum option holding duration; existing 2024–2026 data is already researched.
- Cluster inference by calendar blocks long enough to retain overlapping maturities and crash episodes; include leave-one-crash-period-out diagnostics.
- Evaluate spread/fee/funding stress, overnight gaps, margin utilization, drawdown duration and forced unwind before performance gates.
- Original Sharpe/DSR thresholds may be printed for comparability, but cannot substitute for executable net accounting.
- Forward advancement requires lower 90% paired net excess-return bound above zero, survival of predefined cost stress and no breach of a frozen collateral budget.
- Insufficient quote/settlement coverage yields INCONCLUSIVE; a handful of additional expiries alone cannot establish tail-risk robustness.

## Planned notebook outputs

- New `validation_review.ipynb`: listing/selection audit, quote availability heatmap, official settlement bridge and contract cash-flow fixtures.
- Same-cohort mark/intrinsic/executable-bid-ask comparison with an explicit missing-data waterfall.
- Daily option-plus-hedge NAV, collateral and loss tails; overlap-aware uncertainty and original/forward decision table.

## Not applicable / not verified

Directional OHLC stop/target replay is not the central model here; option quote and collateral validation are mandatory for reuse.
Current exchange specifications, executable quote coverage, margin/liquidation and true independent reproduction were not verified.
All future studies stay under this directory; this document makes no production or allocation change.
