# Cross-study comparison: descriptive validation review

Review date: 2026-09-14. Static review only; no backtests, database queries or downloads.
This document preserves the explicitly descriptive scope of the README and the underlying studies' separate verdicts.

## Verdict and priority

**P2 — keep as a unit-normalized descriptive comparison, not a portfolio ranking.**
Mean trade returns can be compared in the declared notional units, subject to the source-ledger definitions.
Entry-ordered drawdown/MAR and macro-gate overlap do not validate capital allocation or diversification.
Only an actual allocation question warrants a new portfolio test.

## Artifacts reviewed

- `README.md`, `compare.py`, `squeeze_overlap.py`.
- Saved `results/comparison.csv` and `results/squeeze_overlap.txt`.
- No notebook exists in this directory; any future validation should be notebook-led.
- Imports/CSV inputs were traced to R4, SqueezeBull and PDO study ledgers; their full studies are reviewed separately.

## What the comparison does well

- `README.md:3` explicitly says no hypothesis is proven and no new decision rule exists here.
- `compare.py:59` converts basis points to notional percent, and line 77 converts SqueezeBull R using the stated 2% stop.
- The 1% PDO retouch geometry and per-source 15/18 bp cost conventions are disclosed.
- The README warns that R4 concurrency and shipped leverage differ from one-notional-per-trade arithmetic.
- Full-history, post-ETF, individual sleeves and rejected retouch rows remain visible rather than selecting one favorable total.
- Saved overlap output reports only 1 of 114 SqueezeBull fires satisfying the proxy ShortSqueeze macro gate.

## Specific limits and checks needed before reuse

1. `compare.py:33` adds resolved final outcomes in entry order, without an initial zero point.
   That curve is a sequence of trade returns, not marked daily portfolio equity; overlapping trades can exceed the nominal capital budget.
2. Annual return uses first-to-last entry dates (`compare.py:35`), excluding final holding tails and potentially differing idle windows.
   Different samples and event frequency make annualized totals/MAR unsuitable for direct strategy ranking.
3. Multiplying R by a constant is valid only if every input R uses that original stop/notional definition and its stated net costs.
   Changes in upstream sizing, gross/net labels, unresolved filters or code versions must be detected rather than silently inherited.
4. The OOS SqueezeBull rows are an already-published thin segment; the comparison cannot create additional independent validation.
   Full-history versus post-ETF selection remains descriptive research history, not a new holdout.
5. `squeeze_overlap.py:31` uses the completed Asia session for an entire UTC day's gate.
   For earlier same-day SqueezeBull fires this information may not yet be available; it is at most a retrospective daily association.
6. Missing gate data becomes False (`squeeze_overlap.py:57`); coverage failures are conflated with actual gate failures.
   `base` at line 54 bounds only the starting date, so later source history may extend beyond the fire sample.
7. A macro prerequisite is not the full ShortSqueeze signal, actual simultaneous exposure or net daily return correlation.
   Low same-day gate overlap does not establish low shared drawdown, low beta or portfolio diversification.

## Proportionate forward plan

### P2.1 — make descriptive reuse auditable

- Hypothesis: each displayed row is an arithmetically correct description of the frozen input ledger and declared units.
- Freeze CSV/source hashes, strategy revisions, effective date windows, original stop geometry and resolved/unresolved policy.
- Reconcile representative stop, target, timeout and fee-only outcomes from native R/bp to dollars at one fixed notional.
- Print trade-sequence drawdown as such, including the initial zero point; do not relabel it account drawdown.
- Add a common-calendar comparison alongside each native sample, retaining both and showing differences in idle time and frequency.
- For overlap, separate missing, unavailable-at-fire, false and true gates, and restrict base dates to the identical sample window.
- Compare complete actual signal/position ledgers if the question is shared trading events; keep macro-gate association separately labeled.
- Acceptance: every row reconciles in units and every statistic has its sample, denominator and exposure interpretation attached.

### P1 only if the user later asks for allocation or diversification

- Freeze one concrete portfolio question and capital split before evaluating combined performance.
- Controls: cash, each source strategy alone, equal capital combination and equal-gross/equal-volatility alternatives sized from past data.
- Build a timestamped daily MTM book from actual entries/exits, shared cash, concurrent risk and identical leverage constraints.
- Use common venue/execution assumptions, spreads, slippage, fees and holding-dependent funding; also retain native-cost sensitivity.
- Count every strategy/era/allocation choice and prior source-study search in the research ledger; define a small fixed allocation family.
- Reserve new forward data and use chronological folds; existing comparison rows and published OOS periods remain inspected data.
- Resample shared calendar blocks across all sleeves and test paired portfolio improvement, drawdown co-occurrence and tail dependence.
- Require lower 90% paired net utility-improvement bound above zero with no breach of a predeclared capital/drawdown budget.
- A favorable mean trade return or low gate-overlap percentage alone cannot satisfy an allocation gate.
- If there is no portfolio decision, stop after descriptive reconciliation rather than launching new strategy backtests.

## Planned notebook outputs

- New `validation_review.ipynb`: native-unit conversion examples, source lineage and coverage/common-calendar tables.
- Trade-sequence versus real-time NAV distinction; missing/unknown overlap counts and actual event/position overlap if available.
- Only conditionally: equal-capital portfolio ledger, dependent uncertainty and forward allocation decision table.

## Not applicable / not verified

This folder has no independent parameter search or profitability verdict to preregister retroactively.
Source strategy correctness, live exposure, exact funding and executable performance were not revalidated here.
No existing source, saved result or upstream strategy verdict was changed.
