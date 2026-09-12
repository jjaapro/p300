# S-005: frequency sweep and filter matrix

Study 22. 2026-09-11. Question: can S-005 (ADX regime flip + EMA consensus veto,
Sharpe 1.07 / CAGR 42.6% / maxDD -41% at 3.5 switches/yr on daily bars) be tuned
into a higher-frequency swing strategy, and does combining it with additional
independent indicators improve it?

**Answer: no to both.** 1,587 configurations across five indicator families;
none clears the program-wide Bonferroni bar of |z| >= 4.16. Shortening the bar
raises trade count 3.6 -> 30.4/yr and destroys the edge, and fees are not why.

`backtest_adx_regime.py` did not exist -- the ADX review flagged this and it was
correct. The engine was rebuilt from the Pine source and validated against every
published figure before anything was layered on it. That rebuild produced the
most consequential finding in the study, and it is not about frequency at all.

## Three results that outrank the matrix

**1. The veto's contribution is an artifact of the stop-fill model.**
The published figures use a pessimistic engine: the 10% stop breach is detected
on the bar's range but the exit is priced at that bar's CLOSE. Under the
shipped, defect-fixed Pine semantics -- the stop actually filling at the stop
price -- the ranking inverts:

| fill model | S-003 alone | S-005 20/100 |
|---|---|---|
| study (breach -> close fill) | Sh 1.02, 40.9%, -58.8% | Sh 1.05, 41.4%, **-41.2%** |
| **live (fill at the stop)** | **Sh 1.08, 43.6%, -37.5%** | Sh 1.01, 39.4%, -41.4% |

S-003 alone is better than S-005 under live semantics, on both Sharpe and
drawdown. The EMA veto is not adding what the header claims it adds.

**2. The noise floor is larger than every margin ever claimed for this family.**
Closing the "daily" bar at 00/04/08/12/16/20 UTC gives Sharpe 0.85-1.16 on the
same rule and the same data. That is a **+/-0.15 Sharpe noise floor from an
arbitrary convention**, against S-005's claimed +0.03 to +0.07 over S-003.

**3. S-005 does not beat buy-and-hold on return.**

| | S-005 20/100 | buy & hold |
|---|---|---|
| CAGR | 41.4% | **42.3%** |
| Sharpe | **1.05** | 0.86 |
| maxDD | **-41.2%** | -83.4% |
| time in market | **36.5%** | 100% |

Same pattern as everything else in this repo: a risk transformation, not a
return edge. Fees are immaterial at 3.7 round trips/yr (41.8% at 4bp vs 41.4%
at 10bp).

---

## S-005: frequency and filter matrix

**One-line answer: no.** S-005 cannot be tuned into a higher-frequency swing strategy, and none of the 1,587 overlay configurations searched against it — volatility, derivatives positioning, valuation, flow, cross-asset — survives its own controls. This is a clean negative on a 15-year strategy. The only thing worth building next is not an overlay at all; it is a pre-registered test of S-005's short leg (§5).

---

### 1. Does the Python engine reproduce the live strategy?

Yes, within tolerance, and the caveat that produces is larger than any result below.

| Config | Metric | Published | Reproduced | Δ |
|---|---|---|---|---|
| S-003 alone (adx_v3 adaptive) | Sharpe / CAGR / maxDD | 1.00 / 39.6% / −59% | **1.02 / 40.9% / −58.8%** | +0.02 / +1.3pp / +0.2pp |
| S-005 consensus 20/100 | Sharpe / CAGR / maxDD | 1.07 / 42.6% / −41% | **1.05 / 41.4% / −41.2%** | −0.02 / −1.2pp / −0.2pp |
| S-005 | switches/yr, closed trades | 3.5, ~49 in 15y | **3.7, 52** | +0.2, +3 |
| S-005, era 2018-01..2021-01 | CAGR | 44% → 62% | **44.0% → 62.0%** | exact |
| Five-pair EMAX plateau | Sharpe band / maxDD band | 1.04–1.08 / −37…−43% | **0.98–1.08 / −38.8…−42.7%** | 10/50 cell lands 0.98 |

Bitstamp BTC/USD daily, 5,503 bars 2011-08-18 → 2026-09-10, measured 2014-01-01 → 2026-08-10 (12.61y), EMA150 gate + 10% SL, fees charged per leg. All four rejected alternatives (soft 50/50 split, Donchian 20/10 and 55/20, two-sided RSI(3) chop MR) reproduce as worse, as published. Three independent published claims land on the nose, so the state machine is exact.

**Three caveats this places on everything below.**

1. **The stop-fill model had to be recovered and it is load-bearing.** The study engine detects the 10% breach on the bar's range but prices the exit at that bar's close (pessimistic). Under the *shipped, defect-fixed* Pine semantics — the stop actually fills at the stop price — the veto stops being an improvement: S-003 alone gives Sharpe 1.08 / CAGR 43.6% / maxDD −37.5% against S-005's 1.01 / 39.4% / −41.4%. **The veto mostly substitutes for a stop that is working properly.** Every overlay below is measured against the study-fill baseline (1.05), and the frequency conclusion was re-run under the live fill and is unchanged (1.01 / 0.94 / 0.85 / 0.65 / 0.76 at 1d/12h/8h/6h/4h).
2. **The baseline's own margin is inside the measurement noise.** A bar-phase control — the identical daily strategy on daily bars closing at 00/04/08/12/16/20 UTC — spans Sharpe 0.85–1.16, median 1.06. The honest noise floor for this whole family is about **±0.15 Sharpe**, larger than S-005's +0.03 over S-003 and larger than most overlay deltas in §3.
3. **n is the binding constraint everywhere.** 46 closed trades on the full span, 23 per half on the 60/40 chronological split (2021-07-25), 31 on the funding span, 26 on the OI span. Nothing positive is concluded from any train/test split in this study; splits are used only to kill things.

---

### 2. The frequency answer: 1d / 12h / 8h / 6h / 4h

Identical parameters in **bars** (ADX14 25/20, EMA50/150, EMAX 20/100), same measurement window on all five intervals, native Bitstamp steps (8h aggregated from 4h; resampled 4h→1d closes match native daily to 1.8e-4).

| interval | n | trades/yr | avg hold | Sh@4bp | Sh@8bp | CAGR@4bp | CAGR@5bp | CAGR@8bp | maxDD | TiM |
|---|---|---|---|---|---|---|---|---|---|---|
| **1d (baseline)** | **46** | **3.6** | **872 h (36.4 d)** | **1.05** | **1.05** | **41.8%** | **41.7%** | **41.6%** | **−41.1%** | 36.5% |
| 12h | 108 | 8.6 | 404 h (16.8 d) | 0.98 | 0.97 | 39.3% | 39.2% | 38.8% | −54.1% | 39.5% |
| 8h | 161 | 12.8 | 285 h (11.9 d) | 0.85 | 0.84 | 33.4% | 33.2% | 32.7% | −54.0% | 41.5% |
| 6h | 235 | 18.6 | 196 h (8.2 d) | 0.67 | 0.66 | 22.8% | 22.6% | 21.9% | −60.1% | 41.7% |
| 4h | 384 | 30.4 | 123 h (5.1 d) | 0.83 | 0.80 | 32.6% | 32.2% | 31.0% | −68.2% | 42.7% |

Frequency rises exactly as intended — 3.6 → 30.4 trades/yr, hold 36 d → 5 d — and the sample-size problem is fixed (only 3 of 90 grid cells fall below n=30). The edge does not survive it. Sharpe falls, maxDD deteriorates monotonically, and the same shape holds for S-003 alone (1.02 / 0.83 / 0.79 / 0.58 / 0.68) and under the live intrabar fill.

**Fees are not the constraint.** The entire 0bp → 8bp round-trip drag is 0.4pp of CAGR at 1d and 3.2pp at 4h (34.2% → 31.0%); Sh@4 and Sh@8 never differ by more than 0.03. The ~19pp of CAGR that vanishes between 1d and 6h is signal decay, not friction.

**Average hold against BTC sigma over that hold — CORRECTED.** The original script hardcoded σ = 2.355%/day, the repo's 45%-annualised *forward-vol* convention (STATUS.md). Realised Bitstamp daily sigma over this study's own measurement span is **3.560% (68% annualised)**; the 2.355% figure is almost exactly the span's mean absolute daily return, a mean-|move| statistic mislabelled as a standard deviation. Every ratio was inflated 1.51×.

| interval | avg hold | σ_sqrt(T) | σ_emp(T) | avg abs trade ret | ratio (sqrt) | ratio (emp) | 10% SL in σ | stop-exit share |
|---|---|---|---|---|---|---|---|---|
| 1d | 36.4 d | 21.46% | 25.63% | 22.01% | **1.03** | **0.86** | 0.47 σ | 26% |
| 12h | 16.8 d | 14.61% | 15.70% | 11.05% | 0.76 | 0.70 | 0.68 σ | 14% |
| 8h | 11.9 d | 12.26% | 12.69% | 8.24% | 0.67 | 0.65 | 0.82 σ | 7% |
| 6h | 8.2 d | 10.17% | 10.25% | 6.25% | 0.61 | 0.61 | 0.98 σ | 7% |
| 4h | 5.1 d | 8.06% | 7.93% | 4.94% | 0.61 | 0.62 | 1.24 σ | 6% |

The original "below 6h the average trade no longer clears one sigma" claim is **withdrawn as false**: no interval clears one sigma, the daily included (0.86–1.03), and there is no 6h cut-point. The corrected statement is relative, not absolute — measured against the mean absolute BTC move over a *random* hold of equal length, the daily machine captures **1.18×** and every intraday interval captures **less than a coin-flip hold** (12h 0.97×, 8h 0.90×, 6h 0.86×, 4h 0.89×). Alongside it the 10% stop stops binding (0.47σ → 1.24σ of the hold, stop-exit share 26% → 6%) and the ADX/veto exits take over, rising from 74% to 94% of exits. What actually breaks is the hit rate, 47.8% → 36.5%, not the payoff geometry (avg winner/loser 5.4× → 3.3×); profit factor falls 4.98 → 1.87.

**The decisive, selection-free evidence.** Running the *identical* 18-cell (entry/exit × ADX-length) grid in each half of the 60/40 split, no cell picking:

| interval | median TRAIN Sh@8bp | median TEST Sh@8bp | cells with TEST Sh > 0.7 |
|---|---|---|---|
| 1d | 1.12 | **0.72** | **10 of 18** |
| 12h | 1.15 | **0.29** | 0 of 18 |
| 8h | 0.97 | **0.06** | 0 of 18 |
| 6h | 0.83 | **0.23** | 0 of 18 |
| 4h | 0.90 | **0.20** | 0 of 18 |

Every cell-picked intraday winner collapses across the split: 12h 20/15 L20 1.40 → 0.49; 8h 20/15 L10 1.36 → −0.10; 4h 25/15 L10 1.36 → 0.21. The daily baseline is the only row that holds (1.08 → 1.00), at n=23 per half.
*Correction:* the earlier "sign test, p ≈ 1e-6" framing is withdrawn. The 18 cells within an interval have median pairwise test-half return correlation 0.62–0.72 (~1.5 effectively independent cells each), so this is roughly five correlated interval-level observations, not 90 Bernoulli trials. It is reported as a descriptive contrast; the monotone surface across two axes carries the conclusion on its own.

**The two controls that close the design gap.** (i) *Time-scaled* — every lookback multiplied by bars/day so the rule spans the same calendar time and frequency does **not** rise: Sh@4 1.05 / 0.72 / 0.68 / 0.48 / 0.01 at 2.5–4.1 trades/yr. Finer sampling of the same-horizon rule is not free; ADX on 4h bars is a different indicator, not a higher-resolution daily one. (ii) *Intermediate / calendar-anchored* — ADX trigger in bars so frequency still rises, but norm_w, the EMA150 gate, the EMA50 direction and the EMAX veto all spanning daily calendar time. This is how anyone would actually build a faster S-005:

| interval | n | trades/yr | Sh@4bp | Sh@8bp | CAGR@8bp | maxDD |
|---|---|---|---|---|---|---|
| 1d | 46 | 3.6 | 1.05 | 1.05 | 41.6% | −41.2% |
| 12h | 79 | 6.3 | 0.92 | 0.92 | 32.4% | −61.2% |
| 8h | 127 | 10.1 | 0.91 | 0.90 | 34.0% | −57.8% |
| 6h | 167 | 13.2 | 0.89 | 0.88 | 32.8% | −50.7% |
| 4h | 266 | 21.1 | 0.78 | 0.76 | 26.6% | −54.0% |

Nothing reaches the daily baseline and every row costs at least 9pp of maxDD.

**Verdict on frequency: keep S-005 on the daily bar.** 12h is the only arguable interval (0.98 vs 1.05) and it buys 5 extra trades a year for 13pp of maxDD and a lower Sharpe — a bad trade, and its out-of-sample median of 0.29 says it is worse than that. Do not chase the 12h 20/15 L20 cell: its neighbours are 0.93 / 0.92 / 1.05, one interval either side is 0.97 / 0.64, and the split kills it.

---

### 3. The overlay matrix

Every overlay tested, by role. Baselines differ by span; deltas are always against the baseline **on that overlay's own span**. `Δ maxDD` in pp, **+ = shallower drawdown**. Sorted by whether the result survived verification.

**Spans:** **[A]** Bitstamp daily 2014-01..2026-08, base Sh 1.05 / −41.2% / n=46 · **[B]** Binance spot 2018-01..2026-08, base 1.10 / −44.2% / n=37 · **[C]** funding span 2019-09..2026-09, base 1.07 / −38.6% / n=31 · **[D]** OI span 2020-09..2026-09, base 1.29 / −38.6% / n=26 (below the n≥30 bar).

| # | Overlay | Role | Span | n | Sharpe | ΔSh | Δ maxDD | Status |
|---|---|---|---|---|---|---|---|---|
| **Tier 1 — passed every control except independent replication and the Bonferroni bar** ||||||||
| 1 | Binance **spot taker-buy z40** agrees with held direction, size 1.0/0.5 | sizer | B | 37 | 1.31 | **+0.21** | **+15.0** | 12-cell plateau, 5 price placebos all hurt, 24/37 trades helped, both halves positive (+0.23/+0.15). **Fails perp-flow replication (−0.02/−0.03) and Bonferroni (p 0.00030 vs 0.00022).** Do not ship |
| 2 | spot taker-buy z20 / z60 / z90 / z120 / z10 | sizer | B | 37 | 1.28/1.28/1.24/1.26/1.23 | +0.18/+0.18/+0.14/+0.16/+0.13 | +14.4/+11.7/+10.1/+8.9/+12.6 | the plateau around #1 |
| 3 | spot taker-buy, flat while contradicting | exit accel | B | 37 | 1.27 | +0.17 | +13.3 | works but costs 9.2pp CAGR and 8× turnover; #1 dominates |
| **Tier 2 — real plateau, killed by a zero-information control** ||||||||
| 4 | **ATR% `rma(TR,30)/close` rank, amp 0.75** | sizer | A | 46 | 1.25 | +0.20 | −3.3 | **CORRECTED → NEUTRAL.** Estimator mixes trailing drift into "volatility"; residualise on drift-rank → **+0.02**. Drift-free twins: +0.07/+0.07/+0.02 |
| 5 | ATR%(40) amp 1.00 / ATR%(20) amp 0.75 | sizer | A | 46 | 1.29 / 1.20 | +0.24 / +0.15 | −8.9 / −5.3 | same contamination |
| 6 | **f7_p signed funding-percentile crowding, k=+0.75** | sizer | C | 31 | 1.34 | **+0.26 raw / +0.06 vs control** | +5.0 raw / **−6.9 vs control** | **CORRECTED: HELPS → NEUTRAL.** A no-data flat long1.0/short0.25 weight reaches 1.28 (+0.20) and −26.7%. Residual z = +0.25/+0.28, p 0.37–0.39. maxDD claim **deleted** (it was one 2024 episode, n=1) |
| 7 | f7_p k=+0.50 / k=+1.00 / f1_p / f30_p | sizer | C | 31/37/31/31 | 1.27/1.35/1.20/1.26 | +0.20/+0.28/+0.13/+0.19 | +5.0 | same object; monotone k-gradient through zero, lifts all five EMAX pairs (+0.22..+0.28) — but so does the no-data tilt |
| 8 | f7_p de-lever only (cap 1×, spot-implementable) | sizer | C | 31 | 1.19 | +0.12 | +5.0 | ~40% of the gain; the leverable half is the overfittable half |
| 9 | dd_ath / bmsb / mayer / pl_sigma **signed** (measure × direction) | sizer | A | 46 | 1.17/1.15/1.14/1.11 | +0.12/+0.10/+0.09/+0.06 | −7.0/+3.9/−0.4/−2.4 | direction tilt in a valuation costume; NULL-2 (within-direction permutation) p = 0.103/0.019/—/0.185; time-shifted placebo still returns 1.00–1.09 |
| 10 | *CONTROL:* flat long 1.00 / short 0.25, **no data at all** | sizer | A / C | 46 / 31 | 1.24 / 1.28 | +0.19 / +0.20 | +6.1 / +11.9 | not an overlay — the null that beats every valuation overlay and 77% of the funding result. Stable +0.21/+0.23 across halves |
| **Tier 3 — no plateau, or a spike, or n collapses** ||||||||
| 11 | ATR%(30) rank<0.50 second veto | veto2 | A | 58 | 1.30 | +0.25 | +12.8 | flips to −0.13 / −0.19 / −0.18 with drift-free / Parkinson / close-close estimators |
| 12 | rv60 rank<0.75 | exit accel / veto2 | A | 40 / 47 | 1.19 / 1.26 | +0.14 / +0.21 | −0.6 | narrow ridge: rv20 0.88, rv30 1.08, rv45 1.28, rv60 1.26, rv90 1.00 |
| 13 | VIX rank252 > 0.9, cut size | sizer | A | 46 | 1.14 | +0.09 | +6.1 | edge, not plateau (below q=0.75 it is worse than baseline); helps 16/46 trades, top-3 = 48% of the gain |
| 14 | NDX < SMA100, cut size | sizer | A | 46 | 1.13 | +0.08 | +6.3 | 7-cell band 1.09–1.17 but entirely inside permutation noise (p 0.029, z +1.83) |
| 15 | sign(SPX−SMA200) confirms direction | sizer | A | 46 | 1.12 | +0.07 | +6.1 | dies with #14 |
| 16 | SPX < SMA150 (cross-asset plateau centre) | sizer | A | 46 | 1.08 | +0.03 | +6.1 | **TRAIN +0.06, TEST −0.00.** Dead out of sample |
| 17 | atrp20 rank<0.50 | entry filter | A | 35 | 1.12 | +0.07 | +1.4 | burns 11 of 46 trades for 2.4pp CAGR |
| 18 | keep BMSB>0 / keep Mayer>1 at entry | entry filter | A | 25 / 27 | 1.25 / 1.22 | +0.20 / +0.17 | +6.1 | long-only filters in disguise (L/S 25:0 and 24:3); reproduces #10 to two decimals |
| 19 | f7_p q=0.9 / f7_p q=0.6 / f30_p q=0.9 | exit accel / entry filter | C | 31/14/24 | 1.16/1.29/1.28 | +0.09/+0.22/+0.21 | — | all worse than the sizer; two break the n bar |
| 20 | mayer>2.4 → long size 0 / pl_sigma>1 → long size 0 | exit accel | A | 46 | 1.09 / 1.08 | +0.04 / +0.03 | +1.6 / 0.0 | inside noise |
| **Tier 4 — flat or negative; nothing to salvage** ||||||||
| 21 | downside rv30 / Parkinson30 / sma(TR/c,30) / close-close rv30 | sizer | A | 46 | 1.15/1.12/1.11/1.07 | +0.10/+0.07/+0.06/+0.02 | −6.1/−7.3/−7.6/−4.7 | every drift-free volatility estimator. **This was the role that mattered and it produced nothing** |
| 22 | ATR%(30) residualised on drift | sizer | A | 46 | 1.03 | −0.02 | −9.6 | the decomposition of #4 |
| 23 | pl_sigma / sma200w_z / halving / dd_ath / mayer_z / bmsb_z, direction-agnostic | sizer | A | 46 | 1.06/1.03/1.03/0.99/0.96/0.94 | +0.01/−0.02/−0.02/−0.06/−0.09/−0.11 | −3.8/−6.9/−4.0/−15.2/−4.5/−3.3 | **the actual valuation hypothesis.** Plateau spans 1.05–1.08, i.e. it sits *on* the baseline. Tercile Spearman +0.055, p=0.71 |
| 24 | oi_p, doi7_p, doi30_p, gls_p, tta_p, ttp_p, tk_p | sizer + gates | D | 26 | — | −0.01 to +0.04 | — | all \|t_clust\| < 2.0; the +9–10pp CAGR cells are pure leverage at flat Sharpe. **n=26: "not shown to help", not "shown not to help"** |
| 25 | CVD 20d slope / volume rank252>0.7 | sizer | B | 37 | 1.00 / 0.88 | −0.10 / −0.22 | +9.7 / +11.3 | negative |
| 26 | 5 price-only placebos (EMA20, EMA50, RSI14, 5d ret, rv20/r7/r30) | sizer | B / C | 37 / 31 | 1.01–1.08 / 0.94–1.07 | −0.02 to −0.09 / −0.13 to 0.00 | — | the controls that make #1 interesting and #6 not |
| 27 | coupled corr60>0.3 / decoupled corr40<0.3 / gold>SMA200 | sizer | A | 46 | 0.99 / 0.91 / 0.80 | −0.06 / −0.14 / −0.25 | +1.0 / +7.1 / +8.4 | **the macro-coupling hypothesis is dead**: 14 of 18 coupling cells below baseline |
| 28 | SPX<SMA200 / coupled / DXY>SMA50 at entry | entry filter | A | 37 / 25 / 16 | 0.86 / 0.63 / 0.40 | −0.19 / −0.42 / −0.65 | +5.6 / −2.3 / −3.1 | destroys the strategy |
| 29 | taker-buy contradicts / CVD slope contradicts at entry | entry filter | B | 26 / 18 | 0.42 / 0.53 | −0.68 / −0.57 | −3.1 / +3.1 | destroys the strategy |
| 30 | keep pl_sigma<1 / dd_ath<−0.10 / halving>550d | entry filter | A | 43 / 35 / 26 | 1.07 / 0.94 / 0.67 | +0.02 / −0.11 / −0.38 | 0.0 | destroys the strategy |

**The role result is uniform across all five families: gates lose, sizers are the only defensible role, and the best sizers are controls.** Every entry filter tested anywhere cut n from 46 to 14–43 and took Sharpe with it or bought Sharpe by becoming long-only. Exit accelerators are the second-worst role (volatility family: 6 of 80 cells beat baseline, median ΔSharpe −0.33). Sizers keep all trades and pay no round trip — and the drift-free ones land at +0.02 to +0.10.

---

### 4. What survived Bonferroni and the plateau test

**Configuration count — program-wide, not per family.** 323 (frequency) + 532 (volatility) + 282 (derivatives) + 220 (valuation) + 230 (flow/cross-asset) = **1,587 configurations**, all searched against the same ~46-trade S-005 baseline on the same Bitstamp daily price series. Each report stopped at its own family bar (3.78 / 3.90 / 4.30 / 3.71 / 3.70); *that is not the right bar*. The correct family α is 0.05 / 1,587 = **3.15e-5, i.e. |z| ≥ 4.16**.

| Best result anywhere in the programme | statistic | vs 4.16 |
|---|---|---|
| Funding f7_p, bar-level regression with price controls | t_clust = **−3.81** (p 0.00070) | fails |
| Spot taker-buy flow, 10,000-draw circular-shift permutation | z = **+3.57** (p 0.00030) | fails |
| ATR% sizer, paired stationary block bootstrap | z = **+1.83 … +1.97** | fails |
| Best valuation overlay, within-direction permutation | p = 0.019 | fails by ~80× |
| Best cross-asset cell, permutation | z = +2.60 | fails |
| Funding f7_p **against the correct no-data control** | z = **+0.25 … +0.28** | fails by an order of magnitude |

**Nothing clears the program-wide bar. Nothing clears its own family bar either, with one exception** — the derivatives report's "PASSES" against a 10-pre-specified-test bar of |t| ≥ 3.05, and that family definition was chosen after the search.

*Correction to the frequency report's significance arithmetic:* the claim that "a difference between two Sharpes on the same span would have to exceed roughly 1.5" used the independent-samples formula. Two strategies on the same bars are paired and near-perfectly correlated; the measured paired bootstrap SE of a Sharpe difference in this repo is **0.10–0.16**, so the corrected bar is a ΔSharpe of roughly **0.45–0.65**, not 1.5 — a factor-of-three self-contradiction, now fixed. Note this makes the bar *easier*, and still nothing clears it.

**Plateau test — passed:**
- ATR% sizer: smooth interior maximum at L≈30–60 / amp≈1.0–2.0, monotone in both directions, needs a ≥2-year rank window (stable at 504/756/1008), 46/46 leave-one-trade-out positive. **Textbook plateau — and the neighbour that mattered was the estimator axis, where it collapses from +0.21 to −0.05.**
- Funding sizer: monotone k-gradient through zero (−1.00 → 0.74 … +1.00 → 1.35), three adjacent averaging windows 1.20–1.34, lifts all five published EMAX pairs, robust to a swept percentile window (63–756 → 1.20–1.34, interior max), 31/31 jackknife positive. **Not a fitted spike — the defect is benchmarking, not curve-fitting.**
- Spot flow sizer: 12 adjacent cells (6 z-windows × 2 size pairs) at Sharpe 1.21–1.31, every cell above baseline, monotone in window length.
- Valuation direction-agnostic sizers: a genuine 15-cell plateau spanning **1.05–1.08 — sitting exactly on the baseline.** That is the finding.

**Plateau test — failed:** the 12h 20/15 L20 frequency cell (neighbours 0.64–1.05, split 1.40 → 0.49); rv60 exit/veto (0.55 spread across adjacent lookbacks, failure on both sides); VIX rank (an extreme-tail edge, half the grid negative); every macro-coupling grid (14 of 18 cells below baseline); the "signed" valuation surfaces, which are monotone ramps saturating at "shrink shorts" rather than plateaux.

---

### 5. Verdict and the single cheapest next test

**Verdict.**
1. **Frequency: no.** Keep S-005 on the daily bar. Every route to more trades — shortening the bar at fixed bar-parameters, shortening it with calendar-anchored context, or sampling the same-horizon rule more finely — raises trade count and lowers Sharpe while deepening maxDD by 9–27pp. Costs are a red herring; the mechanism is that below the daily bar the average trade captures less than a random hold of the same length and the hit rate falls 47.8% → 36.5%.
2. **Independent indicators: no.** Of 1,587 configurations across volatility, derivatives positioning, long-horizon valuation, order flow and cross-asset macro, **zero survive both a plateau test and a zero-information control at the program-wide Bonferroni bar.** Two corrections are load-bearing: the derivatives family's HELPS verdict is downgraded to **NEUTRAL** (77% of its +0.26 is reproduced by a no-data long/short tilt that also beats its drawdown claim by 6.9pp), and the volatility family's headline sizer is not a volatility overlay at all (`rma(TR,L)/close` mixes trailing drift into the numerator/denominator mismatch; residualised, +0.20 becomes +0.02).
3. **Ship nothing.** The strongest surviving candidate — spot taker-buy imbalance as a 1.0/0.5 sizer, +0.21 Sharpe and +15pp of maxDD — fails an independent replication (perp taker flow is worthless where spot flow gives +0.19) and misses Bonferroni by a hair. It earns a dedicated pre-registered test, not a deployment.
4. **Two repo-wide flags.** (i) `rma(TR,L)/close` is a contaminated statistic (rank correlation with trailing drift −0.29 at L=20, −0.54 at L=60) and is the default ATR% in most charting packages — any study in this repo using ATR-as-%-of-price as a volatility input needs the same drift residualisation before it is believed. (ii) The repo's σ = 2.355%/day convention is a *forward-vol* assumption, not this sample's realised vol (3.560%); no threshold claim may be drawn from a table built on it.
5. **Keep, with no t-statistic attached:** the carry identity. On a perp, longs pay **+28.1%/yr** of funding in the top crowding quintile against **+5.4%/yr** in the bottom, and S-005 on the perp carries −5.2%/yr of drag that sizing down cuts to −2.5%/yr. If S-005 is ever expressed on futures rather than spot, declining to pay 28%/yr for the same exposure is arithmetic, not a bet.

**The single cheapest next test: the short leg.**

Three independent families converged on the same object without looking for it. S-005's short leg averages **+1.0% over 21 trades against +26.1% over 25 longs**. A flat long-1.00 / short-0.25 weight that reads **no data at all** is worth +0.19 on the Bitstamp span and +0.20 on the funding span, stable at +0.21/+0.23 across both halves, and it beats every valuation overlay, both winning entry filters, and 77% of the funding sizer. The funding overlay applied to longs only is **−0.06**; applied to shorts only, **+0.26**. Every apparent winner in this study is the same short-shrink wearing a different costume.

That is either a real asymmetry in the ADX regime machine or it is pure drift on a span where BTC rose ~40%/yr, and **21 trades cannot tell the two apart**. The test costs one script run against data already on disk, has one parameter (short weight), and needs no new fetch:

- **Pre-register:** one hypothesis — "S-005's short leg has no positive expectancy net of BTC's secular drift"; one parameter — short weight ∈ {0, 0.25, 0.5, 0.75, 1.0}; declared in advance.
- **Drift control:** run the short leg in isolation on the 2018 and 2022 bear segments, and on an asset without BTC's secular trend, so a drift explanation and a regime-skill explanation make different predictions.
- **Decision rule, set now:** if the short leg is flat only because of drift, the correct action is to *reduce* short size and re-baseline the entire overlay programme against the tilted engine — at which point the flow, funding and valuation "winners" have to re-clear a bar that is +0.20 higher, and on present evidence none of them will.

Do not run another overlay family against the untilted baseline. That is what produced 1,587 configurations and no result.
