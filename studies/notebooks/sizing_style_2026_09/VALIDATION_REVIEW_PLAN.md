# Sizing style: validation review and forward addendum

Review date: 2026-09-14. Static review only; no exit replay, liquidation simulation, database query or download.
This addendum preserves the frozen README, existing policy decisions, post-hoc S3b label and historical results.

## Verdict and priority

**P0 for reuse of the reported MTM, net-R, overlapping-equity sizing and safety-gate evidence.**
Concrete accounting and chronology defects prevent treating those tables as validated support for no-stop sizing.
This finding does not establish that every exit-policy result reverses, and makes no production change.

## Artifacts reviewed

- `sizing_style.ipynb`, all zero-based cells 0–10, source and meaningful saved text outputs.
- `README.md`, `findings.md`, `sizing_lib.py`, `run_s1_exit_policies.py`, `run_s2_sizing_rules.py`.
- `run_s3_liquidation.py`, `run_s3b_single_open.py`, result tables/ledgers referenced by notebook outputs.
- The execution helper's `trade_r` implementation was checked to distinguish gross R from net dollar P&L.

## Sound research structure

- S1 starts from identical event IDs and separates exit-policy questions from S2 sizing and S3 collateral analysis.
- The notebook retains all tested policy outcomes, historical adverse excursions and per-bot versus pooled views.
- The later single-open ShortSqueeze sensitivity is explicitly POST-HOC in cells 7–8.
- Findings disclose research/live sizing differences and venue-basis limitations.
- The original forward paper-observation conditions remain useful constraints and must not be retroactively relaxed.

## Material implementation and interpretation gaps

1. `sizing_lib.py:109` uses original notional × `(mark / prev - 1)` each day.
   Fixed quantity requires `quantity × (mark - prev)`; the present series implicitly resets quantity and need not sum to trade P&L.
   A zero-cost 100→110→100 path should net zero, whereas the current formula produces a positive residual.
2. `sizing_lib.py:89` saves `ex.trade_r`, which is gross price R; dollar cost is deducted separately.
   `policy_summary` averages that R, while saved output/findings call it net. Gross and net absolute expectancy must be separated.
3. `run_s2_sizing_rules.py:18` sorts entries then realizes each full future outcome before sizing the next trade.
   For overlapping trades, fixed-R-on-equity uses information unavailable at the next entry; fixed notional also uses full-sample median geometry.
4. README's safety test compares liquidation distance with twice the worst observed 24-hour move.
   `run_s3_liquidation.py:51` instead compares twice worst in-hold MAE. This is a changed gate, not a literal preregistered pass.
5. `sizing_lib.py:167` aligns ETH onto BTC minutes by positional offsets, assuming identical uninterrupted timestamp grids.
   Missing/extra asset bars can corrupt pooled mark and margin alignment; exact timestamp joins are required.
6. The liquidation walk uses approximate fixed maintenance assumptions and original notional rather than complete venue/tier mark-dependent rules.
   Liquidation fees, funding/collateral flows, intraminute exit order and termination after a breach are not fully modeled.
7. Simultaneous adverse OHLC extrema across assets need not occur together; conversely gaps and intrabar liquidation can be missed.
   Zero historical modeled breaches is not a probability estimate or a guarantee of safety outside sampled paths.
8. `sizing_lib.py:120` uses additive fixed-capital P&L with NAV drawdown; initial-capital peak handling needs explicit verification.
   Comparisons with separately compounded ADX output and unequal average notional do not cleanly isolate sizing style.
9. Research 3× caps, overlapping entries and alternative no-stop policies differ from the deployed paper configurations described in findings.
   Costs inherited from older holding durations omit policy-specific funding; trade-day resampling does not cover all serial/overlap dependence.
10. Multiple S1 cells, ADX variants, three S2 rules per sleeve and post-hoc S3b were inspected.
    Median-event halves are diagnostic splits, not an untouched holdout for the selected policy.

## Prioritized future plan

### P0.1 — establish cash-flow and causal invariants first

- Hypothesis: fixed-unit trade P&L equals the sum of marked daily cash flows and all policy state uses information known at entry.
- Freeze source, upstream event ledger, dependency versions, original configuration and data cutoff before any rerun.
- Build deterministic 100→110→100, partial-day exit, fee-only, funding-boundary, long/short and overlapping-trade fixtures.
- Require dollar reconciliation to stated numerical tolerance and `net_R = net_PnL / initial_risk_dollars` for every row.
- Process fills, marks, exits and fees in time order; size from actually available cash/equity and open liabilities.
- Derive any fixed-notional reference from a declared pre-period or fixed amount; exclude future median risk geometry.
- Join BTC/ETH by exact UTC minute timestamps and report missing-mark age; stop on unknown required marks.
- Acceptance: all conservation and prefix-invariance fixtures pass before recomputing any performance or safety table.

### P0.2 — restore the literal safety comparison

- Reproduce the README's 24-hour-move gate unchanged; retain in-hold MAE as an explicitly separate sensitivity.
- State exactly which research and paper configurations each comparison represents, including capital, leverage, concurrency and stop rules.
- Use mark-dependent maintenance/tier parameters, collateral currency, liquidation fees and funding; terminate or model recovery after liquidation explicitly.
- Add gap, missing-bar, simultaneous-exposure and delayed-exit stresses; publish bounds where intrabar order is not observable.
- No corrected safety pass may be claimed until original gate inputs reconcile and every adverse scenario stays within its declared budget.
- Do not automatically change deployed parameters from a historical modeling correction.

### P1.3 — only after accounting passes, compare the intended policies

- Hypothesis: the selected exit/sizing change improves net performance under the same capital and margin budget as the current reference.
- Controls: shipped stop policy, fixed-capital risk, fixed notional chosen ex ante and exposure-matched simple resizing.
- Separate same-entry policy differences from a causal single-open portfolio where exits change future eligibility.
- Charge venue-consistent fills, gaps, spread, fees, slippage and holding-dependent realized funding for each policy.
- Report both additive dollar accounting and compounded returns from actual NAV; use the same definition across sleeves and ADX.
- Log all historical S1/S2/S3/ADX/post-hoc choices; freeze one primary comparison per sleeve before examining new data.
- Use chronological validation with purging at least maximum hold duration and a truly unobserved forward segment.
- Bootstrap common calendar blocks across sleeves, preserving overlapping exposures; show stress concentration and block-length sensitivity.
- Require lower 90% paired net improvement bound above zero, compliance with the literal safety gate, and no predeclared risk-budget breach.
- Preserve the existing paper fire-count and observation conditions; repeated looks require an explicitly dated new protocol.
- If accounting corrections remove the advantage or uncertainty remains, retain the reference policy for decision purposes.

## Planned notebook outputs

- New `validation_review.ipynb`: cash-flow fixtures, gross/net R bridge, original-to-corrected MTM differences and source manifest.
- Causal S2 event ledger; equal-exposure comparisons; BTC/ETH timestamp coverage and collateral reconciliation.
- Literal 24-hour gate versus in-hold sensitivity; scenario/quote-order bounds and independent-calendar uncertainty.
- Final table separating historical implementation, corrected research configuration and prospective paper evidence.

## Not applicable / not verified

Options bid/ask is not applicable. Actual exchange liquidation rules, executable fills and corrected performance were not verified.
No existing notebook/source or production setting was changed; this plan does not claim a new safety or profitability result.
