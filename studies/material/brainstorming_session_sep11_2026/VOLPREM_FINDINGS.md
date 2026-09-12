# Volatility premium, decomposed: call side, put side, covered call

Study 20. 2026-09-10. Prompted by a structural argument: the thing that made
[STRATEGY_SHORT_VOL](STRATEGY_SHORT_VOL.md) a NO-GO was that wings cost **43% of
premium**, so defined risk rescues no year. For a holder of spot, that cost is
zero — the coin *is* the wing. That argument is correct. Almost everything else
in the first version of this study was not, and this document leads with the
corrections because three independent adversarial reviewers each reimplemented
the work from the raw caches and **two of three refuted it**.

---

## 1. What reproduced (and it reproduced exactly)

Short 7d ATM straddle, non-overlapping, 285 weeks, 2021-03-24 to 2026-09-10:

| year | n | published | re-run | diff |
|---|---|---|---|---|
| 2021 | 41 | +2.389% | +2.389% | +0.000 |
| 2022 | 52 | +2.200% | +2.200% | −0.000 |
| 2023 | 52 | +0.861% | +0.861% | −0.000 |
| 2024 | 52 | +0.565% | +0.565% | −0.000 |
| 2025 | 53 | +1.375% | +1.375% | −0.000 |
| 2026 | 35 | +0.028% | +0.038% | +0.010 |
| **ALL** | **285** | **+1.264%** | **+1.265%** | +0.001 |

The 2026 gap is one extra week of data, not a methodology difference. The
document's `0.7979·σ·√T` closed form and a full Black-Scholes ATM straddle agree
to 0.002% of spot, so the approximation was hiding nothing. All three reviewers
independently confirmed this.

**One thing the published figures omit: fees.** Net of Deribit's real schedule
(taker min(0.03% of underlying, 12.5% of premium); 0.015% delivery, ITM only)
the 7d straddle is **+1.188%, not +1.264%** — and **2026 flips from +0.028% to
−0.038%**. The current year is already negative on a *naked* straddle before any
wing is bought.

## 2. RETRACTED: "the put leg is where the edge lives"

The first version of this study reported the leg split as its headline finding:

| leg | mean/wk net | t | positive years |
|---|---|---|---|
| short call | +0.378% | +1.28 | 4 of 6 |
| short put | +0.810% | **+3.12** | 5 of 6 |

and concluded the put side carries 68% of the P&L for 50% of the premium.

**This is an algebraic identity, not an empirical result.** At S=K with r=0,
Black-Scholes put = call exactly, so the two legs collect identical premium; and
`max(S_T−K,0) − max(K−S_T,0) = S_T − K` identically. A reviewer measured

```
max | put_gross − call_gross − spot_move |  =  0.000000000000   over all 285 trades
```

So "put +0.810 vs call +0.378" is a restatement of **"BTC returned +0.430%/wk"**,
the gap +0.432 *is* the drift, and "the put leg beat the call leg in 5 of 6
years" means "BTC's weekly return was positive in 5 of 6 years." The drift
itself has t = +0.96. There is no sample size at which the legs differ — the
residual is identically zero.

## 3. The covered call IS the cash-secured put

By put-call parity, a covered call at strike K and a cash-secured put at K are
the same position: identical outlay (S − c = K − p at r = 0) and identical
terminal payoff min(S_T, K). Verified week-by-week at every strike used:

```
ATM  CC 0.882418%  vs  CSP 0.882418%   max weekly divergence 2.22e-16
+2%  CC 0.893307%  vs  CSP 0.893307%   4.44e-16
+5%  CC 0.819451%  vs  CSP 0.819451%   2.22e-16
```

Consequences: the covered call harvests the *put* leg, not the "weaker" call
leg; and there is no separate cash-secured-put strategy to test — it was already
computed twice.

**Risk correction.** "Max loss is forgone upside rather than capital" is right
only *relative to holding spot*. In absolute terms a short put's capital
downside is undiminished, cushioned by ~3.3% of premium. The overlay's own
numbers say so: maxDD −32.9%, worst week −23.2%, and CC+10% returned −45.0% in
2022. It reduces risk versus spot; it does not cap it.

## 4. The covered-call result

Hold BTC, sell a 7d call, roll at expiry. 285 non-overlapping weeks, 5.47 years.

| structure | total | CAGR | maxDD | Sharpe | mean/wk | assigned% |
|---|---|---|---|---|---|---|
| **buy & hold spot** | +50% | +7.7% | **−75.9%** | 0.41 | +0.430% | — |
| covered call ATM | +642% | +44.3% | −32.9% | **1.34** | +0.809% | 51% |
| covered call +2% | +629% | +43.8% | −38.1% | 1.22 | +0.829% | 39% |
| covered call +5% | +450% | +36.6% | −50.8% | 0.98 | +0.770% | 20% |
| covered call +10% | +155% | +18.7% | −63.4% | 0.61 | +0.547% | 8% |

**Roughly half that CAGR gap is compounding, not the trade.** Holding exactly
1 BTC forever with call P&L to a cash sleeve:

| structure | total | CAGR | maxDD | Sharpe |
|---|---|---|---|---|
| buy & hold spot | +50% | +7.7% | −75.9% | 0.41 |
| **covered call ATM** | **+191%** | **+21.6%** | **−30.4%** | **1.06** |
| covered call +2% | +197% | +22.0% | −30.5% | 0.98 |

**Report +21.6%, not +44.3%**, if the coin count is meant to stay fixed.

### By year — this is a sideways-market trade

| year | spot | CC ATM | CC +5% |
|---|---|---|---|
| 2021 | −16.9% | **+52.2%** | +30.4% |
| 2022 | **−61.2%** | **+7.3%** | −20.7% |
| 2023 | **+154.3%** | +102.9% | +108.3% |
| 2024 | **+120.8%** | +74.8% | +116.7% |
| 2025 | −3.4% | **+39.0%** | +28.2% |
| 2026 | −14.3% | −7.8% | −8.1% |

It wins big in flat and down years and gives up 30–50 points of upside in bull
years. It is not free money; it is a trade on BTC going sideways.

## 5. Why two of three reviewers refuted it

**(a) Significance.** The clean test is the paired weekly difference versus spot:
**t = +1.28 / +1.54 / +1.62 / +0.84** across the four strikes. Not one reaches 2.
The log-return difference does better (t = +2.03/+2.32/+2.41) but the sample is
sharply left-skewed (skew −1.98 to −3.20); a studentized bootstrap gives
p = 0.031/0.021/0.021, which is **≈0.08 after Bonferroni over four strikes.**

**(b) Start-date dependence — the actual killer.** The entire headline lives in
2021–2022:

```
from a 2023 start:  CC +51.4%  vs  spot +53.4%
from a 2024 start:  CC +24.9%  vs  spot +24.2%
```

Over the most recent 2.5 years the overlay is a coin-flip against just holding.

**(c) Pricing error: r = q = 0 forces forward = spot.** Deribit prices off the
same-expiry futures forward. Measured live via put-call parity across 14 strikes
on BTC-18SEP26 (7.59d): **F = 77,441 vs index 77,370, +0.0923% of spot** (sd $4);
funding-implied forward premium over the sample **+0.161%/wk** (8.4% annualised,
19.2% in 2021). Repriced with Black-76 on the causal trailing-7d basis:

| | as published | forward-priced |
|---|---|---|
| call leg | +0.378% (t=+1.28) | **+0.464% (t=+1.57)** |
| put leg | +0.810% (t=+3.12) | **+0.733% (t=+2.82)** |
| gap | +0.431 | **+0.269** |

The straddle total (+1.188%) is unaffected, so §1 stands. But 0.086%/wk moves
from the put leg to the call leg, and "put beat call in 5 of 6 years" becomes
**4 of 6 published, 2 of 6 forward-priced.**

**(d) The flat-smile assumption understates the covered call.** Live 7d call skew
is positive (ATM 39.13, +5% 40.66, +10% 44.77 vol). Correctly priced,
**CC ATM CAGR +44.3% → +50.9%** and **CC +10% +18.7% → +31.3%**. This error runs
*in favour* of the strategy, which is why it must be fixed before the result is
believed in either direction.

### What the reviewers cleared

Worth recording, because it narrows what still needs work: no trap on the repo
list binds. Sampling is genuinely non-overlapping (zero date gaps across 1,997
consecutive days, all 7 offsets reported). No lookahead — the DVOL value is 12
hours *stale* relative to entry price, the conservative direction. Trap #4 is
satisfied and was verified live (7d ATM IV 39.1 vs DVOL 39.62 = 0.987×). The fee
model matches Deribit's published schedule. Options are European and cash-settled
so there is no early-exercise or delivery risk, and short-call margin is
comfortably covered by 1 BTC. The −1 to −4 vol-point execution haircut applied is
*over*-conservative: real mark-minus-bid at 7d is 0.05–0.63 vol points.

## 6. Verdict

**CONDITIONAL — not GO.** The structural argument holds: for a spot holder the
43% wing cost genuinely does not apply, and the overlay genuinely transforms the
risk profile (Sharpe 0.41 → 1.06, maxDD −75.9% → −30.4%, non-reinvested). Those
are mechanical properties, not statistical claims, and they survive every
objection.

What does *not* hold is the return claim. Paired alpha is t ≈ 1.3–1.6, survives
Bonferroni only at p ≈ 0.08, and disappears entirely from a 2023 or 2024 start.
The strategy is a bet that BTC goes sideways, sized as a permanent overlay.

**Before any capital:** reprice on the futures forward with a real call smile
(both errors run in opposite directions and must be fixed together), and test on
a path *distribution* rather than the one realised path — the start-date grid
shows how much of the result is one sample.

## 7. Reproduce

```bash
python -m scalp_lab.run_volsplit     # leg split, covered call, start-date grid
python -m scalp_lab.run_volprem      # the original 5.5-year straddle by tenor
```
