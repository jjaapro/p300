# Liquidation map — findings

**Pre-registration:** `PREREGISTRATION_LIQMAP.md` v1.1, frozen F0 2026-09-18 10:02 UTC.
**Results:** `results/liqmap/{preconditions,freeze_F0,report,verdict,holdout_eth,secondary_section7}.json`,
`q1_states.csv.gz`, `q1_states_eth.csv.gz`, `q2_events.csv.gz`, `q2_events_eth.csv.gz`, `trades.csv.gz`.
**Verdict file:** `REPLICATED: up_touch, down_touch`. **What it is worth:** nothing for any strategy, and — once §7's
distance diagnostic is applied — nothing about the map's placement either. Read §4 below before quoting §3.

## 1. What was asked

Whether an estimated liquidation map built from public open interest and taker flow carries information an exit could
use: a **magnet** (price is drawn to dense clusters), a **turn** (price reverses once a cluster is consumed) and an
**end of forced flow** (a burst of estimated liquidations in a trade's favour is a time to leave).

Every test compares the actual map against a **control map built by the identical code path with the information set
to a constant** — one unit of mass every bin, half long, half short, no open-interest-decrease removal — so the
comparison is against what the model would say knowing only the price path.

## 2. Preconditions (all passed)

| | |
|---|---|
| L1 | 3,954 archive zips re-verified against their checksums; every built array, panel, feature file and ledger matched its recorded hash |
| L2 | chento 392 (208 BTC / 184 ETH, 176 ETH from 2022-01-01) = A0; squeeze_bull 122 = S0; secondary 301. Entries equal the perp close before the entry minute exactly for both decision populations |
| L3 | 57 fixtures |
| L4 | five causality cuts on the real BTC series, and five more on ETH: zero differences in every map, feature, threshold and event |
| L5 | day convention pinned first (2025-10-10 $102.5 M long vs $12.9 M / $16.0 M; 2024-08-05 $53.2 M vs $12.8 M / $8.0 M, both "day start"). Raw ρ against Coinalyze 0.761 long / 0.758 short at lag 0, both neighbouring days below 0.35 |
| L6 | counts fixed the family before any outcome |
| L7 | coverage by year; the 2021-05-22 glitch is the largest single-bin jump, as disclosed |
| L8 | the alignment step lands exactly where §2 said: +60 s from 2022-07 through 2024-02, −240 s from 2024-03 to 2026-04 — one bin, at the predicted date. Three single months sit off those runs (2022-05, 2022-06, 2025-04) and are exactly the three with the largest median relative difference, i.e. the noisiest fits |

**The map's amounts are real.** Controlled for the day's range, the estimated daily liquidation series still
correlates 0.572 (long) and 0.555 (short) with the measured series, against 0.363 and 0.283 for a map-free naive
series. So the model is not merely a volatility proxy at saying *how much* is liquidated.

**Its side split is not.** The long-share correlation is 0.680 against the naive series' 0.721, so §5's caveat
triggers: the taker-share rule that decides whether new open interest becomes long or short mass adds nothing over
`(open − low)` versus `(high − open)`. That is a specific, testable weakness of *this* model, not of the idea.

**The take-profit arm could not be decided.** EV1 needs 30 included trades and got 9 (74 of 208 chento BTC trades have
a defined cluster at entry, 40 reach it inside the trade, 9 survive the ≥ 3 era-matched controls). The all-years pool
would have given 25 — still short — so no amendment rescues it. Family: the four Q1 tests plus `chento:EV2`.

## 3. What the pre-registered tests returned

**Q1 touch — INFORMATIVE on BTC, REPLICATED on ETH.**

| | n | Δ | 95 % CI | Holm p | halves | actual / control rate |
|---|---|---|---|---|---|---|
| BTC up_touch | 6,515 | +2.87 pp | +1.74, +4.07 | 0.0005 | +3.01 / +2.73 | 0.209 / 0.181 |
| BTC down_touch | 6,414 | +2.20 pp | +1.25, +3.19 | 0.0005 | +2.18 / +2.21 | 0.214 / 0.192 |
| ETH up_touch | 5,298 | +4.10 pp | +2.60, +5.62 | — | +4.42 / +3.78 | 0.269 / 0.228 |
| ETH down_touch | 5,073 | +2.80 pp | +1.38, +4.10 | — | +1.66 / +3.94 | 0.250 / 0.222 |

Positive in every BTC year and in four of five ETH years.

**Q1 turn — UNDETERMINED on BTC, negative on ETH.** Given both levels were touched, price turns at the actual
cluster *no more* than at the control's: BTC −2.03 pp (−7.17, +2.50) and −1.75 pp (−7.20, +3.09); ETH −6.84 pp
(−12.90, −1.01) and −5.47 pp (−10.92, −0.25), both ETH intervals wholly on the wrong side. The consumed-cluster
reversal is not there.

**Q2 — no exit information.** `chento:EV2` (leave on a burst of estimated liquidations in your favour) gives
−0.26 R against its era-matched controls (CI −0.92, +0.42, p 0.23), and the paired comparison against the identical
rule on the control map is **+0.04 R (p 0.65)** — the real burst and the price-path-only burst are indistinguishable.
The sign control `EV2_against` sits at −0.07 R. On ETH the same test is +0.19 R with a +0.27 R placebo comparison.
squeeze_bull's EV2 (n = 34) is −0.13 R with a +0.01 R placebo comparison. Nothing survives.

## 4. What the replicated result actually is (§7, reported, decisive for interpretation)

§7 reserved one diagnostic for after the verdict: Δ by distance bin and by the sign and size of `d_A − d_B`. It
dissolves the touch result.

The actual map's cluster sits **nearer price than the control map's in 64.8 % of states** on both assets (mean gap
−0.44 % of price; median distance 3.6 % against 4.1 % (BTC) and 4.2 % (ETH)). A nearer level is mechanically easier to touch inside 24 h.
Splitting by that sign: where the actual cluster is nearer, Δ = +7.4 pp (BTC) and +8.5 pp (ETH); where the control's
is nearer, Δ = −9.5 pp and −8.4 pp; where the distances are equal, both maps name the same bucket and Δ = 0 exactly.

Matching on distance removes the entire effect:

| tolerance on `|d_A| − |d_B|` | BTC n | BTC Δ (95 % CI) | ETH n | ETH Δ (95 % CI) |
|---|---|---|---|---|
| all states | 12,929 | +2.54 pp (+1.78, +3.33) | 10,371 | +3.46 pp (+2.53, +4.42) |
| ≤ 50 bp | 5,328 | +0.15 pp (−0.23, +0.59) | 3,968 | +0.20 pp (−0.18, +0.56) |
| ≤ 20 bp | 3,102 | +0.10 pp (−0.09, +0.30) | 2,299 | +0.09 pp (−0.08, +0.27) |
| ≤ 20 bp, distinct levels | 1,628 | +0.18 pp (−0.16, +0.58) | 1,215 | +0.16 pp (−0.16, +0.51) |
| turn, ≤ 20 bp | 566 | +0.18 pp (−1.54, +2.00) | 527 | −0.95 pp (−2.87, +0.85) |

**So: the liquidation map's information does not tell you where price will go. It tells you to put your level nearer,
and nearer levels get touched more.** Two levels at the same distance are touched equally often whether the map knew
about open interest and taker flow or only about the price path.

This is the same trap the pre-registration was hardened against, one level deeper. The control map removed the
*amount* information but kept the price path, and the price path alone still produced clusters — just farther out.
Pairing within a state was not enough; the levels also had to be paired on distance. Next time a level study is
pre-registered, **matching on distance belongs in the primary test, not in §7.**

## 5. Verdict and what it permits

- The pre-registered verdict is `REPLICATED: up_touch, down_touch`, and it must always be quoted with §4.
- §5 already ruled that a replicated Q1 without a replicated Q2 **permits nothing** for chento or squeeze_bull. §4
  removes even the descriptive claim: no evidence the map places levels better than the price path does.
- No production change. No stage-B pre-registration is earned.
- The estimated-liquidation **amount** series is worth keeping (range-controlled ρ ≈ 0.56 against measured
  liquidations, well above the naive proxy). Its side split is not.

## 6. Do not re-propose

- "Take profit at the nearest dense liquidation cluster" on chento or squeeze_bull — undecidable at this frequency
  (9 included trades) and, on the level test, indistinguishable from a price-path level at the same distance.
- "Exit on a burst of estimated liquidations in your favour" — null on BTC and ETH, against a placebo built by the
  same rules.
- This model's **taker-share side split** — beaten by `(open − low)` vs `(high − open)`.

A future liquidation-map idea needs a different input, not a different dial: real liquidation prints at second
resolution (the collector now records them from Bybit and OKX, where the full stream is public), or venue-published
liquidation levels. Both are outside the public 5-minute archive this study was built on.
