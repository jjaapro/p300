# Anchor allocator: validation review and conditional remediation plan

Review date: 2026-09-14. Status: **P0 for causal portfolio/risk reuse; the historical KILL remains a descriptive result of the documented synthetic panel.**

This retrospective audit/addendum preserves the original interpretations, preregistration, and post-hoc corrections.
It does not relabel later controls as frozen, rerun the allocator, access data, or change production/existing artifacts.

## Reviewed evidence

- [anchor_allocator.ipynb](anchor_allocator.ipynb), all zero-based cells **0–12**, including saved text/tables and panel notes.
- [README.md](README.md), [findings.md](findings.md), and [results/panel_notes.md](results/panel_notes.md), full text.
- Key allocation and metric code in [simulator.py](simulator.py), especially lines 116–290.
- Key ADX, chento, carry, activity, and documentation code in [build_panel.py](build_panel.py), including lines 154–306 and 425–570.
- Key paired bootstrap/decision code in [run.py](run.py), plus [results/verdict.json](results/verdict.json) and notebook-displayed summary/contribution/year tables.
- The individual raw strategy ledgers and original predecessor implementations were not exhaustively audited here.

## Useful evidence and strengths

The study freezes a finite allocator comparison and reports negative results instead of porting an attractive-looking variation.
It distinguishes no-overflow, gold, cash-only, no-gold, split, and lagged-claim controls.
The panel notes candidly document synthetic sleeve marking, omitted costs/funding, and activation conventions.
A paired calendar-block bootstrap preserves the common daily shocks across compared variants.
The findings' later corrections separate dynamic gold weighting, cash yield, and overflow effects and keep them distinct from the original decision.
The saved KILL describes the exact synthetic panel and interpretations used; it does not establish the live fleet's future behavior.

## Material limitations

1. **Allocation depends on same-day realized P&L.** `simulator.py:149` calls a tactical sleeve active when `P[s][i] != 0.0`.
   Its same-day return is not known when weights must be chosen; held positions can also have exactly zero P&L.
   The one-day idle-claim diagnostic changes overflow timing but retains P&L-based tactical activity and is not a fully causal remedy.
2. **Synthetic returns leak future outcomes into the path.** `build_panel.py:229–243` spreads chento's final trade return uniformly over a fixed 72-hour window.
   That is not an executable mark-to-market or actual occupancy path; it can distort overlap, caps, and measured drawdown.
   Small chento standalone contribution does not bound its nonlinear effect on allocation and risk constraints.
3. **ADX markings differ from an actual fixed-quantity account.** `build_panel.py:154–185` converts historical harness trades to daily signed close returns.
   Daily-reset short compounding differs from fixed-entry-notional short P&L, and this source inherits the old harness's trail-ordering issue.
   Funding is omitted; the panel notes acknowledge differences. These matter before any capital-risk reuse.
4. **“Sharpe” is a legacy nonstandard statistic.** `simulator.py:273–282` computes CAGR divided by annualized daily volatility.
   The original contract explicitly used this definition, so preserve its historical clauses; it is not standard arithmetic excess-return Sharpe.
   Cash yield and compounding can change this measure mechanically, limiting cross-study comparisons.
5. **Rebalancing costs are incomplete.** Lines 248–254 charge changes in target weight, not the trade needed after price/NAV drift or a long-to-short direction flip.
   Daily return-series mixing also assumes capital can be reset at those weights; a quantity/cash ledger is needed to validate actual turnover.
6. **Controls are not all independent evidence.** The nominal tactical caps sum to 58% but the budget is 47%; actual activity drives proportional scaling.
   The gross cap never binds in the reported run, so changing an inactive cap creates duplicate outcomes, not evidence of robustness to a binding constraint.
   The baseline-gold control changes weight compression as well as exposure; the findings' later attribution correction must accompany any quoted uplift.
7. **Cash/funding and chronology qualify conclusions.** A fixed 4% cash series adds a mechanically favorable return assumption rather than measuring a historical deployable cash alternative.
   Later funding/weight-controls are post-hoc and add to the trial lineage.
   The full 2020–2026 panel was inspected; there is no untouched confirmation period for selecting an improved allocator.

## Disposition now

Retain the original KILL and the decision not to port this allocator from the stylized study.
Quote its result as conditional on the synthetic panel, activity definition, legacy metric, and cash/financing assumptions.
Do not infer that all overflow policies fail, that gold itself supplies the entire uplift, or that the reported drawdown is a live account-risk bound.
No further allocator search is needed to preserve the archive.
If any allocator result is reused in a capital/deployment proposal, the causal activity/MTM issues are a prerequisite to resolve.

## Bounded causal reconstruction if reuse is requested

Create `causal_panel_validation.ipynb` here with an immutable source, rule, date, and data manifest.
Keep the original five core variants and existing finite sensitivities fixed; no new split/cap optimization during reconstruction.
For each sleeve, provide timestamped orders/positions, quantity, reserved margin/notional, executable marks, costs, and funding.
Define activation and reserve release from known lifecycle events, independently of whether the day's P&L is zero.
If allocation is daily, specify whether pending scheduled entries reserve capacity at the decision time; if intraday, model those event times explicitly.
Replace smeared terminal P&L with actual price-path MTM and actual stop/TIF exits.
Reconcile each sleeve's cumulative P&L against its own ledger before combining sleeves.
Treat missing data/unknown position state as an explicit failure or conservative reserve, not idle capital.

Build a self-financing cash/quantity ledger with capital transfers, daily/intraday marks, and true rebalancing trades after drift.
Include entry/exit direction changes, funding settlements, collateral, gross/net exposure, and initial NAV in drawdown.
Specify whether “neutral carry” consumes one or two legs of gross notional and how margin is counted; capital allocation and exposure are different units.
Fixture scenarios should include zero-P&L held positions, same-day entry/exit, opposite BTC sleeves, cap binding, direction flip, and a late/missing sleeve mark.
Use comparable timestamps for gold and crypto; state market closures, stale gold marks, and when rebalancing is actually available.

## Controls, uncertainty, and decision gates

Separate overflow allocation from the gold exposure and anchor-compression effects with matched-weight controls decided before new outcomes.
Compare idle cash with an explicit deployable historical/forward cash-rate assumption, including relevant costs, rather than a universal fixed yield.
Retain the legacy CAGR/vol clause values for historical reproducibility and report standard arithmetic excess-return Sharpe separately.
Any new acceptance thresholds must name their metric and be frozen before the corrected results are inspected.
Use identical calendar blocks across all sleeve and allocator series; retain paired 20-day bootstrap as the legacy diagnostic and show 10/40-day sensitivity.
Expose tail-cluster contributions and binding-cap frequencies; a few stress periods limit effective diversification evidence.
Bootstrap sign frequency is a resampling summary, not the probability that a live allocator adds value.
Include all five core, four sensitivity, two lagged, and later attribution/control variants in the trial record; duplicate-return variants should be identified.

First gate: every sleeve and portfolio ledger reconciles, weights use only available information, and synthetic fixtures obey capital/exposure constraints.
Second gate: an economically material paired net improvement with acceptable marked risk against an equal-capital baseline under conservative execution assumptions.
Intervals spanning the useful-effect threshold yield inconclusive; a negative point estimate is not proof against every allocator.
Third gate, if adoption is pursued: freeze a candidate and monitor a genuinely future period; old historical dates cannot be recycled as untouched OOS.

## Planned outputs and scope limits

- Sleeve-by-sleeve reconciliation/coverage table, causal activation timeline, capital ledger, and corrected-versus-original NAV attribution.
- Matched allocation controls, binding-constraint/turnover table, paired uncertainty, and an explicit historical versus prospective decision record.

ORB session mechanics and stop/target rules at the allocator level are **N/A**; underlying sleeve execution still requires validation.
Equity corporate-action checks are **N/A unless an equity instrument is used for the gold implementation**; actual instrument/market-hour checks remain necessary.
Raw data quality, all underlying sleeve strategies, current fleet wiring, actual rebalance fills, and corrected allocator performance were **not verified**.
