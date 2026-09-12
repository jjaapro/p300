# Risk-premium strategies: the only edges that survived

After six failed directional studies, this is the first result that clears
significance with the overlap correction applied.

## Why look here

A directional strategy has to predict. Everything screened at the 5-minute
horizon says direction is not predictable from price and volume. A
risk-premium strategy instead requires that somebody is structurally willing
to pay — leveraged longs paying funding, option buyers paying more than
realised vol. That payment is mechanically observable, not inferred.

## 1. The volatility premium — SIGNIFICANT

Deribit DVOL against the *subsequent* 7-day realised vol, 2025-08 to 2026-09.

| | n | mean spread | t |
|---|---|---|---|
| overlapping (wrong) | 787 | +2.34 vol pts | +5.73 |
| **non-overlapping (correct)** | **57** | **+3.27 vol pts** | **+2.15** |

Implied exceeded subsequent realised in **72% of weeks**. The inflation factor
from overlapping 7-day windows sampled daily was 3.7x — the same trap that
wrecked the pullback and barrier studies.

**Economics:** +3.27 vol points on a 1-week ATM straddle is roughly
**+0.20% of notional per week**, ~10%/yr gross before costs and slippage.

### The tail, which is the whole story

| p5 | p25 | median | p75 | p95 | worst |
|---|---|---|---|---|---|
| −27.3 | +0.4 | **+6.5** | +10.3 | +15.8 | **−32.3** |

- **mean +3.27 vs median +6.45** — negative skew, confirmed
- losing weeks: **14/57 (25%)**
- average loss **−13.0** vs average win **+8.6** — a **1.5x** ratio

You win most weeks by a moderate amount and lose occasionally by a large one.
Position sizing has to be built around the −32 vol point week, not the +6.5
median. Sold naked, this blows up eventually; the defined-risk expression
(spreads, iron condors) keeps the premium and caps the tail at a known number.

**No entry-level filter helps.** Splitting by DVOL at entry gives +3.45 /
+1.14 / +1.73 / +3.01 across quartiles — no monotonic relationship, so
"only sell when vol is high" is not supported.

## 2. Funding capture — real but small, and it is a hold

Long spot / short perp, delta ~0. 500 prints, 2026-03 to 2026-09.

- mean **+0.0031%/8h**, median +0.0038%, **76.6% of prints positive**
- cumulative +1.55% -> **+3.40% annualised** on notional
- rolling 30d annualised: median +4.1%, range −3.6% to +7.6%
- **20% of 30-day windows were negative**

**It is not a high-frequency trade.** Round-trip cost on the pair is ~0.20%
(spot + perp, both legs, in and out). At 3.4%/yr that is **21 days of carry to
pay for one round trip**. Rebalancing frequently destroys the entire edge.

### But timing by the current print does work

Funding autocorrelation at lag 1 is **+0.75** — highly persistent, so the
current print forecasts the next one:

| current funding | next print, annualised |
|---|---|
| −0.0123% to +0.0001% | **−2.2%** |
| +0.0002% to +0.0037% | +2.5% |
| +0.0038% to +0.0065% | +5.5% |
| **+0.0065% to +0.0100%** | **+7.7%** |

Holding the basis only while funding is in the upper half roughly doubles the
yield versus holding continuously, and avoids the negative windows. That is
a genuine, mechanical improvement requiring no forecast.

## 3. Frequency, honestly

| Strategy | Trades/yr | Edge | Character |
|---|---|---|---|
| user's existing low-freq | ~24 | — | directional |
| weekly vol selling | ~52 | +0.20%/wk gross | negative skew |
| funding carry, monthly rebalance | ~12 | +3-8%/yr | benign, small |

Neither is high-frequency in the market-making sense. They are *more* frequent
than 2x/month and, unlike everything else tested, they have measurable edge.

## What true HFT would require, and why it is not here

Genuine high-frequency edge in crypto lives in order-book microstructure:
queue position, imbalance, short-horizon adverse selection, cross-venue
latency. None of that is testable from public REST history — Binance does not
serve historical order-book snapshots, and the edge is measured in
milliseconds and basis points.

**The one untested domain worth opening:** order-book imbalance as a
short-horizon predictor. It cannot be backtested from history we can download,
but it can be *collected forward* — poll depth every few seconds, store it,
and in a month there is a dataset nothing in this repo currently has. That is
the only remaining place a genuinely high-frequency edge could be hiding, and
it starts with a collector, not a backtest.

## Reproduce

```bash
python -m scalp_lab.run_carry
```

---

# Session results, consolidated (2026-09-09)

## Volatility premium — 5.5 years, short ATM straddle to expiry

| Tenor | n | mean/trade | t | win | worst |
|---|---|---|---|---|---|
| 1 day | 1,995 | +0.514% | **+11.43** | 72% | −12.2% |
| 7 day | 285 | +1.264% | **+3.90** | 71% | −23.2% |
| 14 day | 142 | +1.623% | +2.62 | 68% | −22.8% |
| 30 day | 66 | +1.036% | +0.65 | 64% | −44.0% |

Positive in every calendar year at the 7-day tenor, **but decaying hard**:

```
2021 +2.389%   2022 +2.200%   2023 +0.861%
2024 +0.565%   2025 +1.375%   2026 +0.028%
```

2026 is effectively zero. The mechanism is real; the payment has been competed
away. Short tenors dominate — at 30 days the tail eats the premium.

Caveat on the defined-risk table above: capping the loss raises the simulated
mean because it truncates the tail *without paying for the wing*. A real
spread earns roughly 60-70% of the naked premium. Historical honest estimate
+0.8-0.9%/week; at 2026 levels, far less.

## Order flow (aggTrades, 90 days, 129,579 non-overlapping samples)

| Feature | 1min | 5min | 15min | 60min |
|---|---|---|---|---|
| ofi1 | **−2.41** | −1.54 | −0.56 | +0.60 |
| ofi5 | **−3.05** | **−2.48** | −1.07 | +0.36 |
| big5 | −1.38 | **−2.71** | −0.97 | +0.34 |
| tint5 | **+2.26** | **+2.21** | **+2.33** | +0.61 |

Order flow is **contrarian** at short horizons: aggressive buying is followed
by price falling. Trade intensity is positive and consistent across three
horizons. Only ofi5@1min clears Bonferroni (t=3.0 for 20 tests).

**The 12-day version of this test showed t=−2.37/−2.09/−2.97 at 60 minutes.
All three flipped or died at 90 days.** Extending the sample worked exactly as
intended.

Magnitudes: 0.14bp (ofi5 1min), 0.50bp (ofi5 5min), 1.8bp (tint5 15min),
against 5-10bp round-trip fees. **This is the market-making edge, correctly
located and correctly sized — and it is the same size as the fees required to
capture it.** Not accessible without maker rebates and co-location.

## Scoreboard

| Study | Result |
|---|---|
| sweep-and-reclaim | negative out-of-sample, both directions |
| dip-buy by drawdown depth | symmetric excursions, no edge |
| equity lead-lag | no lead; BTC slightly leads equities |
| NY open direction | nothing after multiple-comparison correction |
| 22-feature direction screen | nothing after overlap correction |
| book imbalance (bookDepth, 173k snapshots) | t < 2 at every horizon |
| order flow (aggTrades, 90d) | real but 0.5bp — the size of the fee |
| **volatility premium** | **real, every year, decaying to ~0 in 2026** |
| **funding carry** | **+3.4%/yr, +7.7% timed on the top quartile** |

Eight directional/microstructure studies, zero accessible edges. Two
risk-premium studies, two edges. The pattern is the finding: in this market,
at this account size, you are paid for providing (liquidity, insurance, carry)
and not for predicting.
