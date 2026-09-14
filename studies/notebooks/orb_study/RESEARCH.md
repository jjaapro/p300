# Opening Range Breakout: research review

**Reviewed 2026-09-14. No new ORB backtests were run.**
This review distinguishes published claims, locally documented findings, freshly
inspected data endpoints and proposed experiments. It is a starting evidence map,
not a systematic review of every publication or a claim that ORB is profitable.

## 1. Define the strategy before comparing results

For a session starting at t0 and an opening interval of m minutes:

`H = max(high[t0:t0+m)); L = min(low[t0:t0+m)); W = H - L`.

The range becomes usable only after t0+m. A continuation strategy buys beyond H or
sells below L. A complete specification also needs a confirmation rule, order type,
entry deadline, stop, target/time exit, position size, maximum entries and calendar.

Example: the completed opening range is 100–102. A subsequent break above 102 is a
possible long signal. A close-confirmed version waits for a bar to finish above 102;
a resting-stop version can trigger intrabar. Entering at 101.8 simply because the
opening candle was green is a different opening-momentum rule. These differences
change both the hypothesis and the prices an order could receive.

| Name used in discussion | Distinguishing feature | Role in this campaign |
|---|---|---|
| High/low ORB | Break of a fixed, completed first-N-minute range | Main hypothesis |
| Confirmed-close ORB | Completed post-range bar closes beyond boundary | Auditable primary variant |
| Opening momentum | Enter in opening-candle direction without waiting for a break | Essential control |
| Threshold from open | Price moves a set distance from the opening price | Separate older ORB family |
| Range-break fade/retest | Fade a failed break, or wait for a specified revisit | Separate reversal/entry hypotheses |
| Rolling-channel breakout | Range continually advances through prior bars | Comparator, not opening-range evidence |

## 2. What the primary literature actually tested

### US equities: genuine range breakout with stock selection

Zarattini, Barbon and Aziz study 2016–2023 US equities, including historically listed
stocks. Their five-minute version places a stop entry in the opening candle's direction,
uses a stop distance of 10% of prior 14-day ATR, and exits by session end. The enhanced
strategy selects the top 20 opening relative-volume stocks after liquidity/price filters.
Table 2 reports Sharpe 0.48 for the base version and 2.81 for the enhanced version,
with a $0.0035/share commission assumption. These are authors' backtests, not p300
replications. The improvement bundles stock selection with ORB; it cannot be assigned
to the breakout alone or transferred directly to two cryptocurrencies.
[University-hosted original paper](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content).

### Nasdaq ETFs: a materially different entry under the ORB name

The author-hosted Nasdaq paper, version September 22, 2025, covers January 2016 to
February 17, 2023. It enters at the second five-minute candle's open, in the first
candle's direction; it does not wait for a subsequent range break. The opposite
opening extreme is the stop, with a 10R target or session-close exit. It assumes
$0.0005/share commission and no slippage. QQQ/TQQQ results are positive, but leverage,
tight-stop sizing and execution assumptions matter; minor text/table discrepancies
also warrant literal code/rule reconciliation. Treat this as an opening-momentum
comparator, not confirmation of the proposed entry.
[Author-hosted paper](https://concretumgroup.com/wp-content/uploads/2026/02/Can-Day-Trading-Really-Be-Profitable.pdf).

### QuantConnect: accessible implementation, limited evaluation window

QuantConnect recreates the equity approach with 1,000 liquid stocks and top-20
relative-volume selection. Its published evaluation covers **2016**, reporting a
2.396 Sharpe; it also explores range lengths and universe sizes. Useful as a reference
implementation, this is neither an eight-year replication nor proof of unseen-period
performance. Review code, not just article prose, when reconciling universes and fills.
[Original implementation article](https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/p1).

### Older futures evidence: definitions and hindsight matter

Lundstrom's volatility-state research uses thresholds measured from the opening
price, rather than a first-N-minute high/low. It studies crude and S&P futures over
roughly 1991–2011/2010 and finds results vary with volatility and costs. Some state
analysis uses the same day's completed return/range, which is explanatory information
unavailable at entry. That cannot become a causal volatility gate without a separate
lagged-feature test. Daily OHLC and continuous-contract adjustments also limit fill
interpretation. [University-hosted full text](https://www.diva-portal.org/smash/get/diva2:732318/FULLTEXT02).

Tsai and coauthors' timely ORB study uses one-minute data on five index-futures
markets, with market-dependent samples spanning portions of 2001–2013. Its boundaries
use one-minute closes, another distinction from high/low ORB. Positive reported
results use an assumed transaction cost and comparisons across observation windows.
Subperiod results do not substitute for freezing a chosen window before evaluating it.
[University record](https://scholars.lib.ntu.edu.tw/entities/publication/d69ecf33-892c-4f8a-9a88-2af1bcc4efcd)
and [accepted manuscript](https://www.researchgate.net/profile/Jia-Hao-Syu/publication/331076454_Assessing-the-Profitability-of-Timely-Opening-Range-Breakout-on-Index-Futures-Markets/links/6285e8a6247e622c2efb5839/Assessing-the-Profitability-of-Timely-Opening-Range-Breakout-on-Index-Futures-Markets.pdf).

### Direct crypto evidence located: useful hypothesis, weak verification

Secuora publishes its own negative BTC/ETH test for June 2025–June 2026, using a
30-minute NY range, five-minute confirmation, swing-based stop and 2R target. Its
method uses spot candles for both directions, signal-close fills, no spread/slippage,
and up to 10x modeled notional. Reported per-asset trade counts exceed calendar days,
so the entry/re-entry interpretation needs reconciliation with its first-break wording.
This is a vendor's reported experiment, not independent or source-code-verified
evidence for p300's one-trade rule. We do not use its result as a decision threshold.
[Vendor's original report](https://secuora.net/strategy/opening-range-breakout).

### Data quality can dominate the result

A later Concretum investigation reports large differences from running the same
opening strategy on different data vendors. It identifies isolated wicks, stale bars,
session/calendar leakage and tick-boundary assignment, and reports historical data
changing between downloads. Detailed independent reproducibility is not established
by the article. This motivates our own frozen snapshots and boundary-bar parity
checks. [Author investigation](https://concretumgroup.com/backtesting-data-quality-can-your-data-provider-be-trusted/).
Their [tutorial index](https://concretumgroup.com/python-matlab-backtesting-tutorials/)
also describes public notebooks as educational translations, not the exact internal
research infrastructure. A paper, tutorial and platform clone must be compared explicitly.

## 3. Hypotheses worth testing in crypto

The following are **inferences for experimental design**, not established BTC/ETH facts:

- Session-linked participation or news may produce continuation after a genuine break.
  Compare NY/London anchors with placebo clocks and opening-direction momentum.
- Unusually active openings may contain more information. Compute relative volume
  from the completed opening interval against prior comparable intervals, never full-day volume.
- A narrow range can signal compression but also create expensive, fragile stops.
  Separate price predictability from cost measured in R and from position-size effects.
- Waiting for confirmation may avoid transient breaks while entering later at worse
  prices. Measure the tradeoff directly with executable paths.
- Strong results may come from a few trend days. Report tail concentration and
  uncertain future frequency rather than assuming a high win rate is required.
- Perpetual positioning/funding may modify the behavior, but those features have
  different history lengths and availability times. Add them only after an unfiltered baseline.

Crypto has no daily cash-exchange opening. The plan uses NY's equity core open as
an external participation anchor, not a BTC exchange opening. NYSE's published core
session is 09:30–16:00 ET, with holidays and shortened sessions.
[NYSE calendar](https://www.nyse.com/trade/hours-calendars).
London's cash-market reference is 08:00 local time; use its actual trading calendar.
[LSE operating-hours statement](https://www.londonstockexchange.com/discover/news-and-insights/london-stock-exchange-launch-lse-24)
and [business days](https://www.londonstockexchange.com/trade/trading-access/business-days).
Local-time anchors move in UTC with DST; London and New York do not always switch on the same date.

## 4. What p300 already knows

The prior repository audit found no classic ORB implementation in current code,
51 notebooks/737 source cells, or the searched local history. That establishes no
recorded experiment was found, not that nothing was ever tried elsewhere.

| Related work | Existing finding | Implication for ORB |
|---|---|---|
| [Asia first-break fade](../range_sanity_2026_09/findings.md) | 00:00–07:00 range, trade against first break; negative gross expectancy | Useful reversal comparator; does not test continuation |
| [Daily consolidation breakout](../../material/brainstorming_session_sep11_2026/RANGE_FINDINGS.md) | Weak held-out magnitude; strong concentration in 2023 | Motivates era/concentration checks; different timescale |
| [Short squeeze](../../../bots/short_squeeze/strategy/README.md) | Session-gated six-hour low sweep plus positioning and reversal | Existing mechanism is not classic ORB |
| [Execution study](../execution_2026_09/README.md) | Per-leg cost analysis and candle-based fill approximations | Reusable methods; costs must be re-evaluated at ORB events |
| [Minute-data repair](../../../docs/strategy_issue_validation_2026_09_07.md) | 218,908 incorrect BTC minute rows replaced September 7; old research outputs not regenerated | Use corrected snapshots and fresh parity checks, not old cached outcomes |

## 5. Local data inventory

First/last timestamps were freshly inspected on 2026-09-14 with read-only SQLite
connections and bounded indexed queries. They establish stored endpoints only;
continuity, completeness and every row's source lineage have **not** been audited here.
Venue labels come from project documentation and fetcher source, not table names alone.

| Local table / instrument | Resolution | Stored UTC coverage at inspection | Planned use |
|---|---|---|---|
| `btc_1m`, `eth_1m` / Binance spot | 1m | 2020-01-01 to 2026-09-14 13:57 | Long price-signal panel and calendar engineering |
| `cd_futures_15m` / BTC perp | 15m | 2019-09-08 17:45 to 2026-09-14 13:45 | Coarse perp parity; aligned 15/30/60m ranges |
| `cd_futures_eth_15m` / ETH perp | 15m | 2021-01-01 to 2026-09-14 13:45 | ETH coarse parity |
| `screener_klines_1m` / BTCUSDT perp | 1m | 2026-02-25 11:28 to 2026-06-05 11:27 | Short engineering/parity sample |
| `screener_klines_5m` / BTCUSDT perp | 5m | 2026-02-25 11:30 to 2026-06-05 11:25 | Aggregation check |
| `cd_spot_5s` / BTC spot | 5s | 2025-06-08 to 2026-06-07 | Historical finer-path sensitivity, not perp execution proof |
| `coinbase_spot_1h` / BTC and ETH | 1h | 2020-01-01 to 2026-09-14 13:00 | Coarse cross-venue context; insufficient for short ranges |

No ETH/ETHUSDT rows were found in either screener fine-candle table. Long-history
perp minutes for both assets are the main acquisition gap. Even 15-minute ORB needs
finer bars for one-minute confirmation, timing and stops. A 15-minute candle cannot
reconstruct a five-minute opening range. The short BTC perp cache overlaps the proposed
historical lockbox, so use synthetic/development data for PnL debugging and keep any
lockbox cache check strictly to metadata/calendar/price-integrity checks before freeze.

Relevant provenance: [bulk perp fetcher](../../../data/sources/binance_klines_bulk.py),
[spot five-second builder](../../../data/sources/binance_klines_5s.py),
[known gaps](../../../data/known_unfillable.json).
The five-second panel is resampled historical spot data, not a current quote feed.
Minute spot tables have OHLC, base volume and trade counts; richer candle tables include
taker-volume fields, but the existence of columns does not establish complete observations.
The `binance_agg_trades_*` tables contain aggregates, not the raw chronological price
sequence needed to resolve ambiguous fills. [Aggregation source](../../../data/sources/binance_agg_trades.py).

Funding tables have long endpoints, but [their source documentation](../../../data/sources/venue_funding.py)
records an April 13, 2026 change from CoinDesk predicted hourly rates to Binance
settlements. Actual historical settlements need reconciliation or study-local acquisition;
forecast rates are not realized cash flows.

For later acquisition, Binance publishes spot and futures candles/trades with checksums.
Its spot archive changes timestamps to microseconds from January 2025. Downloaded data
can later be revised, so retain checksum and retrieval metadata.
[Official public-data repository](https://github.com/binance/binance-public-data).
Exchange filters provide tick/lot constraints; precision fields are not tick/step sizes.
Funding history includes settlement timestamps and associated mark prices; funding
interval adjustments also exist.
[Official derivatives market-data documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
No archive was downloaded and no authenticated account API was called for this review.

## 6. Reuse the validation toolkit carefully

[The study library](../../lib/validation/__init__.py) contains pure numeric helpers.
Use these with explicit units, aligned dates and independent small-case checks:

| Helper | Use and limitation |
|---|---|
| `gates.walk_forward_folds` | Calendar fold builder; does not fit models or enforce causal data access |
| `cpcv.cpcv_splits` / `cpcv_score` | Day-based partitioning; score function slices fixed returns, not fold-wise refitting; partitions can train after test dates |
| `bootstrap.circular_block_indices` | Reuse joint indices across assets/policies; trade-wise independent resampling would lose dependence |
| `bootstrap.block_bootstrap_sharpe` | Defaults to a 90%, not 95%, interval; drops nonfinite data, so validate completeness first |
| `benchmark.paired_block_boot_diff` | Paired contrasts; override quantiles to 0.025/0.5/0.975; share above zero is not a null-test p-value |
| `dsr_pbo.dsr_from_returns` | Per-observation Sharpe/full kurtosis; iid default and actual trial count need attention |
| `dsr_pbo.cscv_pbo` | Selects by mean return, truncates remainder, does not purge; not PBO of a Sharpe picker without adaptation |
| `spa.hansen_spa` | Use documented `p_value_c`; retained port discrepancy makes lower/upper variants identical |
| `stepm.stepm` | Joint multiple testing; explicitly set alpha 0.05 and final bootstrap count, overriding defaults; marginal SE is not HAC |
| `flat_max` | Descriptive parameter-surface shape, not a significance test |
| `metrics.max_drawdown` | Additive drawdown; use actual compounded marked-to-market NAV for percentage capital drawdown |
| `triple_barrier` | Daily-return implementation; unsuitable as the ORB intraday fill engine |

[GATE_VALIDATION.md](../../../GATE_VALIDATION.md) supplies useful conventions for
trial ledgers, chronological tests, benchmarks and execution. Its filter-promotion
thresholds are not general standalone-strategy thresholds, and its informal square-root
grid example is not a substitute for formal multiplicity correction.

Methodological references: [Bailey and Lopez de Prado, Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2460551_code87814.pdf?abstractid=2460551)
addresses selection and non-normality; [White, A Reality Check for Data Snooping](https://onlinelibrary.wiley.com/doi/10.1111/1468-0262.00152)
addresses evaluating a searched family. Neither repairs bad fills, hindsight features
or an already-inspected holdout. No statistical helper was executed during planning.

## 7. Implementation questions to settle before the first outcome run

1. Can a continuous, audited perp minute panel be obtained for both assets, including
   usable funding history and a fine-price subset for boundary events?
2. Which dated fee schedule and spread/latency observations define the primary cost model?
3. Does the implementation reproduce the proposed timestamp, entry, stop, gap and
   calendar conventions on independent fixtures?
4. Are every selectable policy/control, exact confirmatory claim and family size recorded?
5. Are final data/cost/config hashes saved and historical validation access boundaries enforced?

These are preparatory checks for a later implementation phase. The current deliverable
is the research review and [testing plan](TEST_PLAN.md), not an executed study or deployment proposal.
