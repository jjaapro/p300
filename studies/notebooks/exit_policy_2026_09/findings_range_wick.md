# Exit-policy study, stage R — move vs implied range (I) and the rejection-wick exit (J): findings

**CONCLUDED 2026-09-19. Verdict: NONE PROMOTED.** Neither the first minute at which a trade's favourable move
exceeds one option-implied daily move, nor the first 15-minute bar that rejects from a causal level while the trade
is ≥ 0.3 R in profit, carries exit information on chento BTC or squeeze_bull. All three family tests are
UNDETERMINED with **positive** point estimates: holding after the event paid *more* than holding at matched moments,
not less. Run as exit arms, both lose money on every population. This closes rows I and J of the exhaustion
brainstorm at this resolution (1-minute perpetual klines, daily DVOL, 15-minute bars); nothing in production
changes.

Design: [PREREGISTRATION_RANGE_WICK.md](PREREGISTRATION_RANGE_WICK.md) v1.0 with amendment A1 (recorded before the
freeze: the squeeze_bull population is the corrected-open-interest re-cut; one causal-mask defect fixed). Frozen
2026-09-18 23:25 UTC, outcome 23:27 UTC. Review: [07_range_wick_information.ipynb](07_range_wick_information.ipynb).
Results: `results/range_wick/`.

## 1. The decision lines

`Δ` = continuation value at the first event − mean continuation value of matched control minutes (same
subpopulation and direction, non-overlapping, same 0.25 R profit bin, fresh extreme, no prior event, status known;
±365 days under rungs 1–2). Negative would mean the rest of the trade was worth less after the event.

| Test | rung | n | Δ (R) | 95 % | p(Δ<0) | halves | holding after | matched | Holm p | label |
|---|---|---|---|---|---|---|---|---|---|---|
| chento-BTC · I1 implied | 3 | 49 | **+0.256** | −0.47, +1.04 | 0.76 | +0.98 / −0.50 | +0.91 | +0.66 | 1.0 | UNDETERMINED |
| chento-BTC · J wick | 1 | 107 | **+0.371** | −0.22, +0.95 | 0.90 | +0.46 / +0.28 | +0.89 | +0.52 | 1.0 | UNDETERMINED |
| squeeze_bull · J wick | 1 | 51 | **+0.077** | −0.20, +0.40 | 0.70 | +0.15 / +0.00 | +0.21 | +0.13 | 1.0 | UNDETERMINED |
| chento-ETH · I1 implied (replication) | 3 | 40 | −0.921 | −1.99, +0.31 | 0.048 | −0.74 / −1.10 | +0.33 | +1.25 | — | descriptive |
| chento-ETH · J wick (replication) | 1 | 77 | +0.187 | −0.64, +1.10 | 0.66 | −0.45 / +0.85 | +0.77 | +0.58 | — | descriptive |
| squeeze_bull · I1 implied | — | 7 | −0.080 | −0.14, −0.02 | — | — | +0.32 | +0.40 | — | descriptive (15 events, 7 at the target minute) |

**The decision controls say the legs carry nothing.**

- *I, the implied scale.* On chento-BTC the same rule on the trailing realised scale (`I1_realised`, n 56) gives
  Δ +0.07, and the shared-pool contrast implied − realised is **+0.22 R (95 % +0.002 to +0.46)**: the option-priced
  event precedes *better* continuation than the realised-vol event, the opposite of the claim. Where only the
  implied scale had been crossed (`I1_implied_only`, n 10) Δ is +0.67. The day form (`I2_day`, n 66) is −0.19 with
  its realised control at +0.05, both inside noise.
- *J, the level.* The same armed rejection shape away from any level (`J_nolevel`, n 120) gives Δ −0.08; the
  contrast wick − no-level is **−0.03 R (95 % −0.28 to +0.22)**: the level adds nothing to the shape, and the shape
  itself (`J_shape`, n 140, +0.15) adds nothing to being in profit at a fresh extreme. The level reached and held
  (`J_accept`, n 104) is +0.09; the 1-minute arm (`J_wick_1m`, n 103) is −0.07 with its no-level control at +0.16.
  On squeeze_bull every J line sits between −0.04 and +0.09.

**Every placebo agrees.** Under rungs 1–3, S1–S4, R-vol and R-session the chento-BTC J line stays between +0.22 and
+0.47 and the I1 line between +0.11 and +0.55 (R-session, n 11); on squeeze_bull the J lines are −0.06 to +0.11.
Nothing moves when the matching rule changes.

## 2. The ETH line, read honestly

The chento-ETH implied-range line is the one number in this study that would have looked like a result had it been
a decision line: Δ −0.92 R (p 0.048 one-sided, both halves negative, every placebo negative, the shared-pool
contrast −0.52 with 95 % −0.97 to −0.09, the non-overlap subset −1.14 on 18 trades). It is not a result, for three
reasons the pre-registration fixed in advance. It is the replication set, read only after a family test is
INFORMATIVE, and none is. Its sign is the opposite of the BTC line on the same rule (+0.26) and of its own realised
control (+0.10): the implied-range crossing on ETH would carry information that the same crossing on BTC and the
same-sized move on ETH do not, which is not a mechanism. And it is one of 382 reported lines, 31 of which exclude
zero, 24 of those on lines with ten or fewer trades; its own interval (−1.99 to +0.31) includes zero. Inside it the
years disagree too (2025 −2.44 on 12 trades, 2026 +1.26 on 7). It is recorded so that nobody rediscovers it later as
new; it is not a reason to re-open the question.

## 3. As exits, both lose

The reported overlay: exit at the close of the first event minute, otherwise the shipped exit, paired per trade over
the whole subpopulation (non-event trades contribute zero), price only.

| Overlay | trades / events | Δ R per trade vs shipped | 95 % | event trades that stopped out | that hit the target |
|---|---|---|---|---|---|
| chento-BTC · I1 implied | 208 / 80 | **−0.28** | −0.53, −0.05 | 8 | 16 |
| chento-BTC · J wick | 208 / 152 | **−0.55** | −0.91, −0.20 | 44 | 23 |
| chento-ETH · I1 implied | 184 / 77 | −0.18 | −0.47, +0.11 | 15 | 11 |
| chento-ETH · J wick | 184 / 118 | **−0.42** | −0.80, −0.06 | 36 | 15 |
| squeeze_bull · I1 implied | 122 / 15 | −0.01 | −0.05, +0.03 | 0 | 13 |
| squeeze_bull · J wick | 122 / 70 | −0.09 | −0.20, +0.03 | 8 | 36 |

Paladin's +0.12 R per trade does not transfer. On chento the rejection-wick exit dodges 44 stops and still costs
0.55 R per trade, because it books 152 of 208 trades at a median 8 hours and +0.60 R while the shipped plan takes
those same trades to +1.35 R: the trades it cuts are the ones that keep paying. This is the same shape as every exit
arm tried on chento since the exit-policy study began — the 72 h plan's expectancy is in the tail of the winners,
and any rule that books earlier on a fixed trigger gives it back.

## 4. What the counts say about the design

- The implied-range event on chento fires on half the eligible trades (BTC 80 of 164, ETH 77 of 154) at a median
  13–15 hours, at a mark of about +1.25 R; under rung 1 only 29 BTC events found three fresh-extreme controls in that
  bin (51 dropped, 2.6 distinct controls per event), so the pre-registered ladder read it under rung 3. On
  squeeze_bull it fires on 15 of 111 and 7 of those crossings are the target fill itself (+3 % is one implied day
  at a DVOL near 55), the channel section 6 of the pre-registration predicted; it stayed descriptive.
- The rejection wick fires on 73 % of chento-BTC trades (median 8 h, mark +0.5 R): 27 % of armed 15-minute bars
  carry the shape and 29 % of those sit at a level (ETH 17 %, squeeze_bull 24 %). Balance: event bars come on
  volume (median VR 2.0 against 0.9 at control minutes) and later in the trade's sequence of highs (ordinal 20
  against 15). The contrast is partly "a pushy bar in a running trend versus a quiet fresh high", and the trend
  side won.
- Walker identity with stage 1 held on all 584 shared trades; the squeeze_bull set differs from stage 1's by the one
  fire item 30 moved. Causality cuts clean after the A1 fix.

**Erratum (2026-09-19, after the run; the frozen pre-registration is left as it was).** Amendment A1 and the first
version of this section dated the moved fire "2026-09-11 17:00 → 16:00". The two trade ids are epoch seconds
`1788530400` and `1788526800`, which are **2026-09-04 14:00 and 13:00 UTC**: the corrected open-interest table moved
the last bull-regime fire in the revalidation ledger back one hour on 09-04. SJ-4250's bar (2026-09-11 17:00) is in
neither set — on the corrected table it does not fire at all, which is decision 8's evidence. The mistake was a
date read off memory instead of the timestamp; nothing computed depends on it.

## 5. What this permits and closes

Nothing in production changes. Rows I and J of the exhaustion brainstorm are closed at this resolution: do not
re-propose the implied-range exit (any multiplier, either scale, either anchor), the rejection wick at levels (any
bar size, any level set — the level leg was tested and found empty), or an ETH-only re-test of the implied-range
line without new out-of-sample data and its own pre-registration. That was the last untested pair the brainstorm
left; the family is exhausted, and the Exits constraint in the roadmap stays unmet on every arm: the placeholders
(72 h, 48 h, 6 h) have now beaten every invalidation-style exit put against them.
