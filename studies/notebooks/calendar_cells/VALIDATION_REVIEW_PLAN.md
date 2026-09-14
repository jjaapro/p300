# Calendar cells: validation review and optional follow-up plan

Review date: 2026-09-14. Status: **P2 — archived negative screen; useful descriptive evidence for the exact five rules, with narrower claims required.**

This retrospective audit does not change the frozen study or convert later observations into preregistration.
No notebook or backtest ran, no database was queried, and no data or existing artifact was changed.
There is no recommendation to rerun a rejected family simply to find a winner.

## Reviewed evidence

- [calendar_cells.ipynb](calendar_cells.ipynb), all zero-based cells **0–21**; source/markdown and available textual content, without image payloads.
- [README.md](README.md) and [findings.md](findings.md), full text, including NFP caveats and post-hoc opposite-direction observations.
- Key computation in [run_cells.py](run_cells.py): data aggregation, all five rule definitions, evaluation, monthly PBO panel, and post-hoc attribution.
- [results/cells_summary.json](results/cells_summary.json), [results/nfp_sensitivity.json](results/nfp_sensitivity.json), [results/pbo.json](results/pbo.json), and the documented post-hoc notes.
- [../../lib/validation/dsr_pbo.py](../../lib/validation/dsr_pbo.py), especially CSCV row truncation; bootstrap helper semantics were reviewed statically.
- The notebook is largely an artifact viewer; saved raw market data and every individual trade path were not inspected.

## Supported conclusions

The study specifies five decision-bearing cells, a common fee convention, era requirements, and a DSR gate.
The recorded five rules fail their original conjunction of positive pre/post-era t and DSR conditions.
Negative results are retained, and opposite-direction observations are explicitly labeled post-hoc rather than promoted as new validated candidates.
The NFP standalone/incremental distinction usefully recognizes overlap with an existing R4 Friday position.
The quarter-end, 52-week-high, and weekly EMA samples are small; the reported absence of convincing evidence is a reasonable no-build decision.
These observations do not establish that the broad economic mechanisms, correctly timed event releases, or every calendar strategy fail.

## Concrete limitations

1. **The event calendar is a proxy.** The README/findings explain that the stored NFP schedule is generated from a first-Friday rule and does not establish actual release dates.
   `run_cells.py:223–247` trusts those stored dates and uses fixed UTC entry/exit times.
   The recorded outcome describes that fixed-UTC proxy; it is not a verified test of actual release-relative NFP trading across daylight saving time and exceptional releases.
2. **Holding-period/funding prose is incorrect.** README line 38 and findings around line 208 describe all cells as holding at most 72 hours.
   `run_cells.py:303–322` holds HIGH52 for five days (120 hours); `cell_ema_eth`, lines 333–380, can hold across many weeks.
   Funding/borrow can matter substantially, especially for EMA. Signed long/short funding can add or subtract P&L.
   “Funding can only hurt” is therefore not a valid blanket bound.
3. **Endpoint availability is not full coverage.** Missing exact entry/exit quotes are counted, but zero skipped endpoints does not prove complete intervening data.
   Hourly aggregation at lines 93–125 accepts the first/last available minute without a 60-minute completeness requirement.
   HIGH52's 364 prior observations are not necessarily 364 consecutive calendar days if source days are absent.
4. **A final open EMA trade is excluded.** Lines 374–380 disclose it, which is good, but closed-trade mean and cumulative sums omit current marked exposure.
   They are not a complete account-return or drawdown series.
5. **PBO uses an incomplete and non-causal risk panel.** Lines 386–399 assign each whole trade return to its entry month, including multi-month EMA outcomes.
   `results/pbo.json` lists 68 months (2021-01 through 2026-08); `cscv_pbo` at helper line 228 uses only `(T//s)*s=60` rows for s=10.
   The final eight listed months therefore do not enter that statistic. The 252 combinations are dependent splits, not 252 independent trials.
   This PBO is a selection diagnostic, not prospective probability of strategy failure.
6. **Uncertainty and power claims need restraint.** Lines 154–167 apply iid per-trade bootstrap/DSR and realized-dispersion thresholds.
   Correlated market episodes and very sparse trades weaken that approximation.
   Statements that confirmation is “physically impossible” or that more history cannot help are too strong; more independent episodes can increase power.

## Disposition now

Retain the historical no-build conclusions and exact rule definitions.
When quoting this study, say “these five fee-adjusted proxy rules failed the specified evidence gates.”
Do not use the PBO number as a calibrated probability or the fixed-UTC proxy to rule out actual NFP-release effects.
Do not infer the profitability of a reversed direction from the sign of the original result.
Preserve later opposite-direction observations and sensitivities in a trial ledger as post-hoc research lineage.
No new sweep, new horizon, or larger family is necessary for archival use.

## Conditional plan for a specifically requested revival

Choose one economically motivated cell first, with a stated useful minimum net effect and an explicit reason new information could change the decision.
Create a new `validation_followup.ipynb` in this directory; retain the original notebook and outputs unchanged.
Freeze the exact signal, entry/exit timestamps, venue/instrument, capital model, costs, and missing-data policy before evaluating new outcomes.
For NFP, use a versioned actual release calendar and local release timezone, then map to UTC with explicit DST/exception handling.
Compare release days with weekday/time-matched nonrelease controls and isolate incremental exposure beyond R4 at equal capital.
For weekend trading, distinguish the fixed crypto UTC window from an actual futures-close/reopen gap; obtain the relevant calendar only if that claim is intended.
For HIGH52, require contiguous prior daily coverage and compare with matched trend/volatility exposure, not only unconditional BTC drift.
For weekly EMA, specify complete-week formation, next executable open, signed funding/borrow, and terminal open-position MTM.
Quarter-end work needs actual quarter events as independent units, including uncertainty about a rare-event mean.

For a risk or selection claim, rebuild a common marked daily portfolio panel with flat days, rather than assigning future trade P&L to entry months.
Use joint calendar blocks across BTC/ETH; report event/quarter clusters where appropriate.
Predeclare reasonable block sensitivity and intervals for the economic effect; iid DSR remains a supporting approximation.
Record the original five cells, standalone NFP sensitivity, flipped directions, and any new choices in the full trial history.
A new historical implementation is a replication; the already inspected 2020–2026 sample is not a fresh holdout.
Prospective confirmation begins after the new rule/data manifest is frozen.

## Planned outputs and decision rules

- Coverage and event-calendar exception table, exact eligible sessions, exclusion reasons, and signal-available/entry timestamps.
- One frozen rule-versus-control ledger, marked risk panel where relevant, signed financing, and conservative cost sensitivity.
- Economic-effect interval, sample size/effective event count, and a prospective power/minimum-detectable-effect statement.
- Decision: reject the specific implementation, continue as inconclusive, or seek separate confirmation if the lower effect bound clears the predeclared useful threshold.

Stop-loss path ordering and queue priority are **N/A** to the original fixed-time exits; they become relevant only if a new stop/order type is introduced.
Actual NFP history, CME calendars, source completeness, funding series, and the saved outputs' execution provenance were **not verified** in this static review.
