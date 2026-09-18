# Exhaustion, reversal and exit levels — what we do not monitor

**Brainstorm, 2026-09-15. Nothing here is tested; every entry is a hypothesis with its data and a first test.**

The prompt, from the user: *"I just saw today trader timing almost exact tops and bottoms. He turned 25k to 100k in one day.
There clearly is something, something how it can be done, and something we do not monitor. We need to brainstorm this,
there must be more, something that indicates exhaustion and reversal or otherwise good level to exit the trade."*

## 1. Reading the observation

- **What a 4× day takes.** On a day with a 5 % BTC swing, turning $25k into $100k needs roughly 30–60× effective leverage
  on the right side of two or three moves. Or it needs an altcoin moving 20–50 %. The same sizing on a wrong call ends the
  account the same day. We see the day it worked; the days it didn't are not posted.
- **What "exact tops and bottoms" usually is.** Traders who look this precise put a tight invalidation just beyond a level
  where many other traders are forced to act (stops, liquidations, option hedges), and take a large multiple of that risk
  when it holds. That is risk placement at forced-flow levels more than prediction. It still points at where the information
  sits: **where forced flows are, and when they end.**
- **What one day cannot tell us.** Whether it repeats. A dated list of his calls (entries, exits, instrument), or a
  recording, would. Earlier traders' material turned out to be tool stacks, not one secret pattern (chento: eight data
  streams). Paladin's edge was in discretionary exits, not setups.

## 2. What we have already tested (so we do not repeat it)

| Family | Result | Resolution / data |
|---|---|---|
| Absorption (aggressive flow without price progress) | dead as entry (whale 15 m, C3 5 m, chento Rule 1); no exit information on chento; rarely occurs in short_squeeze trades | 1 m – 15 m bars, aggTrades buckets |
| Order-book imbalance | \|t\| < 2 as a predictor; no exit information (±1 %, z ≤ −3) | bookDepth ±1 % every 30 s |
| Rejection of prior extremes (sweep and fail) | no exit information on chento (24 h extreme); Paladin short fresh-high sweep-fade real but small | 1 m bars |
| Levels (FVG, LVN magnet, round numbers, order arrival at levels) | FVG and LVN magnet null; **placebo levels scored the same as real ones**; LVN *reversal* 61 % but cost-killed | 15 m, aggTrades |
| Footprint (price-level delta) | C3 sustained top-zone selling is a real *gross* signal, too small to pay entry costs | aggTrades footprints |
| Positioning (funding level, LSR, OKX delta) | funding-level contrarian rejected 6× upstream; LSR B5 variants killed; OKX gate retired | 1 h – 1 d |
| Liquidation-cascade reversal | rejected upstream (as an entry); our long flush → bounce works (squeeze_bull), short flush → *continuation* | hourly OI, CoinDesk liquidations |
| Top anatomy of squeeze_bull bounces | nothing real-time separates the final top from earlier highs; the extra return fades after 24 h without a new high | 1 m price, 5 m OI and ratios, premium, book |
| Time rules, session momentum, ORB, calendar cells | killed or inconclusive | many |

Two lessons carry into every new test:
- **Coarse flow has worked here and fine-grained flow has not** (bar CVD 3-for-3, trade-level flow 0-for-3).
- **Placebos decide:** a level that looks special must beat a matched fake level.

And one asymmetry helps: **an exit does not pay a round trip.** Signals killed as entries by costs can still be worth
something as exits.

## 3. What moves price at extremes

1. **Forced flows end.** Liquidations and margin calls push price until the forced positions are gone; the turn comes
   when they stop.
2. **Liquidity is targeted.** Price travels to where stops and liquidation prices cluster, takes them, and then has no
   fuel left.
3. **Dealers hedge options.** Long gamma near a big strike pins and mean-reverts price. Short gamma accelerates it.
   Expiry releases the pin.
4. **Passive size absorbs.** A large resting or refilled (iceberg) order stops a move at a level.
5. **Crowds flip.** Late participants pile in at the end of a move, and their positioning and funding show it.
6. **Leverage-led moves fade; spot-led moves persist.** A rally carried by perp buying with spot lagging is borrowed
   demand.
7. **Scheduled flows.** Macro releases, the US open, funding settlements and expiries change who is trading.

## 4. Candidate signals we do not monitor

Data key: **have** (on disk now), **buildable** (from data we have), **collect** (forward only, free), **paid**
(history must be bought).

| # | Signal | Mechanism | Use for exits | Data | Prior | Cheapest first test |
|---|---|---|---|---|---|---|
| A | **Actual liquidation prints**, aggregated across Binance, Bybit, OKX: a liquidation climax in the trade's favour | 1 | exit into a short-liquidation spike on a long (forced buying done), and the mirror | **collect**: Binance pushes only the largest liquidation per symbol per second; Bybit streams every liquidation every 500 ms. **paid** minute history (e.g. Tardis.dev). Free Coinalyze keeps only 1,500–2,000 intraday points. **have**: daily long/short liquidations 2022-02 → 2026-04 (`trader/data/trader.db` `ca_liquidations`), hourly 2026-02 → 06 in prod.db | mixed: upstream rejected cascade reversal as entry; our hourly short flushes continue | start the collector now; meanwhile a **proxy from archive data**: OI falling fast + price rising + taker buy burst at 1 m (2020+), tested at new highs inside trades like the top anatomy |
| B | **Estimated liquidation map** (what chento's "Leviathan liquidation levels" and heatmap products draw) | 2 | take profit at the nearest dense cluster in the trade's direction; expect the turn after it is swept | **buildable**: OI changes placed at the price they happened, split by taker side, projected to liquidation prices at common leverage. Uses 1-minute OI with price and taker volume 2022-06 → 2026-04 (`trader.db` `oi_minutes`), or the 5-minute archive 2020+ | untested; chento's #2 stream; level studies need placebos | build the map for BTC 2022–2026; do post-entry tops and intraday reversals land near clusters more often than at matched placebo levels? |
| C | **Hyperliquid large positions** (liquidation prices of big accounts are public on-chain) | 2, 5 | exit before price reaches a whale's liquidation level, or into its liquidation | **collect** (Hyperliquid public API; third-party trackers) | untested; a new venue since 2023 | collect snapshots of the largest accounts for a month, then pre-register |
| D | **Dealer gamma by strike, expiry timing** | 3 | take profit near large positive-gamma strikes; do not hold a pinned trade through expiry (Friday 08:00 UTC) | **have**: daily per-instrument option open interest and mark prices 2023-12-28 → 2026-04-13 (`trader.db` `cd_options_oi`, OI in its `settlement` columns; units to verify), with IV backed out from the marks. prod.db rows carry OI and IV only since 2026-09-06, a gap 04-13 → 09-06 | untested for levels (VRP and DVOL-state tests were different questions) | build daily gamma by strike for 2024–2026-04; test the largest-gamma strikes as intraday reversal/pin levels vs placebo levels |
| E | **Resting liquidity walls and pulls, iceberg refills** (Bookmap-style) | 4 | exit when a wall ahead holds and refills; stay when walls are pulled | **collect** (depth diff stream; storage-heavy); the ±1 % 30 s archive is too coarse | weak so far at coarse resolution | collect 10 bp price buckets once a second for a month (small), then pre-register |
| F | **Spot-led vs perp-led moves** | 6 | exit a long when a new high is carried by perp taker buying and a rising premium while spot buying lags | **buildable**: Binance spot 1 m klines archive (taker buy volume, 2020+), our perp panel and premium index | short_squeeze's spot/perp CVD divergence works at lows; Coinbase premium killed; chento Rule 1 dead | at the top anatomy's final tops vs earlier highs: spot minus perp taker imbalance and premium, same paired design |
| G | **Multi-venue tape** (what aggr.trade aggregates): CVD and volume across Bybit, OKX, Coinbase, Binance | 1, 6 | as F, when the move is driven off Binance | **buildable** from public trade archives (Bybit daily trade files; others to check) | untested | aggregated CVD at the same tops vs false tops |
| H | **Scheduled flows**: CPI, NFP, FOMC, US cash open 13:30 UTC, funding settlements 00/08/16 UTC, expiries, the US-close ETF window | 7 | leave before a release instead of holding a bounce into it; expect turns around the open | **have** (`scheduled_events`: CPI … quarterly OPEX; `macro_daily`) | killed as entry timing (calendar cells, NY open direction, ORB) | reversal and drawdown frequency inside trades around events vs matched non-event hours |
| I | **Move vs implied range**: move from the day's open (or the flush) ÷ the implied daily move from DVOL | 5 | exit once a bounce exceeds its implied daily range | **have** (DVOL daily since 2022-09, 1 m panel) | DVOL state signals rejected as entries | add "move ÷ implied move" bins to the top anatomy's state map |
| J | **Rejection-wick exit** (Paladin, automatable) | 2, 4 | book on a rejection wick at a level after ≥ 0.3 R profit | **have** | +0.12 R on his entries; never tried on our sleeves; beware its future-built levels (audit P0) | pre-registered overlay on squeeze_bull and chento entries with causal levels |
| K | **Retail vs top-trader divergence**: accounts shorting a rally while top traders hold (SJ-4250: accounts −20 %, top positions −5 %) | 5 | exit when the crowd has flipped to the trade's side, not when it fights it | **have** (metrics 2020+) | confounded with bounce age in the anatomy | match on price and time since entry, then compare tops vs false tops |
| L | **Footprint C3 and LVN reversal as exits** (killed as entries by cost) | 4, 2 | exit a long on sustained top-zone selling; target the HVN edge | **have** (aggTrades archive) | real gross signals; stage 1's 1 m absorption gave no exit information | exit-information test on squeeze_bull and chento entries |

## 5. Recommended order

1. **Ask about the trader.** Which tools were on his screen, which instrument, and the times of his entries and exits. That
   picks the stream to build first, as chento's screenshots did.
2. **Start collecting what cannot be bought back later**, with no trading change: A, C and a small E. The Deribit chains
   with OI are already being collected. Each needs a feed with a freshness contract, and that is production feed
   code, so it needs a go-ahead.
3. **Test with data already on disk**, each as an exit-information test with matched placebos and a holdout (ETH, or a
   second strategy), pre-registered:
   - **B**, the estimated liquidation map: the strongest mechanism, and what chento used.
   - **D**, gamma levels from the 2023-12 → 2026-04 option open interest.
   - **F**, spot- versus perp-led highs.
   - **A's proxy**, forced-flow highs from OI, price and taker flow.
   - **I** and **J**, which are cheap.
4. **Pay for history** (liquidations, L2, option chains) only if step 3 or the first weeks of collection show something.

## 6. What would count

- **Beating a matched placebo.** A level or state must beat matched placebo levels or moments. It must hold in both halves
  of the sample and replicate on a second asset or strategy.
- **Judged by continuation value.** An exit is judged by what the rest of the trade was worth, not by whether it would
  have caught one remembered trade.
- **One day is not evidence.** A dated list of the trader's calls, scored against price, is.
