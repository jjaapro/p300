# The simulated trading style, specified

Study 26. 2026-09-11. A cross-margin leverage experiment run on the two
strongest measured signals in the programme, plus a random control.

**What it settled:** leverage does almost nothing at this size, execution is
worth a 2.15x multiple on identical decisions, and - once sizing is made
comparable - **momentum beats buy-and-hold on both return and drawdown** while
absorption does not.

---

## 1. Account and position mechanics

| parameter | value |
|---|---|
| starting equity | $10,000, **cross margin** |
| margin per trade | $100 |
| leverage | 50x |
| **notional per trade** | **$5,000** |
| position sizing | **FIXED** — does not scale with equity |
| stop loss | **none set manually** |
| liquidation | account-level: `equity < 0.005 x total open notional` |
| fees | MEXC futures: maker 0.010%/side, taker 0.040%/side |
| funding | 0.01% per 8h on open notional |

**"No stop loss" at 50x is misleading.** In *isolated* margin the exchange holds
a stop for you — a ~2% adverse move wipes the margin. In *cross* margin the whole
balance backs the position, so liquidation is an account-level event and a single
trade survives a far larger move.

**The 50x is nearly cosmetic at this size.** $5,000 notional against $10,000
equity is **0.5x account leverage**. A 2% adverse move costs $100, one percent of
the account. The leverage figure only decides how much margin is earmarked, and
cross margin makes that bookkeeping.

What *would* be dangerous is concurrency:

| concurrent positions | notional | account leverage | wipes the account at |
|---|---|---|---|
| 1 | $5,000 | 0.5x | 200% move |
| 5 | $25,000 | 2.5x | 40% move |
| **10** | $50,000 | **5.0x** | **20% move** — BTC does this |
| **20** | $100,000 | **10x** | **10% move** — routinely |

Because trade size is fixed in dollars, effective account leverage **rises as
equity falls**. That is the ruin mechanism, not any single trade.

## 2. The signals, exactly

Both are pure time-exit. **No stops, no targets, no trailing, no filters.** The
position closes after a fixed number of bars and nothing else can close it.

### ABS — absorption (5m bars, 288-bar / 1-day rolling window)

```
imb = (2*taker_buy_base - volume) / volume      # aggressive buy/sell imbalance
enter when   z(volume)   > 1.0                  # heavy volume
        and  z(|imb|)    > 1.0                  # one-sided aggressive flow
        and  z(|return|) < 0.5                  # and price did NOT move
direction    -sign(imb)                         # fade the absorbed aggressor
hold         24 bars = 2 hours
```

Rationale: flow that *should* have moved price and did not implies a hidden
counterparty absorbing it. You join the absorber, not the visible aggressor.

Measured independently in [ABSORPTION_FINDINGS](ABSORPTION_FINDINGS.md):
**+4.8bp drift-adjusted, t=+2.91, episode-clustered t=+2.16, capture 6.7% of
sigma, 1,620 signals over 1,177 days (~502/yr).** The 2h hold is a measured
peak — capture rises to 6.70% then collapses to 1.56% by 4h.

### MOM — big-bar momentum (1d bars, 60-bar rolling window)

```
enter when   z( (high - low) / close ) > 1.0    # unusually large RANGE
direction    sign(close - open)                 # trade the bar's own direction
hold         7 bars = 1 week
```

Measured: **+193.5bp drift-adjusted, t=+3.04, clustered t=+3.99 on 20 clusters,
n=189, capture 21.4%.** The only cell in study 25 that clears its Bonferroni bar.

> **Definition error, found and corrected 2026-09-11.** The first version of the
> simulation used the **body** `|close - open|` instead of the **range**
> `high - low`. They are different signals — only **35% Jaccard overlap** — and
> the body version is materially weaker: +125.3bp at t=+1.96 versus +193.5bp at
> t=+3.04. The original sim result of $27,194 was produced by the weaker signal.
> Corrected to the range definition, the run gives **$22,885**. This is trap 12's
> cousin: a definition mutating between scripts without anyone noticing.

### RND — the control

Random entries, frequency-matched to each strategy, random direction, same
holds. This is what leverage and fees alone do with no signal.

## 3. Results

| strategy | final $ | maxDD | trades | liquidations | outcome |
|---|---|---|---|---|---|
| **ABS 2h, maker, conc<=3** | **$12,821** | −10.8% | 2,287 | 0 | survived |
| ABS 2h, maker, conc<=1 | $12,103 | −10.3% | 1,620 | 0 | survived |
| ABS 2h, taker, conc<=3 | $5,960 | −41.8% | 2,287 | 0 | bled |
| **MOM 1wk, maker (corrected)** | **$22,885** | −22.2% | 345 | 0 | survived |
| MOM 1wk, taker (corrected) | $21,852 | −23.1% | 345 | 0 | survived |
| RND 2h, maker | $6,592 | −35.2% | 2,375 | 0 | bled |
| **RND 2h, taker** | **$0** | **−100%** | 2,326 | 9 | **RUINED @ day 1,099** |

### Against buy-and-hold, FIXED sizing - and why this comparison was rigged

| | multiple | CAGR | maxDD | exposure |
|---|---|---|---|---|
| MOM 1wk maker | 2.29x | 10.0% | -22.2% | ~42% |
| buy & hold, same 8.8 yr | 4.90x | 19.8% | -83.2% | 100% |
| ABS 2h maker | 1.28x | 8.0% | -10.8% | ~12% |
| buy & hold, same 3.2 yr | 2.59x | 34.7% | -53.8% | 100% |

> **CORRECTED.** The conclusion originally drawn here - "both underperform
> holding" - was an artifact of the sizing rule, not a property of the
> strategies. The simulation kept a **fixed $100 margin / $5,000 notional for
> the entire run** while buy-and-hold compounded every dollar. That handicaps
> the strategy on the way up and over-leverages it on the way down.

### 3b. Compounding - position size scales with equity, as buy-and-hold does

"Risk 1%" means margin = 1% of **current** equity at 50x, i.e. notional = 50% of
equity, recomputed at every entry: at 50x a 2% adverse move on that position
costs exactly 1% of equity. Vol-targeting instead sizes so one sigma over the
actual hold costs 1% of equity, which equalises risk across horizons that differ
by 80x in length.

**MOMENTUM, 1d/1wk, 8.8 years**

| sizing | final $ | CAGR | maxDD |
|---|---|---|---|
| 50% of equity, maker | $28,148 | 12.5% | -37.0% |
| **100% of equity, maker** | **$50,499** | **20.3%** | **-63.0%** |
| 100% of equity, taker | $41,128 | 17.5% | -64.9% |
| vol-targeted (11% of equity), maker | $13,086 | 3.1% | **-9.0%** |
| **buy and hold** | $48,960 | 19.9% | -83.2% |

**At comparable sizing momentum beats buy-and-hold on both axes** - 20.3% vs
19.9% CAGR at -63% vs -83% drawdown, on ~42% time in market. It is also nearly
fee-insensitive: the taker version still returns 17.5%, because 345 trades over
8.8 years barely register the cost.

**ABSORPTION, 5m/2h, 3.2 years**

| sizing | final $ | CAGR | maxDD |
|---|---|---|---|
| 25% of equity, maker | $11,471 | 4.4% | -7.0% |
| 50% of equity, maker | $13,057 | 8.6% | -13.6% |
| 50% of equity, taker | $6,575 | -12.2% | -35.6% |
| vol-targeted (140% of equity), maker | $19,505 | 23.0% | -34.5% |
| vol-targeted (140% of equity), **taker** | $2,871 | **-32.1%** | -72.5% |
| random control, 50%, maker | $7,002 | -10.5% | -31.1% |
| **buy and hold** | **$25,931** | **34.4%** | -53.8% |

**Absorption still loses to holding at every sizing.** Its window (2023-06 to
2026-09) was a strong bull run, which flatters the benchmark - but that is the
honest comparison over available data.

Note what leverage does to it: raising notional from 50% to 140% of equity takes
the maker CAGR from 8.6% to 23.0% **and simultaneously** takes the taker version
from -12.2% to -32.1%. Leverage amplifies whichever sign you already have. It
does not create one.

**The signals still carry positive drift-adjusted edge** (+4.8bp and +193.5bp).
The compounding tables above are raw equity paths and include drift; the
drift-adjusted figures are the ones measured in study 25.

Note the distinction that is easy to lose: the *signals* carry positive
**drift-adjusted** edge (+4.8bp and +193.5bp, both measured with the
unconditional forward return subtracted). The *simulation* is raw equity and is
therefore dominated by drift. Both statements are true simultaneously.

## 4. The finding that does survive: execution is a 2x multiplier

Identical signals, identical entries and exits, **only the fee changes**:

| | maker | taker | ratio |
|---|---|---|---|
| ABS 2h, conc<=3 | $12,821 | $5,960 | **2.15x** |
| ABS 2h, conc<=10 | $12,355 | $5,071 | **2.44x** |
| RND 2h, conc<=3 | $6,592 | **$0** | **taker RUINED** |

Execution more than doubled the account on identical decisions, and on the
zero-edge control it was the difference between losing 34% and total ruin.

**The ruin was fees, not leverage.** The account that died paid
2,326 trades x $4 round trip = **$9,304 on a $10,000 account — 93% of it.** The
nine liquidations only occurred after costs had already consumed the equity.
Frequency x fee is the dominant term in the whole experiment, which is why the
1-week strategy barely notices the fee mode (345 trades) while the 2-hour one
lives or dies on it.

**But execution is a multiplier on edge, not a source of it.** RND at maker
fees — perfect execution, zero signal — still lost 34% of the account. Good
execution makes a worthless strategy lose more slowly. It does not make it win.

## 5. Incidental validation

The absorption run implies gross P&L of +$5,108 over 2,287 trades =
**+4.5bp per trade**, against **+4.8bp** measured independently in study 25 by a
different code path. A genuine cross-check of that number.

## 6. What would have to improve

Both signals are candidates precisely because they are the two strongest
measured results in 25 studies. What each needs:

**ABS** — the edge is +4.8bp against a 4.1bp maker-maker round trip whose own
uncertainty is 0.2–3.0bp per leg. It cannot be sized until queue position is
known. That is the two-week minimum-size quoting test in
[EXECUTION_FINDINGS](EXECUTION_FINDINGS.md), still unrun. Everything else is
downstream of it.

**MOM** - now the stronger candidate. At comparable sizing it beats
buy-and-hold on both return and drawdown and is nearly fee-insensitive. Two
cautions: it is the same daily-horizon momentum S-005 trades, and study 22 found
S-005 did *not* beat buy-and-hold - so the difference here is likely the SIZING
RULE rather than a different edge, which is itself worth knowing. And 345 trades
across ~20 volatility clusters on one asset's history is thin for a 20% CAGR
claim. Cross-exchange and cross-asset replication is the next test.

Neither has been tested with any management layer, and that is deliberate: an
overlay once turned a −0.197% signal into +0.238% and the overlay was what
failed out of sample.

## 7. Reproduce

```bash
python -m scalp_lab.run_leverage_sim     # fixed sizing
python -m scalp_lab.run_leverage_sim2    # compounding + vol-targeted
```
