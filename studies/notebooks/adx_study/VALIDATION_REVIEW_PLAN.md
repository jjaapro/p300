# ADX development studies: validation review and remediation plan

Review date: 2026-09-14. Status: **P0 — exploratory evidence; annual Sharpe, historical holdout, pivot timing, and execution claims require qualification before reuse.**

This retrospective addendum preserves the existing findings and experiments. It is not a preregistration of work already seen.
No source was imported or executed, no database was opened, and no strategy or production artifact was edited.

## Reviewed evidence

- **No `.ipynb` notebooks are present in this directory.** Notebook-cell review is therefore N/A.
- [findings.md](findings.md), all text including its later metric and funding corrections.
- [harness.py](harness.py) and [experiments.py](experiments.py), full source.
- [pivot_accuracy.py](pivot_accuracy.py), [pivot_detector.py](pivot_detector.py), [cvd_divergence_test.py](cvd_divergence_test.py), and [funding_overlay.py](funding_overlay.py), full source.
- The findings' reported case-study/long-context conclusions and links were read; `analyze.py`, `case_study.py`, `long_context.py`, and Pine implementations were not independently replayed or exhaustively validated.
- Downstream [ADX robustness](../adx_robustness_2026_09/VALIDATION_REVIEW_PLAN.md) and [ETH transfer](../adx_eth_2026_09/VALIDATION_REVIEW_PLAN.md) are dependent evidence, not independent confirmations.

## What remains useful

The directory documents how EMA short filtering, alternative exits, funding crowding, and pivot confirms were proposed.
The findings retain explicit corrections to previously reported Sharpe and trade-close drawdown.
Showing both price and funding contributions to countertrend shorts is more informative than judging shorts by price alone.
Pivot markers are eventually acknowledged as confirmed late and backplotted; their visual descriptive role can be retained.
The experiments are useful hypothesis-generation evidence, provided their attractive configurations are not relabeled as independently validated.

## Material issues with evidence and claims

1. **Historical Sharpe is a trade t-like statistic.** `harness.py:320` multiplies mean/std by square root of trade count.
   It is not an annual calendar-time Sharpe. The findings correct this later, but copied older headline values remain unsuitable for comparison.
   The original maximum drawdown uses closed-trade equity and misses open-position losses.
2. **Daily ATR exits have temporal leakage.** `harness.py:214–226` raises/lowers a trail using the current day's close and ATR, then tests that day's low/high against it.
   That updated stop was not known throughout the day. Filling at the level also omits gap execution.
   Same-close entries and signal exits require an explicit completed-bar availability and executable-price convention.
3. **IS/OOS labels overstate isolation.** `experiments.py:23–44` runs full paths, then slices trades by entry date.
   A trade entering before the split can contain exit information after it; repeated inspection of alternative rules consumes the purported holdout.
   The named split is a historical slice, not untouched confirmation of the selected Tier 2 rule.
4. **Funding-veto selection is outcome-informed.** `findings.md:205–219` studies thresholds around known recent losing trades before discussing a shippable veto.
   The short filter, exit variants, funding thresholds, and case-driven confirms belong in the trial history.
   A local fixed-rule or N=1 claim does not erase that lineage.
5. **Pivot accuracy is not executable accuracy.** `pivot_accuracy.py:35–55` requires eight future bars to identify a pivot but measures return from the pivot close.
   A 30-bar horizon longer than the confirmation delay does not remove the first eight unavailable bars from the reported return.
   `cvd_divergence_test.py:48–63` uses the same construction; `pivot_detector.py:58–62` checks alignment from backplotted pivot dates.
6. **Funding timestamp and coverage assumptions need evidence.** `funding_overlay.py:33–47` maps daily dates to candle timestamps and uses endpoint proximity as a coverage proxy.
   A held intraday exit needs funding at actual settlement instants; source rows must be confirmed as settlements, not repeated rate observations.
   Summed fixed-notional funding and marked position-value funding are distinct accounting choices.
7. **CVD coverage is asserted, not established by the code.** `cvd_divergence_test.py:27` filters `volume_buy>0`; lines 35–38 fill absent post-start days with zero flow.
   Missing source data must not be silently treated as genuine zero CVD; descriptive “100% since 2019” needs a coverage manifest.

## Bounded remediation if the ADX evidence is reused

Start with a new notebook, `validation_reconciliation.ipynb`, under this directory.
Freeze baseline, the selected Tier 2 configuration, and any actually proposed funding-veto comparison; do not repeat the full parameter search.
Record the historical trial lineage and distinguish historical selection from prospective testing.
Archive hashes of source/data manifests, the selected rule, price venue, funding venue, candle boundaries, and complete cutoff.
Reconstruct one causal event ledger with initial stop, prior-known trail, signal close availability, next executable entry/exit, and signed funding.
Use matching perp execution if making perp risk claims; a spot-price proxy must retain that limitation.
Carry open trades across chronological boundaries with their original state and include terminal marked exposure.
Do not drop a trade's post-split risk from a training score without explicitly defining the training estimand and embargo.

Use deterministic fixtures for same-day ATR ratchets, price gaps, same-boundary exits/reentries, a missing funding print, and an open final trade.
Reconcile the corrected minute engine with the historical harness on events, not just aggregate returns.
The shared robustness engine itself needs the entry-day fix identified in its review before it can be a reference implementation.
Its `adx_lib.py:168` also inherits `sizing_style_2026_09/sizing_lib.py:109`, which applies unchanged original notional to successive mark-to-mark percentage returns.
Resolve that [fixed-quantity MTM defect](../sizing_style_2026_09/VALIDATION_REVIEW_PLAN.md) and require daily P&L to telescope to the trade ledger before accepting the downstream risk corrections.
Report actual calendar daily NAV, standard arithmetic excess-return Sharpe, trade expectancy, and marked drawdown separately.
Account-risk sizing must simulate the chosen quantity/current-NAV policy; linear scaling of compounded maximum drawdown is not a risk bound.

Compare the frozen ADX policy with cash and exposure-matched BTC holding/trend controls on identical dates.
Assess a funding veto with paired calendar policy returns and the affected opportunity set, including replacement trades and exposure reduction.
Keep parameter-neighbor checks diagnostic; selecting another neighbor creates a new candidate and trial entry.
Use calendar or trend-episode blocks for uncertainty and show the few trades/episodes driving the result.
The available multi-year record contains few independent trend cycles; report a minimum useful effect and inconclusive intervals explicitly.
Future confirmation must use observations after a new rule freeze; relabeling 2024–2026 cannot make previously inspected data fresh.

## Separate treatment of visual pivot research

Keep the existing backplotted markers as descriptive annotations; no trading return is required for that use.
If a predictive/tradable claim is revived, create `pivot_confirmation_validation.ipynb` with pivot timestamp and confirmation timestamp as separate fields.
Measure outcomes only from the first executable price after confirmation, with both fixed-horizon and actual trade results clearly separated.
Compare with an unconditional same-horizon reversal control matched on prior move/volatility; cluster overlapping pivots and time windows.
Freeze the L/R/prominence/confirm combinations before any new outcome inspection and count all earlier alternatives as development.
Report detection delay, signal count, missing-feature exclusions, false positives, and incremental performance, not only selected historical tops/bottoms.

## Decision gates and limits

Before numeric evidence is reused: causal execution fixtures pass, funding/MTM reconciles, and historical metric/OOS labels are explicit.
Before a new trading claim: fixed-rule net economics and interval estimates must clear a predeclared practical threshold on new observations.
A failure to clear that threshold is insufficient evidence, not proof that every ADX or pivot construction lacks edge.
No further optimization is necessary to preserve the exploratory archive.
ORB sessions, equity corporate actions, and limit-order queue modeling are **N/A** to these daily market-order rules.
Pine visual equivalence, exchange dispatch parity, current shipped settings, data completeness, and actual future performance were **not verified**.
