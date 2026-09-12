# Classic TA, tested head-on

Study 23. 2026-09-11. The largest single study in this programme: **7,908
populated test cells across seven indicator families, plus ~3.6 million
null-calibration evaluations.**

**The question, in the user's framing:** most traders use basic TA, and few can
have a special edge — so do the basic tools work? The counter-framing that had
to be addressed alongside it: retail derivatives loss rates of 70-90% are
equally consistent with the tools carrying no edge at all.

**The answer: no, and worse than no.** Zero of 7,908 cells survive a correctly
calibrated family-wise threshold. The toolkit carries one robust piece of
information — polarity — and it points against the textbook reading. The retail
default is not a coin flip; it is the losing side of a real asymmetry.

## What was tested that had not been before

Prior studies had covered Williams %R (= Stochastic), ADX, Donchian, range
detectors and level fades. Untested until now, and covered here: **RSI, MACD,
CCI, MFI, ROC, moving-average crossovers, SuperTrend, Parabolic SAR, Ichimoku,
Bollinger Bands, Keltner Channels, the TTM squeeze, eleven candlestick patterns,
OBV, CMF, A/D, anchored VWAP, four kinds of divergence, and N-of-M multi-
indicator confluence.**

## Design: signals measured bare

No stops, no take-profits, no management, no threshold tuning. Each signal is
measured only as a forward-return predictor at fixed horizons, drift-adjusted,
sampled non-overlapping. This is a direct lesson from [WILLIAMS](WILLIAMS_FINDINGS.md):
a TP1-plus-breakeven overlay turned a -0.197% signal into +0.238%, and the
overlay — not the signal — was what failed out of sample. Isolating the signal
is the point. Management is where false positives come from.

## Three corrections from adversarial review, all material

All three reviewers refuted, and two found the same defect independently.

1. **The bands family's 11 "survivors" were a clustering artifact.** `BB50_2_outside_hi`
   treated 727 serially adjacent daily bars as independent; at H=1 the
   non-overlap rule imposes zero spacing and 605 of 726 gaps are one calendar
   day. The 727 entries are ~51-72 contiguous band-riding episodes.
   Cluster-robust SEs: **t 5.57 -> 3.74 (episode) -> 3.48 (60-day block) ->
   2.71 (calendar year) -> -0.27 (episode-equal-weighted)**. Verdict changed
   from SIGNAL to NOTHING.
2. **The null study's headline was fatally wrong.** It claimed "Bonferroni is
   too LENIENT here" and handed all seven agents a bar of |t| > 5.29, reasoning
   from L=20/L=60 block bootstraps. But longer blocks retain genuine structure,
   so they are a contaminated null. On the clean L=5 bootstrap, pooled p95
   max|t| = **4.60** against Bonferroni(2,419) = 4.26 — a ratio of 1.02-1.08,
   i.e. **Bonferroni is approximately correct**. Per-cell P(|t|>3) is **0.23%**,
   *thinner* than Gaussian, not 3.6x fatter. The bar moved DOWN to ~4.88, making
   the test easier, and nothing passes it anyway.
3. **A code defect hid 96 cells.** `adx()` returned all-NaN, so 12 ADX-regime
   configs never ran. The one-line fix recovers them; the best reaches t=+3.57,
   still far below any bar.

---

## Classic TA, tested head-on

**Verdict: the classic retail technical-analysis toolkit carries no exploitable forward-return information on BTC. Zero of 7,908 cells survive a correctly calibrated family-wise threshold. This closes the question.**

The one thing the toolkit *does* carry is polarity, and it points the wrong way: every textbook mean-reversion reading is a small, persistent loser. That is a mechanism for systematic retail loss that fee drag alone does not explain.

---

### 1. The search, and the bar it has to clear

Seven parallel sweeps produced **7,908 distinct populated |t| cells** (recomputed by pooling the seven per-cell result files directly; long/short mirrors collapsed, since for a sign-adjusted cell the short leg is the exact arithmetic negative of the long leg and is not a second test). Nominal configuration counts run roughly 40% higher — e.g. oscillators report 2,784 configurations for 1,375 real tests. On top of the primary grid sit **~3.6 million null-calibration and robustness evaluations** (2.9M block-bootstrap TA cells, 160k frequency-matched random-signal cells, ~512k circular-shift trend cells, 23k permutation-null oscillator cells, 23k confluence placebo cells).

| bar | value | source |
|---|---|---|
| Naive Bonferroni, K = 7,908, two-sided α = 0.05 | **\|t\| = 4.52** | computed |
| Corrected empirical correlated null, same search size | **\|t\| ≈ 4.6 – 4.9** | corrected null study × 1.02–1.08 |
| **Bar used below** | **\|t\| = 4.88** | Bonferroni × 1.08 |

**This threshold is itself a correction.** The null study originally handed all seven agents a pooled bar of **|t| > 5.29**, on the reasoning that per-cell fat tails (`P(|t|>3) = 0.98%` vs Gaussian 0.27%) more than cancel the correlation discount, and that "Bonferroni is too LENIENT here." Adversarial review refuted this as **fatal**: the clean L=5 block bootstrap gives pooled whole-grid p95 max|t| = **4.60** (n≥8) / 4.34 (n≥30) against Bonferroni(2,419) = 4.26 — a ratio of 1.02–1.08, i.e. **Bonferroni is approximately correct, not lenient**. Per-cell `P(|t|>3)` under the corrected null is **0.23%**, *thinner* than the Gaussian 0.27%, not 3.6× fatter. The bar moved down from 5.29 to ~4.88, which makes the test *easier*, and the toolkit still fails it.

Sanity check on the arithmetic: Bonferroni at K = 528 computes to 3.904, reproducing the bands study's independently stated threshold to three decimals.

### 2. The histogram: the null and the observed distribution coincide once one effect per family is removed

Pooled across all 7,908 cells (recomputed from `cache/osc_rows.pkl`, `classic_trend_cells.csv`, `bands_vol_cells.csv`, `candle_cells.csv`, `volvwap_cells.pkl`, `divergence_cells.csv`, `confluence_rows.pkl`):

```
                      observed      Gaussian N(0,1)
  mean                  +0.092            0.000
  sd                     1.024            1.000
  min / max      -5.633 / +4.151

  bin              obs      null
  [-6.0,-5.5)        3       0.0
  [-5.0,-4.5)        6       0.0
  [-4.5,-4.0)        2       0.2
  [-4.0,-3.5)        8       1.6
  [-3.5,-3.0)       13       8.8
  [-3.0,-2.5)       56      38.4
  [-2.5,-2.0)      123     130.8
  [-2.0,-1.5)      296     348.4
  [-1.5,-1.0)      554     726.3
  [-1.0,-0.5)      975    1185.3
  [-0.5, 0.0)     1444    1514.1
  [ 0.0,+0.5)     1748    1514.1
  [+0.5,+1.0)     1308    1185.3
  [+1.0,+1.5)      755     726.3
  [+1.5,+2.0)      399     348.4
  [+2.0,+2.5)      145     130.8
  [+2.5,+3.0)       57      38.4
  [+3.0,+3.5)        8       8.8
  [+3.5,+4.0)        5       1.6
  [+4.0,+4.5)        3       0.2
```

| tail | observed | Gaussian null | corrected bootstrap null (L=5) |
|---|---|---|---|
| \|t\| > 2 | 429 (5.42%) | 359.8 (4.55%) | 243 (3.07%) |
| \|t\| > 3 | 48 (0.61%) | 21.3 (0.27%) | 18 (0.23%) |
| \|t\| > 4 | 14 (0.18%) | 0.5 (0.01%) | — |

Read naively that is a right tail the null does not produce. **It is not.** The excess is a *location* shift, not extra dispersion, and each of the three families contributing it independently diagnosed its own shift as **one effect duplicated across correlated cells** — validated against its own circular-shift or permutation null (trend +0.443; bands −0.797; confluence +0.793; the other four sit within 0.36 of zero). Remove one common shift per family and the pooled distribution lands on the null almost exactly:

```
  de-meaned pooled sd          0.927
  empirical null sd implied
    by the corrected bootstrap 0.926      <- essentially identical

  |t| > 2   283  (3.58%)   vs Gaussian 4.55%   vs bootstrap null 3.07%
  |t| > 3    31  (0.39%)   vs Gaussian 0.27%   vs bootstrap null 0.23%
  |t| > 4     5  (0.06%)   vs Gaussian 0.01%
```

**After removing seven location shifts, the classic TA toolkit produces *fewer* |t|>2 cells than Gaussian noise, at a dispersion indistinguishable from the empirical null to three decimals.** That is the central result of the study. The distributions coincide. There is no discovery tail — there are seven restatements of one polarity/regime tilt, plus noise.

The residual 14 cells above |t| = 4 are also corrected downward: 10 of them are the bands 1d panel, whose t-statistics review showed treat serially adjacent daily bars as independent. Cluster-robust SEs take that panel's `|t|>4` rate from 5.68% to 2.84% (19 → 14 cells above 3; 10 → 5 above 4), so the **corrected pooled count above |t| = 4 is 9, not 14**.

### 3. Per-family results

Best |t| is the raw sweep maximum; the "clears 4.88" column applies the programme-level bar to a 7,908-cell search.

| family | \|t\| cells | best \|t\| | clears 4.88 | verdict |
|---|---|---|---|---|
| oscillators (RSI, Stoch, MACD, CCI, MFI, ROC) | 1,375 | 5.63 | 1 → **0** | **NOTHING.** The one cell (MFI14 exit-oversold, 1d, H=24, n=30) fails cross-exchange replication: Binance daily t = −1.45. Bitstamp-2014-2017 artifact. Stochastic 14/3/3 — the most-used retail oscillator — gives 0 of 72 cells above \|t\|=2. |
| trend-following (MA cross, SuperTrend, PSAR, Ichimoku, Donchian) | 1,752 | 4.15 | 0 | **NOTHING individually.** Max is the Donchian *control* on n=34 with a dead short side, p=0.025 against its own null max. A real aggregate daily-state tilt (below). 4h/1h flat null. |
| bands & volatility (Bollinger, Keltner, ATR, TTM) | 528 | 5.57 → **4.06 / 2.71** corrected | 11 claimed → **0** | **CORRECTED from SIGNAL to NOTHING.** See §"Corrections". 1h panel reproduces the null to two decimals (\|t\|>2 in 4.55% vs 4.55% expected). |
| candlestick patterns | 1,203 | 3.36 | 0 | **NOTHING.** Max \|t\| is *below* the 3.48 expected maximum of 1,203 iid normals; best textbook-direction cell is +2.75, below the expected max of even a 100-cell null (2.74). |
| volume & VWAP (OBV, CMF, A/D, CVD, anchored VWAP, POC) | 1,082 | 3.78 → **1.41** episode-clustered | 0 | **NOTHING.** Distribution is *under*-dispersed (sd 0.869). 11 of the 12 strongest cells collapse or reverse sign under episode weighting. "Price crosses VWAP" is a coin flip on all three anchors. |
| divergences (RSI/MACD/OBV/CCI, classic + hidden) | 816 | 2.72 | 0 | **NOTHING.** Causal distribution is *narrower* than its own random-entry null (sd 0.939 vs 1.022; 3.2% vs 5.4% above \|t\|=2). Bonferroni-adjusted p on the best cell = 1.00. |
| confluence (N-of-M agreement) | 1,152 | 4.14 | 0 | **NOTHING.** Distribution genuinely shifted (p<0.017 vs circular-shift null) but not one cell clears its own null's max\|t\|. 0 of 27 train-significant cells survive out of sample with the same sign; 1h cross-half sign agreement 42.2%, below a coin flip. |
| **pooled** | **7,908** | **5.63** | **5 → 0** | **NOTHING** |

Five cells clear 4.88 as originally computed. Four are the bands upper-band cells that review corrected to 2.71–4.06. The fifth fails cross-exchange replication. **Net survivors: zero.**

### 4. Polarity: fade loses, in seven of seven families

Recomputed by pooling the cells that carry an explicit textbook-direction label:

| reading | cells | mean signed t | fraction t > 0 |
|---|---|---|---|
| **FADE** (buy oversold, sell the upper band, trade the reversal pattern, classic divergence) | 2,433 | **−0.489** | 38.0% |
| **FOLLOW** (momentum, continuation, hidden divergence, trend state, confluence) | 4,608 | **+0.493** | ~72% |

Per family, every one agrees:

- **oscillators** — fade 631 cells, signed t −0.454, only 32.5% positive, sign-test **z = −8.80**; follow +0.153, 55.0% positive.
- **bands** — fade mean −0.797; **the maximum fade t anywhere in 528 cells is +1.695. Not one fade configuration in the entire family reaches |t| = 2.** Twelve of fourteen event kinds have a negative mean fade t.
- **candlesticks** — reversal patterns 866 cells, mean t −0.424, 34.2% positive; the small-body/long-wick "rejection" patterns retail treats as reversals are the worst (pin_bull_66 at −1.23, only 8% of its cells positive).
- **divergences** — classic (fade) 408 cells, mean −0.283, 37.0% positive; hidden (follow) +0.120, 57.6% positive. The fade side produces 21 of the 26 |t|>2 cells, i.e. the fade side is where the loud noise lives.
- **volume/VWAP** — every fade construct negative; the single worst rule in the family is the textbook "sell the 2σ VWAP stretch" at mean t −1.80.
- **trend-following** — follow-coded by construction: mean +0.443, 74.9% of cells positive; **302 of 304 daily state cells positive**, so fading daily trend structure loses in 99% of configurations tested.
- **confluence** — follow +0.793, 86.0% positive, 15 of 16 rules favour follow; the sole exception is solo-volume at −0.006, which is zero.

**This corroborates the repo's standing rule decisively.** It was previously four-times replicated; these seven independent families make **eleven replications**, now across ~7,900 cells, three timeframes, two exchanges and fifteen years. The rule should be treated as settled and used as a **veto**, not a signal.

Two honest limits on it. The magnitude is half a t-unit — a tilt, not a trade. And the accurate phrasing is the oscillator study's: *"fading costs, and not fading is neutral-to-slightly-positive."* Follow winning does not mean follow pays.

### 5. What survived, and what it costs

Round trip: 4bp maker-maker / 5bp maker-taker / 8bp taker-taker.

| candidate | edge (gross) | capture (% of σ_H) | vs round trip | statistical standing | label |
|---|---|---|---|---|---|
| **Bollinger squeeze released downward, short, 1d** (BBsqz20_fire_dn) | +240.9bp H=4, +526.4bp H=12, +1008.4bp H=24 (n=142/90/46) | 30.0 / 32.7 / 34.4% | clears 8bp by 30–125× | cluster-robust t **4.77 / 4.77 / 4.19** — clustering does *not* bite (G ≈ n); medians track means (409 vs 526bp at H=12) | **Best object in 7,908 cells — and still below the 4.88 bar.** Not a strategy. A candidate needing its own out-of-sample confirmation. |
| **Daily trend STATE, in aggregate** | 12–167bp | 3.3% (H=1) → 9.2% (H=24) | clears cost on 1d | mean t of 304 cells **+1.289** vs circular-shift null −0.091 ± 0.506, **0 of 120 reps exceeded**, p<0.008 | Real but **unattributable** — family means span only 0.22 to 0.77, less than the null sd of a single cell. Cost is not the constraint; **reliability is** (split-half r = +0.249; first-half \|t\|>2 cells regress to t = +0.41). Already harvested by S-003/S-005. |
| **RSI entering overbought, 1d, LONG** | +231.8 to +839.8bp | **33 – 54%** | clears 8bp by ~30× | best cell t = 3.78; plateau across lengths 7/14/21 and thresholds 65/70/75; replicates on Binance at +21 to +50% | **Information, not a strategy.** Fires ~7×/year; below the programme bar; almost certainly the same daily trend persistence ADX/EMA already capture. Worth exactly one incremental-information check against existing ADX/EMA state. |
| **Bands upper-band continuation, 1d** | +99bp H=1 | 24.6% | clears 8bp by 12× | **corrected: t 5.57 → 4.06 (episode) → 2.71 (year)** | **Refuted.** Equal-weighting the 122 episodes gives **−29.4bp (t = −1.66)**. Excluding 2013 + 2017 drops it to t = 2.84, capture 11.4%, **median edge −0.6bp**. Regime artifact. |
| **The entire intraday book (1h, 4h)** | 0.6 – 8bp | 0.72 – 3% | **fails at every horizon** | at 1h/4h the real t-distribution is *marginally weaker* than block-bootstrapped price (real 1h mean\|t\| 0.608 vs 0.720; P(\|t\|>3) 0.12% vs 0.35%) | **Dead before costs, then dead again after them.** |

The cost arithmetic that ends the intraday case in one line: **σ(H=24) at 1h is 2.434%, so an 8bp taker round trip is 3.3% of σ — and the median cell across these sweeps captures 2.6–5.2% of σ.** The median classic-TA signal at 1h does not cover its own commission. Median capture by family: volume/VWAP 2.59%, trend 2.85%, oscillators 3.08%, confluence 0.72% (1h) to 8.0% (1d), divergences 5.22%. This is the 18th through 24th study to land in the repo's standing 3–13% capture band.

### 6. The plain answer to "most traders use these tools"

**Your argument was worth running, and it loses — but it loses in a more interesting way than the counter-argument predicted.**

The counter-argument said 70–90% retail loss rates are equally consistent with the tools carrying no edge. That is now the confirmed reading, with one sharpening. It is not merely that the tools carry a small edge that costs eat. **At 1h and 4h, real BTC price run through the real toolkit produces a weaker t-distribution than bootstrapped price with all cross-block structure destroyed.** There is nothing there to be eaten. The tools are informationally empty, not marginally unprofitable.

And the toolkit is worse than a coin flip in one specific, measurable respect. Its **default polarity is the losing side of a real asymmetry**: −0.489 mean signed t over 2,433 fade cells, 38.0% positive, with a sign-test z of −8.80 in the oscillator family alone. Buying oversold, selling the upper band, trading the reversal candle and taking the classic divergence are not neutral acts that fees turn negative — they are systematically, replicably negative before fees. That is a mechanism by which a trader using exactly these tools loses faster than a random-entry trader with the same cost structure.

**Why the folklore survives is also measured, and it is the most actionable output of the programme.** One bar of lookahead is worth roughly +5 t-units (oscillators), 13.7× the mean t (trend), 5.5× mean |t| and 18.9× max |t| (candlesticks), and ~90× on 1h confluence. A 3-to-5 bar pivot confirmation — the standard way divergences are drawn on a chart and scripted on TradingView — manufactures **+40 to +719bp** of apparent edge on rules worth zero to slightly negative when computed honestly; on daily OBV a five-bar confirmation turns a −62bp loser into a +719bp winner, a swing of 781bp out of pure hindsight. **The standard backtest of a divergence or a pattern is not wrong at the margin; it is wrong by five t-units — more than the entire distance between the null and any real effect in this study.** So the tools look excellent to anyone who tests them the way they are normally tested, which is precisely how they are normally tested.

What this does **not** say: that nothing works. Two things survive seven families of scrutiny — a directional asymmetry (do not fade) and a weak, unattributable daily regime tilt worth 3–9% of σ. This repo already harvests both through S-003 and S-005. The correct use of the classic toolkit is as a veto on counter-trend entries, not as a source of signals.

### Corrections applied

Three reviewer findings were sustained and are reflected above; figures below are the corrected ones.

1. **Bands-volatility, refuted as fatal.** The headline claim — "11 of 528 non-redundant cells clear the family-wise Bonferroni threshold of |t| > 3.904" — treated 727 serially adjacent daily observations as independent. At H=1 the non-overlap rule (`i ≥ last + H`) imposes zero spacing; 605 of the 726 gaps are one calendar day, and the 727 entries are ~51–72 contiguous band-riding episodes. Cluster-robust SEs under three independent cluster definitions give **3.74 / 3.48 / 2.71** against the study's own 3.904 bar. Eight of the eleven fail outright. **Corrected count at a correctly specified threshold: 0 of 528, not 11. Verdict changed from SIGNAL to NOTHING.** The three that hold under clustering are the squeeze-down cells (G ≈ n), which **inverts the study's own ordering of its findings** — the squeeze-released-downward short survives, the upper band does not. Also corrected: the reported per-timeframe sd(t) of 1.922/1.201/0.945 and the "over-dispersed by ~42%" headline were computed on mirrored both-polarity sets; true non-redundant dispersion is 1.523/0.979/0.789, and 63% of the claimed variance excess is the polarity location shift double-counted as dispersion.
2. **Null-distribution study, refuted as fatal.** Recommended bar corrected from **5.29 to ~4.6**; "Bonferroni is too lenient" corrected to "Bonferroni is approximately correct (ratio 1.02–1.08)"; per-cell `P(|t|>3)` corrected from 0.98% to **0.23%**, thinner than Gaussian. Separately, `adx()` returned all-NaN, so 12 ADX-regime configs never ran; the one-line fix (`nan_to_num` on `dx` before Wilder smoothing) recovers 96 cells including adx14>25&+DI at t = +3.57 — still far below any bar.
3. **Oscillators, arithmetic transposition.** "654 cells below zero vs 721 above" is reversed; the true counts are 721 below / 654 above. The left-skew conclusion is correct, the labels were swapped.

One further inconsistency found while assembling this section, not previously flagged: the oscillator study states that RSI14_enter_OB75 at t = 3.78 "sits below the family-wise median and is therefore not individually significant," but that family's own stated bar is median 3.28 / p95 3.63, both of which 3.78 exceeds. **The conclusion is unaffected** — 3.78 is comfortably below the programme-level bar of 4.88 that applies to a 7,908-cell search — but the stated reason is wrong on the study's own numbers.

### What this closes

The classic-TA prediction question is answered and the family should be closed to further sweeps. Seven independent teams, 7,908 cells, ~3.6M null evaluations, three timeframes, two exchanges, fifteen years, and every methodological attack the programme knows how to mount — cluster-robust SEs, circular-shift and permutation nulls, block bootstraps, split-half, cross-exchange, era jackknives, deliberate-lookahead calibration — produce **zero survivors**, a pooled dispersion of 0.927 against an empirical null of 0.926, and one settled behavioural rule pointing the opposite way from the textbook.

The programme's remaining edge, if it has one, is not in what these indicators predict. It is in execution, in regime state, and in the veto.
