# Carry exit rule: validation review and remediation plan

Review date: 2026-09-14. Status: **P0 — reconcile transaction-cost and claim definitions before reusing the reported policy uplift; tail protection remains unvalidated.**

This is a retrospective static addendum, not a new preregistration of the original decision.
The findings state that CUM-30D was shipped on 2026-09-12; this review neither verifies nor changes production behavior.
No study was run, cached or live data queried, files imported, or existing artifact edited.

## Reviewed evidence

- [carry_exit_rule.ipynb](carry_exit_rule.ipynb), all zero-based cells **0–4**, including saved textual results.
- [README.md](README.md), [findings.md](findings.md), and [run_p2_exit_rules.py](run_p2_exit_rules.py), full text.
- [results/p2_exit_rules.json](results/p2_exit_rules.json), including rule economics, era/tail summaries, paired bootstrap, and decision clauses.
- Dependency [../brainstorm_validation_2026_09/run_c2_carry_timing.py](../brainstorm_validation_2026_09/run_c2_carry_timing.py), lines 1–155, especially COST_RT and `run_rule`.
- The documented production parity test was not executed; source/data hashes tying the saved run to today's dependency were not established.

## Supported strengths

The study compares explicit exit policies on a common funding stream and charges each rule for its own turnover.
Daily state decisions are shifted into later held settlement observations, avoiding obvious same-print entry hindsight.
The LIVE comparator has a saved parity check against the preceding carry study.
A practical uplift threshold, paired block uncertainty, and worst-year check were specified before the reported comparison.
The findings acknowledge that a sustained negative-funding regime is absent from the observed sample.
Choosing a defined exit instead of interpreting historical always-on as universally safe is a coherent operational preference, separate from measured efficacy.

## Material discrepancies and limits

1. **Cost conventions disagree.** `run_p2_exit_rules.py:17` imports the C2 module and uses its `run_rule` unchanged.
   In that dependency, line 15 defines `COST_RT=0.002`, and lines 42–54 split it between entry and exit: 20 bp per roundtrip.
   README discusses 0.20%+0.04% and 24 bp roundtrip; notebook/findings call 24 bp a toggle, and `findings.md:38` says 24 bp to leave plus 24 bp to return.
   Those are three different economic conventions. The saved one-roundtrip ALWAYS_ON gross/net gap is consistent with the 20 bp source convention.
   Without a historical code hash, exact run provenance is not proven; the present source cannot reproduce the stated cost definition.
2. **Warmup/annualization are inherited from a different question.** The evaluation mask waits for C2's one-year median with at least 300 prints.
   That median is not needed by these daily exit rules; the resulting start is 2019-12-19.
   `years` and block lengths use a fixed 1,095 prints/year, which needs verified actual settlement cadence and elapsed-calendar reporting.
3. **ALWAYS_ON has a specific meaning.** Its held mask is the valid evaluation span, not “always-on while the 7-day average entry condition holds.”
   `findings.md:48–55` mixes those descriptions; sustained holding through a negative average is central to this comparison.
   Do not transfer results to a differently gated always-on implementation.
4. **Pointwise intervals do not correct selection.** Four alternatives are compared with LIVE using individual 90% intervals.
   Common block draws are valuable, but selecting two passing policies from the same inspected history requires explicit family-level uncertainty and trial accounting.
   The preceding C2 study had already exposed the always-on comparison; the historical period is not independent confirmation.
5. **Funding yield is not full account return.** These returns concern sleeve notional funding minus toggles.
   They omit spot/perp basis, actual execution, collateral/margin, borrowing, and hedge breaks; “same or better worst year” is conditional on that narrow ledger.
6. **Tail efficacy is untested.** Worst observed 30/90/180-day funding windows do not establish what happens during an unseen long negative regime.
   Calling 0.85%/year a known future insurance price or claiming a defined exit provides demonstrated tail protection overstates the evidence.
   A cumulative threshold may trigger only after losses and does not bound gap, basis, or margin losses.

## First remediation: bounded reconciliation, no retuning

Create `validation_reconciliation.ipynb` here with an immutable manifest and the exact source/runtime versions behind each saved artifact.
Define a leg, a hedge entry/exit, and a roundtrip in one cost table; separate fees, spread/slippage, and borrowing.
Resolve whether the intended total is 20, 24, or 48 bp per exit/reentry cycle before evaluating outcomes.
Preserve all old results and produce a clearly labeled corrected table for the same five policies and original cutoff.
This replay is justified by a material measurement discrepancy, not a search for a better threshold.
Reconcile rule-level gross funding, charged entries/exits, terminal liquidation convention, and net totals exactly.
Report annual yield using elapsed UTC time and actual settlement timestamps; missing or duplicate prints must fail a coverage gate rather than become zero funding.
Document why a common warmup is needed and distinguish the original median-mask result from any newly proposed causal warmup.

Freeze day boundaries, the definition of a complete funding day, when the exit becomes knowable, and the first executable hedge adjustment afterward.
Fixtures should include a midnight settlement, missing day, three negative days, the −0.5% cumulative crossing, exit-active reentry blocking, and end-of-data closure.
If production parity is important, compare intended states and actual order lifecycle separately; matching rule states is not proof of two-leg execution parity.

## Uncertainty, controls, and tail analysis

Keep LIVE, ALWAYS_ON, SYMMETRIC_7D, MINHOLD21, and CUM30D as the bounded historical family.
Build their common daily funding/cost matrix and jointly resample calendar blocks, with 30-day primary and 14/60-day diagnostic sensitivity.
Estimate each alternative-minus-LIVE contrast on identical resampled dates; report simultaneous intervals or a declared family-level procedure for four alternatives.
Report the full earlier carry trial lineage and any later threshold changes; DSR or bootstrap cannot make reused history untouched.
Preserve the original ≥0.5 pp/year and worst-year tolerance as historical decision rules, and show uncertainty relative to the useful-effect threshold.
If the interval spans that threshold, describe the economic conclusion as inconclusive even if the point estimate passes.

Separate funded-hedge economics from pure funding timing: use matched prices and quantities for both legs, settlement funding, capital employed, and stressed unwind costs.
Stress constructed negative-funding durations and magnitudes with delayed exits, positive spikes, and repeated reentry; label these scenarios, not estimated probabilities.
Add basis/margin and failed-leg scenarios if proposing an account-risk claim.
Define in advance which losses the exit is intended to limit and what minimum protection would justify its turnover cost.
If observed history contains too few relevant regimes, say tail insurance is unvalidated rather than infer protection from ordinary years.
A future monitoring/confirmation period starts after rule and ledger freeze; do not optimize −0.5% or 30 days on the already seen sample.

## Planned deliverables and decision limits

- Cost and timestamp reconciliation, state traces, exact five-rule comparison, and old-versus-corrected clause table.
- Paired family-level uncertainty and a notional-yield versus capital-return bridge.
- Tail-scenario panel with explicit loss mechanism, assumptions, and conditions under which the rule would be reconsidered.

This review alone authorizes no reversal of the documented shipped choice and establishes no new expected uplift.
Directional breakout controls, equity calendars, and stop/target intrabar ties are **N/A** to the funding-state study.
Two-leg execution, basis risk, settlement timing, and reentry are applicable.
Cache completeness, actual production parity, exchange funding provenance, and the numerical impact of correcting costs were **not verified**.
