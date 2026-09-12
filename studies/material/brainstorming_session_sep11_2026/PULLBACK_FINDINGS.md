# How deep is normal chop, and can you buy the dip?

BTCUSDT 5m/15m/1h, 365 days to 2026-09-09.

## The useful part: the distribution

Drawdown from a rolling high, measured continuously.

| Rolling high | p50 | p75 | p90 | p95 | p99 |
|---|---|---|---|---|---|
| **2h** (24 x 5m) | 0.30% | 0.57% | **0.97%** | 1.33% | 2.34% |
| **6h** (72 x 5m) | 0.55% | 1.02% | **1.73%** | 2.34% | 3.95% |
| **24h** (96 x 15m) | 1.21% | 2.33% | 3.55% | 4.50% | 6.87% |

Completed pullback episodes (dip from a high, then a new high):

| Window | median depth | p75 | p90 | median duration |
|---|---|---|---|---|
| 2h | 0.60% | 1.21% | 2.04% | **5.5h** |
| 6h | 0.96% | 2.12% | 3.45% | 13.1h |
| 24h | 1.39% | 2.75% | 3.89% | 29.0h |

**Read: against a 2-hour high, 0.3% is a median wobble, 1.0% is the 90th
percentile, and 2.3% is the 99th.** That is the "normal chop vs correction"
line the question asked for, and it is solid — it is descriptive statistics,
not a fitted strategy.

## The part that does not work: trading it

A first pass suggested the 1.0-1.5% band had positive excess forward returns
(+0.052% at 4h, t=+3.46). **That t-statistic was wrong, and the error is worth
recording.**

It counted every 5-minute bar sitting inside the band as an independent
observation — 5,946 of them. Consecutive bars in the same pullback are the
same event sampled repeatedly, so the effective sample is far smaller and the
t-stat is inflated. This is pseudo-replication.

Re-run on **entries** (first touch of the band, 2h cooldown), the sample drops
to 736 and the effect disappears:

### With a stop (fee 0.05%)

| Band | Stop | Target | n | Expectancy | t |
|---|---|---|---|---|---|
| 0.75-1.25% | 0.6% | 1.5R | 1166 | −0.088R | −2.80 |
| 1.0-1.5% | 0.6% | 1.5R | 736 | −0.080R | −1.95 |
| 1.0-1.5% | 1.0% | 1.5R | 736 | −0.043R | −1.28 |
| 1.25-1.75% | 0.6% | 1.5R | 489 | **+0.002R** | +0.03 |

Best case is exactly zero. Out of sample: train −0.075R, TEST −0.066R.

### Without a stop (pure time exit)

| Band | Hold | n | mean | t | net @0.05% |
|---|---|---|---|---|---|
| 1.0-1.5% | 120m | 736 | −0.006% | −0.19 | −0.056% |
| 1.25-1.75% | 120m | 489 | +0.054% | +1.21 | +0.004% |
| 1.5-2.5% | 120m | 345 | +0.059% | +1.07 | +0.009% |

Nothing reaches t=2. The best net figures are +0.004% and +0.009% per trade.

## Why it fails: the excursions are symmetric

For entries in the 1.0-1.5% band:

| Horizon | median MAE | median MFE | P(MAE worse than −0.6%) |
|---|---|---|---|
| 60m | −0.38% | +0.39% | 33% |
| 120m | −0.51% | +0.54% | 44% |
| 240m | −0.68% | +0.69% | **54%** |

**MFE and |MAE| are equal at every horizon.** There is no asymmetry to
harvest. A 0.6% stop is hit before any drift arrives in 54% of cases at the
4-hour horizon, which is exactly why the stopped version loses and the
unstopped version merely reverts to the unconditional drift.

## Conclusion

The depth distribution is real and useful for **context and sizing** — knowing
that 1.0% off a 2h high is the 90th percentile tells you whether the tape is
doing something ordinary or unusual.

It is not a **trigger**. Buying a fixed drawdown band has no edge in either
direction, with or without a stop, in any regime, at any of the horizons
tested. A dip that is statistically "deep" is not more likely to bounce.

## Reproduce

```bash
python -m scalp_lab.run_pullback   # distributions + conditional forward returns
python -m scalp_lab.run_dipbuy     # tradeable rule, train/test, fee curve
```
