# PDO adjacents: validation review and bounded remediation plan

Review date: 2026-09-14. Status: **P0 — CDO short-return arithmetic and broad interpretation need correction before numeric reuse; preserve archived KEEP −10 / KILL dispositions.**

This retrospective addendum respects the original frozen A/B questions and later archive notice.
No study was executed, database accessed, data downloaded, or existing/production artifact changed.

## Reviewed evidence

- [pdo_adjacents.ipynb](pdo_adjacents.ipynb), all zero-based cells **0–25**, source/markdown and available saved textual content.
- [README.md](README.md) and [findings.md](findings.md), full text including the archive addendum and implementation caveats.
- [common.py](common.py), full source; key feature/state/execution/statistics code in [question_a_regime.py](question_a_regime.py) and [question_b_cdo.py](question_b_cdo.py).
- [parity_check.py](parity_check.py), especially lines 79–167; [results/parity.json](results/parity.json).
- [results/qa_clauses.json](results/qa_clauses.json), [results/qb_clauses.json](results/qb_clauses.json), and notebook-displayed summary/fold/band tables.
- Saved large trade ledgers were reviewed through source, schemas, and reported aggregates, not every raw minute path.

## What is well supported

The two questions are separated: a narrow existing-PDO regime-threshold change and a new current-day-open continuation rule.
Question A reruns state for each threshold rather than simply deleting trades; this correctly allows replacement opportunities.
The feature parity artifact reports 4,224 checked timestamps and zero discrepancies against selected sleeve feature functions.
The findings explicitly acknowledge only one BTC and six ETH historical “OOS” trades, with zero discriminating band trades.
Thus KEEP −10 is a default/no-evidence-to-change decision, not proof that −10 is superior.
Question B retains the frozen first-bar touch convention, reports negative slices, and declines to rescue it with a post-hoc filter.
The archive remains useful without commissioning another strategy search.

## Material findings

1. **CDO short return is inconsistent with the stated linear payoff.** `question_b_cdo.py:143–147` uses `entry/exit−1` for shorts.
   For a linear position sized on entry notional, the short return is `1−exit/entry`.
   With the documented 1% stop/2% target, inverse-ratio arithmetic produces roughly −0.9901%/+2.0408% rather than −1%/+2% before fees.
   This affects mean R and the stated exact −1.18R/+1.82R payoff; specify the actual contract and denominator before reuse.
2. **The hit-rate break-even explanation assumes no time exits.** `question_b_cdo.py:337–339` and notebook cell 22 use `(1+cost_R)/(2+1)=39.3%`.
   About 15.9% of reported trades expire at TIF, so the all-trade target rate cannot be compared with a binary stop/target break-even threshold.
   Include the actual time-exit mean and probabilities in the payoff identity.
3. **Zero-cost does not isolate the signal from its wrapper.** `qb_clauses.json` reports about +0.016R at zero cost.
   That outcome still depends on the 1R stop, 2R target, TIF, side rule, and first-bar convention.
   Calling it independent of stop/target choice or proof of coin-flip behavior is unsupported.
4. **No same-minute ties does not validate execution.** Minute paths choose stop-first ties and fill at levels (`question_b_cdo.py:117–140`).
   This does not cover gaps, missing intervals, spread, or close-signal entry latency.
   Full-hour excursion and touch in the same bar do not prove an excursion followed by a retouch; this is a known frozen proxy, not a reconstructed event sequence.
5. **Funding is not necessarily common or adverse.** Question A changes entry times, counts, and holding state; its funding cash flows need not cancel in policy differences.
   Both questions include directional exposure, so signed funding can help or hurt.
   The findings' claims that omission cannot change the relative result or only worsens CDO are not general bounds.
6. **Uncertainty is not joint or selection-complete.** Question B iid-resamples pooled BTC/ETH trades despite shared calendar shocks.
   Its “walk-forward” at lines 261–283 evaluates a fixed rule in chronological slices; it does not fit a model or establish that those dates were unseen.
   Question A lines 299–305 say a paired difference is undefined; a paired calendar policy-return contrast is well-defined even when trade sets differ.
7. **Feature parity has a limited scope.** `parity_check.py:112–143` compares PDO/CDO, hourly-bar, and regime values.
   It does not verify order dispatch, fill prices, at-most-one-per-day lifecycle, funding, or actual historical strategy execution.

## Required correction before numeric reuse

Create `arithmetic_and_claim_reconciliation.ipynb` in this directory and preserve original artifacts.
Specify the P&L unit: linear quote-currency perp/spot notional, inverse contract, and R denominator must not be mixed.
Add deterministic long/short stop/target/TIF examples that reconcile price P&L, fees, R, and ledger totals.
Using the same saved entries/exits, reconcile corrected short arithmetic and payoff decomposition with the original results.
This bounded arithmetic correction does not require a threshold/window sweep or a new data fetch.
If an entry/exit semantic replay is later required, retain the frozen rule and label it implementation correction, not independent confirmation.
Report the original decision and corrected decision separately; do not infer a KILL reversal from a code defect alone.

For any executable claim, freeze signal-available time, first executable entry, interval coverage, gap fills, and terminal treatment.
Use matching venue prices, fees, and settlement funding; include a spot-proxy limitation if perp paths are unavailable.
Fixtures should cover an excursion/touch in one hour, first UTC hour, day reset, simultaneous prior exit/new fire, missing TIF quote, and stop/target ambiguity.
Document all excluded events and distinguish “missing data” from “no setup.”

## Conditional future research

Do not revive archived PDO/CDO solely because the archive lacks a perfect modern validation stack.
If Question A is specifically revisited, retain only −10 and −7 and prospectively log eligible band episodes and replacement trades.
Use the joint calendar policy-return difference, exposure, and trade-count difference; affected opportunities are more informative than total accumulated trades.
Preserve the historical +5 bp per-trade requirement for comparison, but define a capital/opportunity-normalized estimand before new testing.
Plan enough discriminating episodes to estimate an economically useful difference; zero band trades means no information about that choice.
If Question B is revisited, changing same-bar retouch semantics, stops, targets, or direction defines a new hypothesis and new trial family.
Use unconditional/matched intraday drift and same-time excursion controls to isolate what the CDO condition contributes.
Do not claim mechanism from net returns after an optimized wrapper alone.

Jointly resample calendar blocks across BTC/ETH, preserving each candidate policy and zero-trade dates.
Use 7/14/30-day block sensitivity and report the effect of major correlated episodes.
Count the original two questions, regime variants, directional subsets, and post-hoc first-bar exclusion in the research lineage.
DSR's local trial count and iid default remain supporting diagnostics, not a substitute for correlated-sample inference.
Future observations begin after a rule/metric freeze; the already inspected historical “OOS” period is a labeled historical comparison.
Use reject / inconclusive / sufficient-to-seek-confirmation decisions based on interval location relative to a predeclared useful net effect.

## Deliverables and limits

- Arithmetic/payoff identity table, corrected-versus-original ledger summaries, and qualified historical dispositions.
- If revived: causal event examples, coverage/exclusion manifest, joint policy controls, and prospective discriminating-episode report.

Order-book queue simulation and equity corporate actions are **N/A** to the original crypto market-entry rules.
Intrabar sequencing, funding, UTC day reset, and correlated BTC/ETH risk are applicable.
Current live/archive state, raw database coverage, original execution provenance, and the size of corrected P&L changes were **not verified**.
