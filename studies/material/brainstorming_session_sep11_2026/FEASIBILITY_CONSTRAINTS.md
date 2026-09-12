# Trading feasibility assessment — 8 September 2026

**Decision: the available evidence does not support live trading toward a one-month 10x target.** This is a completed initial assessment for the user's project, not a profitable strategy or a completed month of paper trading. No trades, deposits, account transfers, or purchases were initiated. No background study is running.

The user lives in Finland, accepts a maximum EUR 100 loss, and requires that reaching that loss permanently ends the study. The original seed range was EUR 100–200; the calculations below use EUR 100 without treating another EUR 100 as authorized replacement capital. Constraints are recorded in [study_constraints.json](../trading_feasibility/study_constraints.json). That file records the limit; it does not enforce it on an exchange. Any future study must honor permanent termination at the loss limit, with no reset, redeposit, or continuation to recover losses.

Turning EUR 100 into EUR 1,000 in 30 days requires EUR 900 profit: **900% net return, equivalent to 7.98% compounded daily**. Starting with EUR 200 instead changes the target to EUR 2,000 and leaves the required percentage return unchanged. Willingness to lose the seed does not establish a profitable edge. These calculations establish the hurdle, not the probability of success.

Operating expenses make the hurdle higher. These are hypothetical costs, not estimates of this session's bill:

| Expense per day | Expense over 30 days | Return on EUR 100 just to cover expense | Ending capital before paying expense to retain EUR 1,000 |
|---|---:|---:|---:|
| EUR 0 | EUR 0 | 0% | EUR 1,000 |
| EUR 1 | EUR 30 | 30% | EUR 1,030 |
| EUR 5 | EUR 150 | 150% | EUR 1,150 |
| EUR 10 | EUR 300 | 300% | EUR 1,300 |

This table assumes expenses are paid at month end. Trading fees, spread, slippage, funding where applicable, currency conversion, and taxes are additional. Actual incremental model/subscription costs and whether they count against the EUR 100 ceiling are unknown. OpenAI provides an organization Costs endpoint for API billing; its availability does not give this session access to the user's bill or establish how this app/subscription is charged. [Official API usage and costs reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage). Recomputed arithmetic is in [economics.json](../trading_feasibility/economics.json).

The existing squeeze strategy fails its available holdout check. Reusing the repository's cached BTC 15-minute data and existing signal/exit functions yields 29 trades from the chronological holdout beginning 19 December 2024 at 01:30 UTC, through August 2026:

| Exit | Holdout win rate | Mean net trade return | Compounded trade series |
|---|---:|---:|---:|
| 1R | 51.72% | -0.1012% | -3.1607% |
| 1.5R | 44.83% | -0.0922% | -2.9304% |
| 3R | 34.48% | -0.1693% | -5.1066% |
| Partial-exit ladder | 41.38% | -0.1271% | -3.8913% |

These figures subtract the existing fixed 13 basis-point roundtrip charge but **exclude funding**. All four mean returns are negative. They are descriptive historical results, not forecasts or estimates of the chance of reaching EUR 1,000.

The [audit script](../trading_feasibility/audit_existing_strategy.py) was run successfully against the existing local caches. Its [JSON output](../trading_feasibility/strategy_audit.json) records the results, source/data SHA-256 hashes, and runtime versions. To reproduce from the repository root with the existing Python dependencies, run `python -B trading_feasibility/audit_existing_strategy.py`. It makes no network data requests and writes only its audit JSON. The constraints and break-even arithmetic were also checked programmatically.

The implementation has material accounting limitations:

- [The existing README](ASTRO_README.md) promises realised funding and [config.py](../astro_validation/src/config.py) enables it, but the squeeze and pooled simulators subtract only fixed roundtrip costs. The helper in [stats.py](../astro_validation/src/stats.py) has no callers. Funding can be paid or received; its omission invalidates the claim that it was included.
- [The pooled simulator](../astro_validation/src/tests/c2_pooled.py) evaluates entries without cash allocation or concurrent-position limits. The full-history 1.5R variant has 13 overlapping entries among 164 trades. Compounding those trades sequentially, as the reported +37.2% does, is not a demonstrated feasible portfolio return.
- Sharpe annualization uses holding duration instead of calendar equity observations, and drawdown omits starting equity from the running peak. Stop fills also lack gap-aware pricing. These metrics should not guide sizing until corrected.
- The best exit was selected from alternatives, and the displayed holdout has now been inspected. It cannot serve as untouched validation for further tuning. The existing ETH 1R check is also negative: 111 trades, 45.95% wins, and a -22.46% compounded trade series. [Existing ETH results](../astro_validation/out/c2_eth_oos.csv).

The 164 BTC signals cover about 5.7 years, roughly 2.4 signals per month historically. A one-month forward trial may yield too little information to establish an edge. The separate [BTC forecast study](../btc_study/README.md) used June 2026 information to forecast July; it is neither a current trading signal nor an independently validated execution strategy.

MEXC also fails venue verification for this assessment. On 8 September 2026, a text search of ESMA's [authorized-provider register](https://www.esma.europa.eu/sites/default/files/2024-12/CASPS.csv) found no `mexc` or `mxc` match. The [noncompliant-entity register](https://www.esma.europa.eu/sites/default/files/2024-12/NCASP.csv) positively identifies MEXC Global / MEXC / mexc.com, citing the Dutch regulator's licensing finding. A name search is not exhaustive proof about every possible legal entity, and the Dutch finding is not a Finland-specific judgment. Nevertheless, authorization for this user's account and products has not been established. [AFM warning, 24 September 2025](https://www.afm.nl/en/consumenten/actueel/2025/sep/mr-mexc-waarschuwing).

ESMA says unauthorized providers must wind down EU services after the transition ended on 1 July 2026, stop new EU onboarding, and restrict remaining services to orderly exit. Its narrow exclusive-client-initiative exception is not general authorization. Therefore MEXC should not be used for this live experiment on the evidence verified here. Any alternative needs verification of the actual contracting entity and permitted services; a spot authorization would not by itself establish derivatives permission. [ESMA statement, 23 June 2026](https://www.esma.europa.eu/sites/default/files/2026-06/ESMA75-113276571-1710_Public_Statement_MiCA_transitional_period_ends.pdf).

MEXC public market data could technically support local simulation. Its [API FAQ](https://www.mexc.com/mexc-api) states that there is no sandbox/test environment and that the API connects to live trading. Public [candles](https://www.mexc.com/api-docs/spot-v3/market-data-endpoints/klinecandlestick-data), [quotes](https://www.mexc.com/api-docs/spot-v3/market-data-endpoints/symbol-order-book-ticker), and [exchange information](https://www.mexc.com/api-docs/spot-v3/market-data-endpoints/exchange-information) are available. Pair minimums and precision must come from current exchange information; [fees](https://www.mexc.com/fee) vary. Technical API availability is separate from legal eligibility. No authenticated MEXC or TradingView Premium session has been verified or used.

The justified next research steps are finite:

1. Correct funding, cash allocation, overlapping positions, gap execution, and calendar equity metrics; reproduce results from cached data before downloading or paying for anything.
2. Freeze a fully specified hypothesis and a new forward test period. Log signals before outcomes, and simulate fills with observable quotes, venue rules, and conservative costs. The current squeeze strategy is a rejected baseline, not a selected profitable strategy.
3. Keep a euro-denominated ledger of equity, realised and unrealised P&L, fees, conversion, and separately identified study expenses. Do not assume a stop order guarantees a precise cash-loss ceiling.
4. At the end of the month report net results, uncertainty, drawdown, costs, and the number of independent opportunities. Insufficient evidence means insufficient evidence; it does not justify increasing leverage or changing rules to meet the target.

This assessment neither demonstrates a 10x opportunity nor promises one. Execution remains with the user; no full-access credentials are needed for the current research.
