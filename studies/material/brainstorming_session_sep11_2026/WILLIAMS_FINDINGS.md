# Williams %R — strategy search, 5m to daily

Study 16. 2026-09-10. Prompted by a user-supplied indicator and a
user-identified real trade, which is why the design is not a fit: the
parameters (%R 56, 1h signal, levels −80 / −20) came from outside the search.

**Verdict: CLOSED, negative.** No tradeable edge at any timeframe. One
conditional split (grade A, t=+3.58) survived every correction — but it is
assigned 6h after entry, and the follow-up test showed it is not predictable at
entry in any way that transfers to the payoff (§5). The swing variant's
apparent edge comes entirely from an exit overlay that fails TRAIN → TEST.

`%R = 100·(close − HH(n)) / (HH(n) − LL(n))` is the fast Stochastic %K minus
100 — identical information, different offset. It is also position within the
Donchian channel, so §9.3 of [RANGE](RANGE_FINDINGS.md) already covered the
daily fade case under a different name.

---

## 1. The arithmetic that framed everything

Before any test: BTC at ~45% annualised vol has a daily sigma of 2.355%, so
sigma at horizon T is 2.355%·√T. Edge must exceed 2× round-trip cost. Across
every effect measured in this repo, **price prediction captures 3–13% of
available sigma, clustering near 5%**; risk premia capture 20–80%.

> **CORRECTED 2026-09-11 (audit).** The σ(T) column below originally used a
> flat 2.355%/day constant. That is right for the current regime (2023-26
> realised: 2.421%/day) but **−32% off for long samples** (full 2017-26:
> 3.456%/day; 2021-26: 2.993%/day). Recomputed against each effect's own sample:

| effect | horizon | edge | σ(T) corrected | edge/σ | was |
|---|---|---|---|---|---|
| ofi5 order flow | 5m | 0.5bp | 14bp | 3.5% | 3.6% |
| best 3-signal combo | 1h | 2.5bp | 49bp | 5.1% | 5.2% |
| post-cascade reversion | 4h | 10.9bp | 99bp | 11.0% | 11.3% |
| daily breakout, OOS | 20d | 141bp | **1546bp** | **9.1%** | ~~13.4%~~ |
| short straddle | 7d | 126bp | **792bp** | **16.0%** | ~~20.3%~~ |

**The measured ceiling is therefore ~3.5–11%, not 3–13%**, and every correction
runs the same way: capture down, required holding period up. The
closed-horizon conclusions get *stronger*, not weaker. Note the scripts
(`run_absorption*.py` and the workflow studies) measured σ per panel and are
unaffected; the contamination was in hand-written summary tables only.

Combining the ceiling with a fixed fee gives a **minimum viable holding
period**:

| execution | round trip | at 5% capture | at 9.1% (the corrected ceiling) |
|---|---|---|---|
| taker/taker | 10bp | **65.5h** | 19.8h |
| MEXC taker/taker | 8bp | 41.9h | 12.7h |
| MEXC maker/maker | 4.1bp | 11.0h | 3.3h |

(Recomputed 2026-09-11 against current-regime σ of 2.421%/day and the corrected
9.1% ceiling. The earlier version of this table used an uncorrected 13% ceiling
and so understated every required hold.)

Maker execution is **not** the rescue three earlier docs hoped for. Binance
futures maker is 0.02%/side, so maker-maker is 4bp round trip, not zero —
against a 0.5–2.5bp edge that is still negative before adverse selection.

**Corollary, measured directly:** what one %R point is worth in price.

| signal TF | 14-bar range | 1 %R pt | TP1 (10 pts) | TP2 (20 pts) |
|---|---|---|---|---|
| **15m** | 0.98% | 1.0bp | **9.8bp** | 19.6bp |
| 1h | 2.21% | 2.2bp | 22.1bp | 44.1bp |
| 4h | 4.75% | 4.7bp | 47.5bp | 95.0bp |
| 1d | 16.17% | 16.2bp | 161.7bp | 323.4bp |

On 15m a −10 → −20 target is **9.8bp against a 10bp round trip**. Hitting your
first take-profit loses money. This killed the first two script designs before
any statistics were needed.

## 2. Intraday — no signal, not even a cost problem

36 configs, 3 lengths × 4 holds, drift-adjusted, non-overlapping, 1177 days.

| TF | best gross | t | n |
|---|---|---|---|
| 5m | +1.1bp | +1.08 | 7,098 |
| 5m | +0.5bp | +1.76 | 20,663 |
| 1h | ±3bp | <1.1 | — |

All 36 land between −1.1 and +1.1bp gross. **At zero fees it is still not
significant** (best t=1.76 vs a ~3.2 Bonferroni bar). This is not a fee
finding — there is no signal underneath the cost.

Live confirmation on TradingView, 15m INDEX:BTCUSD, Feb–Sep 2026: 382 trades,
41.9% win, **PF 0.818**, −2.37%. Commission bill ≈ $382 against a −$237 net,
i.e. gross ≈ +4bp/trade eaten by a 10bp fee. The wall, on the user's own chart.

## 3. Daily — right polarity, fails out of sample

3,200 daily bars, 24 unique tests.

| len | signal | hold | n | gross | net | t | win% |
|---|---|---|---|---|---|---|---|
| 20 | FOLLOW long (%R≥−20) | 1d | 821 | +24.4bp | +14.4bp | **+2.17** | 49.9 |
| 30 | FOLLOW long | 20d | 78 | +295bp | +285bp | +1.69 | 55.1 |
| 30 | FADE short (%R≥−20) | 20d | 78 | −295bp | −305bp | −1.69 | 44.9 |

Buying %R overbought beats selling it at **every** length and hold. Textbook
%R says sell at −20; on BTC that is the losing side. This is the fourth
independent replication of *do not fade trend structure* (after sweep-reclaim,
sweep-reject and range boundaries).

The one nominally significant config does not survive:

```
TRAIN n=469  +25.9bp net  t=+2.08  win 51.2%
TEST  n=352   −1.6bp net  t=+0.68  win 47.7%
```

Win rate 49.9% with a positive mean = trend-following payoff shape, winning by
magnitude not frequency.

**PERSIST variant** (k consecutive days pinned at an extreme) was the only cut
with consistent sign: *pinned high → long* net-positive in **17 of 18** configs;
*pinned low → short* negative in 12 of 18. Best single: len14, k=5, hold 10d,
+281.8bp net, t=+2.16, n=57 — underpowered, not tradeable, but it is a
*duration* measure that ADX / Efficiency Ratio / Choppiness all failed to
capture in §9.1 of RANGE.

## 4. The swing design — the real one

Derived from a trade the user identified on the chart, then located in the data.
BTC, %R(56) on 1h, executed on 15m, times UTC+3:

| | time | %R | close | |
|---|---|---|---|---|
| Entry | Sep 2 16:00 | −88.28 → **−70.46** | 77,146 | crosses **up** through −80 |
| TP1 | Sep 3 15:00 | −43.03 → **−19.22** | 78,652 | crosses **up** through −20 (+1.95%) |
| Exit | Sep 4 03:00 | −17.06 → **−22.00** | 80,972 | crosses **down** through −20 (+4.96%) |

The −20 is a **rollover trigger, not a price target**. Entry is a turn up out
of oversold; exit is momentum rolling over at the top. TP1 and exit share the
level −20 and are still different events — one is a cross up, one a cross down.

Median hold 63 hours. ~500bp of move against a 10bp fee is a 50:1 ratio versus
roughly 1:1 for the same levels read on 15m. **This is a low-timeframe swing,
and it is the first %R variant at a timescale the arithmetic permits.**

### The stop loss is the bug, not the levels

| Stop | Trades | Median hold | Net/trade | Total |
|---|---|---|---|---|
| **None** | 264 | 63h | **+0.53%** | **+279%** |
| 5% | 338 | 48h | −0.01% | −20% |
| 3% | 422 | 37h | −0.07% | −36% |

BTC's sigma over 63 hours is ~4%, so a 3–5% stop sits **inside the noise** and
cuts trades that would have reached the rollover. The %R cross-down *is* the
exit. Answer to "percent or time stop": neither, as a trading stop.

### But the headline number does not survive the drift control

| | |
|---|---|
| Raw net per trade | +0.530% |
| Buy & hold, same span | **+246.7%** (strategy +279%) |
| Time in market | **45.8%** |
| **Drift-adjusted per trade** | **+0.238%, t = +1.65** — not significant |
| TRAIN → TEST | +0.458% (t=+2.36) → **−0.090% (t=−0.43)** |
| By year | 2023 +0.617% (t=+2.74) carries it; **2026 −0.344%** |

Same shape as the §9.4 daily breakout: strong in-sample, one year carrying it,
gone out of sample. 730-day sample agrees: +0.137%, t=+0.71, TEST −0.122%.

**The one property that does survive:** it matched buy-and-hold while in the
market 46% of the time. Halving exposure for the same return is a real
risk-adjusted improvement and, unlike the alpha, is not a statistical claim.

### The signal contributes nothing; the overlay produces the number

Entry → rollover with **no TP1**, each leg a fixed 10% of equity:

```
leg drift-adjusted  −0.197%   t = −0.87
```

With the TP1-at-−20 partial and breakeven stop it becomes +0.238%, t=+1.65.
**The entry signal has no edge on its own.** A breakeven stop reshapes a
zero-mean distribution into frequent small wins and capped losses, which on a
finite sample reliably looks like an edge — trap #5 in a new costume, and it is
precisely the component that fails TRAIN → TEST.

## 5. Trade grading — the only strong result

Grade assigned causally from the first 6 hours after entry. **C** = %R fell back
through the entry level inside that window; **A** = it did not.

| Cohort | n | raw/trade | drift-adj | t | win% | hold |
|---|---|---|---|---|---|---|
| **Grade A** | 99 | +1.016% | **+0.782%** | **+3.58** | 69.7% | 50h |
| Grade C | 165 | +0.239% | −0.089% | −0.48 | 52.1% | 70h |

Difference **+0.871%, t = +3.03**. Grade-C trades contribute nothing and hold
*longer*. This clears the correction bar for the number of tests run and is the
only result in the study that does.

**It is not actionable as stated** — the grade is known 6 hours after entry.

### ANSWERED 2026-09-10: not predictable at entry in any useful way

Nine pre-registered causal features at the entry bar, 263 trades.

| feature | mean in A | mean in C | diff t | corr w/ return | t |
|---|---|---|---|---|---|
| **ema200_dist** | −0.12% | −0.97% | **+2.97** | −0.037 | −0.60 |
| r_vel3 | −3.45 | +2.55 | −2.48 | −0.052 | −0.84 |
| bars_below | 3.63 | 5.33 | −2.13 | +0.001 | +0.01 |
| r_vel1 | 13.21 | 11.00 | +2.11 | −0.015 | −0.25 |
| rvol24 | 2.16 | 2.04 | +0.93 | +0.131 | +2.13 |

`ema200_dist` clears the 9-feature bar (|t| ~ 2.7) and the story is intuitive:
grade-A entries happen near or above the 200h trend, grade-C entries happen
about 1% below it — a %R oversold-turn taken well under the trend is a falling
knife. **But it separates the LABEL, not the PAYOFF.** Its correlation with the
trade's drift-adjusted return is −0.037 (t=−0.60), i.e. zero.

A weak separator of a strong label transfers almost none of the label's return
advantage, and the split is unstable:

```
TRAIN  above median +0.235%   below +0.689%   diff t = -1.16   (wrong sign)
TEST   above median +0.217%   below -0.397%   diff t = +1.46
```

The sign flips between train and test. **Grade A stays hindsight, and with it
the %R line closes.** No feature correlates with the payoff at the corrected
bar (best is rvol24, t=+2.13, against a bar of ~2.9 for 18 feature-target
tests).

## 6. Every rescue rule loses

~40 management variants on the same 1500 days. None beat leaving the trade alone.

| Scheme | n | hold | drift-adj | t | Total | maxDD |
|---|---|---|---|---|---|---|
| **base — do nothing** | 264 | 63h | **+0.238%** | +1.65 | **+278.7%** | −23.2% |
| C → BE on retouch | 523 | 27h | +0.052% | +0.74 | +138.5% | −24.9% |
| C → time stop 24h | 393 | 33h | +0.015% | +0.14 | +77.8% | −33.4% |
| C → fast TP −60 | 345 | 39h | +0.114% | +1.10 | +161.9% | −17.2% |
| C → cut at market | 712 | 20h | −0.024% | −0.44 | +52.9% | −31.2% |
| C → scale to 30% | 264 | 63h | +0.179% | +1.22 | +223.2% | −24.7% |
| C → cut + 72h cooldown | 245 | 21h | +0.072% | +0.70 | +47.1% | **−10.2%** |

**Why the conditional split does not convert into a rule:** cutting a grade-C
trade frees the setup to re-enter the same chop. The Aug 11 case becomes three
trades instead of one — out after 1h (+0.05%), back in, out after 1h (−0.04%),
back in, out after 5h (−0.97%). Three round trips to be chopped three times.
Scaling down avoids the whipsaw but is then smaller in the trades that recover.
The grade-C bucket **contains the recoveries** and they cannot be separated at
the grading moment.

### Strike exits are actively harmful

Exit on the Nth time %R falls back through the entry level:

| Rule | n | hold | drift-adj | t | Total |
|---|---|---|---|---|---|
| strike 1 | 1179 | 11h | −0.127% | **−2.95** | −64.4% |
| strike 2 | 655 | 23h | −0.144% | −1.73 | −32.1% |
| strike 3 | 491 | 33h | −0.159% | −1.42 | −17.1% |

**t = −2.95 is the most significant number in the study and it is negative.**

### There is no money in the whipsaw

Forward return from a strike bar to the trade's eventual exit, drift-adjusted:

| measured from | n | mean | t |
|---|---|---|---|
| the original entry | 264 | −0.081% | −0.36 |
| strike 1 bar | 211 | −0.056% | −0.22 |
| **any strike bar** | 915 | **−0.284%** | **−2.13** |

The dip back under −80 is a slightly *worse* entry, not a better one. Adding
legs there is a wash once capital is counted honestly (see trap #9):

| Variant | avg capital | per-leg drift-adj | t | Total | vs base at equal size |
|---|---|---|---|---|---|
| no adds | 10.0% | −0.197% | −0.87 | +2.6% | — |
| add 1 strike | 18.0% | −0.187% | −1.09 | +5.3% | base 1.8× = +4.4% |
| add 3 strikes | 28.6% | −0.258% | −1.84 | +2.4% | base 2.9× = +6.6% |

### The Aug 11 case, in full

Entry 63,987 at 00:00 UTC+3, %R −78.89. Back under −80 one hour later.
Ten strikes over 140 hours, exit −0.57%. Not a *loss* problem — a
**capital-efficiency** problem: 5.8 days of capital for roughly nothing. The
fix for that is more instruments, not a tighter stop.

## 7. Test ledger

Recorded so the correction can be applied honestly if anything is ever
selected out of this search:

| block | tests |
|---|---|
| intraday %R (5m, 1h) | 36 + 36 |
| daily %R | 24 |
| MTF daily-signal/15m | 36 |
| PERSIST | 18 |
| swing stop variants | 12 |
| grade × rescue schemes | ~40 |
| strike / pyramid | 14 |
| **total** | **~216** |

Bonferroni bar at 216 tests is |t| ≈ 3.6. Grade A at t=+3.58 is at that bar,
not comfortably past it.

## 8. Reproduce

```bash
python -m scalp_lab.run_williams        # 5m / 1h / daily, both polarities
python -m scalp_lab.run_williams_mtf    # daily signal, 15m execution + PERSIST
python -m scalp_lab.run_wr_swing        # the 1h swing, stops, drift, train/test
python -m scalp_lab.run_wr_grade        # grading and the rescue matrix
python -m scalp_lab.run_wr_strikes      # Nth-strike exits
```

Pine: `williams_r_levels.pine` (W%R Swing, with grade/strike chart diagnostics),
`williams_r_lab.pine` (the earlier price-distance version, superseded).

## 9. What would settle it

1. ~~**Entry-time predictors of grade A.**~~ **DONE — negative.** See §5.
   Grade A is weakly predictable (ema200_dist, t=+2.97) but the predictor has
   zero correlation with the payoff and flips sign out of sample.
2. Extend the sample — Binance 1h reaches back to 2017, roughly doubling 264
   trades.
3. Run the same rule on ETH and SOL — cross-sectional out-of-sample is harder
   to fool than another time split.
4. Turn on the short mirror. If the long-only result is mostly drift capture,
   the short side exposes it immediately.
