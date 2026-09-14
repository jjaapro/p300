# R4 bot preparation: validation review and forward risk plan

Review date: 2026-09-14. Status: **P1 — the frozen stop screen supports a provisional no-change decision; live risk and late-entry tolerance need additional evidence.**

This retrospective addendum preserves the original preregistration, no-stop decision, and later descriptive appendix.
No study was run, database accessed, data fetched, or production/existing artifact changed.

## Reviewed evidence

- [r4_bot_prep.ipynb](r4_bot_prep.ipynb), all zero-based cells **0–3**, source/markdown and available saved textual content.
- [README.md](README.md), [findings.md](findings.md), and [sl_sweep.py](sl_sweep.py), full text.
- [results/sl_sweep.csv](results/sl_sweep.csv) and [results/late_entry.csv](results/late_entry.csv), schema and reported comparison rows; the larger fire ledger was reviewed through its source and documented summaries.
- The portfolio addendum in `findings.md:74–104`; no corresponding marked portfolio implementation is present in `sl_sweep.py`.
- Earlier [R4 development evidence](../r4_study/VALIDATION_REVIEW_PLAN.md) was reviewed for selection history and calendar parity.
- Individual raw minute paths and every fire in the large CSV were not manually inspected.

## What is supported

Four named calendar windows, three stop levels plus no stop, and a finite delay grid are specified.
The primary decision considers both expectancy loss and worst single-fire improvement across windows and eras.
Minute lows are used for the stop screen, and the README explicitly warns that level fills are optimistic under gaps.
The saved primary screen does not find a stop satisfying its original conjunction; retaining the baseline is a reasonable provisional outcome.
The findings correct the maximum overlapping window count to three and retain examples of clustered losses.
These are useful implementation-preparation observations, not validated bounds on account loss.

## Remaining gaps

1. **Scheduled exit does not bound loss.** Holding for 10–24 hours limits intended duration, not adverse price movement, liquidation, or an unavailable exit.
   Historical worst single-fire losses are observed scenarios; they do not establish a maximum risk budget.
2. **Stop path completeness is not checked.** `sl_sweep.py:63–80` requires entry/exit rows but accepts any intervening minute subset.
   A missing path can hide a stop. When a stop is detected, execution is assumed exactly at the stop despite gaps/spreads.
   Thus the reported worst-fire shrink is conditional on the optimistic fill convention.
3. **Venue/cost coverage is limited.** `sl_sweep.py:84` loads spot BTC/ETH prices and deducts a constant 15 bp roundtrip.
   Perp funding, realistic market exits, collateral/margin, and liquidity-dependent slippage are absent.
4. **Delay comparisons need paired eligibility and uncertainty.** Lines 100–105 independently skip unavailable delayed entries.
   Lines 123–128 compare group means, which need not represent identical dates if quotes are missing.
   A few bps of mean difference cannot establish a safe lateness threshold without a paired policy contrast and interval.
5. **The decision implementation needs explicit empty/inconclusive handling.** `decide` applies `.all()` over selected rows, which can pass vacuously if expected rows are missing.
   Its fallback grace needs reconciliation with the README's inconclusive convention before being used as a live specification.
6. **The portfolio appendix overreaches its inputs.** `findings.md:74–104` turns `sl_fires.csv` outcomes into annual return, maximum drawdown, and MAR claims.
   The saved fire fields have entry day and final return but no full intraday marked NAV/quantity path; `sl_sweep.py` does not implement a capital portfolio ledger.
   Whole-fire or trade-close sequencing is not a validated marked account drawdown for overlapping BTC/ETH positions.
   “Removes the one argument” for stops and “cannot be managed” by per-fire stops go beyond this conditional historical comparison.
7. **This is calibration after selection.** The four windows came from earlier broad R4 searches over largely the same history.
   Era agreement is a robustness check, not an independent untouched holdout; BTC/ETH and Wednesday/Friday observations are correlated.

## Bounded forward validation

Keep no stop as the historical reference; do not open a finer stop grid because existing levels failed.
Create `execution_and_risk_validation.ipynb` here if current R4 deployment choices need evidence.
Freeze the exact four windows, stop/delay grids, common cutoff, cost assumptions, and decision thresholds before any new replay.
Record day-of-month semantics explicitly: R4_ETH uses the **following day's** day-of-month (`sl_sweep.py:51–59`).
Reconcile that calendar with the original `r4_study`, which filters the entry day's day-of-month.
Include month ends, the 14th boundary, leap days, UTC midnight, and overlapping ETH/V2 windows in calendar fixtures.

Build a minute coverage manifest over every eligible fire and delay; mark missing prices separately from a no-stop or no-trade outcome.
Use matching venue/instrument prices for any perp claim, actual funding timestamps, and conservative gap-through-stop/late-exit fills.
Fixtures should cover an opening gap, missing interior minute, simultaneous stop/exit boundary, and three overlapping positions.
Measure delayed entry against the original fixed exit on common fires, then report actual policy results if missing quotes cause skipped trades.
Require every expected strategy/era row before applying the historical adoption clauses; insufficient data yields inconclusive.

Construct marked portfolio NAV from signed quantities, entries/exits, cash, and funding at the intended fixed allocation and leverage rules.
Report maximum concurrent notional, portfolio drawdown, intraday loss, margin usage, and observed cluster outcomes.
Initial equity belongs in the drawdown high-water mark; costs must be charged once per actual leg.
Show the original single-fire risk metric alongside portfolio risk, rather than substituting one for the other.
Stress delayed scheduled exits, correlated BTC/ETH moves, gaps through stops, and a temporarily unavailable venue.
These are scenario losses with specified assumptions, not estimates of a hard loss ceiling.

## Inference and decision rules

Use matched policy-minus-no-stop daily portfolios and paired block intervals; jointly sample BTC/ETH and overlapping windows by calendar time.
Start with 14-day blocks and show 7/30-day sensitivity; rare loss clusters should also be listed individually.
Track all original window-selection trials, the stop/delay grid, era views, and the later portfolio appendix in the research ledger.
Choosing an attractive delay or different stop after inspecting results creates a new candidate and requires its own plan.
Retain original expectancy-loss/worst-fire thresholds as historical criteria; add a separate predeclared capital-risk tolerance for a deployment proposal.
For delay tolerance, report an upper confidence bound on the economically acceptable loss at the proposed grace, not just a pointwise mean.
For stop adoption, require both a credible portfolio risk benefit and acceptable net economic cost on the fixed policy comparison.
Wide intervals mean inconclusive; no-stop can remain the default without claiming that stops never protect risk.
New confirmation starts after rule and ledger freeze; the already seen historical eras are replication only.

## Planned outputs and limits

- Exact calendar parity and completeness tables, paired delay curves with eligible counts, and causal gap/stop fixtures.
- Four-policy event/quantity ledger, jointly marked portfolio NAV, cluster loss table, and stressed execution assumptions.
- Original versus corrected clause table, clear inconclusive handling, and a risk-policy decision separate from signal-edge validation.

ORB range/entry-direction controls and equity corporate actions are **N/A**; the relevant mechanism is scheduled crypto exposure.
Queue-priority simulation is **N/A** to a market-order assumption, but stop-market gap/slippage is applicable.
Actual exchange fills, current live sizing/dispatch, raw-data completeness, and the appendix's precise computation provenance were **not verified**.
