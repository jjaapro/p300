# ETH/BTC regime pool: descriptive validation review

Review date: 2026-09-14. Static source review only; no study execution or database query.
This is a retrospective review and conditional forward plan; existing code is unchanged.

## Verdict and priority

**P2 — descriptive exploratory script, not allocation evidence.**
The script can describe sampled conditional ETH/BTC returns after its calendar conventions are checked.
It does not establish that replacing BTC with ETH, or adding ETH, improves an executable portfolio.
An allocation test is conditional on a specific future portfolio decision.

## Artifacts reviewed

- `eth_vs_btc_in_regime.py`, the only study source in this directory.
- No notebook, saved decision report or preregistration was present in the reviewed study inventory.
- The imported `strategies/support/regime_jplus.py` classifier was inspected as source.
- Data-loader calls were traced at the interface; underlying database contents were not queried.

## Sound elements of the approach

- The comparison uses paired BTC and ETH observations rather than comparing unrelated full-history averages.
- ALL, BULL, strong-bull, mild-bull and OFF strata are explicitly reported.
- Correlation and return spread are separate descriptive outputs.
- The classifier is designed around prior-day information; its main classification logic uses a prior observation.
- The script is compact enough that its statistic and selection definitions can be audited directly.
- No recorded promotion decision needs to be reversed by this review.

## Specific gaps and boundaries

1. `eth_vs_btc_in_regime.py:78` joins common dates and uses a prior available BTC date.
   A missing calendar day can turn a nominal daily return into a multi-day return; ETH and BTC must have identical endpoints.
2. The imported classifier's `classify_series` initializes the peak from its first 51 observations (`regime_jplus.py:114`).
   Early warm-up labels require explicit exclusion and a future-perturbation check before claiming complete prefix invariance.
3. `eth_vs_btc_in_regime.py:57` compounds selected-day returns and the spread ETH minus BTC.
   Compounding the spread implies daily long ETH/short BTC rebalancing; one notional on each leg is 200% gross exposure.
4. A daily conditional return difference does not include entry/exit turnover at regime changes, spread, fees, borrowing or perpetual funding.
   The statistical spread and a capital-allocated market-neutral portfolio are different objects.
5. The elementary t-style statistic in `stats` treats observations as independent.
   Regime persistence, shared market shocks and volatility clustering reduce effective information.
6. Five overlapping strata are not five independent confirmations; any favorable choice of regime is part of selection.
   Historical involvement of the classifier in upstream research remains part of the search history.
7. Comparing raw returns does not equalize ETH's and BTC's volatility, beta, tail loss or gross notional.
   Higher conditional ETH return can be compensation for greater market exposure rather than incremental selection value.

## Proportionate forward plan

### P2.1 — if the descriptive table is needed

- Hypothesis: paired next-day ETH minus BTC returns differ descriptively across the fixed lagged regimes.
- Create a source manifest with symbol, venue, bar convention, sample cutoff and missing-day coverage.
- Reindex both assets to the exact UTC calendar and exclude any pair without both consecutive closes.
- Define regime availability at the decision instant; exclude warm-up and prove prefix invariance with synthetic future perturbations.
- Print unlevered asset returns, simple spread and compounded rebalanced-spread wealth under distinct labels.
- Preserve all five strata; no threshold or regime redesign follows from this descriptive table.
- Use calendar-block intervals for paired means/correlation and disclose regime run count and tail-event concentration.
- Acceptance: every paired row has identical known endpoints and no statistic is described as tradable alpha without a trading model.

### P1 only if an actual allocation question is introduced

- Freeze the hypothesis first: ETH substitution, incremental ETH allocation or market-neutral ETH/BTC spread.
- Define capital weights, rebalance schedule, maximum gross exposure, risk budget and transaction venue before historical scoring.
- Controls: BTC-only, ETH-only, fixed BTC/ETH blend, and a lagged equal-volatility comparison on identical dates.
- Size from trailing data only; separate a beta-neutral spread from a dollar-neutral spread and charge their distinct turnover.
- Use bid/ask or conservative spread assumptions, fees, slippage and financing; use realized funding for any perp cash flows.
- Include cash during OFF periods, existing portfolio sleeves and simultaneous collateral demands.
- Register one primary allocation rule and the fixed controls, counting the five old descriptive cuts as previously inspected choices.
- Use rolling chronological validation; embargo overlapping estimation/holding windows and reserve genuinely unseen future data.
- Estimate paired portfolio differences with calendar blocks long enough to preserve regime spells; show block-length sensitivity.
- Advancement requires lower 90% paired net utility-improvement bound above zero under a predeclared tail-risk limit.
- Set the minimum economically useful gain and maximum turnover before testing; a positive raw spread alone cannot pass.
- If no allocation question or adequate timestamped data exists, remain descriptive and do not run a new sweep.

## Planned notebook outputs

- New `validation_review.ipynb`: data/calendar audit, classifier availability and the five fixed descriptive strata.
- Paired ETH/BTC return and beta/volatility tables with dependent intervals and exposure labels.
- Only if justified: capital-conserving net allocation ledger, equal-risk controls and forward decision table.

## Not applicable / not verified

Options settlement, stop/target order priority and exchange liquidation are not part of the current descriptive script.
Actual data coverage, the printed numerical results, execution feasibility and allocation performance were not verified.
No existing notebook existed to execute or alter; future testing belongs in this directory as a new notebook.
