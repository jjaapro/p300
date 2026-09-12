# Double tops and bottoms, including the imperfect versions

Study 24. 2026-09-11. **19,660 de-duplicated causal cells** across four agents.

**The question:** traders trade double tops and bottoms, but not the textbook
equal-highs idealisation — "not equal top, but a bit lower, and not equal
bottom, but still down after short recovery." Does the imperfect version work?

**The answer: no, and the asymmetry the question rests on does not exist.**
Max |t| anywhere is 4.644 against a corrected bar of 5.27-5.41. Zero cells
survive. Three of four legs have *thinner-than-Gaussian* tails.

## Why this was worth running separately

Study 23 swept 7,908 cells of classic TA but covered only 1-2 bar candlestick
patterns, oscillators, bands, volume and divergences. **Multi-bar chart patterns
built on swing pivots had never been tested.**

And the hypothesis cut against the repo's strongest prior in an interesting way.
A textbook double top is a pure FADE, and fading has failed five times here. But
a second peak that is *lower* is evidence the uptrend already broke, which is
closer to FOLLOWING a new downtrend. If any reversal pattern were to survive,
that asymmetry is why. It was measured directly and it is not there.

## The finding that outlives the pattern

**Pivot lookahead inflates the apparent edge by roughly +3.4 to +6 t-units.**

A swing high is only knowable k bars after it prints. Detecting it with hindsight
and entering at the pivot bar rather than at pivot+k produces, on identical
patterns with identical machinery:

| leg | causal mean t | lookahead mean t | effect |
|---|---|---|---|
| double bottom (2,923 matched cells) | −0.119 | **+5.900** | \|t\|>2 goes 4.8% -> **76.9%**; max t +3.34 -> **+37.22** |
| context (629 matched pairs) | −0.066 | **+4.214** | max \|t\| 3.61 -> 18.46 |
| null agent, k=3 | — | — | +3.41 t, **+34.6pp of sigma capture**, win rate 52.6% -> 71.1% |
| double top | — | — | +6.5 to +7.0 t; **21.6% of all cells become "discoveries"** |

At k=3 — the confirmation lag every TradingView pivot script uses — **zero of
464 pattern cells clear the bar causally and 132 clear it with lookahead.**

This is the number to apply to any published double-top backtest that labels
pivots with hindsight: discount it by about three and a half t-units, which is
to say entirely.

## Three corrections from adversarial review, all making the result more negative

1. **Residual lookahead in the referee.** `run_pattern_null.py` — the script
   whose whole job was policing pivot lookahead — used a non-strict right side
   in its fractal test and then collapsed pivot runs keeping the *more extreme*
   one, which consults future bars. Corrected: the real grid's mean t falls
   +0.297 -> **+0.091**, and the single-pivot placebo falls from +1.348 to
   **−0.129** (max |t| 5.38 -> 2.38).
2. **The one "real" claim rested on 16 non-independent cells.** The context
   agent reported the short side of swing-high structure beating its
   geometry-matched control at t = +3.44 over 16 cells. Those are 4 panels x 4
   horizons: H=1/4/12/24 are nested windows over the same entries, the "4h"
   panel is the 1h series resampled, and the two daily panels are the same asset
   nested. Clustered by panel: **t = +2.19, p ~ 0.12.** Directionally consistent
   with the don't-fade rule, but not itself a replication.
3. **The bars were ~10% too lenient.** The x1.08 correlated-null factor is an
   indicator-cell factor; for chart-pattern cells (median n ~39) it is 1.11-1.20.
   Corrected: double-top K=9,195 -> |t| = **5.41** (observed max 4.426 = 0.82 of
   bar); double-bottom -> **5.27** (4.644 = 0.88); context -> **5.11** (4.141 =
   0.81).

---

## Double tops and bottoms, including the imperfect versions

**The answer is no. Across 19,660 de-duplicated causal test cells on four independent measurement legs, the largest |t| anywhere in the programme is 4.644 against a correctly calibrated programme bar of |t| = 5.60 — zero survivors, and the observed t-distribution is thinner-tailed than Gaussian in three of the four legs.** The specific thing the question was built on — that an *imperfect* double top, where the second peak sits a bit **below** the first, is evidence the uptrend has already broken and should therefore beat the textbook equal-highs idealisation — is not there: on the percent-of-price offset axis the lower-high band scores mean t **+0.063** against the equal band's **−0.057** (panel-stratified, corrected), a gap of 0.12 t-units on cells whose own noise sd is 1.0, and the continuous version of the same hypothesis produces 68 regression slopes with mean t +0.077 of which 53% carry the wrong sign.

This is study 24, run as four legs: a double-top sweep (9,195 causal cells), a double-bottom sweep (5,269), a context-and-neighbours sweep (2,860), and a null-and-lookahead calibration leg (2,336). Three adversarial reviewers refuted two of the four legs on material grounds; every refuted figure below is replaced with the corrected one and flagged as such.

### What the reviewers corrected

| Claim as first reported | Corrected | Why |
|---|---|---|
| Search bars of \|t\| = 4.91 / 4.78 / 4.64 (×1.08 correlated-null factor from study 23) | **5.41 / 5.27 / 5.11**, and a programme bar of **5.60** over all 19,660 cells | The ×1.08 factor was calibrated on *indicator* cells with hundreds of samples. Chart-pattern cells have median n ≈ 39, so the per-cell reference is Student-t (df≈38), not normal. The null leg's own L=5 block bootstrap measures the correct inflation over a normal Bonferroni at **1.11–1.20**. All three bars were ~10% too lenient. Nothing clears either way; the verdict is unchanged and the margin is wider. |
| Null leg: 2,336 causal tests, mean t +0.297, max **+4.25**; single-pivot placebo mean t **+1.348**; "the double-pattern geometry *subtracts* from a plain confirmed swing pivot, negative in 13 of 14 bands" | Causal: mean t **+0.091**, sd 1.006, max **+3.755**, \|t\|>2 in 4.60% (Gaussian 4.55%). Placebo mean t **−0.129**, 41.7% positive, max \|t\| 2.38, capture −0.61%. Excess over placebo **+1.64pp**, positive in 55.4% of cells, negative in **2** of 14 bands | Residual pivot lookahead in that script's own `_alternate()`: a run of same-type fractal pivots was collapsed most-extreme-wins, and a later pivot can only displace an earlier one from bars that do not exist at the causal entry bar. 20–25% of raw pivots were deleted; 57–65% of the deletions were decided by future bars; the deleted pivots were worse in 18 of 18 timeframe×k×side combinations. The placebo's entire tilt was that artifact, and the derived "geometry subtracts" finding reverses sign. Scope is confined to one script — `run_doubletop.py`, `run_double_bottom.py` and `run_dtdb_context.py` pair consecutive raw fractal pivots with no alternation collapse and are causally clean; the double-top leg's strongest cell was independently reimplemented from raw bars and reproduced byte-identically (n=13, +928.0bp, t=+4.426). |
| Context leg headline: "the short side of swing-high structure beats its geometry-matched control by **+5.84% of sigma (t=+3.44)**" — offered as a sixth replication of don't-fade | Point estimate roughly survives (**+4.33% of sigma** at event level) but the SE was fabricated by an independence assumption. Clustered by panel (G=4): **t = +2.19**. Largest **event-level** t anywhere in the comparison: **+2.47** (1d, H=4); 11 of 16 panel×horizon cells below \|t\|=1.3. The double-bottom mirror: naive −2.04 → panel-clustered **−1.49**, all 16 event-level \|t\| ≤ **1.22** | The 16 "independent" cells are 4 panels × 4 horizons and none of the sixteen is independent: H=1/4/12/24 are nested forward windows over the same entries, the 4h panel *is* the 1h series resampled, and the two daily panels are the same asset with one span nested in the other. The effect is one daily, one-horizon measurement seen twice on two exchanges. |
| Double-bottom geometry: higher-low bins are the only ones with a majority of positive cells (+0.483 and +0.400) | Panel-stratified: **−0.160** and **+0.230** | Cell-equal-weighted pooling across panels with different cell counts and different panel-level means. The +0.483 bin is 1d −1.562 / 4h +0.307 / 1h +0.776. The "higher-low tilt" the leg leaned on does not survive stratification. |
| Double-top best cell: "a near miss at 4.43 against 4.91" | **Expected ~30 times over.** Single-cell p = 8.3e-4 under t(12); a frequency-matched placebo (Bitstamp 1d, H=24, n=13, 60,000 draws) gives P(\|t\|≥4.426) = **0.33%**, i.e. 30.3 such cells expected in a 9,195-cell search | n=13 with a −4.45-skew signed variable is nowhere near the Gaussian regime the bar assumes. |
| Confirm-vs-neckline read as a sixth replication of don't-fade | **Withdrawn.** Three legs find the neckline better by +0.08 to +0.38 t; the fourth finds it strictly worse by −0.26. The sign is not stable | The comparison is itself inside the noise. |

A second, lookahead-adjacent defect was found and fixed mid-study and belongs on the trap list: **any multi-bar pattern with a conditional "wait for confirmation" trigger must have its entry stream sorted and de-duplicated before non-overlap thinning.** For a conditional trigger the entry bar is not monotone in the pattern index — a late break of an earlier pattern can land after an early break of a later one, and two patterns can break on the same bar — so the greedy `accept if entry ≥ last + H` filter silently admits overlapping forward windows. The double-bottom leg's first run reported a headline of **t = −6.15** that held up under all four cluster-robust SEs and formed a clean plateau in k, H and recovery height. Sorting and de-duplicating took the search maximum from **6.15 to 4.64** and pulled the whole distribution back onto the null. Cluster-robust SEs did not catch it, because the contamination was window overlap, not episode clustering; the two corrections are orthogonal and both are needed.

---

### 1. The geometry surface — where P2 sits relative to P1

This is the heart of the question, so it gets the most space. Three of the four legs measured the offset as a **percentage of price**; the context leg normalised it by **pattern amplitude**. Those are different axes and are reported separately — they must not be pooled.

#### 1a. Double top, percent-of-price offset (9,195 causal cells, Bitstamp 1d 2011–2026 + Binance 4h + Binance 1h)

Pooled over k ∈ {2,3,5,8,10}, three trough-depth filters, three spacing buckets, H ∈ {1,4,12,24}, both triggers. Positive = the **short** made money after drift adjustment. Bands overlap by design.

| offset band (P2 vs P1) | cells | mean n | mean bp | **mean t** | sd t | max \|t\| | % t>0 | mean capture |
|---|---|---|---|---|---|---|---|---|
| [−5%,−3%] | 744 | 32 | +11.3 | **−0.170** | 0.874 | 2.53 | 38.4 | −4.66% |
| [−4%,−2%] | 780 | 59 | +13.6 | **−0.187** | 0.967 | 3.29 | 41.5 | −3.88% |
| [−3%,−2%] | 720 | 43 | +26.1 | **+0.155** | 1.006 | 2.56 | 57.8 | +1.09% |
| [−3%,−1.5%] | 780 | 72 | +25.7 | **+0.210** | 1.031 | 4.11 | 60.8 | +2.53% |
| [−2%,−1%] | 708 | 86 | +16.4 | **+0.064** | 1.063 | 2.91 | 50.0 | +0.35% |
| [−1.5%,−0.5%] | 766 | 127 | +16.1 | **+0.041** | 1.047 | 3.08 | 48.3 | −0.28% |
| [−1%,−0.5%] | 681 | 93 | +31.8 | **+0.095** | 1.063 | 2.94 | 50.1 | +0.57% |
| [−0.5%,0] | 714 | 143 | −22.6 | **+0.020** | 0.889 | 2.49 | 49.9 | −2.45% |
| **EQUAL [−0.25%,+0.25%]** | 680 | 141 | +1.8 | **−0.122** → **−0.057** | 1.018 | 3.41 | 47.2 | −3.42% |
| [0,+0.5%] | 648 | 116 | +17.5 | **−0.085** | 1.149 | 3.45 | 50.0 | −1.30% |
| [+0.5%,+1%] | 634 | 74 | −8.6 | **+0.200** | 0.818 | 2.82 | 58.4 | +0.91% |
| [+0.5%,+2%] | 760 | 115 | +47.1 | **+0.323** | 0.919 | 4.30 | 66.4 | +3.65% |
| **LOWER [−3%,0] — the variant asked about** | 914 | 257 | +4.2 | **+0.002** → **+0.063** | 0.977 | 3.07 | 52.7 | −1.36% |
| **HIGHER [0,+2%]** | 808 | 190 | +40.4 | **+0.218** → **+0.332** | 0.991 | 4.43 | 63.6 | +2.73% |
| NEAR [−1%,+1%] | 832 | 297 | +9.3 | **+0.101** | 0.972 | 3.30 | 54.4 | +0.08% |
| ALL [−5%,+2%] | 972 | 378 | +19.0 | **+0.076** | 0.939 | 2.51 | 54.2 | +0.38% |

Arrows are the reviewer's panel-stratified corrections for the three bands the question turns on; the corrected surface is *flatter* than the reported one, and every band remains inside ±0.4 t.

**The surface is flat, and it is not monotone.** The mean-t column spans −0.187 to +0.323 — half a t-unit of range against a within-band sd of ~1.0. The −0.5% to −3% region the question singled out reads +0.02 / +0.10 / +0.06 / +0.04 / +0.16 / +0.21 and is indistinguishable from the equal case and from the deepest lower highs (−0.17, −0.19). If anything the ordering runs **backwards**: the only bands above +0.2 are those where P2 is *higher* than P1, which is not a double top at all — and those bands are dominated by small-n long-horizon Bitstamp daily cells.

#### 1b. Where n is actually large — clean disjoint buckets (Binance 1h, k=5, no overlapping bands)

| bucket | confirm H=4: n / bp / t / capture | confirm H=24: n / bp / t / capture |
|---|---|---|
| [−5%,−3%) | 82 / −16.9 / −1.53 / −17.2% | 79 / −26.2 / −1.00 / −10.8% |
| [−3%,−2%) | 102 / −1.9 / −0.17 / −1.9% | 100 / −3.4 / −0.13 / −1.4% |
| [−2%,−1%) | 233 / −3.8 / −0.66 / −3.9% | 213 / −5.4 / −0.32 / −2.2% |
| [−1%,−0.5%) | 278 / −1.1 / −0.21 / −1.1% | 247 / +14.1 / +0.93 / +5.8% |
| [−0.5%,−0.25%) | 216 / −0.7 / −0.15 / −0.7% | 193 / +3.1 / +0.20 / +1.3% |
| **[−0.25%,+0.25%) EQUAL** | 480 / −3.7 / −0.93 / −3.7% | 371 / −13.0 / −1.00 / −5.3% |
| [+0.25%,+0.5%) | 190 / −6.2 / −0.97 / −6.3% | 171 / −26.8 / −1.42 / −11.0% |
| [+0.5%,+1%) | 242 / +3.0 / +0.64 / +3.1% | 212 / +0.9 / +0.06 / +0.4% |
| [+1%,+2%) | 244 / +3.4 / +0.54 / +3.5% | 216 / +3.2 / +0.22 / +1.3% |

Every bucket is inside |t| = 1.6 and every edge is inside 27bp at H=24, where sigma is 2.43%. The 4h and Bitstamp daily panels give equally scattered surfaces with **no cross-panel agreement on any bucket's sign.**

#### 1c. Double bottom, percent-of-price offset (5,269 causal cells)

Negative offset = the imperfect **lower low**. All figures are the long/reversal reading; the short/continuation reading is its exact arithmetic negative and is not double-counted.

| offset bin | cells | mean t | % cells t>0 | med drift-adj edge | med RAW edge | med capture | med n |
|---|---|---|---|---|---|---|---|
| −5.0…−3.0% (much lower low) | 259 | **−0.225** | 41.3% | −7.5 bp | +1.3 bp | −4.01% | 41 |
| −3.0…−2.0% | 290 | **−0.194** | 39.0% | −5.7 bp | +3.0 bp | −3.48% | 37 |
| −2.0…−1.0% | 333 | **−0.475** | 31.8% | −8.4 bp | −3.2 bp | −6.33% | 87 |
| −1.0…−0.5% | 309 | +0.141 | 48.9% | −0.1 bp | +5.0 bp | −0.17% | 71 |
| **−0.5…+0.5% (EQUAL — textbook)** | 395 | **−0.103** | 42.0% | −1.2 bp | +5.3 bp | −0.98% | 135 |
| +0.5…+1.0% (higher low) | 362 | +0.483 → **−0.160** | 76.2% | +7.0 bp | +12.0 bp | +4.64% | 113 |
| +1.0…+2.0% (higher low) | 432 | +0.400 → **+0.230** | 67.1% | +6.6 bp | +15.1 bp | +4.81% | 112 |

The leg originally read a tilt out of this — higher lows favour the long, lower lows favour the short — and noted that it inverts between 1h and 1d (1h neckline: agg_lower −1.12, agg_equal −0.51, agg_higher +1.20; 1d: agg_lower **+0.64**, agg_higher **−0.14**). The panel-stratified correction removes most of what was left: the strongest higher-low bin goes from +0.483 to −0.160. **A geometry effect that flips sign with timeframe and does not survive panel stratification is not a geometry effect.**

#### 1d. The same axis on the null-calibration leg (2,336 distinct causal tests)

Per-band means below carry the same ~+0.2 t inflation the reviewer measured grid-wide (mean +0.297 → **+0.091** causal), so read the *shape*, not the levels. DT = double top (short-signed), DB = double bottom (long-signed).

| P2 vs P1 offset | kind | cells | mean t | frac t>0 | mean capture |
|---|---|---|---|---|---|
| −3.0…−2.0% | DT | 116 | **+0.577** | 77.6% | 6.41% |
| −2.0…−1.0% | DT | 148 | +0.533 | 69.6% | 7.06% |
| −1.0…−0.3% | DT | 164 | +0.188 | 56.1% | 0.64% |
| **EQUAL ±0.3%** | DT | 156 | +0.201 | 61.5% | 0.14% |
| +0.3…+1.0% | DT | 152 | −0.013 | 50.7% | −2.35% |
| +1.0…+2.0% | DT | 156 | +0.236 | 54.5% | 2.97% |
| +2.0…+3.0% | DT | 132 | **+0.582** | 64.4% | 12.65% |
| −3.0…−2.0% | DB | 147 | −0.064 | 42.2% | −3.12% |
| −2.0…−1.0% | DB | 148 | −0.020 | 44.6% | −0.80% |
| −1.0…−0.3% | DB | 144 | +0.470 | 62.5% | 6.82% |
| **EQUAL ±0.3%** | DB | 152 | +0.169 | 55.3% | 3.01% |
| +0.3…+1.0% | DB | 168 | +0.216 | 58.9% | 2.48% |
| +1.0…+2.0% | DB | 168 | **+0.720** | 81.5% | 8.62% |
| +2.0…+3.0% | DB | 104 | +0.507 | 74.0% | 7.19% |

The double-top side is **U-shaped and symmetric**: the extreme lower-high band (+0.577) and the extreme higher-high band (+0.582) are statistically identical. That is the signature of noise — of *distance* from equality mattering slightly through sample composition — not of "the uptrend has already broken". The double-bottom side runs the **opposite** way to the hypothesis: the lower-low bins are dead flat (−0.064, −0.020) while the higher-low bin is the grid's strongest (+0.720).

#### 1e. The context leg's amplitude-normalised axis (separate axis, do not pool)

Offset r = (P2 − P1) / pattern amplitude. Pooled over four timeframes × three confirmation lags × four horizons.

| offset bin | DT cells | DT mean t | DT mean cap | DB cells | DB mean t | DB mean cap |
|---|---|---|---|---|---|---|
| r ≤ −0.60 (much lower) | 32 | +0.336 | +3.9% | 44 | −0.135 | −2.6% |
| −0.60…−0.35 | 47 | −0.253 | −1.4% | 24 | +0.312 | +0.1% |
| −0.35…−0.20 | 44 | +0.419 | +7.1% | 24 | −0.198 | −3.1% |
| −0.20…−0.08 (a bit lower) | 30 | +0.105 | +0.6% | 24 | +0.428 | +5.1% |
| **EQUAL ±0.08 (textbook)** | 28 | +0.302 | +4.5% | 33 | −0.652 | −6.5% |
| +0.08…+0.20 | 24 | −0.289 | −2.5% | 42 | −0.417 | −6.9% |
| +0.20…+0.35 | 24 | +0.261 | +0.9% | 44 | −0.225 | −1.1% |
| +0.35…+0.60 | 30 | +0.328 | +2.2% | 48 | −0.446 | −3.9% |
| r ≥ +0.60 (much higher) | 48 | −0.716 | −10.1% | 39 | +0.460 | +8.7% |

Read left to right, the double-top sequence is +0.34, −0.25, +0.42, +0.11, +0.30, −0.29, +0.26, +0.33, −0.72 — a **zigzag with adjacent bins of opposite sign**. The hypothesis predicts a monotone left-to-right decline. It is not there on either axis, in either pattern, on any leg.

#### 1f. The hypothesis tested once, as one hypothesis, instead of as a grid

The cleanest test is to stop bucketing and regress each pattern's drift-adjusted return on the continuous offset. A "lower high is better" claim requires a **negative** slope for double tops.

| test | result |
|---|---|
| Double top, 68 per-pattern OLS (3 panels × k ∈ {3,5,8} × 2 triggers × 4 H), non-overlapping | slope t: mean **+0.077**, sd 1.044, range −2.11 to +2.80, **52.9% negative**, 7 of 68 above \|t\|=2 |
| — excluding its one coherent block (Bitstamp 1d k=5 neckline, slope t +2.38/+2.80/+2.64/+2.62 on n=30–36, **positive**, i.e. the opposite of the hypothesis, absent at 4h and 1h at every k) | 60 slopes, mean **−0.105**, sd 0.871, 3 above \|t\|=2 |
| Reviewer's independent reimplementation, 36 regressions over [−5%,+2%] | mean slope t **+0.041**, sd 0.896, 55.6% negative, max \|t\| **2.44**, Spearman ρ in **[−0.04, +0.05]** everywhere |
| Double bottom, 48 event-level regressions, Bonferroni bar for 48 tests = 3.28 | best **+3.22** (1h neckline k=3 H=1) → **+2.42** 60-day clustered → **+2.30** year-clustered → **+1.46** on the Bitstamp 1h replication (**+0.94** clustered); **opposite sign on daily** (1d k=2 H=4, slope −91.1bp, t = −2.63) |

**Plain statement of the answer to the question as asked: the "a bit lower" second peak does not differ from the equal-tops idealisation.** Double top, corrected and panel-stratified: LOWER +0.063 vs EQUAL −0.057 — 0.12 t-units apart on cells with sd 1.0. Double bottom: the lower-low bins are the *worst* bins on one leg (−0.225 / −0.194 / −0.475) and dead flat on another (−0.064 / −0.020), against an equal bin of −0.103 and +0.169. On the coarse three-way comparison the LOWER band does edge out EQUAL in both patterns (+0.422 vs +0.161 for tops, +0.058 vs −0.322 for bottoms) — the direction the question predicted — but the narrower BASE band (|r| ≤ 0.35) beats all three at +0.748, the extreme-lower bins in the fine grid go the other way, and the whole spread is inside the per-cell noise sd. There is no signal to have an asymmetry in.

One genuine sub-finding worth keeping: on the daily panel the near-equal bins frequently fall below the minimum-n threshold and go unpopulated. **Exactly-equal daily double tops are rare** — which is the question's own premise confirmed — but it also means the daily panel cannot resolve the equal-vs-near-miss comparison at all, and that comparison rests entirely on 4h and 1h.

---

### 2. Entry trigger: second-pivot confirmation versus the neckline break

The textbook is emphatic that you must wait for the close through the neckline. **The data does not settle it, and the sign is not even stable across the four legs.**

| leg | confirmation trigger | neckline break | delta |
|---|---|---|---|
| Double top (5,660 matched pairs on identical geometry) | mean t **−0.003**, mean edge +4.4bp, mean n 182 | mean t **+0.132**, +29.1bp, mean n 108 | **+0.116 t**, +23.7bp; neckline better in **54.9%** of pairs |
| Double bottom (2,923 vs 2,346 cells) | mean t **−0.119**, median edge −0.94bp, median capture −0.77% | mean t **+0.258**, median +4.75bp, median capture +3.26% | **+0.377 t**, +5.7bp, +4.0pp capture |
| Context (407 matched pairs) | mean t −0.066, −4.1bp, −0.65% capture | mean t +0.092, +19.8bp, +2.64% capture | **+0.081 t**, +1.77pp; discards **48%** of entries |
| Null-calibration leg (1,128 vs 927 cells) | mean t **+0.415**, 63.9% positive, median capture 3.98% | mean t **+0.153**, 56.3% positive, median capture 2.53% | **−0.262 t** — neckline **strictly worse** on every axis |

Three legs say the neckline helps by +0.08 to +0.38 t; the fourth says it hurts by −0.26. The earlier reading of this as a sixth replication of don't-fade has been **withdrawn on review**: a comparison whose sign flips between measurement legs is inside its own noise. What all four agree on is the **cost**: the neckline fires on only 52–59% of confirmed patterns within the wait window (mean n 182 → 108, median 51 → 30, 73,723 events → 38,310), so you trade roughly half as often, later, and at a worse price. Neither trigger produces a single cell that clears any bar — max |t| is 4.43 at confirmation and 4.11 at the break in the double-top leg, 3.61 and 3.51 in the context leg.

For the neighbouring patterns the neckline is affirmatively harmful: 4h triple top goes from t = +0.87/+0.06/+1.36/+0.34 at confirmation to +0.04/−0.39/−0.39/−0.43 at the break; 1h triple bottom from +1.60/+0.03/−1.18/−1.91 to −0.65/−1.31/−1.47/−0.31.

**The one operationally useful thing about the neckline is not profit — it is that it is the lookahead-immune trigger.** Its causal-vs-hindsight gap is +0.15 to +0.38 t-units, against +2.7 to +7.0 for the confirmation trigger, because the break usually lands well after P2+k anyway and the two variants frequently pick the same bar. If anyone insists on testing double tops, test them at the neckline: that is where a sloppy pivot implementation does the least damage. Note also one researcher degree of freedom that crept in and bought nothing: the wait-window length was scaled by the completed pattern's width, which is causal at entry time but is still a fitted knob.

---

### 3. The lookahead number — the most reusable output of this study

**Detecting the second pivot with hindsight and entering at the pivot bar instead of at pivot+k inflates the apparent edge by roughly 2.9 t-units and 31 percentage points of sigma capture at k=3** — the confirmation lag every hand-drawn double top and most TradingView pivot scripts implicitly use. Design: identical patterns, identical drift adjustment, identical non-overlapping sampling; the only difference is that the causal leg enters at close[P2+k] and the lookahead leg at close[P2], using a pivot label that will not exist for another k bars. The difference *is* the bias.

| k | mean Δt (lookahead − causal) | Δ capture (pp of sigma) |
|---|---|---|
| 1 | **+1.825** | +18.2 |
| 2 | **+2.645** | +27.1 |
| **3 (the standard)** | **+2.895** | **+31.3** |
| 5 | **+3.892** | +42.6 |
| 8 | **+4.133** | +51.0 |

These are the reviewer-corrected values; the leg first reported +2.01 / +2.98 / **+3.41** / +4.06 / +4.49, ~15% too large at k=3, because the lookahead leg was contaminated by the `_alternate()` defect more than the causal leg was. **The corrected wording to publish is "roughly 2.9 t-units and 31 percentage points at k=3", not 3.4 / 35.** The bias grows monotonically in k, which is the tell that it is mechanical: a longer confirmation window means a more extreme pivot and more of the subsequent move stolen.

Three causally clean scripts measured the same thing independently on their own pattern inventories and corroborate it, larger:

| leg | pairs | mean Δt | mean Δedge |
|---|---|---|---|
| Double top, all pairs | 13,840 | **+2.903** (median +1.418) | +93.5bp (median +47.5) |
| — confirmation trigger only | 7,553 | **+5.124** | **+160.0bp** |
| — by horizon, confirmation trigger | H=1 **+6.495** / H=4 **+6.992** / H=12 +4.341 / H=24 +2.667 | | +72.6 / +177.6 / +193.1 / **+196.7bp** |
| Double bottom, all pairs | 2,921 | **+6.023** (median +4.235) | **+83.8bp** (median +59.8) |
| — by timeframe | 1d 515 pairs **+2.38** / 4h 1,158 **+4.30** / 1h 1,248 **+9.12** | | +188.3 / +80.2 / +44.0bp |
| Context | 629 | **+4.280** (max +19.23) | **+129.4bp**, +36.0pp of capture |

**What it does to significance counts, which is the part a reader of someone else's backtest needs.** Same patterns, same returns, same sampling — only the pivot timing differs:

- Double top: causal 9,195 cells, **zero** clear the bar, max t +4.43. Hindsight 13,863 cells, mean t **+2.956**, max **+38.91**, and **2,994 cells (21.6%) clear the bar** — 21.6% "discoveries" manufactured from a pattern that carries nothing.
- Double bottom: causal mean t −0.119, 4.8% of cells above |t|=2, **zero** above |t|=4. Hindsight mean t **+5.900**, max +37.22, **76.9% of cells above |t|=2 — every one of them positive — and 53.1% above |t|=4.**
- Context: causal 6.2% of cells above |t|=2 and 0.28% above |t|=3; hindsight **77.9%** and **59.4%**, max |t| 18.46, mean capture +35.36% of sigma.
- Null leg at k=3: of 464 cells, causal **0** clear |t|=4.85 and 38 clear |t|=2; hindsight **132** clear 4.85 and 343 clear 2. Mean win rate 52.6% → **71.1%**. The sign of t flips outright in 22–46% of cells, so hindsight does not merely scale a real effect — **it manufactures a different one.**

Worked examples of what a chart-drawn pattern looks like when the pivot is taken as known at the pivot bar: 1h triple top H=4 goes from t = +1.46 (+5.7bp) to **t = +14.52 (+53.6bp)**; 1d triple top H=1 from +1.26 (+57.0bp) to **+5.94 (+155.6bp)**; 4h inverse head-and-shoulders H=1 from t = +0.32 / +6.9bp to **+2.78 / +46.2bp**.

**The discount to apply.** Any double-top, head-and-shoulders, divergence or ZigZag backtest that identifies its pivots from the completed series and enters on or near the pivot bar should have **roughly three t-units and thirty percentage points of sigma capture subtracted before it is believed** — with the caveat that the measured bias ranges from +1.8 t on daily to +9.1 t on hourly depending on n, and from +44bp to +274bp per trade depending on sigma. In t-units the bias is worst intraday; in basis points it is worst on the daily chart. In every case it is larger than the entire distance between the null and the best honest cell in this study. This independently reproduces study 23's finding ("one bar of lookahead is worth roughly +5 t-units"; "a 3-to-5 bar pivot confirmation manufactures +40 to +719bp of apparent edge") on a completely independent pattern family, and puts a t-number on it.

---

### 4. Context, volume and the neighbouring patterns

**Volume.** The textbook rule — a valid double top has *lower* volume on the second peak — is backwards in the data. Double tops: second peak on lower volume gives mean t **+0.230** versus **+0.496** on higher volume. Double bottoms: **−0.397** on lower volume versus **−0.047** on higher. The inversion holds in both patterns. Neither number is significant; the point is that the rule does not even point the right way.

**Trend context.** The single most interesting object in 2,860 context cells is a double top in a market that had **not** rallied into it — low run-up percentile into P1, Binance 1h, H=24: **+66.5bp, t = +4.14, capture 27.3% of sigma, n=175.** It is the only cell in the entire programme that behaves the way a real finding behaves:

- survives all four cluster definitions: episode +4.08 (G=131), 60-day +4.29, calendar-year +6.72, episode-equal-weighted **+3.55**;
- plateaus in the run-up threshold (cuts at 0.20/0.25/0.33/0.40/0.50 → t = +3.12/+4.09/+4.02/+3.48/+2.93, edge +69.7/+76.7/+65.1/+51.1/+38.4bp) — genuinely flat;
- ramps smoothly in horizon rather than spiking (H=1/4/8/12/18/24/36/48 → +1.17/+1.66/+2.70/+2.13/+3.85/**+4.14**/+3.29/+2.09);
- positive in both chronological halves (+2.25 then +3.66) and in **all five calendar years** (+1.83/+1.09/+2.49/+2.64/+1.79);
- reproduces on Bitstamp 1h over the same span (**+64.1bp, t = +3.93, capture +26.3%**);
- adds roughly 3× over its geometry-matched control (shorting every low-run-up bar: +12.8bp, t=+1.63; shorting every swing-high pair in that context: +23.2bp, t=+2.15; the double-top filter: +66.5bp).

**And it still fails, on four counts.** It is below the corrected bar for its own leg (5.11) and far below the programme bar (5.60); its programme-adjusted p is **0.68**, not the 0.099 first reported over its own 2,860 cells; it **fails cross-timeframe** (the identical construction at 4h gives t = **−1.10**, and the double-bottom mirror gives −0.47 / −1.27 / −1.32); the two spot venues over the same four years are a data-integrity check, not an independent sample; and it is only a peak in k, not a plateau (k=1 +0.40, k=2 +2.33, **k=3 +4.14**, k=4 +2.17, k=5 +1.68, k=7 +1.03). Most importantly it **contradicts the pattern's own textbook precondition** — it requires the *absence* of the uptrend a double top is supposed to be topping. Whatever it is, it is information about continuation of weakness, not about double tops.

**Neighbouring patterns corroborate the null, including the control.** k=3, causal confirmation entry, pooled over 4 timeframes × 4 horizons:

| pattern | cells | total n | mean t | max \|t\| | mean capture |
|---|---|---|---|---|---|
| triple top (short) | 16 | 2,688 | +1.057 | 2.76 | +11.49% |
| **double top (short)** | 16 | 5,950 | **+0.748** | 2.37 | +6.89% |
| **ctrl_anypair (short, NO pattern filter)** | 16 | 15,341 | **+0.287** | 1.52 | +1.04% |
| head-and-shoulders (short) | 8 | 619 | +0.015 | 1.04 | +3.95% |
| ctrl_allbars (long, every bar) | 16 | 71,087 | +0.011 | 0.21 | +0.15% |
| triple bottom (long) | 16 | 2,675 | −0.015 | 1.91 | +0.50% |
| inverse H&S (long) | 8 | 849 | −0.106 | 1.78 | +0.51% |
| **ctrl_anypair (long, NO pattern filter)** | 16 | 15,129 | **−0.291** | 1.48 | −1.69% |
| **double bottom (long)** | 16 | 5,837 | **−0.328** | 1.47 | −3.64% |

Not one neighbouring pattern reaches |t| = 3 anywhere. **Head-and-shoulders — the most-taught multi-pivot pattern there is — is exactly zero on both polarities** (+0.015 short, −0.106 long), and it is too rare on daily to populate at all: 15 events on Bitstamp and 9 on Binance in 12.6 years at a 1.5×ATR amplitude filter. Triple tops are the best of the family and their best causal cell is t = +2.76 at **+4.8bp gross against an 8bp taker round trip** — dead before it starts. The decisive row is the control: `ctrl_anypair`, every consecutive swing pair in the same separation window with no offset filter, no amplitude filter and no pattern meaning, shows the **same sign and the same ordering** as the real patterns.

Further controls, all confirming: a **single-pivot** version of the identical neckline mechanics scores t = −1.04 to +1.07 on 1h, statistically the same as the double bottom; a bare N-bar breakout with no geometry scores t = −0.01 to +1.17; a +37-bar time-shift placebo of the lower-low neckline arm returns t = −1.11 against the real arm's −1.75; and a circular-shift placebo (real signal counts, real geometry, alignment destroyed, 50 reps × 32 groups) puts **every** real cell inside its own null, with not one group reaching P < 0.10. **The second trough, the offset geometry and the temporal alignment each add nothing measurable on top of "price closed above a recent high."**

---

### 5. Polarity, against the repo's don't-fade rule

The tension is real as stated: a double top short is a **fade** of an uptrend, and this repo has now found five times that fading trend structure on BTC loses — while a *lower* second high is arguably not a fade at all, since the structure has already broken. If the don't-fade rule is right and the question's intuition is right, the lower-high band should beat the equal band by a visible margin. **It does not, and the tension dissolves because there is nothing on either side of it to measure.**

What the numbers actually say:

1. **The offset axis carries no fade/follow information.** The null leg's double-top surface is symmetric — extreme lower-high +0.577 and extreme higher-high +0.582 — so distance from equality, not direction from it, is what moves the number, and that is a sampling effect. The double-top leg's corrected panel-stratified bands put HIGHER (+0.332) *above* LOWER (+0.063), i.e. backwards for the fade/follow reading.
2. **The one polarity result that replicated cleanly has been withdrawn.** The claim that the short side of swing-high structure beats its control by +5.84% of sigma at t=+3.44 was refuted: the sixteen "independent" cells are four nested horizons on four non-independent panels. Honestly computed, the double-top event set beats its any-swing-pair control by about **+10% of sigma on the daily panels at H=4** and by roughly nothing intraday (4h +2.8%, 1h −0.1%); the largest **event-level** t anywhere in the comparison is **+2.47**, it is **+2.19** clustered by panel, and it clears neither the multiplicity bar nor the study's own spike-vs-plateau standard. The double-bottom mirror is not distinguishable from its control at all (all 16 event-level |t| ≤ **1.22**). **There is no sixth replication of don't-fade in this data** — there is one daily, one-horizon measurement seen twice on two exchanges.
3. **The related claim that the geometry subtracts from a bare pivot also reverses.** Against a causally clean placebo, excess capture goes from −0.66pp to **+1.64pp**, positive in 55.4% of 2,198 cells, and the excess-t column is negative in **2 of 14** bands rather than 13 of 14. A bare confirmed swing pivot does **not** carry mean t +1.348 / +4.51% of sigma; causally it carries **−0.129 / −0.61%**, and with no alternation at all, −0.213 / −0.95%. The double-pattern geometry adds a hair over a bare pivot rather than subtracting — a hair being the operative word.
4. **What weak polarity evidence remains points the don't-fade way.** In the double-bottom leg every cell is stored as the long/reversal reading, and negative cells outnumber positive above |t|=2 (137 vs 114) and above |t|=3 (8 vs 5) — i.e. the short/continuation side is marginally better. In the context leg the informative half of the pair, insofar as any half is, is the one that **follows weakness**, not the one that fades strength. And the single most robust object in the programme — the low-run-up double top — is a continuation-of-weakness trade that explicitly requires the uptrend to be absent.

**Resolution.** The repo's don't-fade rule survives this study without being confirmed by it. Nothing here is strong enough to be a sixth replication, and nothing here contradicts it. The "lower second high is not a fade" intuition is a coherent reading of the mechanism, but the offset axis it would have to show up on is flat to within a tenth of a cell's own noise sd, so it makes no measurable difference which way you label the trade.

---

### 6. Verdict

**Nothing. Double tops and double bottoms on BTC carry no measurable directional information, in any geometry, at any confirmation lag, on either entry trigger, on any of four timeframes or two exchanges — and the imperfect lower-second-pivot variants the question asked about are not the exception; on the double-bottom side they are the weakest bins in the grid.**

| | value |
|---|---|
| Distinct causal cells, four legs | **19,660** (9,195 + 5,269 + 2,860 + 2,336) |
| Programme bar, Bonferroni(19,660) × 1.19 | **\|t\| = 5.60** |
| Global max \|t\| anywhere in the programme | **4.644** (double bottom, 1d k=2, sep 4–40, rec 1σ, offset +0.5…+1.0%, neckline, H=4, n=20 — and **negative**, i.e. the long/reversal reading lost money) |
| Ratio to bar | **0.83** |
| Survivors | **zero** |
| Per-leg t-distributions vs Gaussian | DT: mean +0.074, sd 0.972, \|t\|>2 in **3.76%** (Gaussian 4.55%), \|t\|>3 in **0.17%** (0.27%) — *thinner* than noise. DB: mean +0.048, sd 1.022, 4.76% / 0.23%. Context: mean +0.080, sd 1.017, 4.44% / 0.28%. Null leg, corrected: mean +0.091, sd 1.006, 4.60% / 0.55%. |

**No discovery tail exists in any of the four.** The strongest cells are spikes, not plateaus, and every one of them fails replication. The double-top leg's best cell (Bitstamp 1d, k=5, HIGHER[0,+2%], near spacing, H=24, n=13, +928bp, t=+4.426) is isolated on every axis simultaneously — moving the spacing bucket from "near" to "any" takes it from **t=+4.43 (n=13) to t=+0.68 (n=18)**; five extra patterns destroy it. It sits on a band where the second peak is *higher* than the first (a higher high, not a double top), at the one horizon where the drift adjustment is largest (+694bp added to every short), at the one n where a frequency-matched placebo produces a single-draw max |t| of **6.76** over 4,000 draws, and it halves to **t=+1.87** on Binance. Corrected: it is **expected about 30 times over** in a search this size, not a near miss. Across all four legs, **twelve of twelve** strongest double-top cells fail cross-exchange or cross-timeframe replication and four flip sign outright; the double-bottom leg's twelve strongest Bitstamp cells are all weaker on Binance (−3.34→−0.98, −3.15→−0.59, −2.64→−0.39, −2.60→−0.89). Clustering does not rescue anything and was never the problem here — unlike study 23's band-riding state signals, a chart pattern is a discrete **event**, so the episode count G nearly equals n (1,062 of 1,065 in the worst case) and cluster-robust SEs move these cells by a tenth of a t-unit. What kills them is search size and small-sample fat tails.

**Now the cost line, which is what makes the label unambiguous.** Median |capture| across the context leg's 2,860 causal cells is **6.67% of sigma(H)** — the nineteenth study in this programme to land inside the standing **3–13%** capture band, and like all eighteen before it, it does not clear cost. The null leg's median is 3.58%; the double-bottom leg's best trigger has a median of **+4.75bp / +3.26% of sigma**; the double-top leg's honest number at the only place n is large enough to trust it (Binance 1h, confirmation trigger, all offsets, **n = 3,403**) is **+1.0bp of drift-adjusted edge, 1.9% of sigma.**

The round trip is **4bp maker-maker, 5bp maker-taker, 8bp taker-taker.** At 1h, sigma(H=24) is 2.434%, so an 8bp taker round trip is **3.3% of sigma** on its own — and the median pattern cell at 1h has a median |edge| of **5.1bp**. The median 1h double-top or double-bottom trade does not cover its own commission, before slippage and before any management overlay.

So the label is not "a pattern that carries information but cannot pay its costs." That would be too generous. **It is a pattern with no measurable information whose largest noise-level tilts would still not pay their costs if they were real.** The honest use of this result is defensive: if you see a double-top backtest with a t-statistic in the fours and an edge in the hundreds of basis points, the first thing to check is whether its pivots were labelled with hindsight — because that alone is worth about three t-units and thirty percentage points of sigma capture at the standard k=3, which is more than the entire distance between zero and anything this programme could find.

**Trap list additions from this study.**
- **Trap 17.** Any multi-bar pattern with a conditional trigger must have its entry stream **sorted and de-duplicated before non-overlap thinning** — the entry bar is not monotone in the pattern index. Cost of not doing it here: a false headline of t = −6.15 that survived all four cluster-robust SEs.
- **Trap 18.** Pivot **alternation collapse** ("keep the more extreme of a run of same-type pivots") is lookahead, because the displacing pivot is decided by bars after the causal entry. Use first-wins, or pair consecutive raw fractal pivots. Cost of not doing it here: a placebo that appeared to carry +1.348 t and reversed the study's geometry conclusion.
- **Trap 19.** Bonferroni's correlated-null inflation factor is **not transferable between cell families**. Study 23's 1.08 was measured on indicator cells with hundreds of samples; chart-pattern cells have median n ≈ 39 and need **1.11–1.20** over a normal Bonferroni (equivalently, ≈1.02–1.06 over a Student-t Bonferroni with df≈38). Three of four legs used a bar that was 10% too lenient.
- **Trap 20.** Do not compute a cross-cell t-statistic over cells that are **nested horizons on resampled or overlapping panels**. Sixteen cells that are 4 panels × 4 horizons are not sixteen draws; here that inflated a t from +2.19 to +3.44 and promoted a noise-level result to a headline.
- **Trap 21.** Pool cells across panels only with **panel stratification**. Cell-equal-weighted pooling across panels with different cell counts and different panel means turned a −0.160 bin into +0.483 and manufactured the double-bottom leg's entire "higher-low tilt".

**Reproduction.** All runnable from `C:\Source\Repos\ai_trading` on cached data, no network:
- `C:\Source\Repos\ai_trading\scalp_lab\run_doubletop.py` and `run_doubletop_controls.py` → `doubletop_cells.csv` / `.pkl` (27,820 rows)
- `C:\Source\Repos\ai_trading\scalp_lab\run_double_bottom.py` (`DB_STAGE2=1` controls + Bitstamp-1h replication, `DB_STAGE3=1` monotonicity regressions) → `double_bottom_cells.csv`, `double_bottom_cells_binance1d.csv`
- `C:\Source\Repos\ai_trading\scalp_lab\run_dtdb_context.py` → `dtdb_context_cells.csv` (3,612 rows)
- `C:\Source\Repos\ai_trading\scalp_lab\run_pattern_null.py` (`--fast` for a smoke run) → `scalp_lab\cache\pattern_null_out.json`, `pattern_null_cells.pkl`. **This script still contains the `_alternate()` defect described above**; the fix is first-wins (`if m > 0 and ot[m-1] == typ[a]: continue`) or dropping the alternation entirely, as the other three scripts already do. Its published figures in this section are the reviewer's recomputed causal values, not the script's current output.

Sigma and drift are measured on each panel's own span throughout — Bitstamp 1d realised daily sigma **3.553%** over 2014–2026 and **4.312%** over 2011–2026; the repo's old 2.355%/day constant is not used anywhere (trap 12). Mirrored polarities are counted once, never twice (trap 16).
