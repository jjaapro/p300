# Delta neutral: validation review and forward addendum

Review date: 2026-09-14. Static review only; no studies, database queries or downloads were run.
This retrospective review preserves the original preregistration and every historical result.
Future work below is conditional and must be recorded as an addendum before it starts.

## Verdict and priority

**P1 — retain the two-venue dispersion and funding-only sizing rejections.**
Retain the three-venue arm's INCONCLUSIVE-on-power qualification.
The study does not establish the value of crash protection in a real two-leg carry book.
Current dependency drift prevents assuming that an unchanged rerun reproduces the saved baseline.

## Artifacts reviewed

- `delta_neutral.ipynb`, all zero-based cells 0–26; the viewer has no saved code outputs.
- `README.md`, `findings.md`, `dispersion.py`, `carry_sizing.py`, and their result references.
- Decision tables, parity, cost corrections, era corrections and all sensitivity conclusions in the findings were read.
- Current `bots/carry/strategy/signal.py` and `config.py` were read to verify the imported interface.

## What is well checked

- Causal dispersion in `dispersion.py:169` requires contiguous eight-hour intervals and collects the next settlement's selected venue spread.
- Contemporaneous spread is explicitly non-tradeable; the full 120-row scenario family is disclosed.
- Findings retain zero-cost sensitivity: the historical proxy model's 2.73% annualized result still misses the 5% and carry benchmarks.
- `carry_sizing.py:164` shifts the trailing funding z-score/expanding threshold to the following decision date.
- Saved parity checks cover 2,540 daily funding aggregations and three accrual windows; this tests implementation consistency.
- Paired 20-day block bootstrap and all 18 sizing sensitivities support the rejection within the funding-only model.
- `findings.md:159` corrects the earlier causal story: the forecast/settlement era comparison is confounded by the funding level.
- The preregistration already states that basis blow-outs, the actual crash mechanism, are absent (`README.md:337`).

## Claim-specific gaps

1. `carry_sizing.py:38` imports `EXIT_NEG_DAYS`; current carry config defines cumulative-funding exit constants instead.
   The historical replay also expects `neg_streak_days` at line 243, absent from the current `_evaluate_today` result.
   Importing mutable live code therefore no longer provides a frozen historical specification; no import was executed in this review.
2. `dispersion.py:58` and `carry_sizing.py:73` sample `fr_close` at scheduled hours without authenticating historical settlements.
   The documented pre-cutover predicted-rate proxy remains a limitation; the corrected era comparison does not prove it caused dispersion.
3. `dispersion.py:173` charges a complete four-fill cycle on OFF→ON and ON→OFF transitions.
   Findings openly acknowledge double charging; exact strategy economics need actual changed-leg order accounting before reuse.
4. Funding-only P&L deliberately sets spot/perp or cross-venue price P&L to zero.
   A zero-price-P&L calculation is not a mathematical upper bound: basis changes can help or hurt, while margin and execution can dominate.
5. The carry benchmark is BTC-based even for ETH arms (`dispersion.py:285`); exposure, asset, collateral and financing are not fully aligned.
   Annualization on retained intervals/daily rows also needs complete-calendar accounting when data gaps occur.
6. A gate of 365 eight-hour observations is about 122 days, not a year, and observations are serially clustered.
   Iid mean bootstrap in `dispersion.py:205` and trial-adjusted Sharpe do not by themselves establish independent power.
7. Statistical negativity supports the specified historical model; it does not prove cross-venue carry or crash hedges generally cannot work.
   The three-venue recent low-funding sample should not be reclassified as conclusive simply when its row count passes 365.

## Prioritized conditional plan

### P1.1 — preserve reproducibility before exact numbers are reused

- Freeze the historical signal/config revision and original constants alongside source/result hashes in a study-local manifest.
- Add explicit interface expectations for the old negative-streak exit; treat the current cumulative exit as a different comparator.
- Hypothesis: the historical clauses can be reproduced from a fixed snapshot, without silently changing entries or exit rules.
- Build settlement-boundary and missing-day fixtures, including decision publication delay and resize-before/after-settlement ownership.
- Reconcile price units, funding rate units, signs, eight-hour cadence and complete days across each venue and each lineage era.
- Acceptance: original ledger and gate values match within stated rounding, or every discrepancy has a source/timing/accounting explanation.
- Do not repair the old preregistration to match the current production interface.

### P1.2 — only if better market coverage or a new crash question warrants work

- Dispersion hypothesis: a fixed causal rule beats equal-capital carry after changed-leg costs and observed basis P&L.
- Carry-sizing hypothesis: the frozen z-score rule reduces true two-leg tail losses enough to offset lost funding and resize costs.
- Required data: synchronized venue bid/ask/mark/index prices, definitive settlements, contract multipliers, borrowing and collateral constraints.
- Use available-as-of funding for decisions and realized settlements only for P&L; keep forecast and settlement features distinct.
- Price each changed leg, close the final position, include transfer/borrow/funding costs and impose per-venue margin headroom.
- Controls: cash; matched-asset carry; the unchanged rule-off book; equal capital and gross exposure across all arms.
- Preserve 20 nominal dispersion trials, 120 disclosed scenarios and 9/18 sizing variants; record later asset/era/cost choices separately.
- Use calendar walk-forward folds with a purge at least as long as positions remain open; no already-inspected era becomes a fresh holdout.
- Resample common calendar blocks across venues/assets; show stress-regime coverage and sensitivity to block lengths, not just row counts.
- Retain original gates for literal comparability: dispersion ≥5% and ≥carry, sizing Sharpe uplift ≥0.10 and improved MAR.
- Any forward advancement also requires a lower 90% paired net-uplift bound above zero and compliance with a predeclared margin/drawdown budget.
- Missing true settlement history or too few independent stress episodes means INCONCLUSIVE, not automatic escalation to wider parameter search.

## Planned notebook outputs

- New `validation_review.ipynb` with dependency/interface manifest and original-versus-current strategy definitions.
- Funding lineage/calendar audit; changed-leg cost ledger; equal-capital daily cash-flow reconciliation.
- Full price-plus-funding tail attribution, paired control curves, block uncertainty and a clause-preserving decision table.

## Not applicable / not verified

Options pricing is not applicable. Exchange fills, independent settled-rate history and actual collateral liquidation were not verified.
Saved findings were reviewed as evidence; no current run, production parity or new profitability result is claimed.
All proposed work stays under this study directory and changes no production strategy.
