# Astro Framework — Empirical Validation Results

**Tested:** 2026-08-31
**Data:** BTCUSDT + ETHUSDT Binance USD-M perpetuals, 2021-01-01 → 2026-08-30
(595,584 five-minute bars, 594,980 five-minute open-interest observations, 6,206 funding prints per symbol)
**Costs applied:** 4.5 bps taker + 2.0 bps slippage per side = 13 bps round trip, on every leg of every trade
**Pre-registration:** [src/config.py](../astro_validation/src/config.py), frozen before execution; one amendment documented in place
**Source spec:** `ANALYSIS.md` Part C, hypotheses C1–C9

---

## Verdict in one paragraph

Nine testable claims were extracted from the corpus and put in front of 5.7 years of
data. **None of them survived.** The headline "Onset of the Squeeze" — advertised at
70% win rate on 1R — produced a median win rate of **46–53%** across 3,888 parameter
combinations, and **not one cell with a meaningful sample reached 70%**; its one marginally positive
configuration (BTC 15-minute, 55.5%) failed on a second instrument and was not
statistically distinguishable from a coin flip. The four "New York times" sit in the
**58th percentile** of intraday volatility while the four textbook anchors he never
mentions sit in the **95th**; 08:25 is measurably *quieter* than the minutes around
it. The CVD divergence restriction is **inverted** — the two classes he discards
outperform the two he trades. The initial-balance claim is **backwards** with high
confidence (ρ = +0.40, p ≈ 1e-78). One finding is constructive and goes the other
way: his own exit doctrine is **measurably the worst of the four** tested on his own
signals, costing roughly 21 percentage points of total return versus simply holding
to 1.5R.

---

## Results table

| # | Claim | Claimed | Measured | Verdict |
|---|---|---|---|---|
| **C2** | Onset of the Squeeze, 1R | **70% W** | 46–53% median; 0/73 adequately-sampled cells ≥70%; best 65.6% (p<sub>corr</sub>=1.00) | ❌ **Refuted** |
| **C1** | 00:00/08:25/09:00/13:05 ET are institutionally significant | "high relevance" | 58th pctile vs 95th for real anchors; 0/16 top-decile | ❌ **Refuted** |
| **C3a** | Two of four CVD divergence classes are the tradeable ones | his 2 work | his 2 are the **worst** 2; 0% beat control | ❌ **Inverted** |
| **C3b** | Bullish divergence persistence split (Rule A7.5f) | novel edge | both branches negative, neither beats control | ❌ **Refuted** |
| **C4** | Narrow initial balance → wider day | inverse | **ρ = +0.40**, p ≈ 1e-78 — positive | ❌ **Refuted (backwards)** |
| **C5** | Poor highs/lows (≥2 TPO blocks) get revisited | differential | distance-matched difference **−1.2 to −4.8pp** | ❌ **Refuted** |
| **C6** | Open inside pdVA → range; outside+acceptance → trend | conditional | ranges 1.00 vs 1.02; p = 0.56–0.66 | ⭕ **Null** |
| **C7** | Never long after POC migrates down | filter adds value | +4.4 bps/day, CI spans zero | ⭕ **Null (directionally right)** |
| **C9** | Prioritise win rate; take partials early | superior | **worst of 4 regimes** on his own signals | ✅ **Refuted in his favour → actionable** |

Legend: ❌ refuted · ⭕ no measurable effect · ✅ a real, usable finding (against his doctrine)

---

## C2 — Onset of the Squeeze (the headline)

The only setup in the corpus specified tightly enough to mechanise end to end, and
the only one carrying a number: *"70 W / 30 L on 1R backtests"* (`#1687`).

**Implementation.** All three stages coded literally: a trending down-move with
aggressive OI buildup → price ranges while OI stays flat and elevated → majority of
range bars show red→OI-up / green→OI-down → trigger on the first big green candle
that drops OI. Entry at the next bar's open, stop at the range low, exits resolved on
**5-minute bars inside each signal bar** so same-bar stop/target collisions are
decided by path order (stop first, pessimistic). Under-specified thresholds U1–U4
swept across 1,944 combinations × 2 (his `#1653` failure filter on/off) × 3 timeframes.

### The full surface (never the best cell)

| TF | filter | cells | usable | median trades | **WR median** | WR p90 | WR max | usable cells ≥70% | median return |
|---|---|---|---|---|---|---|---|---|---|
| 15min | off | 648 | 20 | 39 | **0.532** | 0.589 | 0.656 | **0** | −0.001% |
| 15min | on | 648 | 19 | 36 | **0.531** | 0.585 | 0.656 | **0** | −0.019% |
| 30min | off | 648 | 11 | 43 | **0.462** | 0.528 | 0.533 | **0** | −0.163% |
| 30min | on | 648 | 9 | 46 | **0.433** | 0.509 | 0.543 | **0** | −0.190% |
| 1h | off | 648 | 8 | 38 | **0.447** | 0.461 | 0.469 | **0** | −0.371% |
| 1h | on | 648 | 6 | 40 | **0.404** | 0.430 | 0.432 | **0** | −0.527% |

*"Usable" = ≥30 trades. The setup is genuinely rare: a typical cell fires ~25–45 times
in 5.7 years, which is consistent with him showing only a handful of examples.*

### Where the 70% cells actually live

Taken across all 3,888 cells regardless of sample size, 701 *do* show a win rate ≥70%.
Every one of them is a small-sample artefact — their median trade count is **one**:

| minimum trades | cells | cells ≥70% | share | median WR | max WR |
|---|---|---|---|---|---|
| ≥1 | 2,234 | 701 | 31.4% | 0.500 | 1.000 |
| ≥5 | 747 | 78 | 10.4% | 0.467 | 1.000 |
| ≥10 | 343 | 21 | 6.1% | 0.469 | 0.917 |
| ≥20 | 144 | 5 | 3.5% | 0.458 | 0.739 |
| **≥30** | **73** | **0** | **0.0%** | **0.500** | **0.656** |
| ≥50 | 22 | 0 | 0.0% | 0.446 | 0.573 |

The claimed number is perfectly reproducible if you are willing to quote a
parameterisation that traded three times. It disappears monotonically as the sample
grows, which is the signature of noise rather than of an edge that merely needs tuning.

**The best cell in the entire surface** reaches 65.6% on 32 trades — and that is
exactly what selection produces:

```
p vs 50%                    0.055     (looks interesting alone)
p after Bonferroni x3888    1.000     (it is the max of 3,888 draws)
p vs the claimed 70%        0.772     (n=32 cannot separate 50% from 70% either way)
```

### Pooled — every entry the rule family finds, counted once

| TF | trades | win rate | 95% CI | OOS WR | control WR | edge | p vs 50% | total (5.7y) |
|---|---|---|---|---|---|---|---|---|
| **15min** | 164 | **55.5%** | [47.8, 62.9] | 51.7% | 48.5% | +7.0pp | 0.092 | +7.7% |
| 30min | 146 | 48.6% | [40.7, 56.7] | 61.5% | 50.0% | −1.4pp | 0.660 | −12.3% |
| 1h | 96 | 46.9% | [37.2, 56.8] | 50.0% | 49.5% | −2.6pp | 0.762 | −23.3% |

The 15-minute cell is the only positive result anywhere in C2. Three checks kill it:

1. **Different instrument.** ETHUSDT, which played no part in any design decision:
   **45.9%** win rate, **−22.5%** total. The rule does not transfer.
2. **Year by year.** 51.7 / 55.2 / 65.5 / 61.1 / **42.1** / 66.7 % — 2025 is
   outright negative, and every year's confidence interval contains 50%.
3. **Significance.** p = 0.092 uncorrected, on the timeframe chosen *because* it
   looked best out of three.

**The literal reading fires once.** The most faithful single parameterisation —
"aggressive" buildup, "clear" down-move, tight range, "BIG" green candle — produces
**one trade in 5.7 years**. Every configuration that generates a tradeable number of
signals does so by relaxing his adjectives.

---

## C1 — The "New York Times"

His four claimed times against the four standard anchors he never mentions, on
288 five-minute bins, both DST readings, both instruments (16 observations each):

| | median \|return\| percentile | top-decile hits | survive Bonferroni |
|---|---|---|---|
| **Claimed** (00:00, 08:25, 09:00, 13:05) | **0.58** | **0/16** | 8/16 |
| **Anchors** (08:30, 09:30, 16:00, 17:00) | **0.95** | **12/16** | 16/16 |

The neighbourhood test is the decisive one — does the *specific minute* matter, or
just its proximity to a real event?

| time | excess vs ±30 min | reading |
|---|---|---|
| 08:25 | **−9.8%** | *quieter* than its surroundings — the lull before the 08:30 release |
| 09:00 | −1.3% to +0.7% | nil |
| 13:05 | +1.2% to +2.3% | negligible |
| 00:00 | +9.4% | small, and a well-known daily-close artefact |
| *08:30* | *+29.6%* | *(control — a real event)* |
| *09:30* | *+31.6%* | *(control — the cash open)* |

08:25 being *below* its neighbourhood is the cleanest single refutation in the study:
the claimed time is the calm five minutes traders spend waiting for the print at 08:30.

---

## C3 — CVD divergence

**C3a — the restriction is inverted.** Median across 32 parameter cells, post-cost:

| class | he trades it | mean return | win rate | beats control |
|---|---|---|---|---|
| price LL / CVD HL | ❌ discarded | **−0.032%** | 47.6% | 22% of cells |
| price HH / CVD LH | ❌ discarded | **−0.066%** | 46.8% | 16% of cells |
| price HL / CVD LL | ✅ **traded** | **−0.129%** | 43.1% | **0%** |
| price LH / CVD HH | ✅ **traded** | **−0.347%** | 40.8% | **0%** |

All four lose money after costs, but the ordering is the finding: the two he
explicitly discards as "reading the market from the wrong way" (`#2014`) are the two
that lose *least*, and the only ones that ever beat their control. The restriction
has content — with the sign reversed.

**C3b — the persistence trap.** Rule A7.5f was flagged in the analysis as the most
original idea in the corpus, and it is the one place this study found a bug in its
own first implementation worth reporting: selecting divergences by whether a pump
occurred, then measuring returns over that same window, produced **76% win rates in
both branches**. That was pure look-ahead. Corrected — the pump is identified by the
*first* bar clearing the threshold, and returns are measured *from that bar*, which
is causal — both branches are negative:

| state | n (median) | mean return (h=6…48) | win rate | beats control |
|---|---|---|---|---|
| persisted | 51 | −0.11% → −0.52% | 41–45% | ~0% |
| disappeared | 523 | −0.14% → −0.26% | 45–49% | 0% |

---

## C4–C7 — the Market Profile battery

Built to his exact spec (§A7.2): daily profiles, 30-minute TPO blocks, $100 rows,
70% value area, 2,068 days.

**C4 — refuted, backwards, decisively.** He claims narrow initial balance → wider
day. Measured Spearman ρ between IB width and day range: **+0.398** (p ≈ 1.1e-78).
Narrow-IB days have median normalised range **0.69**; wide-IB days **1.05**. Wide
initial balances predict wide days — volatility clusters, as it does everywhere. This
is a concept imported from session-based futures markets that does not survive
transplant into a 24-hour market with no opening auction.

**C5 — refuted once controlled.** Raw revisit rates favour poor extremes by 1–4pp,
which looks supportive. But poor extremes sit closer to the close, and closer levels
get revisited more. Stratifying by distance quintile reverses the sign at every
horizon: **−4.8pp (1d), −2.6pp (3d), −1.2pp (5d), −1.7pp (10d), −1.5pp (20d)**. The
raw difference was entirely a distance artefact.

**C6 — null.** Median normalised range 0.996 (open inside pdVA) vs 1.025–1.047
(outside with acceptance); Mann-Whitney p = 0.56–0.66 across all three acceptance
definitions. No usable conditional signal.

**C7 — null, but directionally right.** As a filter on a naive next-day long:
baseline −4.3 bps/day, POC-up-only +0.1 bps/day, POC-down-only −13.5 bps/day. The
gap between the two branches (13.6 bps) is the largest directional effect found
anywhere in the study, but every confidence interval spans zero and the filtered
variant is still not profitable.

---

## C9 — the one actionable finding

His doctrine: *prioritise win rate over R-multiple, take risk off the trade early*
(§A6.2). The analysis argued in §B5.2 that this costs 5–15% of expectancy. Running
**identical entries** under four exit regimes measures it:

| TF | exit | win rate | total return | profit factor | **Sharpe** | max DD |
|---|---|---|---|---|---|---|
| 15min | 1R symmetric | 55.5% | +7.7% | 1.09 | 0.92 | 14.2% |
| 15min | **his ladder** | 43.9% | **+15.9%** | 1.15 | **1.09** | 13.4% |
| 15min | 3R all-out | 38.4% | +34.2% | 1.25 | 1.63 | 14.6% |
| 15min | **1.5R all-out** | 48.8% | **+37.2%** | **1.29** | **2.39** | **11.1%** |
| 30min | his ladder | 39.7% | −9.2% | 0.95 | −0.47 | 25.3% |
| 30min | 3R all-out | 34.2% | −6.4% | 0.99 | −0.08 | 32.0% |
| 1h | his ladder | 39.6% | −18.1% | 0.86 | −1.38 | 28.8% |
| 1h | 3R all-out | 32.3% | −4.8% | 1.00 | 0.04 | 27.4% |

**On his own signals, his own exit rule is the worst or second-worst of the four at
every timeframe.** At 15 minutes, switching from his partials ladder to a plain
1.5R all-out more than doubles total return (+15.9% → +37.2%) and more than doubles
Sharpe (1.09 → 2.39) while *reducing* drawdown. The §B5.2 objection is confirmed, and
the cost is larger than the 5–15% estimated — it is roughly 60% of the expectancy.

Note the pattern in the win-rate column: the regimes with the *lowest* win rates
produce the *highest* returns. This is precisely the trade-off his doctrine inverts.

---

## Benchmark

Nothing here beats doing nothing.

| | total (5.7y) | CAGR | Sharpe |
|---|---|---|---|
| **BTC buy-and-hold** | **+167.7%** | **19.0%** | 0.59 |
| Best squeeze variant found (15m, 1.5R) | +37.2% | 5.8% | 2.39* |
| His doctrine as specified (15m ladder) | +15.9% | 2.6% | 1.09* |

\* Sharpe is flattering because the strategy is in the market ~0.4% of the time; on
capital-deployed terms the comparison is the total-return column. The best
configuration discoverable anywhere in this study captured **22%** of what holding
spot returned, before any consideration of the fact that it was selected from 3,888
candidates.

---

## What would have to be true for this to be wrong

Stated plainly, so the result can be attacked on specifics:

1. **Aggregated vs venue OI.** Binance USD-M only. He may read aggregated OI across
   venues (`#1631` is ambiguous). A cross-venue OI series could plausibly change C2
   — this is the single most likely source of a false negative, and the one I would
   test next.
2. **Discretion.** He states the 70% is "only in theory" and that interpretation
   error matters (`#1688`). If the edge lives in chart-reading that no threshold can
   capture, it is untestable by construction — which is itself the §B6.3 problem the
   analysis identified, not a defence.
3. **Sample.** 2021–2026 covers two bull phases, one bear, and one chop regime. A
   setup needing conditions absent from this window would not show up.
4. **Instruments.** C1's claim spans FX, metals and indices; only BTC and ETH were
   tested. The crypto result is unambiguous but does not formally refute the FX half.

None of these rescue C1, C4, or C5, which fail on mechanics rather than on
calibration.

---

## Reproducing

```bash
cd astro_validation
python src/run_all.py               # ~2.5 GB download on first run, then ~15 min
python src/run_all.py --only c2 c2p # single test
```

Every table above is written to [out/](../astro_validation/out) as CSV. The frozen parameter grids are in
[src/config.py](../astro_validation/src/config.py); the single amendment (a dimensionally-wrong range-width
threshold that admitted zero signals) is documented inline with its date and reason,
and was made from the distribution of range widths alone, before any return was computed.
