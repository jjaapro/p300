# Review of the BTC Price-Action Playbook

Reviewed 2026-09-10 against the original playbook, related local research, the active TradingView chart, and the primary sources linked below.

**Verdict: retain the decision discipline, but revise the playbook before using it as an executable policy.** It contains useful lessons about precommitment, separating long-term holdings from tactical trades, and checking invalidation rules. However, several execution claims are mechanically wrong, flow interpretations overstate what the data reveals, and unverified research is presented as calibrated probability. The corrections in section 10 have not consistently reached the entry rules and checklists.

This review preserves the original file and chart. It proposes changes; it does not certify a profitable strategy or confirm any exchange order, fill, balance, or hedge. Your statement—**0.5 BTC held plus cash sufficient to buy roughly another 0.5 BTC**—takes precedence over allocations described in older planning documents. Horizon, loss tolerance, cash currency, core allocation, and tax circumstances remain unspecified. Treat this as decision support, not advice from a licensed financial adviser.

## What to keep

- Prewrite the reason for a position, its size, and its invalidation.
- Distinguish strategic BTC exposure from tactical trades; do not relabel a losing trade to avoid its exit.
- Review completed daily/weekly candles for the corresponding decisions and avoid reacting to every intrabar move.
- Keep a reserve, a decision journal, script backups, and explicit checks for fired exit rules.
- Retain the methodological lessons about circular reasoning, regime selection, and consistent comparison windows.
- Use price zones as reference areas. A zone can organize a decision without being a proven source of excess returns.
- Prefer surviving several outcomes to requiring one market forecast to be correct. **No action is a valid response in a scenario.**

## Changes that matter most

### 1. Correct the stop and hedge language before relying on it

**Original: section 9, lines 170–174; checklist line 212. Priority: critical.**

A sell stop-limit becomes a limit order after triggering. It can remain unfilled while price continues downward. A daily-close exit also cannot guarantee a fill at the close or at the invalidation level. Replace “bounded tail” and “bounded slippage” with a distinction between **planned exit loss**, **stressed execution loss**, and any **contractual hedge payoff**. A stop-market prioritizes execution but still does not guarantee price. See the [SEC order bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15) and [Deribit stop-limit explanation](https://support.deribit.com/hc/en-us/articles/25944761379613-Stop-Limit).

For fully funded spot, loss from BTC's price falling to zero is limited to the money invested. A stop does not guarantee the intended exit price or enforce the planned loss budget. “Unbounded” is therefore the wrong description of the asset-price loss or its possible slippage; the relevant problem is that execution can be materially worse than the plan assumes.

A matched protective put can establish a contractual expiry floor, subject to settlement and counterparty performance. It is not the only way to know a maximum asset-price loss: holding less fully funded spot also limits the amount at risk. A collar is not automatically free and does not guarantee a loss cap at every point before expiry.

Deribit inverse BTC options are European and settle in BTC. At settlement price S, a put pays max(K−S,0)/S BTC per BTC of contract exposure. Its USD-equivalent payoff is max(K−S,0). With matched remaining BTC quantity q, cash C, and separately funded net hedge cost A, the idealized expiry values are:

- Put: C − A + q × max(S, put strike).
- Collar: C − A + q × min(max(S, put strike), call strike).

These calculations assume matched quantities and settlement, payment as contracted, and no intervening liquidation. BTC-paid premiums can reduce the spot quantity and create a mismatch if the option quantity is not adjusted. The settlement benchmark also need not equal a later spot execution price. [Deribit inverse option specifications](https://support.deribit.com/hc/en-us/articles/31424939096093-Inverse-Options).

Short calls require collateral and margin management. BTC held elsewhere does not automatically support that margin, and automatic liquidation can prevent the intended expiry outcome. Put/collar implementation belongs in a separate, optional hedge policy after checking account type, collateral, quantity, expiry, fees, renewal cost, and liquidity. It should not be a default obligation for this spot portfolio. [Deribit margin types](https://support.deribit.com/hc/en-us/articles/25944811317149-Margin-types-and-usage), [liquidations](https://support.deribit.com/hc/en-us/articles/25944769313309-Liquidations).

### 2. Replace “spot-green required” with a more accurate interpretation

**Original: sections 1, 4, 5, 7, 10 and 11; lines 12, 58, 69, 78, 119, 195, 200, 211 and 216. Priority: high.**

The arithmetic is correct: delta = 2 × taker-buy base volume − total base volume. It measures the balance of aggressive buying versus aggressive selling **on the selected market**, not net ownership acquired by the market as a whole.

Positive delta on a falling bar is not proof of bullish absorption: passive sellers can absorb aggressive buyers. Heavy negative delta with price holding can be consistent with passive buyers absorbing market sells on that very exchange. Whole-bar totals do not establish where within the bar absorption occurred. [Binance spot kline schema](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market), [Bookmap absorption examples](https://bookmap.com/absorption/).

**Delete the deduction that negative visible flow plus stable price “means” an off-exchange buyer.** ETF/OTC activity is one hypothesis; passive exchange liquidity, incomplete venue coverage and cross-venue price discovery are alternatives.

Suggested replacement:

> Delta describes executed aggression. Assess it alongside the price response at a predefined zone and the completed-bar reclaim/hold rule. It cannot identify the buyer or seller. Until the exact filter is validated out of sample, delta is contextual evidence rather than a compulsory buy condition or a veto over an exit.

This resolves the document's contradiction: it calls delta a tie-breaker, describes negative-delta reversals, and then still makes positive delta mandatory.

### 3. Remove certainty about participant intent from OI and positioning

**Original: section 4, lines 60–64; section 7, lines 120–124. Priority: high.**

Each outstanding futures contract has a long and a short side. Increasing OI establishes expanding outstanding exposure, not new-long “conviction,” informed shorts, or stacked longs. Falling OI during a rally is consistent with covering, but does not establish that the rally must stall. Use competing explanations. [CME open-interest definition](https://www.cmegroup.com/trading/about-volume.html).

Rename the positioning fields to match the actual endpoints:

- **Top-trader position long/short ratio**: the selected Binance cohort, not all whales.
- **Global account long/short ratio**: Binance accounts, not a measured retail-only population.

A ratio can stay unchanged while both sides shrink. These series do not reveal spot sales, identity, external hedges, or informed intent. Remove “whales not selling,” “they bought the flush,” and “informed de-risking.” Current official endpoint documentation also specifies access requirements for the top-trader data; the appendix should handle authentication failures and unavailable data explicitly. This review did not test that endpoint's live availability. [Binance futures definitions and schemas](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).

Funding is a cost/basis observation, not a liquidation detector. A high reading does not establish that a cascade is in progress; negative funding does not prove spot-led demand. Record the actual interval, historical versus latest reading, and units: 0.0005 = 0.05%. [Binance funding mechanics](https://www.binance.com/en/support/faq/detail/360033525031).

### 4. Repair the data contract before using the dashboard for decisions

**Original: Appendix A, lines 230–250. Priority: high.**

The script joins the latest spot, perp, and OI arrays by position. That can associate different intervals and includes unfinished klines. It also prints OI levels rather than calculating the interval's OI change.

Required changes:

1. Establish one UTC cutoff and retain only closed candles for close-based rules.
2. Join spot and perp by their explicit interval timestamps.
3. Align OI snapshots to the same interval boundaries; request the extra observation needed for a change.
4. Preserve missing or stale data as **unavailable**, never zero or neutral.
5. Record symbol, venue, units, bar start/end, retrieval time, and provisional/closed status.
6. Retry and log API errors, rate limits, and schema/access changes. Persist source observations needed for later audit.

Kline and OI timestamp semantics are documented in the [spot](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market) and [futures](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) schemas.

Coinbase BTC/USD minus Binance BTC/USDT mixes quote currencies and sequential last trades. Prefer synchronized midquotes or bars, correct for USDT/USD, and report basis points:

`premium_bps = 10000 × (Coinbase_USD / (Binance_USDT × USDT_USD) − 1)`

Specify timestamp-skew and spread tolerances. Label any unadjusted proxy honestly. A positive spread does not identify a US institution. Coinbase's ticker exposes time and bid/ask fields. [Coinbase ticker documentation](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-ticker).

Replace fixed raw-flow cutoffs with a documented combination of raw BTC, delta/volume, and same-venue/timeframe historical percentiles. Hourly and 4H thresholds are not interchangeable. If taker buy/sell ratio r uses the same observations, normalized delta equals (r−1)/(r+1): counting both as separate confirmations duplicates evidence.

### 5. Downgrade unsupported probabilities and research statistics

**Original: sections 6, 8 and 10; lines 107, 130–146 and 186–189. Priority: high.**

The bottom ladder's 35–80% probabilities have no specified outcome horizon, event definition or calibration record. A cycle bottom is itself a retrospective label unless the event is operationalized. Replace these numbers with qualitative confidence, or mark them **subjective planning estimates** and define an outcome such as “the June low is not breached before [date].”

I could not reproduce the quoted delta correlation (r=0.104, n=379), FVG fill rates, sweep MAE, regime returns, or FOMC probabilities from the available research. The September session documents repeat some numbers but do not supply the underlying observations and scripts. The [session README](../btc_study/2026-09-08_session/README.md) describes recovered temporary scratchpad work; the referenced monitor/scratchpad is absent. The [June research dataset](HARNESS.md) is INDEX OHLC and cannot establish Binance taker-volume statistics.

**Unverified does not mean disproved.** Keep the research lessons; remove the precise figures from decision rules until their inputs and procedures can be recovered.

Specific methodological repairs:

- Overlapping five-day returns are not independent observations. Report an appropriate uncertainty estimate.
- Weak daily correlation does not establish an hourly-at-support trading edge.
- “First 90 days after a cycle bottom” is a hindsight regime unless the low was identified causally.
- FVG research needs touch/partial/full-fill definitions, a fixed horizon, sample counts, and treatment of gaps still open when the sample ends.
- MAE of all setups does not establish MAE of successful setups or whether their stop was hit before their target.
- A common flush-open anchor can help an event study, but a tradable backtest must enter only after the reclaim was knowable.
- Levels derived from the same price/volume series are not independent evidence merely because their indicator names differ.

There is relevant cautionary evidence in the repository: the [365-day scalp study](HARNESS.md) reports negative out-of-sample performance for its selected sweep configurations after removing hindsight and adding chronological testing. This is a **different 5-minute strategy**, so it does not disprove a 4H accumulation rule. It does mean “sweep-and-reclaim → fill” should not be treated as a generically demonstrated edge. I inspected its documentation/results; I did not rerun the full backtest.

### 6. Correct the September claim and qualify the macro/whale claims

**Original: lines 27, 108, 146 and 190. Priority: medium, but a useful reliability check.**

The local monthly OHLC gives **seven red Septembers out of ten in 2014–2023**, not eight or nine. Three consecutive green Septembers were 2023–2025; 2023 predates US spot-BTC ETP approval. Only 2024 and 2025 are completed post-approval Septembers as of this review. Two observations cannot calibrate a regime shift. The approval date was January 10, 2024. [Local monthly data](../btc_study/data/monthly.csv), [SEC approval statement](https://www.sec.gov/newsroom/speeches-statements/gensler-statement-spot-bitcoin-011023).

Two other facts in the document **are correct**: Kevin Warsh is Fed chair, and the September FOMC meeting is September 15–16, 2026. [Federal Reserve biography](https://www.federalreserve.gov/aboutthefed/bios/board/warsh.htm), [meeting calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm).

The claim that a hike is the market's base case needs a timestamped live probability distribution. I did not obtain the current probability table from the accessible [CME FedWatch page](https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html), so I have not endorsed a current percentage or base case.

Strategy's ledger does show the cited $80,318 purchase average, reported August 31, and the $63,957/$64,262 sale averages. That validates the historical prices, **not** an ongoing defense or resistance order at those prices. Preserve reporting date and covered purchase/sale period; do not assume a guaranteed Monday publication schedule or infer live buying from an old average. [Strategy ledger](https://www.strategy.com/ledger).

### 7. Clarify volatility, indicator and timeframe semantics

**Original: lines 66, 103–104, 118, 144, 251 and Appendix B. Priority: medium.**

- **DVOL units:** the approximate dollar move is `S × (DVOL/100) × sqrt(days/365)`. A DVOL reading of 45 means annualized volatility of 0.45; the approximate 30-day magnitude is 12.9% of spot. Its 30-day implied-volatility construction does not itself guarantee 68%/95% realized coverage, symmetry, or a tail bound. [Deribit DVOL methodology](https://insights.deribit.com/exchange-updates/dvol-deribit-implied-volatility-index/).
- **Option delta:** it is a price sensitivity, not a calibrated real-world probability. Even in basic Black–Scholes, put delta magnitude N(−d1) differs from risk-neutral expiry probability N(−d2). Neither is a strike-touch probability or cycle-bottom probability. Remove the unqualified “lower bound” comment. [Deribit Greeks convention](https://docs.deribit.com/api-reference/market-data/public-get_order_book).
- **200W SMA:** the present `lookahead_off` snippet can update during the open week. Label it developing. For a previous completed-week value on a lower-timeframe chart, use the documented offset pattern, `ta.sma(close,200)[1]` together with `lookahead_on`. Do not silently change semantics. A completed 200W SMA changes by (new weekly close − displaced weekly close)/200; $200–300/week is not a fixed rule. [TradingView repainting documentation](https://www.tradingview.com/pine-script-docs/concepts/repainting/).
- **VWAP:** these are periodically reset, bar-volume-weighted prices, not the average cost of current holders or a direct measure of institutional demand. They depend on source volume and chart resolution. The caution about INDEX volume must also apply to its VWAP and profiles. I have not independently validated the INDEX volume methodology. [TradingView VWAP](https://www.tradingview.com/support/solutions/43000502018-volume-weighted-average-price-vwap/).
- **Close definitions:** specify symbol, exchange/session timezone, and completed-bar timestamp. Display timezone alone does not establish Pine's session boundaries. [TradingView time semantics](https://www.tradingview.com/pine-script-docs/concepts/time/).
- **Structure:** a break of a selected lower high is evidence of improvement, not proof a full reversal is complete. Retire universal bottom claims about the 200W SMA unless precisely defined and verified.

### 8. Resolve rule conflicts and excessive rigidity

**Original: lines 9–13, 48, 89–90, 118–124, 142, 153–174 and 197. Priority: high.**

Use a clear precedence order:

1. Portfolio constraints and operational failure rules.
2. Prewritten risk exits and cancellations.
3. Entry eligibility and allocation caps.
4. Execution filters.
5. Market commentary.

Flow must not veto an already-fired exit. “Never sell weakness” should become “do not make impulsive discretionary sales; required risk exits still apply.” One versus two closes must be specified per rule rather than subject to a discretionary “full flow signature” exception.

Scheduled reviews can reduce noise, but “never reforecast on 4H” and “never rewrite branches mid-week” are too absolute. Allow documented exceptional reviews for genuinely new information, data corrections and failed assumptions. A decision may change sharply when material evidence changes; forced gradual probability updates can delay appropriate action.

Participation buying conflicts with “every buy must be at an edge.” If retained, it needs its own budget, suspension conditions and precedence under the total allocation cap. “Every branch must own something” should become “every branch must have an acceptable portfolio response.” Existing holdings can supply that exposure.

## What your current chart adds to the review

Observed around **06:48–06:50 UTC, September 10, 2026**. These are dated observations, not prices to use for a later order. Screenshot values below come from approximately 06:50 UTC; earlier API snapshots differ slightly as the market moved.

| Item | Observed value/state | Meaning for this review |
|---|---:|---|
| Active chart | INDEX:BTCUSD, 4H | Index reference, not an executable exchange quote |
| Price | About $78,487 | Below displayed weekly/monthly VWAP references at that moment |
| Weekly / monthly VWAP | About $78,808 / $79,096 | Contextual references; volume provenance matters |
| Developing 200W SMA | About $65,193 | Broad historical reference, not a guaranteed floor |
| 4H RSI | About 44.4 | Below its midpoint; no independent buy/sell instruction |
| MACD histogram | About −48.7 | Negative at this snapshot; not a separate independent regime vote |
| Current 4H candle | 04:00–08:00 UTC, unfinished | Its price and study values cannot satisfy a completed-4H rule |
| Studies | Seven registered, five with reported values | EMA and one SMA were hidden; do not invent missing values |

The chart suggests weaker short-term momentum within the displayed range, rather than a clean confirmed upside break. That is a limited interpretation of this snapshot, **not** a verdict on the cycle low or an instruction to sell the core.

The more useful finding is operational: its annotations are not a verified order register.

- “Hard stop-limit $71,800 … bounded tail” repeats the execution error.
- “Oct 30 $70k put … ~$860 premium” is an old estimate; it does not prove a hedge exists. The September 8 plan explicitly described insurance as not yet bought.
- The $73.4–74.5k event-bid box differs from the later plan's revised event zone.
- $81,273 remains described as first support/launch reference despite price being below it. Mark a historical trigger as historical; current support status requires a new observation.
- Several near-price labels overlap, making it difficult to identify which rule is active.

Use the chart for visual references and a separate ledger for actual orders and holdings. I made no changes to drawings, indicators, alerts, or positions.

[Captured chart](../btc_study/out/playbook_review_2026-09-10.png) · [Raw read-only observations](../btc_study/out/playbook_review_2026-09-10_snapshot.json)

## What to add for your 0.5 BTC plus cash

### Define the portfolio before optimizing entries

Let q be BTC held, S the reference price and C the cash allocated to this plan, in one reporting currency. Define allocation value E = qS + C and BTC weight w = qS/E.

If C is roughly the value of 0.5 BTC, your starting BTC weight is roughly 50% **within this BTC-and-cash allocation**, not necessarily within your overall wealth. Spending all the cash roughly doubles the BTC price exposure. Your existing 0.5 BTC already participates in a rally.

The arithmetic below assumes purchases at the same reference price, unchanged cash value, and no fees, tax, FX effects or subsequent trades. It is a stress illustration, not a proposed allocation.

| BTC held after hypothetical purchase | Initial BTC weight | Allocation loss if BTC falls 20% | Allocation loss if BTC falls 35% |
|---|---:|---:|---:|
| 0.50 BTC | 50% | 10% | 17.5% |
| 0.75 BTC | 75% | 15% | 26.25% |
| 1.00 BTC | 100% | 20% | 35% |

The “25% reserve” must name its denominator. At the same reference price, reserving 25% of the total allocation leaves about 0.25 BTC of additional buying capacity. Reserving 25% of the spare cash leaves about 0.375 BTC. These are materially different plans.

Add these explicit policy fields before approving sizes:

| Field | Required definition |
|---|---|
| Objective | Grow BTC quantity, preserve fiat value, or balance the two; specify horizon |
| Reporting basis | Actual cash currency; loss measured from cost, today's value, or peak |
| Core | Amount intentionally held long term and any exceptional exit conditions |
| Exposure limit | Maximum BTC weight and maximum committed cash, including pending orders |
| Reserve | Exact currency amount or percentage with a fixed denominator and review rule |
| Risk budget | Acceptable planned loss and stress loss for the whole allocation and tactical portion |
| Cash needs | Money unavailable for BTC because it is needed elsewhere |
| Instruments | Spot baseline; options only under a separately approved and understood hedge policy |

A useful stress check is w × assumed BTC drawdown ≤ tolerated allocation loss. This is scenario arithmetic, not a guarantee that BTC cannot fall further.

### Add one authoritative position and order ledger

For every tranche: ID; thesis/tactical classification; confirmed quantity held; cash committed; entry criterion; invalidation; decision timeframe; exact action; order type; order ID/status; quantity remaining after partial fills; cancellation rule; last verification time.

Distinguish **proposed, approved, submitted, acknowledged, partially filled, filled, cancelled, rejected, and expired**. A chart label or a statement in an old plan is not evidence of a live order. Sum all orders that could fill on the same price path before accepting a new one; do not count cash from a hoped-for exit as already available.

Add missed-check recovery, stale-data handling, alert delivery verification and re-entry rules after an exit. Alert delivery and exchange execution are separate events.

### Add an evidence register and a benchmark

For each statistical claim, retain raw observations, reproducible code, venue/timeframe, sample dates, event definition, forecast horizon, sample count, point-in-time regime definition, costs, chronological test results and uncertainty. Mark it verified, descriptive, hypothetical or retired.

Compare any active strategy against holding the current 0.5 BTC plus cash, a precommitted accumulation schedule, and a simple allocation/rebalancing rule over the same dates and capital flows. Evaluate drawdown, final fiat value, BTC quantity, turnover, time spent, and fees/tax effects. A convincing narrative or high win rate is not enough.

## Suggested replacement for the base instruction block

> Maintain an accurate BTC/cash and order ledger before producing a market opinion. Use the user's confirmed holdings and constraints, not inferred allocations from chart labels or old plans.
>
> Report triggered risk rules and unavailable/stale data first. Distinguish observations, interpretations and actions. A chart annotation, alert, draft order or proposed option hedge is not a confirmed holding or live order.
>
> Base each close-triggered decision on the specified venue, timeframe and completed candle. Treat developing indicator values as provisional. Align flow observations by time and units.
>
> Use delta, OI, funding and exchange price differences as contextual measurements. Do not infer trader identity or treat correlated indicators as independent confirmations. Do not require positive delta for every valid reversal.
>
> Apply portfolio limits and prewritten exits before entry filters. Do not delay a risk exit because of a preferred fill level or a contradictory narrative. Describe stop-based risk as planned and stressed loss, never a guaranteed cap.
>
> Maintain daily/weekly structure and a small set of active zones. Every scenario must have a tolerable response; that response may be holding the existing BTC or retaining cash.
>
> Label probabilities as subjective unless their outcome, horizon, evidence and calibration are recorded. Unverified historical statistics cannot justify larger sizes.
>
> Do not increase BTC exposure beyond the agreed budget. Scheduled buys also consume that budget. Keep reserve definitions explicit.
>
> Treat options as a separate strategy requiring contract, funding, collateral, settlement and expiry checks. Never infer protection from an option price line on the chart.
>
> Record material rule changes and why they were made. Do not rewrite rules to excuse an already-fired stop; allow prompt correction of factual errors or genuinely changed assumptions.

## Suggested document organization

Keep enduring rules and the compact checklist in the base playbook. Move dated levels, positions, scenario judgments and macro readings into a current campaign sheet. Move historical anecdotes and research outputs into an evidence log. Move version-specific MCP incidents, workarounds and monitor scripts into an operations guide.

This separation would make the playbook shorter and make it much harder for a stale price, broken-tool workaround, or unverified story to become a permanent investment rule.
