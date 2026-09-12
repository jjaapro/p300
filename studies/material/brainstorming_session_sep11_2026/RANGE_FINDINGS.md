# Ranges: definitions, detectors, boundaries, and why none of it trades

A systematic pass at "identify ranges and trade them", covering the standard
techniques and the structural levels traders actually watch.

## 1. How we defined "range" — six ways

| Approach | Implementation |
|---|---|
| Rolling statistical | 20-bar mean ± ATR bands |
| Trend-strength | ADX(14) below threshold |
| Efficiency | Kaufman Efficiency Ratio (net move / path length) |
| Purpose-built | Choppiness Index (Dreiss) |
| Volatility compression | Bollinger band width |
| Statistical | Rolling variance ratio, Hurst (R/S) |
| Structural anchors | PDH/PDL/PDC, PWH/PWL/PWO, PMO, session H/L |
| Auction | Prior-day value area (VAH/VAL/POC) |

## 2. Do the detectors predict forward mean reversion?

Scored on **forward** variance ratio over the next 48 bars (1h data, 730 days,
non-overlapping). A range detector is only useful if the state it reports NOW
predicts reversion LATER — scoring it on the same window it was computed from
is circular.

| Detector | Q1 fwd VR | Q5 fwd VR | t |
|---|---|---|---|
| ADX(14) | 0.908 | 0.852 | −1.06 |
| Efficiency Ratio(20) | 0.900 | 0.861 | −0.74 |
| Choppiness(14) | 0.829 | 0.874 | +0.87 |
| Bollinger width(20) | 0.935 | 0.862 | −1.28 |
| Variance Ratio(120,5) | 0.865 | 0.868 | +0.04 |
| Hurst(128) | 0.882 | 0.899 | +0.32 |
| EMA200 regime | 0.841 | 0.890 | +0.87 |

**None of them predict anything.** Note ADX runs *backwards* from convention:
low ADX ("range") predicted slightly less forward mean reversion.

**But the base rate is the real finding:** mean forward VR = 0.880, median
0.823, **P(VR < 1) = 67.2%**. BTC is mildly mean-reverting by default at this
horizon. The detectors add nothing because the state barely varies.

## 3. The oracle test — perfect foresight doesn't help

Bucket periods by their **actual future** variance ratio (cheating), then run
a fade-the-extreme strategy inside each:

| Bucket | fwd VR | trades | net | t | gross (0 fees) |
|---|---|---|---|---|---|
| Q1 most mean-reverting | 0.24–0.60 | 945 | −0.158% | −3.78 | −0.108% |
| Q3 | 0.76–0.92 | 1,383 | −0.234% | −4.69 | −0.184% |
| Q4 | 0.93–1.15 | 1,313 | +0.112% | +2.42 | +0.162% |
| Q5 most trending | 1.15–2.11 | 1,596 | +0.079% | +1.70 | +0.129% |

Fading loses *most* in the periods that mean-revert most, gross of fees.
Caveat: forward VR over 48 bars at q=5 is a noisy estimate, so treat the
inversion as suggestive. What is solid: even the cheating version does not
profit where theory says it must.

## 4. What actually determines a ceiling or floor?

First touch of each level, 15m bars, 730 days. `reject%` = price moved 0.3%
away within 8 bars; `break%` = price moved 0.3% through.

| Level | touches | reject% | break% | fade mean | t |
|---|---|---|---|---|---|
| PDH | 358 | 59% | 59% | −0.089% | −1.81 |
| PDL | 337 | 69% | 58% | −0.053% | −0.86 |
| PWH | 53 | 68% | 64% | −0.183% | −1.37 |
| PWL | 44 | 75% | 68% | −0.079% | −0.42 |
| PWO | 105 | 67% | 66% | +0.045% | +0.40 |
| ASIA_H | 359 | 69% | 61% | −0.003% | −0.05 |
| LDN_H | 480 | 66% | 60% | −0.077% | −1.42 |
| VAH | 474 | 58% | 47% | −0.029% | −0.68 |
| VAL | 445 | 64% | 51% | −0.089% | −1.95 |
| **POC** | 508 | **60%** | **43%** | +0.030% | +0.86 |

**Reject% and break% are both high for every price-based level.** Price does
both within 8 bars — it oscillates *through* the level rather than reversing
at it. Only the volume-profile levels show a real gap: POC 60/43, VAH 58/47.

**So the answer to "what determines a boundary" is volume, not prior price.**
Where trade actually occurred contains price better than where price merely
visited. That is the one mechanism with support in this data.

### The control that settles it

Random levels placed at the same distance from spot: **n=4,048, mean
−0.0388%, t=−3.12.** Fading an arbitrary price is reliably unprofitable —
that is the fee. So a real level must beat −0.039%, not zero.

| Level | fade mean | edge vs control | t |
|---|---|---|---|
| PWO | +0.045% | +0.083% | +0.40 |
| POC | +0.030% | +0.068% | +0.86 |
| ASIA_H | −0.003% | +0.035% | −0.05 |

POC and the weekly open beat the control by the most. Neither is significant.

## 5. Value area as a range — the decisive result

Prior-day value area, tested as an actual range (not just levels):

**15m bars, inside the VA, rotating to POC:**

| hold | n | mean | t |
|---|---|---|---|
| 4 | 18,964 | −0.056% | −16.27 |
| 12 | 18,963 | −0.064% | −10.55 |
| 48 | 18,963 | −0.041% | −3.40 |

**Outside the VA — and this is the important table:**

| hold | n | FADE back in | t | FOLLOW the break | t |
|---|---|---|---|---|---|
| 4 | 40,732 | −0.0503% | −20.13 | −0.0497% | −19.92 |
| 12 | 40,728 | −0.0491% | −11.77 | −0.0509% | −12.19 |
| 24 | 40,718 | −0.0462% | −7.94 | −0.0538% | −9.25 |

**Fade and follow are both negative, and they sum to −0.100% — exactly twice
the 0.05% fee.** They are exact opposites, so if either had a directional
edge the other would show the mirror image. Instead both lose by precisely
the transaction cost.

That is as clean a demonstration as this data can produce: **at the value-area
boundary the directional information is zero to three decimal places.** The
entire result is the fee.

Descriptive: VA width is a median **1.46% of spot**, and price sits inside the
prior day's value area only **41–42% of bars**. The "range" contains price
less than half the time.

## 6. Timeframes tested

5m, 15m, 1h. Holds of 4, 12, 24, 48 bars (20 minutes to 12 hours). Forward
windows to 48 hours. Nothing changed the sign.

## 7. Why it fails — the arithmetic, not the statistics

```
1h bar sd            = 0.480%
48-bar random walk   = 3.33%
48-bar actual (VR .88) = 3.12%
                       ──────
total reversion available = 0.21%   over two days
round-trip fee            = 0.05–0.10%
```

The mean reversion is real — VR<1 in 67% of windows. It amounts to **0.21% of
excess reversion spread across two days and every possible entry point.** Two
round trips consume it.

This is the same wall as sweep-reclaim (0.5bp), order flow (0.5bp) and
dip-buying. Range trading is not uniquely broken; it fails for the identical
reason. The edge is smaller than the cost of harvesting it.

## 8. What would change the answer

- **Maker-only execution.** At 0.01–0.02% instead of 0.05–0.10%, several
  results move from −0.05% to roughly zero. Still marginal, but it is the only
  lever that shifts every table at once.
- **Longer horizons, untested here.** Every test above is intraday. A weekly
  range 5% wide against a 0.05% fee is a 100:1 ratio rather than 30:1. Range
  trading on daily/weekly structure is a genuinely different question and is
  the one range variant these results do not condemn.

## Reproduce

```bash
python -m scalp_lab.run_ranges    # detector race on forward VR
python -m scalp_lab.run_oracle    # perfect-foresight bound
python -m scalp_lab.run_anchors   # structural levels vs random control
python -m scalp_lab.run_va        # value area as a range
```

---

# 9. Daily and weekly range structure

The one variant §8 did not condemn. Tested on 3,200 daily bars
(2017-11 to 2026-09, 8.8 years). Fee 0.10% round trip. **All results are
drift-adjusted** — BTC rose enormously over the sample, so every long-biased
rule looks good until the unconditional forward return is subtracted.

## 9.1 Detectors, again

| Detector | Q1 fwd VR | Q5 fwd VR | t |
|---|---|---|---|
| ADX(14) | 0.866 | 0.763 | −1.20 |
| Efficiency Ratio(20) | 0.846 | 0.746 | −1.22 |
| Choppiness(14) | 0.792 | 0.877 | +0.96 |

Nothing, same as intraday. Base rate: mean forward VR 0.821, **P(VR<1) = 73%**
— BTC is *more* mean-reverting by this measure at the daily scale, and it
still cannot be traded (see 9.3).

## 9.2 Explicit consolidation ranges

Defined causally: high/low of the last N days spans less than W%.

| N | W | bars in range | % of time |
|---|---|---|---|
| 10 | 8% | 441 | 14% |
| 15 | 10% | 371 | 12% |
| 20 | 12% | 404 | 13% |
| 20 | 15% | 759 | 24% |
| 30 | 15% | 368 | 12% |

BTC is in a tight daily consolidation roughly **12-24% of the time**.

## 9.3 Fading the edges — emphatically negative

**All 24 configurations lost.** A selection:

| N | W | band | hold | n | mean | t | win% |
|---|---|---|---|---|---|---|---|
| 30 | 15% | 0.15 | 10 | 72 | **−5.17%** | −4.24 | **26%** |
| 30 | 15% | 0.15 | 5 | 72 | −3.90% | −4.24 | 33% |
| 15 | 10% | 0.15 | 10 | 68 | −3.33% | −2.61 | 41% |
| 20 | 15% | 0.25 | 10 | 331 | −2.20% | −4.00 | 45% |
| 20 | 12% | 0.25 | 10 | 162 | −2.67% | −3.37 | 44% |

Random control at the same hold: −0.13%, t=−0.97. The fade losses are far
beyond control and far beyond fees. **This is a directional finding, not a
cost finding.**

## 9.4 The inverse (breakout) — real direction, unvalidated magnitude

Since fading loses, following should win, and in-sample it does: 32 configs
tested, nearly all positive, best +4.71% at t=+4.28.

**But it does not survive validation:**

| Config | train | TEST |
|---|---|---|
| N=20 W=12 hold=20 | +11.70% (t=5.14) | +1.41% (t=1.31) |
| N=20 W=15 hold=20 | +10.46% (t=4.23) | +1.83% (t=1.87) |
| N=30 W=15 hold=10 | — | +2.35% (t=1.95) |

By year for the best config: **2023 +8.61% (t=5.57, win 81%)**, 2025 −0.91%,
2026 −2.27% (win 35%). Other years had too few signals to score.

**2023 carries the entire result** — the year BTC broke out of the 2022
consolidation and trended. Also: worst single trade **−43.8%** on a 20-day
hold with no stop, and only **18.5 signals/year**.

Verdict: the *direction* is well supported (positive out-of-sample on all
three configs, positive across 32 in-sample configs). The *magnitude* is a
single-regime artifact. Not tradeable as specified.

## 9.5 Weekly and monthly levels on daily bars

| Level | hold | n | mean | t |
|---|---|---|---|---|
| PWH | 5 | 234 | +0.06% | +0.12 |
| PWL | 5 | 180 | −0.85% | −1.43 |
| PWO | 5 | 448 | +0.39% | +1.09 |
| PWO | 10 | 447 | +0.49% | +0.95 |
| PMH | 5 | 57 | −1.15% | −1.05 |
| PMO | 5 | 104 | −0.27% | −0.38 |

Nothing significant. The weekly open is again the least-bad level, as it was
intraday, but t=1.09 on n=448 is not an edge.

# 10. The complete answer on ranges

| Timeframe | Fade the boundary | Mechanism of failure |
|---|---|---|
| 5m – 1h | −0.05% | edge (0.21% per 2 days) is smaller than fees |
| Daily / weekly | **−1% to −5%** | boundaries are continuation points, not reversals |

Intraday it fails on **arithmetic**. At daily scale it fails on **direction**,
much more emphatically, and no fee reduction can rescue it.

**RULE: do not fade a range boundary on BTC at any timeframe.**

This is the third independent "don't" rule from this work:

1. Do not buy sweep-reclaims in a BEAR regime (−0.095R, t=−2.54, n=796)
2. Do not sell sweep-rejects in a BULL regime (−0.102R, t=−2.75, n=782)
3. Do not fade range boundaries at any timeframe (24/24 configs negative daily)

All three say the same thing in different words: **fading trend structure on
BTC loses money.** That is the single most replicated result in this repo.

## Reproduce

```bash
python -m scalp_lab.run_htf_range   # daily consolidation ranges + weekly levels
python -m scalp_lab.run_breakout    # the inverse, with train/test and by-year
```
