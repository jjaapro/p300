# Strategy spec: short-dated BTC volatility premium

**Status as of 2026-09-10: NO-GO on live pricing. But the premise of
"the premium is decaying" was WRONG -- see the correction below. The
strategy is dormant, not dead.**
The viability gate in §4 fails on today's chain. This document specifies the
strategy, the gate, and what the main project needs to implement — so that it
can be run automatically and will fire when conditions return.

---

## 1. The edge

Options are priced off *implied* volatility. What subsequently happens is
*realised* volatility. Implied is systematically higher, because:

1. **Insurance demand is one-directional.** Far more participants want to buy
   protection than sell it, so protection prices above fair value.
2. **The seller's payoff is badly shaped** — small steady gains, occasional
   large losses. Most capital does not want that profile and demands
   compensation to hold it. The premium *is* that compensation.
3. **Dealers charge for gamma.** A short-gamma book must hedge continuously
   and prices in the expected hedging cost, which on average exceeds what
   they actually spend.

This is a risk premium, not a forecast. You are paid for providing something,
which is why it survived when ten directional studies did not.

## 2. Evidence

Short ATM straddle, held to expiry, non-overlapping, 2021-03 to 2026-09
(5.5 years, Deribit DVOL + Binance spot):

| Tenor | n | mean/trade | t | win | worst |
|---|---|---|---|---|---|
| 1 day | 1,995 | +0.514% | +11.43 | 72% | −12.2% |
| 7 day | 285 | +1.264% | +3.90 | 71% | −23.2% |
| 14 day | 142 | +1.623% | +2.62 | 68% | −22.8% |
| 30 day | 66 | +1.036% | +0.65 | 64% | −44.0% |

By year, 7-day tenor: 2021 +2.389%, 2022 +2.200%, 2023 +0.861%,
2024 +0.565%, 2025 +1.375%, 2026 +0.028%.

### CORRECTION (2026-09-10): there is NO significant decay

An earlier version of this document said the premium had "decayed by two
orders of magnitude" and been "competed away". That was reading a 35-week
sample as a trend. Tested properly on non-overlapping weekly straddles,
P&L expressed as a fraction of premium collected:

| year | n | mean | median | win% | worst |
|---|---|---|---|---|---|
| 2021 | 41 | 0.211 | 0.489 | 71% | -2.30 |
| 2022 | 52 | 0.228 | 0.529 | 75% | -2.78 |
| 2023 | 52 | 0.114 | 0.428 | 71% | -3.96 |
| 2024 | 52 | 0.061 | 0.278 | 67% | -2.63 |
| 2025 | 53 | **0.247** | 0.414 | 72% | -1.37 |
| 2026 | 35 | -0.048 | 0.232 | 69% | -3.29 |

```
2026 vs 2021-2025: difference -0.219, t = -1.26   NOT SIGNIFICANT
95% CI on 2026's true mean: -0.379 to +0.283
linear trend across six years: slope -0.037/yr, t = -1.50   NO TREND
n needed to detect a drop this size at t=2: 56 weeks (1.1 years)
```

2025 was the BEST year in the sample. A decay story cannot explain that.

**2026 is three weeks.** Excluding the worst one: +0.047. The worst two:
+0.128. The worst three: +0.210 -- back to 2021 levels. The three worst are
-3.29, -2.63, -2.47 in premium units, out of 35 weeks. Win rate is unchanged
at 69%, mid-range for the sample.

### What the decomposition DID establish

| year | IV | RV(7d) | IV/RV | P&L |
|---|---|---|---|---|
| 2021 | 91.1 | 66.6 | 1.517 | +2.407% |
| 2025 | 46.0 | 35.2 | 1.559 | +1.116% |
| 2026 | 43.4 | 37.6 | 1.390 | +0.187% |

**The IV/RV ratio is intact** (92% of 2021) while **the IV level halved**
(47.6% of 2021). The premium pays the same per unit of vol sold; there is
simply half as much vol to sell. The strategy is DORMANT, not dead, and it
scales back up with the vol level.

### The harder problem: wings cost more than the cap recovers

Truncating the loss recovers 2026 from -0.048 to +0.115 (cap at 1x premium)
or +0.234 (cap at 0.25x). But those are GROSS of the wing cost, and the wing
was priced live: a Sep-13 butterfly collected $1,276 against a $2,244 naked
straddle -- **the wings cost 43% of the premium.**

Applying that: 2026 defined-risk lands near -0.32 to -0.20 in premium units,
and even 2025 at cap -0.5x gives 0.314 - 0.43 = -0.12. **At current wing
pricing, defined risk does not rescue any year in the sample.**

This is trap #5 in its sharpest form and it tightens the conclusion: the
strategy needs BOTH a higher vol level AND cheaper wings. Wing cost is a
function of skew steepness, not of vol level, so the two do not necessarily
arrive together. The gate in section 4 tests the combination directly, which
is why it is the right mechanism.

### Two corrections that must not be lost

**(a) Term-structure error.** The backtest priced every tenor off DVOL, which
is a *30-day* implied vol index. Measured live on 2026-09-09:

| Expiry | days | ATM IV | vs DVOL |
|---|---|---|---|
| 2026-09-10 | 0.6 | 31.38 | **0.78x** |
| 2026-09-11 | 1.6 | 36.41 | 0.91x |
| 2026-09-18 | 8.6 | 40.51 | 1.01x |
| 2026-09-25 | 15.6 | 38.65 | 0.96x |
| 2026-10-30 | 50.6 | 38.04 | 0.95x |

At 7+ days DVOL is a fair proxy (0.95-1.01x). **At 1 day it is not** — short
tenors trade ~0.78x DVOL in calm conditions, so the 1-day backtest overstated
premium by ~0.31% of spot per trade. Corrected, the 1-day edge is ~+0.20%, not
+0.51%, and 2026's +0.151% becomes roughly **−0.16%**.

**Do not use DVOL to price anything under ~5 days.** Use the actual chain.

**(b) Defined-risk figures in earlier notes are optimistic.** Capping the loss
in simulation truncates the tail *without paying for the wing*. A real spread
costs 30-40% of the ATM premium. Multiply any capped backtest figure by
~0.6-0.7.

## 3. The structure

**Short iron butterfly** on the nearest expiry ≥ 5 days.

```
SELL  1x ATM call
SELL  1x ATM put
BUY   1x call at ATM + W
BUY   1x put  at ATM − W
```

- ATM = strike nearest spot at entry
- W = wing width, chosen so max loss lands inside the risk budget (§6)
- Max loss = W − net credit, known before entry
- Breakevens = ATM ± net credit
- Hold to expiry. No delta hedging, no early management.

Naked straddles are **not** authorised: the 5.5-year worst case is −23% of
notional at 7 days and −44% at 30 days.

## 4. THE VIABILITY GATE — run before every trade

This is the core logic and the thing the main project most needs. The
strategy trades **only** when live pricing beats the historical move
distribution by a margin.

```
1. Fetch the live chain for the target expiry. Compute:
      credit    = (ATM call bid + ATM put bid) − (wing call ask + wing put ask)
      max_loss  = W − credit
      breakeven = credit / spot          (as a fraction)
      need_win  = max_loss / (credit + max_loss)

2. From >= 1000 days of BTC daily closes, compute the empirical
   distribution of |return| over exactly H days, where H = days to expiry.
      hist_win = P(|H-day move| < breakeven)

3. margin = hist_win − need_win

4. TRADE ONLY IF margin >= +5 percentage points.
   Otherwise stand down and log the no-go.
```

Use **bid** for legs you sell and **ask** for legs you buy. Pricing at mid
manufactures an edge that does not exist at execution.

The +5pp buffer covers: fees, slippage, the empirical distribution being a
finite sample, and vol regimes shifting mid-trade.

### Today's gate output (2026-09-09) — why it is a NO-GO

| Structure | breakeven | need_win | hist_win | margin |
|---|---|---|---|---|
| 2d naked straddle | ±1.80% | 50% | 46.1% | **−3.9pp** |
| 4d naked straddle | ±2.85% | 50% | 49.0% | **−1.0pp** |
| 4d butterfly ±2.5% | ±1.62% | 36% | 32.0% | **−4.3pp** |
| 1d butterfly ±2.5% | ±0.88% | 65% | 36.4% | **−28.6pp** |

Every candidate fails. Implied vol is currently too low relative to how much
BTC actually moves. This is the decay in §2 showing up in real prices.

## 5. Entry timing

- Evaluate once daily at a fixed time (suggest 07:30 UTC, before the 08:00
  Deribit expiry/roll).
- Target the nearest expiry **≥ 5 days out** — avoids the term-structure trap
  and the worst gamma.
- One position at a time. No pyramiding, no averaging.
- If the gate passes on multiple expiries, take the one with the largest
  margin, not the largest credit.

## 6. Sizing

Risk budget: **max loss on any single position ≤ 1.5% of stack.**

```
contracts = floor( (0.015 * stack_usd) / max_loss_per_contract )
```

For a 0.95 BTC stack (~$74,500): max loss budget ≈ $1,120/position.
At the live 4-day butterfly (max loss $724/contract) that is 1 contract.

Portfolio caps:
- ≤ 1 open position
- ≤ 6% of stack cumulative max-loss exposure across all open option positions,
  **including** any protective puts held by the main book
- Premium funded from deployable powder, never the reserve

**Note the interaction with the main book:** the plan there buys protection
(long Oct-30 70k put). This strategy sells it. Running both is not
contradictory — one is insurance on a spot position, the other is a premium
harvest — but the *net* vega must be tracked, and the combined max loss must
respect the 6% cap.

## 7. Exit

Hold to expiry and settle. No profit target, no stop.

Rationale: the edge is the full premium-minus-realised difference. Managing
early gives up premium while keeping the tail. The tail is already capped by
the wings, which is why the structure is defined-risk rather than naked.

The single exception: if the exchange, the position, or the account is at
operational risk, close it. That is an operations decision, not a trading one.

## 8. What the main project needs (implementation checklist)

**Data**
- [ ] Deribit public API: `get_instruments`, `get_book_summary_by_currency`,
      `get_index_price`, `ticker`. No auth needed for pricing.
- [ ] Live bid/ask per leg (not just mark) — the gate depends on it
- [ ] ≥1000 days of BTC daily closes for the empirical move distribution
- [ ] DVOL optional, for context only — **not** for pricing under 5 days

**Logic**
- [ ] Expiry selection (nearest ≥ 5 days)
- [ ] ATM strike selection and wing-width search
- [ ] The §4 gate, including the empirical H-day move distribution
- [ ] Sizing from the risk budget
- [ ] Net-vega tracking against the main book's long puts

**Execution**
- [ ] Deribit authenticated order placement, 4 legs
- [ ] Leg-in protection: if any leg fails to fill, unwind the filled legs
      rather than holding a naked short
- [ ] Settlement capture at expiry

**Logging (§9)**

## 9. What to log — this is how it gets tuned

One row per *evaluation*, not per trade. The no-go rows are the data that
tells you whether the gate is calibrated.

```
timestamp, spot, expiry, days_to_expiry, atm_strike, wing_width,
credit, max_loss, breakeven_pct, need_win, hist_win, margin,
gate_passed, reason_if_not,
[if traded] contracts, fill_credit, slippage_vs_quote,
[at expiry] settle_price, realised_move_pct, pnl_usd, pnl_pct_of_stack
```

Derived metrics to track continuously:
- running mean P&L per trade and its **t-statistic**
- realised win rate vs `hist_win` predicted at entry — *this is the key
  calibration check.* If realised consistently undershoots predicted, the
  empirical distribution is stale and the gate needs a wider buffer.
- slippage: `fill_credit` vs quoted `credit`. If this exceeds ~10% of credit,
  execution is eating the edge.
- gate pass rate. If it is near zero for months, the premium has not returned.

## 10. Validation protocol

Paper-trade first. Sample size needed to distinguish the edge from zero at
t = 2:

| edge/trade | sd/trade | n | weeks at 1/wk |
|---|---|---|---|
| +0.50% | 2.0% | 64 | 64 |
| +0.30% | 1.0% | 44 | 44 |
| +0.20% | 1.0% | 100 | 100 |

At ≥5 days per position this is roughly one trade per week, so **expect
40-100 weeks to validate.** That is the honest cost of a small edge at low
frequency, and it is the main argument against expecting quick confirmation.

Two ways to shorten it, both with caveats:
- Run the gate on multiple expiries simultaneously — but positions overlap,
  so the effective sample grows more slowly than the trade count.
- Include the no-go evaluations as counterfactuals — cheap, and it tests the
  gate itself even when not trading.

## 11. Kill criteria

Stop trading and re-examine if any of these fire:

- Realised win rate below `need_win` over 20 consecutive trades
- Running t-statistic below 0 after 40 trades
- Any single loss exceeding the computed max loss (means the structure or
  the execution is broken, not the strategy)
- Slippage above 15% of credit on 5 consecutive fills
- Gate margin negative for 6 consecutive months — the premium is gone

## 12. Honest summary

The mechanism is real and it is the only edge that survived a ten-study
search. It was worth +2.4%/week in 2021 and is worth approximately nothing
today. The value of this spec is the **gate**: it encodes exactly when the
trade is worth doing, it currently says no, and it will say yes without
anyone having to remember to check.

Build the gate first. It is useful even if the strategy never trades, because
it converts "is short vol attractive right now?" from an opinion into a
number.

## Reproduce the analysis

```bash
python -m scalp_lab.run_volprem     # 5.5-year straddle simulation by tenor and year
python -m scalp_lab.run_carry       # IV-RV spread and funding carry
```
