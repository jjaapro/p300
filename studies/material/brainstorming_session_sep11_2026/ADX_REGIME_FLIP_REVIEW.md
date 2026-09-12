# ADX Regime Flip review — September 10, 2026

## Verdict

The active S-003 ADX Regime Flip (SL-10) v2 has a coherent daily trend-following rationale and an encouraging historical trade record. This review does not establish an independently validated future trading edge. Two implementation issues, incomplete execution assumptions and unverified research claims limit reliance on its headline results. Its continuing long regime is compatible with a short-term retracement.

No Pine source, strategy settings, alerts or exchange orders were changed. Read-only chart access and local evidence copies were used. TradingView's tab-switch command activated a tab without rebinding subsequent reads; the second chart was therefore read directly through its existing local CDP connection.

## Exact strategy inspected

- TradingView layout: Validation, chart `yGVRmVFW`, INDEX:BTCUSD, ordinary daily candles.
- Study: `8wnRXp`, S-003 ADX Regime Flip (SL-10) v2.
- Running saved revision 6.0; editor revision 10.0. Both source revisions were fetched from the user's saved script API and were identical after trimming terminal whitespace. The editor source also matches the local `adx_v2.pine` after whitespace normalization.
- Pine language version 5; saved-script revision numbers are separate from the language version and strategy's v2 title.
- Inputs: ADX 14; entry 25 / exit 20; direction EMA50; entry trend filter EMA150; 10% stop; shorts enabled.
- Actual Properties: 100% equity per position, $1,000,000 initial capital, 0.05% commission per side, zero slippage, same-close order processing, calculation on every tick off, recalculation after fills off, bar magnifier off. Margin requirements are set to zero. Zero margin here does not itself mean the strategy uses leverage: configured order sizing is 100% of equity, but margin/financing constraints are not modeled.

## Logic and current position

After ADX falls below 20, the strategy arms one entry attempt. When ADX subsequently reaches 25, direction is selected from close relative to EMA50 and must agree with EMA150. A long is therefore opened above both EMAs. The attempt is consumed even when blocked by the trend filter. ADX subsequently falling below 20 closes the position; the 10% stop is a separate exit. The EMA direction/filter conditions gate entry and do not create ongoing EMA-cross exits.

The live report contains one open simulated long at **$77,088.42**, on the daily bar dated **August 22, 2026**. The user acknowledged that the initially recalled $78,300 might be incorrect. This is a strategy simulation, not evidence of an exchange purchase. Its nominal 10% stop is **$69,379.58**; that is a model parameter, not an approved stop for the user's holdings or a guaranteed execution price.

An independent calculation on the 300 loaded daily candles put ADX at about 22.02 on August 21, 25.58 on August 22 and 47.41 on the latest completed September 9 bar. This supports the entry timing and continuing trend-strength reading. ADX's warmup effect is negligible at the recent end of this sample; the separately computed EMA150 retains visible initialization error against the full-history study and must not replace the chart's value. Today's forming daily bar was excluded from confirmed decisions.

The existing four-hour reference chart separately closed its04:00–08:00UTC candle at **$78,114.58**. That observation does not change the daily strategy rules.

## What the current backtest actually reports

The reported historical range begins in October 2009; the first trade is January 2011. The current report through September 10 includes 49 closed trades and one open trade.

| Metric | Reported value |
|---|---:|
| Closed trades |49|
| Winning closed trades |28 /49, or57.14%|
| Profit factor |4.768|
| Maximum strategy drawdown |31.92%|
| Average closed-trade duration |46.37 daily bars|
| Closed long trades |31, with 19 winners|
| Closed short trades |18, with 9 winners|

These are outputs under the stated historical settings, not certified executable results. The million-dollar starting balance implies buying approximately 3.12 million BTC at the first $0.32 entry, and later compounding produces enormous hypothetical quantities. The test does not model historical market capacity. Dollar-weighted profit factors and profits also depend on that compounding path. Early-index performance is not an adequate modern execution benchmark.

The extracted ledger also supports a more recent descriptive check: nine closed trades entered from 2024 onward, six profitable, averaging 38.9 days. Of those, five are long trades with three winners. These entry-date cohorts exclude positions opened before the cutoff and are not fresh out-of-sample tests. See the separate actual-trade diagnostic for definitions and broader cohorts.

## Material issues

1. **Stop creation is delayed after entry.** Source lines 114–121 only submit `strategy.exit` if the current position is already nonzero. With same-close entry processing and no post-fill recalculation, a newly entered position is first seen at the next scheduled daily calculation. Its stop can therefore be absent throughout the first subsequent day. Enabling bar magnification alone does not create an order that the script has not submitted. Correcting this needs careful event-ordering verification, not a blind settings toggle. TradingView documents [post-fill calculations and same-close processing](https://www.tradingview.com/pine-script-docs/concepts/strategies/).

2. **The background can remain LONG after a stop fill.** `cur_dir` is updated by regime logic but never reconciled against a stop-filled flat position. The background uses this variable. This can misrepresent an open position even though the strategy report is flat. Intended regime and actual simulated position should be displayed separately; changing visual state need not change the rule requiring a new compression cycle before re-entry.

3. **Execution and portfolio assumptions differ from the user's task.** Long-and-short, 100%-equity compounding with zero slippage is not the performance of 0.5 BTC plus cash managed long-only. A spot-only implementation should be rerun with shorts disabled, appropriate exchange data, realistic trading costs and an explicit cash/exposure policy. The report's existing Long column is not automatically equivalent to that rerun, because trade sizes reflect the combined strategy's prior equity path. Same-close fills also need comparison with achievable next-tick execution.

4. **Research claims remain unverified.** The referenced `backtest_adx_regime.py`, P-200/Fundamental-Law audit, bootstrap IR confidence interval and exact in/out-of-sample split artifacts were not located in this repository. The source's EMA150 optimization claim cannot be certified from comments alone. Later v3/unified revisions use periods previously called out-of-sample for additional selection, so their results do not establish untouched validation for those revisions. None of this proves v2 has no edge; it limits the strength of the evidence.

5. **No obvious future-data lookahead was found.** The reviewed source uses causal ADX/EMA calculations, no higher-timeframe requests and no pivot backdating. Under its current close-only settings, the important concerns are state/fill handling and statistical validation. A clean source audit alone does not demonstrate profitability.

## Role in the BTC playbook

Use this as evidence about the daily trend regime, with the weekly EMA5/21 signal providing a separate horizon. Their price-derived information overlaps; do not multiply their historical success rates as if they were independent probabilities. ADX measures trend strength, while this strategy's EMAs choose direction. See [TradingView's ADX explanation](https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/).

Its multiday holding periods and 10% nominal stop show that it is built to tolerate price movement much larger than the recent $78.3k-to-$78k fluctuation. A current long does not establish support at the entry price, a favorable immediate entry, or protection from FOMC volatility. Any existing tranche-specific risk exit continues to take precedence; do not retroactively relabel a tactical holding to inherit this strategy's wider stop.

Before using it to govern real allocation, fix the stop/state issues, compare a spot-only version with holding the same starting BTC and cash, test realistic execution assumptions, and reserve an untouched forward period. Preserve the current baseline so improvements can be assessed honestly.

## Evidence

- [Editor source snapshot](../btc_study/out/adx_live_source_2026-09-10.pine)
- [Running revision6 source](../btc_study/out/adx_running_revision6_2026-09-10.pine)
- [Saved source revisions and metadata](../btc_study/out/adx_live_revisions_2026-09-10.json)
- [Live chart and strategy state](../btc_study/out/adx_chart_state_2026-09-10.json)
- [Backtest, trade ledger and settings](../btc_study/out/adx_live_strategy_2026-09-10.json)
- [Property-name mapping](../btc_study/out/adx_live_metadata_2026-09-10.json)
- [Independent recent ADX calculation](../btc_study/out/adx_current_calculation_2026-09-10.json)
- [Modern actual-trade diagnostic](ADX_TRADE_DIAGNOSTIC.md)
