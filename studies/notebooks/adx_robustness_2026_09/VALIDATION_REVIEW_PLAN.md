# ADX robustness: validation review and remediation plan

Review date: 2026-09-14. Status: **P0 — execution, funding attribution, and account-risk claims require correction before reuse.**

This is a retrospective static audit/addendum. It does not alter the original preregistration or claim that new checks preceded the old results.
No studies were run, databases accessed, data fetched, or production files changed.

## Reviewed evidence

- [adx_robustness.ipynb](adx_robustness.ipynb), all zero-based cells **0–10**, including saved text outputs.
- [README.md](README.md), [findings.md](findings.md), [adx_lib.py](adx_lib.py), full text.
- [run_p1a_live_semantics.py](run_p1a_live_semantics.py), [run_p1bc_phases.py](run_p1bc_phases.py), [run_p1d_late_entry.py](run_p1d_late_entry.py), and [run_p1e_funding.py](run_p1e_funding.py), full text.
- Saved JSON: [p1a](results/p1a_live_semantics.json), [p1bc](results/p1bc_phases.json), [p1d](results/p1d_late_entry.json), [p1e](results/p1e_funding.json); phase and ledger tables shown in the notebook.
- Original [../adx_study/harness.py](../adx_study/harness.py) and relevant daily MTM/helper contracts were inspected statically.

## Supported strengths

The study explicitly separates the old daily harness from a proposed live-like minute walk.
It distinguishes fees, funding, phase sensitivity, sizing, late entry, and funding overlap rather than treating one headline backtest as validation.
All 24 phases are reported; equal-risk phase ensembles are documented instead of selecting a best offset.
The saved P1a check reproduces the original harness aggregate and compares entry signal sets.
Recognizing trade-close drawdown as insufficient was an important correction to the earlier ADX study.
These strengths establish a useful audit framework, not proof that the new execution implementation is correct.

## Blocking findings

1. **Entry-day stops are skipped.** `adx_lib.py:117–158` is a daily outer loop with a minute stop loop.
   At line 141, the newly entered position takes `continue`; the minute loop at lines 146–157 is never visited that day.
   The comment describes skipping the entry minute only. A first-day stop can change both trade outcomes and subsequent state.
2. **Funding is assigned to the wrong risk dates.** `adx_lib.py:161–168` turns the full trade funding amount into an exit cost for `daily_mtm_pnl`.
   Daily funding affects NAV while the position is open, so daily risk statistics and portfolio overlap cannot be validated this way.
   `live_walk` returns closed trades only; final open exposure also needs an explicit mark.
3. **Calendar mismatch undermines attribution.** Saved P1a compares 2,446 harness days with 2,400 live days.
   P1bc computes per-phase statistics before alignment (`run_p1bc_phases.py:26`); saved phase lengths range from 1,988 to 2,400 days.
   The ensemble union/fill step does not retrospectively fix the individual phase statistics.
   Therefore “the whole gap is funding” is not a clean controlled decomposition.
4. **Compounded maximum drawdown is not linear in allocation.** `run_p1bc_phases.py:40–41` multiplies 1x maximum drawdown by shipped notional and inverts that number for a risk budget.
   A compounded account curve needs to be recomputed from quantities, changing NAV, cash flows, and allocation rules.
   The resulting percentage is not an established account-loss bound or a validated size recommendation.
5. **Late-entry counterfactual mixes exposures.** `run_p1d_late_entry.py:38` retains the original funding charge after changing entry time.
   The full delayed machine also inherits the entry-day stop omission.
   The reported six-hour delta of about −0.481 pp is close to the −0.5 pp rule and has no paired uncertainty interval.
6. **Funding netting is overstated.** P1e and `findings.md` infer savings from ADX/carry overlap days.
   On equal instruments and matched quantities, separate long/short funding already sums to `−q_long*f + q_short*f`.
   Netting does not create that funding saving again; reduced gross margin, turnover, or capital requirements are separate benefits.
   Counting overlap days does not match notionals or settlement-time positions, and a carry state using a day's funding is not a prior-day exposure trace.
7. **The shared daily MTM arithmetic is not fixed-quantity P&L.** `adx_lib.py:168` calls `sizing_style_2026_09/sizing_lib.py:95–113`.
   That helper's line 109 multiplies original notional by each successive `mark/prev−1`; `prev` then changes while notional remains constant.
   A fixed-quantity position instead earns `quantity × direction × (mark−prev)` with quantity fixed from its fill.
   This changes cumulative P&L and drawdown, not only their timing. See the [sizing-style dependency review](../sizing_style_2026_09/VALIDATION_REVIEW_PLAN.md).

## Remediation order and bounded scope

Preserve all current artifacts. Create a versioned corrected run only if these numbers inform a new risk or deployment decision.
Freeze the shipped Tier 2 rules, funding-veto exclusion, 24 phase definitions, delay list, venue, and common start/end before replay.
Record source files/hashes and data coverage separately for candles, minute execution, and funding settlements.
Explicitly label spot prices plus perp funding as a proxy until matching perp execution paths are available.

First build deterministic fixtures for the first holding day, a stop gap, a boundary signal exit, trail activation, delayed entry, and terminal open exposure.
The old daily trail may use that day's close to raise a stop before testing that day's low; preserve it only as the historical comparator.
The corrected engine must state when each completed daily bar becomes available and when the newly computed trail becomes active.
Specify the ordering of an old stop, first-minute fill, signal exit, and updated stop; an ambiguous bar needs a conservative bound or finer path.
Do not claim live parity from matching an entry set alone: reconcile event times, prices, quantities, reasons, and missed opportunities.

Next build one cash ledger with price P&L, entry/exit fees, signed settlement funding, position quantity, and marked NAV.
Require summed daily price P&L to equal signed fixed quantity times exit-minus-entry price; changing the number of interim marks must not change that total.
Reconcile ledger totals against individual trades, include zero-exposure days, and include the initial NAV in drawdown calculation.
Compute all counterfactuals on the exact same calendar and terminal policy.
Attribute harness-to-corrected differences by a fixed sequence of semantic, fee, funding, and marking changes; disclose interaction effects.

For sizing, simulate the actual fixed quantity or fraction-of-current-NAV policy at the existing proposed size.
Report gross and net exposure, margin usage, worst calendar drawdown, concurrent losses, and gap/funding stresses.
Choose any future maximum account-loss tolerance before results; a historical worst drawdown is a scenario observation, not a guaranteed bound.

For late entry, compare matched fires with the original scheduled exit and correct funding, then rerun full state transitions for each already specified delay.
Report both paired opportunity cost and policy-level differences, including missing eligibility and first-day stops.
For funding overlap, reconcile ADX and carry quantities at every settlement on separate and net ledgers.
Show net portfolio funding unchanged by pure bookkeeping, then quantify only independently justified cost/margin effects.
Spot-versus-perp and carry removal are distinct portfolio counterfactuals and require their own basis/financing assumptions.

## Statistics and decision gates

Keep phases and ensembles as robustness diagnostics; choosing one retrospectively becomes another trial.
Use the complete inspected ADX research lineage for selection caveats; N=24 phases is not 24 independent replications.
Compare policies on common daily return and exposure panels with paired calendar/episode blocks; report 14/30/60-day sensitivity.
Sparse multiweek trends limit effective sample size; no bootstrap can add missing trend regimes.
Retain old frozen thresholds as historical decision inputs, but report interval overlap with the economic threshold as inconclusive.
A future confirmation period starts only after the corrected rule and decision metric are frozen; earlier inspected history is development/replication.

## Planned notebook outputs and limits

- New `validation_reconciliation.ipynb`: manifest, causal engine fixtures, exact trade/event diff, funding reconciliation, and equal-calendar P1a table.
- New `risk_and_overlap_validation.ipynb`: actual-size NAV, all 24 phases, paired delay curves, and settlement-level netting identities.
- A clause table preserving old values and showing corrected values with uncertainty; a separate list of claims withdrawn or narrowed.

The correction gate is zero unexplained event/ledger discrepancies on the fixtures and a reconciled common calendar.
The research gate is a stated economically useful effect with uncertainty and capital constraints satisfied, not a prettier MAR.
No new sweep is needed to preserve existing conservative choices.
ORB session controls, equity corporate actions, and passive-limit queue priority are **N/A** here.
Current live behavior, database completeness, actual exchange fills, and the quantitative impact of the defects were **not verified**.
