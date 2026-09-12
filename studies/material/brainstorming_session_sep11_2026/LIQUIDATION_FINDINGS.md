# Liquidation cascades, level memory, and a backtest bug that cost 0.38pp

Work on backlog item 4, 2026-09-09/10. Data: 338,976 5m bars (2023-06 to
2026-09), 5-minute OI back to 2021, and 53,398 real forced-liquidation orders.

## 1. The cascade proxy is validated

A cascade is defined as a large move (|z| >= 4 of the trailing 24h return
distribution) coinciding with an OI DROP >= 0.30%. OI is the discriminator:
price moving with OI *rising* is new positioning, not forced flow.

Binance publishes real liquidation orders, but only for COIN-margined futures
and only 2023-06-25 to 2024-10-14 (discontinued, like bookTicker). That window
is enough to validate the proxy:

| | all bars | cascade bars |
|---|---|---|
| had any liquidation | 13.8% | **90.5%** |
| median contracts liquidated | 0 | **797** |
| mean contracts liquidated | 69.1 | **3,502** (50.7x lift) |

Of 317 detected cascades, **127 landed in the top 1% of liquidation bars —
40% against a 1% chance rate. 40x lift, z = +69.9.**

The proxy is not an approximation of cascades; it essentially is one. That
means the discontinued feed does not matter — the proxy works on current data,
on the USDT-margined contract, with no coverage gap.

Incidental: 32,826 long liquidations vs 20,572 short, a 1.6x asymmetry
consistent with the market's structural long bias and with funding being
positive 77% of the time.

## 2. Cascades cluster where stops predictably sit

233 cascades over 400 days (17.5/month), median magnitude 0.69%, p90 1.27%.

Distance to the nearest stop level, cascades vs random bars:

| | n | median dist | <0.25% | <0.5% |
|---|---|---|---|---|
| cascades | 233 | **0.140%** | **63%** | **79%** |
| random | 4,000 | 0.306% | 44% | 68% |

Difference in mean distance −0.167%, **t = −3.71**.

Which level is nearest when one fires:

| level | cascade% | random% | lift |
|---|---|---|---|
| **24h rolling LOW** | 27.0% | 8.2% | **3.31x** |
| **24h rolling HIGH** | 18.5% | 9.8% | **1.89x** |
| round $1000 | 34.8% | 47.5% | 0.73x |
| prior day low | 7.7% | 11.6% | 0.67x |
| prior day high | 6.4% | 12.4% | 0.52x |

Cascades happen at the **recent swing extreme**, and *avoid* round numbers and
prior-day levels relative to chance. Stops sit below the last low, not at
psychological prices.

## 3. Price levels carry liquidation memory — confirmed

Backlog item 4b. Expanding-window density map, $250 buckets, 209,807 scored
bars, base rate P(cascade within 2h) = 4.27%.

| density quintile | P(cascade) | lift | z |
|---|---|---|---|
| Q1 | 3.58% | 0.84x | −6.9 |
| Q2 | 3.69% | 0.86x | −5.9 |
| Q3 | 4.23% | 0.99x | −0.4 |
| Q4 | 4.67% | 1.09x | +4.1 |
| **Q5** | **5.17%** | **1.21x** | **+9.1** |

Cleanly monotonic. **And it survives the confound control** — dense buckets
are not merely heavily-visited buckets:

| visits | low density | high density | diff | z |
|---|---|---|---|---|
| 50-389 | 3.86% | 4.75% | +0.89pp | +5.9 |
| 389-848 | 3.44% | 4.58% | +1.14pp | +7.5 |
| 848-3,906 | 3.90% | 5.07% | +1.17pp | +7.7 |

Present at every visit level, strongest among heavily-visited buckets. Leverage
genuinely rebuilds at the same prices.

**Limit:** density predicts OCCURRENCE, not MAGNITUDE. Given a cascade fires,
the move is the same size at dense and sparse levels (median 0.686% vs 0.661%).

## 4. None of it is tradeable

Resting bids in high-density buckets, distance-matched against low-density and
random buckets. **HIGH minus LOW was negative in all four configurations**
(−0.012% to −0.106%). Knowing where cascades happen confers no return edge.

Post-cascade reversion remains +10.9bp gross against a 10bp taker fee — the
closest near-miss in this repo, and still a near-miss.

## 5. THE BUG — position-blocking manufactured 0.38pp

The most expensive error of the session, recorded so it is never repeated.

An intermediate test appeared to show resting bids earning **+0.279% long and
+0.234% short**, drift-adjusted, symmetric, surviving 30-minute execution
delay, working in down years, with a matched control at t=+6.45. It looked
like the strongest result in the repo.

It was a sampling artifact. The simulator held one position at a time:

```python
hit = <scan forward for the fill>
...
busy = hit + hold          # no new placement until the position closes
```

Individual trades were causal — the fill was found by scanning forward from
the placement, entry came after it. But **which bars got a placement depended
on when future fills occurred.** The blackout after each fill systematically
skipped the window where price kept falling — precisely the continuation
losses. Removing it:

| | LONG | SHORT |
|---|---|---|
| blocked (one position) | **+0.279%** | **+0.234%** |
| unblocked (every signal) | **−0.102%** | **−0.099%** |
| non-overlapping subsample | **−0.068%** (t=−1.97) | **−0.094%** (t=−2.99) |

This is trap #1 (overlapping samples) in a nastier form. There the inflation
was in the t-statistic; here it was in the **mean itself**.

**Rule adopted: any simulator with position-state must be run in an unblocked
variant that takes every signal. If the two disagree, the blocked version is
wrong.**

With the bug removed, everything agrees again: run_dipbuy negative,
run_reconcile negative, unblocked resting bids negative, and the three
"do not fade structure" rules intact.

## Reproduce

```bash
python -m scalp_lab.run_cascades       # cascade detection + stop clustering
python -m scalp_lab.run_liq_validate   # proxy vs 53,398 real liquidations
python -m scalp_lab.run_liqmap         # 4b: density map + confound control
python -m scalp_lab.run_final          # the decisive blocked/unblocked test
```

## 6. Do cascades travel to the next cluster? No.

Tested the FOLLOW hypothesis: after a cascade breaks level X, does price run
to the next high-density cluster Y? This is the continuation direction, which
every replicated rule in this repo favours over fading — so it deserved a test.

419 cascades with a density cluster ahead within 6%.

**Travel is independent of cluster distance:**

| distance to next cluster | n | median travel | reached | median adverse |
|---|---|---|---|---|
| 0.36-0.88% | 104 | 1.53% | 81% | 1.31% |
| 0.89-1.28% | 104 | 1.64% | 77% | 1.34% |
| 1.28-2.09% | 104 | 1.64% | 55% | 1.53% |
| 2.09-5.71% | 107 | 1.71% | 26% | 1.19% |

**correlation(distance, travel) = −0.043.** The falling "reached%" is geometry
(near targets are hit more often), not a magnet.

**Nor does the move terminate at clusters.** Density of the bucket containing
the post-cascade extreme: 0.00275 vs 0.00245 for random bars, t=+1.85.

**Trading it:** best cell +0.072% (t=1.03, 2% stop). Random targets at matched
distance: −0.016%. No significant difference.

### The asymmetry

Liquidation density predicts where cascades **START** (3.31x at the 24h
extreme; 1.21x from level memory, z=+9.1). It does not predict where they
**STOP**.

Mechanically that fits: a cascade begins when clustered stops are triggered.
Where it ends depends on how much resting liquidity absorbs the forced flow —
a different variable, and one that leaves no footprint in liquidation history.

Descriptive constant worth keeping: after a cascade, price travels a median
**1.6% with 1.3% adverse excursion** over the following 24h, roughly symmetric,
stable across every configuration. That symmetry is why no stop/target
combination profits.

```bash
python -m scalp_lab.run_cluster_target
```

## 7. How these findings could make OTHER strategies viable

None of the above trades on its own. But the measurements are reusable, and
several of them attack the exact constraint that killed other work. These are
hypotheses, not tested results — flagged as such.

### 7.1 Stop placement (the strongest practical use)

Cascades cluster at the **24h rolling extreme at 3.31x chance**, and at
high-density buckets at **1.21x**. A stop resting just below the 24h low is
therefore sitting exactly where forced selling concentrates.

Implication: for any strategy with a price stop, moving it *away* from the 24h
extreme and away from high-density buckets should measurably reduce stop-out
rate at the same risk budget. This is directly testable and would improve
every stopped strategy in this repo, several of which failed with win rates
near 40%.

It also bears on the main book: the Layer-2 hard stop and the shelf rails are
sited at exactly the kind of levels this study flags.

### 7.2 Execution and slippage

Density is a map of where forced flow appears. Sending size into a
high-density bucket should cost more. For a strategy whose edge is 10bp, a
2-3bp slippage difference is a third of the edge. Routing decisions
(passive vs aggressive, split vs single) could be conditioned on it.

### 7.3 Calibration constant for stops and targets

After a cascade, price travels a median **1.6% with 1.3% adverse excursion**
over 24h, stable across every configuration tested. That is a directly usable
number for sizing stops and targets on any strategy operating at that horizon
— it says a 0.6% stop will be hit by ordinary post-cascade noise most of the
time, which matches the 54% stop-out rate found in the sweep-reclaim work.

### 7.4 A validated feature for the combination matrix (backlog #2)

The cascade proxy is validated at 40x against real liquidations. That makes
"cascade in the last N bars" and "distance to the nearest high-density bucket"
legitimate features to include in combination testing — genuinely different in
kind from the tick-derived signals (ofi, tint, big), which are all correlated
with each other. Independence is what makes combination work, and this feature
is derived from OI, not from trade flow.

### 7.5 Short-vol sizing (backlog #1)

Cascade probability is a forward proxy for realised-volatility risk. A short-
vol position is short exactly the event a cascade represents. If cascade
probability is elevated (price near a high-density bucket, or at the 24h
extreme), that is an argument for smaller size or wider wings — a risk overlay
rather than an entry signal.

### 7.6 What it does NOT support

Density predicts initiation, not termination (§6). So it cannot be used for
targets, for range boundaries, or for any "price will travel to level Y"
claim. Attempts to use it that way have been tested and failed.
