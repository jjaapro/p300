# Research backlog

Set 2026-09-09 after the first systematic search (see FINDINGS_INDEX.md).
Ordered as agreed. Each item states the question, why it matters, and what
would count as an answer.

---

## 1. Why is the volatility premium decaying?

**Status: DONE 2026-09-10 — the premise was false.**

There is no statistically significant decay (t=-1.26 vs prior years;
trend slope t=-1.50). 2026's negative mean is three weeks out of 35.
IV/RV ratio intact at 92% of 2021 while the IV level halved -- the
premium is dormant, scaling with vol, not competed away. Separately
established: wings cost 43% of premium, so defined risk does not
rescue any year. See STRATEGY_SHORT_VOL.md.

<details><summary>original framing</summary>

The only edge that replicated across every year 2021-2025, and it has fallen
from +2.4%/week to +0.03%/week:

```
2021 +2.389%   2022 +2.200%   2023 +0.861%
2024 +0.565%   2025 +1.375%   2026 +0.028%
```

Abandoning it without understanding the decay is premature. It could be:

- **Competition** — more sellers arrived, premium competed away. Would imply
  it stays gone.
- **Realised vol falling faster than implied** — a denominator effect, not a
  premium effect. Would imply it returns when vol picks up.
- **Regime** — 2026 has been unusually calm; the premium may be intact but
  the sample is short (n=35 weeks).
- **Term-structure shift** — the whole curve has flattened, so ATM straddles
  capture less than they used to.
- **Measurement** — the DVOL-vs-tenor error (trap #4) may distort the trend
  if the term structure itself changed shape over the years.

**What would answer it:** decompose the yearly change into (a) change in
implied level, (b) change in realised level, (c) change in the spread between
them, (d) change in term-structure shape. If the spread is intact and only the
vol level fell, the strategy scales back with vol rather than being dead.

**Why it matters:** there is a written spec (STRATEGY_SHORT_VOL.md) with a
go/no-go gate that currently says NO. Knowing *why* tells us what to watch for
the gate to flip.

</details>

---

## 2. Strategy combination matrix

**Status: DONE 2026-09-10.** See COMBO_FINDINGS.md.

Combinations do NOT stack. Best 53.8% win / 2.5bp vs a 5-10bp fee (target was
~57%). Every combination underperformed what independence predicts, because
the predictive features are correlated 0.53-0.57. Structural finding: the
features that PREDICT are one signal in variants; the features that are
genuinely INDEPENDENT (liq_dens, casc_recent) have no predictive power. You
get independence or prediction, not both.

Everything tested so far was univariate. If the small edges are independent,
they stack: three independent 52% signals in agreement gives roughly 56%,
which is the difference between unusable and marginal.

**Candidates that produced any measurable effect:**

| Signal | measured | direction |
|---|---|---|
| ofi5 (executed order flow, 5min) | t=−3.05 | contrarian |
| tint5 (trade intensity) | t=+2.26/+2.21/+2.33 | positive, 3 horizons |
| big5 (large-trade flow) | t=−2.71 | contrarian |
| post-cascade reversion (24h extreme) | 10.9bp gross | mean-reverting |
| 1h return autocorrelation | ρ=−0.017 | contrarian |
| regime (EMA200 + slope) | strong as a VETO | filter |
| don't-fade-structure rules | 3 replicated | filter |

**What would answer it:** a matrix of pairwise and triple combinations,
measuring whether the combined win rate exceeds what independence predicts.
Must control for the signals being correlated with each other — order flow,
trade intensity and large-trade flow are all derived from the same tick data
and are probably not independent.

**Watch for:** this is the highest-risk item for false positives. Combination
search multiplies the number of tests enormously. Requires strict Bonferroni
and out-of-sample confirmation, or it will manufacture a curve.

---

## 3. Edge-to-noise ratio as a screening tool

**Status: DONE 2026-09-10.** See RATIO_FINDINGS.md.

Funding carry has 3x the ratio of anything else (0.780) and validates in 7
trades. Structural law confirmed: drift/carry edges IMPROVE with hold length
(ratio ~ sqrt(T)); one-shot edges DEGRADE (ratio ~ 1/sqrt(T)). The screen is
TWO tests -- learnability AND profitability -- and candidates fail on
different ones. Rule adopted: reject if ratio<0.05 and trades/yr<1000, or if
edge < 2x round-trip fee. Rejects six of ten prior studies a priori.

The insight from the "why is it only 0.1%" discussion: absolute edge size is
the wrong screen. What decides whether something is *learnable in a human
timeframe* is edge divided by per-trade noise.

```
1-day straddle    edge 0.33%  noise 1.00%  ratio 0.33  ->  ~40 trades to validate
1h contrarian     edge 0.011% noise 0.673% ratio 0.016 ->  15,152 trades
```

Same market, 380x difference in learnability.

**What would answer it:** compute edge/noise for every effect measured so far,
rank them, and establish the threshold below which a candidate should not be
pursued regardless of how "real" it looks. Then apply that threshold as a
front-line filter to all future ideas before spending compute on them.

**Secondary question:** which structural features raise the ratio? Longer
holds raise both edge and noise — does the ratio improve or degrade? Do
defined-risk structures (which truncate the loss tail) improve it mechanically
by cutting noise more than edge?

---

## 4. Liquidation data — direct measurement, and level memory

**Status: DONE 2026-09-10.** See LIQUIDATION_FINDINGS.md.

4a: the official feed exists but was discontinued after 2024-10-14 and covers
only COIN-margined futures. It did not matter — it was enough to VALIDATE the
OI-drop cascade proxy at 40x against 53,398 real liquidations (z=+69.9), and
the proxy works on current data with no coverage gap.

4b CONFIRMED: price levels carry liquidation memory. Density predicts future
cascades at 1.21x (z=+9.1), monotonic across quintiles, surviving the
visit-count control at every stratum.

Not tradeable: density predicts INITIATION, not TERMINATION. Distance-matched
high-vs-low density was negative in all four configs, and the follow-on test
(does price travel to the next cluster?) found travel independent of cluster
distance (corr −0.043). Post-cascade reversion remains 10.9bp vs a 10bp fee.

The most valuable output was trap #6 — see FINDINGS_INDEX.

Two parts.

**4a. Use real liquidation data instead of inferred stops.**
The cascade study inferred where stops sit from price structure (24h extreme,
prior-day levels, round numbers) and found cascades cluster at the 24h extreme
at 3.31x chance. Binance publishes actual forced-liquidation orders. Real
liquidation prices and sizes should be sharper than the inference.

**4b. Do historical liquidation clusters predict future ones?**
The hypothesis: price levels that have produced heavy liquidation in the past
accumulate a "memory" — leverage rebuilds at the same places, so approaching
such a level again raises the probability of another cascade.

This is a genuinely different claim from 4a. 4a says cascades happen at
structurally predictable prices *now*. 4b says a specific historical price
carries information about the future, independent of current structure.

**What would answer it:** build a liquidation-density map by price bucket over
a long window, then test whether approaching a high-density bucket predicts
(i) elevated cascade probability and (ii) larger realised moves, versus
approaching a low-density bucket at the same distance.

**Why it matters:** this is the only remaining candidate where the effect size
is measured in percent rather than basis points. Everything else has died on
the fee hurdle.

---

## Screening rule adopted

Before spending compute on any new idea, state:

1. the expected edge per trade,
2. the per-trade noise,
3. the ratio, and
4. the number of trades needed to reach t=2.

If that number exceeds what can be observed in a year of trading, the idea is
not testable in practice regardless of whether it is real.
