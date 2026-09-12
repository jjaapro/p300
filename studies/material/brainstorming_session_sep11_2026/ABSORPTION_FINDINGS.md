# Absorption and rejection as level-agnostic triggers

Study 25. 2026-09-11. Two runs, 348 configurations.

**The idea:** stop trying to predict *where* price will turn — every level study
died on that. Instead react to *what is happening*: aggressive flow being
absorbed without price moving, or an excursion being rejected. No level required.

**The result splits.** Absorption is the best-behaved short-horizon signal in
this repo and still cannot pay its costs. Rejection is momentum wearing a
costume — the wick contributes nothing.

## 1. Definitions

Every kline carries `taker_buy_base`, so signed aggressive flow is available
across full history, not only the 90 cached aggTrades days:

```
signed = 2*taker_buy - volume          (aggressive buy notional minus sell)
```

- **A1, crude absorption.** Volume z-high AND one-sided imbalance z-high AND
  |return| z-low. Trade **against** the aggressor — it is being absorbed.
- **A2, impact residual.** Causal rolling regression of return on signed flow
  gives lambda (Kyle's price-impact coefficient). `residual = actual − lambda*flow`.
  A large residual against the flow direction is a hidden counterparty's
  footprint. Trade **with** the residual.
- **R, rejection.** Bar travels >= k sigma from its open and closes back through
  >= f of that excursion. Level-agnostic.

## 2. Absorption works and does not pay

5-minute panel, 1,177 days, drift-adjusted, non-overlapping:

| signal | n | bp | t | clustered t | capture | net @4bp |
|---|---|---|---|---|---|---|
| ABS z>1.0, H24 (2h) | 1,620 | +4.8 | **+2.91** | **+2.16** | +6.7% | **+0.8** |
| ABS z>1.0, H1 | 2,431 | +0.7 | +2.50 | **+3.20** | +4.8% | −3.3 |
| ABS z>1.5, H24 | 665 | +5.5 | +2.06 | +1.92 | +7.7% | **+1.5** |
| ABS z>2.0, H1 | 265 | +1.7 | +2.04 | +2.22 | +11.4% | −2.3 |

**Every absorption cell is positive**, and the episode-clustered t sometimes
exceeds the naive one. That is rare here — clustering took a Bollinger headline
from t=5.57 to −0.27 in study 23. It not biting means the effect is not a
handful of regime episodes.

Capture runs 4.8–13.5% of sigma, at the upper end of the 3–13% band the
programme's 17 prediction studies clustered in. But the edge is 1–5bp, so only
the longest horizons clear a 4bp round trip, and then by **+0.8 to +1.5bp**.

**Mechanism.** A1 fades the *aggressor*, not a price move — the setup condition
is explicitly that price did **not** move. This is a better-conditioned version
of study 7's contrarian order flow, which got 3.6% capture where this gets
4.8–13.5%. Marginally better, still not enough.

A2 (impact residual) was weaker: naive t 1.5–2.0, and clustering killed it on
5m (t=+1.75 -> tc=+0.45 at H4).

## 3. It does not extend to long horizons

The horizon was pushed to one week, one month and one quarter on every panel,
plus a weekly-bar panel resampled from daily. RATIO_FINDINGS' accumulation law
predicts an accumulating edge should improve as sqrt(T) while the fee stays fixed.

| 1h panel | n | bp | t | clustered t | clusters |
|---|---|---|---|---|---|
| ABS z>1.0, H24 | 114 | +6.5 | +0.28 | +1.10 | 82 |
| ABS z>1.0, **H168 (1 week)** | 76 | +46.7 | +0.59 | +0.64 | **9** |
| ABS z>1.0, **H720 (1 month)** | 34 | +70.6 | +0.27 | — | **1** |
| ABS z>1.5, H168 | 27 | −109.8 | −0.83 | −0.75 | 14 |

The basis-point column grows — +6.5 -> +46.7 -> +70.6 — which *looks* like the
accumulation law working. It is not. **The sample collapses faster than the edge
grows**: 114 -> 76 -> 34 non-overlapping observations, cluster count falling to
9 then 1, at which point the clustered t is undefined. At the tighter threshold
it turns negative.

**Absorption is a short-horizon microstructure effect. It does not survive being
stretched.**

### 3b. The decay curve (added after the 2h cell was challenged as a grid edge)

The original grid STOPPED at H24 and the best cell sat on that boundary with
every metric still rising. That is the classic "best cell at the edge" error: it
means the grid was too small, not that an optimum was found. A dense sweep of
the 5m panel from 1h to 2 days settles it.

| horizon | n | bp | t | clustered t | capture | net @4.1bp |
|---|---|---|---|---|---|---|
| 1h | 1,818 | +2.9 | +2.54 | +1.91 | 5.70% | −1.2 |
| **2h** | 1,620 | **+4.8** | **+2.91** | **+2.16** | **6.70%** | **+0.7** |
| 3h | 1,488 | +4.8 | +2.25 | +1.31 | 5.47% | +0.7 |
| 4h | 1,395 | +1.6 | +0.62 | +0.69 | **1.56%** | −2.5 |
| 8h | 1,104 | +1.5 | +0.39 | −0.23 | 1.05% | −2.6 |
| 16h | 813 | −1.4 | −0.22 | −0.05 | −0.70% | −5.5 |

2h IS the peak — but now as a measured fact rather than a grid artifact, and the
shape is less flattering than the boundary cell suggested. Capture rises to
6.70% then **falls off a cliff at 4h (1.56%)** and never recovers; the clustered
t goes negative by 8h. This is a decaying effect, not an accumulating one, which
**directly falsifies** the hypothesis that holding longer would amortise the fee.

At z>1.5 there is a spike at 1.5 days (+30.7bp, t=+2.09) but the clustered t
falls to +1.28 on only 22 clusters, and its neighbours at 1d (+0.82) and 2d
(+0.90) are weak with the 4-8h region dead. A spike flanked by nothing, with the
cluster count collapsing - the shape study 23 taught us to distrust.

### 3c. What the capture number means, since it is easy to misread

`capture = edge / sigma(H)`, i.e. the edge as a FRACTION OF ONE STANDARD
DEVIATION of the move over that horizon. It is not a move size and not a share
of the realised move.

```
typical 2h move (1 sigma)   0.715%     (per-5m-bar sigma 0.146%, annualises to 47%)
measured edge per trade     0.048%     = 4.8 basis points
capture = 0.048 / 0.715  =  6.7%
```

On 0.5 BTC at $78k ($39,000 notional), 502 signals/yr:

| execution | gross/trade | cost/trade | net/trade | per year |
|---|---|---|---|---|
| maker-maker (4.1bp) | $18.72 | $15.99 | **+$2.73** | **+$1,370 (3.5%)** |
| maker-taker (5bp) | $18.72 | $19.50 | −$0.78 | −$392 |
| taker-taker (8bp) | $18.72 | $31.20 | −$12.48 | −$6,265 |

One taker leg and the whole thing is negative.

## 4. Rejection is momentum with an unnecessary filter

Reversing a sign-adjusted signal is the exact arithmetic mirror (trap 16), so
"reverse the expectation" is not a new test: t = −2.47 becomes +2.47 by
construction. The test that *is* informative: does the wick add anything over
the excursion alone?

Daily bars, wick-conditioned (reversed) versus excursion-only momentum:

| cell | wick-conditioned | **excursion only** |
|---|---|---|
| ex>1.0, H7 | +216bp, t=+1.69, tc=+1.76, n=63 | **+193bp, t=+3.04, tc=+3.99, n=189** |
| ex>1.5, H7 | +361bp, t=+2.67, tc=+3.06, n=42 | +196bp, t=+2.57, **tc=+3.47**, n=132 |
| ex>1.0, H30 | +306bp, t=+1.22, n=43 | +421bp, **t=+1.93**, n=77 |
| ex>1.5, H30 | +545bp, t=+1.96, n=32 | +617bp, **t=+2.92**, n=60 |

**In every daily cell the control matches or beats the wick version with two to
three times the sample.** Requiring a rejection wick discards two-thirds of the
observations and buys nothing. It is not a rejection signal; it is "big bar,
trade its direction."

In its textbook (fade) polarity, rejection loses: 1h ex>2.0 H24 gives −41.2bp
(t=−2.28, tc=−2.25); 1d ex>1.5 H12 gives −545bp (t=−2.47), win rate 39.5%.
That is the **sixth** independent confirmation that fading a price move on BTC
loses money.

## 5. The one cell that clears, named honestly

**Daily, range >1 sigma, trade the bar's own direction, hold one week:**
+193.5bp, t=+3.04, **clustered t=+3.99 on 20 clusters**, n=189, win 55.6%,
capture **21.4% of sigma** — above the 3–13% band measured everywhere else.

It clears the Bonferroni bar of 3.13–3.44. It is also daily-horizon momentum,
which is exactly what S-005 trades, and study 22 showed S-005 does not beat
buy-and-hold on return (CAGR 41.4% vs 42.3%) — only on risk. Twenty clusters is
the honest effective sample, since large-range days arrive in volatile runs.

**Accounting flaw, recorded:** the control cells were not included in the
t-statistic tally, so run 2's headline "28 cells, 0 above bar" understates the
search. Counting them, one clears — the cell above.

## 6. Totals

| run | configs | cells | t mean | t sd | max \|t\| | above bar |
|---|---|---|---|---|---|---|
| run 1 | 252 | 174 | +0.254 | 1.039 | 2.91 | 0 (bar 3.63) |
| run 2 | 96 | 28 (+controls) | −0.220 | 1.167 | 2.91 | 1 (bar 3.13–3.44) |

Run 1: |t|>2 occurred 10 times against 7.9 expected from noise; |t|>3 zero times
against 0.5 expected.

## 7. Reproduce

```bash
python -m scalp_lab.run_absorption      # A1, A2, rejection at short horizons
python -m scalp_lab.run_absorption3     # dense horizon sweep, the decay curve
python -m scalp_lab.run_absorption2     # long horizons, weekly bars, wick control
```
