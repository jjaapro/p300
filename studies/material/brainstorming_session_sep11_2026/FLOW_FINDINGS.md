# Does observable flow predict? Levels, spot sells, ETF creations

Studies 17–19. 2026-09-10. Three independent flow hypotheses, three negatives.

All three ask a version of the same question: **is there a flow you can watch
that leads price rather than following it?** The answer in every case is no,
and in two of the three the naive version of the test produces a confident
false positive that only a placebo or a lag correction removes.

---

# 17. Order arrival rate at structural levels

**Question:** does the *count* of trades hitting the tape rise as price
approaches a level — not volume, not direction, arrival rate.

**Data:** 90 days of aggTrades (2026-06-10 to 09-07), aggregated to 1-minute
bars. Levels tested causally: prior-day high/low, prior-day close, round $1000
and $500.

**Three controls, because this measurement fakes a positive very easily:**

1. **Activity regime** — every minute normalised by its own trailing 12h median
   count. Raw counts swing an order of magnitude and price is more likely to be
   *at* an extreme when the tape is busy.
2. **Intraday seasonality** — each hour normalised against its own mean, so
   "levels get hit during US hours" cannot masquerade as an effect.
3. **Placebo levels** — each level shifted by a random 0.3–1.5% offset; round
   numbers offset by a fixed $317 / $211.

Significance across **days (n≤90)**, never across the ~130k minute bars —
minute bars are heavily autocorrelated (trap #1).

## The raw table looks like a result

| level set | 0-2bp | 5-10 | 40-80 | >80 |
|---|---|---|---|---|
| PDH/PDL | 0.935 (−1.9) | 1.009 (+0.4) | 1.027 (+1.6) | **1.058\* (+3.2)** |
| round1000 | 1.013 (+0.6) | **1.077\* (+2.9)** | 1.009 (+1.2) | — |
| round500 | 1.010 (+0.6) | **1.029\* (+2.3)** | — | — |
| PLACEBO PDH/PDL | 0.995 (−0.1) | 1.007 (+0.3) | 1.014 (+0.7) | **1.032\* (+2.8)** |
| PLACEBO round1000 | 0.976 (−0.8) | **1.053\* (+2.3)** | **1.028\* (+2.2)** | — |
| PLACEBO round500 | 1.009 (+0.4) | **1.044\* (+3.4)** | 1.000 (−0.0) | — |

**`PLACEBO round500` at 5–10bp scores t = +3.4 — higher than any real level
anywhere in the study.** A price offset by an arbitrary $211 shows a stronger
"level effect" than actual round numbers.

## The paired test kills it

Real minus placebo, paired by day (day effects cancel, far more powerful):

| real − placebo | 0-2bp | 5-10 | 20-40 | >80 |
|---|---|---|---|---|
| PDH/PDL | — | +1.0pp (+0.2) | −0.1 (−0.0) | +3.0 (+1.2) |
| PDC | — | — | −0.1 (−0.0) | +5.7 (+1.5) |
| round1000 | +5.7pp (+1.3) | +2.1 (+0.5) | +1.2 (+0.7) | — |
| round500 | −0.0 (−0.0) | −1.4 (−0.7) | +0.2 (+0.2) | — |

**Not one cell reaches |t| = 1.5** against a Bonferroni bar of 3.2 for 28 cells.
Every marginal result in the raw table dissolves once the placebo is subtracted.

The far-distance cells were measuring *distance-from-reference proxying for
having travelled*, which proxies for a busy tape. The 0–2bp bucket — right at
the touch, where a real effect should be **largest** — is empty everywhere.

**Limits:** 0–2bp is underpowered for PDH/PDL and PDC (<20 paired days). Round
numbers are well powered at the touch and show nothing. This measures executed
trades, not resting orders — whether limit orders *stack* at levels needs
`bookTicker`, discontinued ~2024-03, so that question is closed with public
history and would need a forward collector.

```bash
python -m scalp_lab.run_levelflow
```

---

# 18. Do spot sells mark local tops?

**Premise:** spot selling is real distribution, perp selling is leverage — so
spot sell pressure, especially spot selling *more* than perp, might lead a top.

**Data:** 90 days, Binance spot + USDT-M perp aggTrades joined into a 5-minute
panel (25,920 bars). Four causal signals, z-scored against a trailing 24h
baseline because the two markets have structurally different sell shares
(43.8% spot vs 48.6% perp on a sample day).

## Nothing at any horizon

| Horizon | Best Q5 forward | t | Q5 drawdown vs base |
|---|---|---|---|
| 1h | +2bp | +0.84 | −4bp |
| 4h | +9bp | +1.03 | +7bp |
| 12h | +32bp | +1.19 | +23bp |
| 24h | −36bp | −1.18 | +0bp |

Max |t| across 16 cells is **1.19** against a 2.9 bar. Signs flip across
horizons. The **divergence** signal — the actual hypothesis — was the flattest
of the four: Q5 drawdown +2, −3, −3, +0bp.

The drawdown column points the **wrong way**: high spot selling is followed by
*shallower* drawdowns, consistent with study #7 (aggressive flow is contrarian).

## The Aug 28 correction, hour by hour

81,117 → 77,101, −4.95% over 15 hours. Times UTC+3:

| Time | Price | z spot sell share | z spot volume |
|---|---|---|---|
| 08-28 00:30 | 80,061 | +1.47 | −0.44 |
| 08-28 03:30 | 80,394 | −0.41 | −0.14 |
| **08-28 04:30 — PEAK** | **81,117** | **−0.49** | **+4.55** |
| 08-28 05:30 | 80,025 | −0.75 | +0.76 |
| **08-28 10:30** | 79,695 | **+3.04** | +2.64 |

**The top was made on buying.** Spot volume at the peak was 4.55σ above normal
while the sell *share* was below average — that volume was overwhelmingly
aggressive buying. Buyers exhausting themselves, not sellers distributing.

The largest spot-selling reading of the episode, **z = +3.04, arrived six hours
after the peak**. The one strong pre-peak divergence spike (z = +3.72 at 00:30)
was followed by a further **+1.3% rally** into the actual high.

Across all six biggest corrections, mean z **before** each peak:

| signal | 6h before | 24h before | baseline |
|---|---|---|---|
| spot sell share | −0.11 | −0.02 | 0.00 |
| fut sell share | −0.05 | −0.02 | 0.00 |
| divergence | −0.06 | −0.00 | 0.00 |
| spot sell volume | −0.11 | +0.09 | +0.03 |

Selling was marginally *below* average before tops — all four signals, both
windows, wrong sign for the hypothesis. n=6, so not significant.

**Why the intuition feels right:** the selling is real, but concurrent and
lagging. Any chart viewed after a drop shows heavy sell volume near the top,
and the timestamps put it *after* the high. That is trap #2 (hindsight labels)
in a new form — the eye reads "selling at the top" from a picture that includes
the aftermath.

## The sample could not answer the question it was asked

```
2026-06-10 .. 2026-09-07   61,804 -> 79,076   (+27.9%)
24h drawdown: median -0.84%  p5 -3.40%  worst -5.99%
episodes worse than -5% (24h): 4
```

Ninety days of a rally whose worst 24h drawdown was −6%. **This is not
"hypothesis refuted" — it is "tested on a sample with no crashes in it."**
The definitive run is ~60 days around the **2025-10-06 ATH at $126,219** and
the −50% markdown after it. aggTrades reaches back far enough. Not yet run.

**Limits:** taker flow only — distribution via passive limit orders or OTC is
invisible. One venue pair.

```bash
python -m scalp_lab.run_spotsell
```

---

# 19. ETF flow

**Data:** TFTC open dataset (CC BY 4.0, tftc.io/bitcoin-etf-flows), 683 US
trading days, 2024-01-11 (launch) to 2026-09-09, with per-ETF breakdown and BTC
close. Cross-checked against `btc_study/2026-09-08_session/TAPE_BRIEF.md`:
Sep 1 −$236.5M and Sep 4 +$174.6M match exactly.

## Flow chases price

| correlation of flow z with | corr |
|---|---|
| **same-day** return | **+0.450** |
| prior-day return | +0.374 |
| prior 3-day return | +0.341 |

Money arrives *after* price moves. Flow for day T is published after the US
close on day T, so the earliest actionable moment is T+1. That +0.45 is what
makes "ETF inflows drove the rally" feel obviously true, and it is unusable.

## Measured from T+1, it predicts nothing

| Horizon | Q1 (outflow) | Q5 (inflow) | Q5 t | corr with fwd |
|---|---|---|---|---|
| 1d | +0.18% | +0.09% | +0.34 | −0.003 |
| 3d | +0.48% | +0.47% | +0.67 | −0.000 |
| 5d | −0.94% | +0.11% | +0.09 | +0.098 |
| 10d | −1.69% | +1.58% | +0.61 | +0.035 |

Max |t| = 0.67. **Big inflows do not mark tops** — Q5 forward returns are mildly
positive at all four horizons.

## The playbook tripwire is not supported

`TAPE_BRIEF.md` encodes *"two to three consecutive outflow days is the warning.
One red day is noise"*; `TAPE_ANALYSIS.md` escalates it to *"Act, don't debate."*

| Condition | n | fwd 5d | t | fwd 10d | t |
|---|---|---|---|---|---|
| baseline | 68 | −0.04% | −0.08 | −0.06% | −0.05 |
| 1 outflow day | 46 | −0.02% | −0.03 | +0.50% | +0.35 |
| **2 consecutive** | 38 | **+0.09%** | +0.08 | −0.22% | −0.14 |
| **3+ consecutive** | 31 | **+0.04%** | +0.04 | −0.82% | −0.46 |

Two consecutive outflow days are followed by a slightly **positive** excess
return at 5 days. Nothing reaches |t| = 0.5. The distinction the playbook draws
— one red day noise, two or three a warning — has no measurable content.

The rule is not harmful, it is decorative. It is currently written as an
act-without-debating trigger, which is a stronger claim than the data carries.
The same applies to the "invisible buyer" reading (ETF +$174.6M on the worst
down day interpreted as absorption): real observation, no demonstrated
predictive value.

**Limits:** 683 days covering a single cycle since launch; daily frequency only;
the 10-day horizon has 62 non-overlapping samples, so a moderate effect could
hide there.

```bash
python -m scalp_lab.run_etfflow      # data cached at cache/etf_flows.json
```

---

# What these three share

| study | naive result | what removed it |
|---|---|---|
| order arrival at levels | t up to +3.2 | placebo levels scored the same or higher |
| spot sells at tops | "obvious" on a chart | timestamps — the selling is 6h **late** |
| ETF flow | corr +0.45 with price | publication lag; from T+1 it is zero |

In each case the effect is genuinely visible and genuinely useless. Two of the
three required a control that did not exist in the first version of the test.
That is the reusable lesson: **for any flow-leads-price claim, build the
placebo or the lag first — the raw version will look significant.**
