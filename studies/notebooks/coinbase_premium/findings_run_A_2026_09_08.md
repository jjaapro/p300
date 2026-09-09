STATUS: CONCLUDED KILL per pre-registration

The frozen Coinbase-premium rule (14-day z of the hourly Coinbase-minus-Binance
spot premium, fire at z >= +2.0, long 48h, 2xATR14 stop, 18 bp round trip) fires
**both** pre-registered KILL clauses. ETH — the genuine out-of-sample asset —
fails C1; BTC fails C2 decisively.

Run 2026-09-08. Every number below traces to a file in `results/`.
Pre-registration: `README.md`, written and saved before any outcome was computed.

---

## 1. Clause table

| ID | Clause (verbatim from the pre-registration) | Measured | Fired? |
|---|---|---|---|
| **C0** | Evaluability: either asset produces **< 2 fires** | BTC **302** fires, ETH **312** fires | **NO** |
| **C1** | **ETH** 95% two-sided percentile bootstrap CI on mean R **includes or lies below zero** (`ci_lo <= 0`) | mean R **+0.2187**, CI95 **[-0.0052, +0.4587]** -> `ci_lo = -0.0052 <= 0` | **YES -> KILL** |
| **C2** | **BTC** entries >= 2025-01-01 UTC: **n >= 20** AND **mean R <= 0** | **n = 68**, mean R **-0.3191**, total R **-21.70**, CI95 [-0.622, +0.038] | **YES -> KILL** |

**Verdict: KILL.** Source: `results/clauses.json`.

Bootstrap spec as frozen: iid percentile, 10,000 iterations, seed 42, two-sided 95%.

---

## 2. Headline numbers

Full sample 2020-01-28 -> 2026-09-04 (entries), hourly bars 2020-01-01 -> 2026-09-08.
Source: `results/headline.json`, `results/trades_BTC.csv`, `results/trades_ETH.csv`.

| | BTC (carry-over) | ETH (**genuine OOS**) |
|---|---|---|
| Fires | 302 | 312 |
| Trades / year | 45.8 | 47.3 |
| Mean R | +0.1549 | +0.2187 |
| Median R | **-1.069** | **-1.054** |
| Total R | +46.8 | +68.2 |
| Max drawdown (R) | 30.8 | 23.8 |
| Win rate | 36.4% | 39.4% |
| Stopped out | 57.9% | 57.4% |
| Mean net return / trade | +0.178% | +0.482% |
| 1R (mean, % of notional) | 1.87% | 2.47% |
| Bootstrap mean R, CI95 | **[-0.083, +0.416]** | **[-0.005, +0.459]** |
| Bootstrap mean R, CI90 | [-0.050, +0.372] | [+0.028, +0.417] |
| P(mean R > 0) | 0.889 | 0.972 |
| Per-trade Sharpe (CI90) | +0.069 [-0.026, +0.149] | +0.103 [+0.015, +0.180] |
| Skew / kurtosis of R | 2.73 / 12.4 | 2.44 / 11.6 |
| DSR, n_trials = 1 | 0.906 | **0.981** |
| DSR, n_trials = 12 (primary for BTC) | **0.302** | n/a |
| DSR, n_trials = 24 | 0.194 | n/a |

The median trade is a **full stop-out** on both assets. The positive mean is
carried by a right tail (best trade +13.6R BTC, +13.7R ETH); 58% of trades die
at the stop.

---

## 3. The honest reading of C1 — it fired by a hair, and that is not the point

`ci95_lo = -0.0052`. Five thousandths of an R. Had the pre-registration named a
**90%** interval instead of 95%, ETH's CI would have been **[+0.028, +0.417]** and
C1 would **not** have fired. I am saying this plainly rather than burying it: the
clause fired on a knife edge, and a reader is entitled to know the verdict would
have flipped on that one pre-registered choice.

Two things stop this from being the study's weak point.

**First, the one-sided evidence is consistent, not contradictory.** ETH's
`P(mean R > 0) = 0.972` and its DSR at n_trials = 1 is 0.981. A two-sided 95% CI
is a one-sided test at 97.5%, so ETH sits at roughly one-sided p ~ 0.028 — real
evidence, just short of the bar that was set in advance. The pre-registered bar
was 95% two-sided and it was set before the number existed. It stands.

**Second, and decisively: ETH's entire positive mean is a pre-2024 artefact.**
Source: `results/era_split.json`, `results/trades_by_year.csv`,
`results/descriptive_addendum.json`.

| ETH, per-trade R | n | mean R | total R | CI95 |
|---|---|---|---|---|
| Pre ETH-spot-ETF (< 2024-07-23) | 221 | **+0.299** | +66.1 | [+0.019, +0.597] |
| Post ETH-spot-ETF (>= 2024-07-23) | 91 | **+0.024** | +2.1 | [-0.338, +0.420] |
| Calendar 2024+ | 114 | **-0.047** | -5.3 | [-0.348, +0.281] |
| Calendar 2025+ | 75 | **-0.041** | -3.1 | [-0.407, +0.371] |

ETH by year: 2020 +0.26, 2021 +0.22, 2022 +0.35, 2023 **+0.74**, 2024 -0.06,
2025 -0.12, 2026 +0.08. The edge lives in 2020-2023 and stops. So even at the
90% level, where C1 would not have fired, the *modern-era* ETH number is
negative and the verdict would still have been a kill on C2 alone. The knife
edge changes the paperwork, not the conclusion.

BTC tells the identical story: pre-ETF mean R +0.292 (n=195), post-ETF -0.095
(n=107), 2025+ -0.319 (n=68), 2026 -0.408 (n=25).

---

## 4. Descriptive results (requested)

### 4.1 The premium has decayed, hard

`results/premium_by_year.csv`. Premium in **basis points** of the Binance price.

| Year | BTC mean | BTC sd | ETH mean | ETH sd |
|---|---|---|---|---|
| 2020 | +4.58 | 16.55 | +4.44 | 16.95 |
| 2021 | +5.56 | 8.74 | +5.54 | 8.71 |
| 2022 | -1.26 | 8.77 | -1.39 | 8.73 |
| 2023 | +2.62 | 10.40 | +2.30 | 10.39 |
| 2024 | -0.38 | 6.73 | -0.70 | 6.45 |
| 2025 | +0.32 | 4.72 | +0.32 | 4.68 |
| 2026 (to 09-08) | **-6.04** | 6.05 | **-5.85** | 5.73 |

Two separate decays, both large:

1. **Dispersion collapse.** The premium's standard deviation fell from ~16.6 bp
   (2020) to **4.7 bp (2025)** — a 3.5x compression. Cross-venue arbitrage has
   done its job. This matters mechanically for the rule: a z-score is
   scale-free, so a +2 sigma fire in 2025 is a ~+10 bp dislocation where the same
   fire in 2020 was a ~+38 bp dislocation. The rule keeps firing at the same
   rate (43-51 fires/year every year) on progressively smaller real events, while
   the 18 bp cost does not shrink.
2. **Level flip.** The mean premium is +2.90 bp pre-BTC-ETF and **-1.64 bp**
   post; in 2026 it is **-6.0 bp**, persistently negative. The "US demand
   proxy" now reads as US *supply*. Whatever the ETF era did to US demand
   dynamics, it did not leave the Coinbase spot premium where it was — the
   marginal US institutional buyer now transacts in the ETF wrapper and in the
   creation/redemption basket, not by lifting Coinbase spot offers.

BTC and ETH premiums move together almost identically (means within 0.3 bp every
year), which is itself worth knowing: the two are not independent evidence about
*the premium*, only about *the trading rule applied to it*.

### 4.2 The z-score / forward-return relationship IS monotone — no red flag

`results/z_decile_table.csv`, `results/z_bucket_table.csv`,
`results/z_decile_by_era.csv`, `results/z_bucket_by_era.csv`,
`results/correlation.json`.

The pre-registration flagged in advance that "a signal that only works at the +2
cut but has no monotone relationship is a red flag." **That red flag is absent.**
Forward 48h return by z-decile, mean bp, no threshold, no stop, no cost:

| Decile | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | -28.9 | -13.3 | -5.9 | +20.0 | +31.6 | +43.2 | +44.8 | +52.0 | +59.4 | **+73.6** |
| ETH | -19.9 | -35.7 | -1.9 | +18.5 | +55.6 | +65.4 | +70.2 | +63.8 | +84.4 | **+90.9** |

Near-monotone in both, and the coarse buckets extend it past the trading cut:
BTC z in [2,3) -> +76 bp, z >= 3 -> **+264 bp**; ETH z in [2,3) -> +115 bp, z >= 3 ->
**+278 bp**. Correlations: BTC pearson **+0.072** / spearman +0.056 (n = 58,225
overlapping; +0.126 pearson on 1,214 non-overlapping bars); ETH pearson **+0.065**
/ spearman +0.060 (+0.063 non-overlapping). Small, but the sign is consistent
across assets, across eras, and across the whole z range.

So the honest statement is: **the premium z-score carries weak, monotone,
genuinely present forward information — and the frozen trading rule still loses
money in the modern era.** Those are compatible, for two measurable reasons.

**Reason 1 — the conditional excess over drift has collapsed.**
`results/descriptive_addendum.json`. The decile table sits on top of a bull-market
drift, so the number that matters is the *excess* of the z >= 2 bucket over the
unconditional forward return:

| | BTC pre-ETF | BTC post-ETF | ETH pre-ETF | ETH post-ETF |
|---|---|---|---|---|
| Unconditional fwd48h | +34.7 bp | +17.0 bp | +55.4 bp | +4.5 bp |
| fwd48h when z >= 2 | +167.3 bp | +44.3 bp | +187.1 bp | +68.4 bp |
| **Excess** | **+132.6 bp** | **+27.3 bp** | **+131.7 bp** | **+63.9 bp** |
| n fires (bars) | 1,135 | 860 | 1,316 | 726 |

The edge over drift fell ~5x on BTC and ~2x on ETH. Post-ETF BTC's +27 bp excess
does not survive an 18 bp round trip with anything left over worth trading.

**Reason 2 — the frozen 2xATR stop is inside the noise.** Same fires, same 48h
window, same 18 bp, stop removed entirely:

| | mean R with stop | mean R no stop | stop hit rate |
|---|---|---|---|
| BTC | +0.155 | **+0.397** | 57.9% |
| ETH | +0.219 | **+0.353** | 57.4% |

A 2xATR14 stop over a 48-hour hold is hit by a coin-flip majority of trades and
costs roughly half the gross expectancy. **This is an observation, not a
recommendation.** "Remove the stop" is an unregistered, post-hoc parameter change
on a rule that has already been through a 12-cell selection in the predecessor
repo; it would need its own pre-registration and its own out-of-sample asset
before anyone should believe it. It is recorded here only because it explains the
gap between "the signal has information" and "the rule loses."

---

## 5. Multiple-testing accounting

BTC is a carry-over. The exact cell `(thr = 2.0, hold = 48h, lookback = 336h)`
was the **training-set argmax of a 12-cell grid** already run on BTC in
`C:/Source/Repos/trader/probes/diagnostic_coinbase_premium_zscore.py` (2026-04-21),
whose declared shadow hypothesis is word-for-word this rule. Deflating BTC
accordingly:

| n_trials | 1 | 12 (primary) | 24 |
|---|---|---|---|
| BTC DSR | 0.906 | **0.302** | 0.194 |

At the honest trial count BTC's full-sample result does not clear any reasonable
bar, before even reaching C2. ETH's DSR of 0.981 at n_trials = 1 is the one
number in this study that argues *for* the rule — and section 3 shows it is
entirely sourced from 2020-2023.

**PBO is not reported and is not applicable.** CSCV requires a matrix of
competing configurations; this study ran exactly one configuration per asset with
no sweep, so there is nothing to cross-validate combinatorially. The
multiple-testing correction is carried by the DSR at n_trials = 12.

**Predecessor reproduction check.** Our BTC per-year mean net returns
(+0.67%, +0.08%, +0.06%, +0.67%, +0.12%, **-0.28%**, **-0.44%** for 2020...2026)
track the predecessor's own published series for the same cell (+0.46%, +1.33%,
+0.13%, +0.73%, +0.48%, **-0.10%**, **-0.17%**) in sign every year except 2021,
and reproduce its 2025-2026 decay. The 2021 sign flip and the level differences
are expected: we use a *level* trigger with a 48-bar cooldown where the
predecessor used a *cross* trigger with a 4-bar cooldown (302 fires vs their 213
over a longer window), and 18 bp of cost against their 10 bp. The reimplementation
is faithful enough that the decay is a property of the data, not of our code.

---

## 6. Sensitivities (reported, never used to choose anything)

`results/sensitivities.json`. All computed after the primary numbers were fixed.

| Variant | BTC n / mean R | ETH n / mean R |
|---|---|---|
| **Primary (frozen rule)** | 302 / **+0.1549** | 312 / **+0.2187** |
| Drop bars with abs(premium) > 2% (predecessor's filter) | 302 / +0.1549 | 312 / +0.2187 |
| Entry at next bar's open | 302 / +0.1642 | 312 / +0.2514 |
| Zero cost | 302 / +0.2787 | 312 / +0.3124 |

The extreme-premium filter changes nothing: only 8 bars per asset out of ~58,600
exceed abs 2%, and none of them is a fire. Execution at the next open is
marginally *better*, so the verdict is not an artefact of the close-entry choice.
Cost matters a lot in R terms (18 bp against a 1.87%/2.47% 1R is ~0.09/0.07 R per
trade) but does not rescue the modern era.

---

## 7. Deviations from the pre-registration

| # | Deviation | Status |
|---|---|---|
| D1 | **The Binance ETH spot leg is not `cd_spot_binance`.** That table is BTC-only — no `asset` column, one series, no ETH row. ETH's Binance spot leg is built from `eth_1m` (Binance ETHUSDT **spot** 1-minute klines) aggregated to hourly: open = open of the `:00` minute, high = max, low = min, close = close of the `:59` minute. Same aggregation production already uses (`data/loaders.py`). Spot-against-spot preserved; the perp (`cd_futures_eth_15m`) was **not** used. | **Declared in the pre-registration (section 3) before running.** Not a post-hoc change. |
| D2 | The final still-forming bar is dropped per asset. BTC's joined series ended 17:00, ETH's at 16:00, so ETH loses a complete 15:00->16:00 bar that BTC keeps. | Immaterial: no completed 48-hour trade can start in the final 48 bars anyway. |
| D3 | Windows and holds are **index-based**, not calendar-based, matching the predecessor. Across the 16 known Coinbase outage hours (6 windows, registered in `data/known_unfillable.json`), 4 BTC and 5 ETH trades out of ~305 span 49-51 calendar hours instead of exactly 48. | Recorded; 1.5% of trades, no material effect. |
| D4 | PBO not computed. | Undefined for a single-configuration study — see section 5. Not a clause. |

**No clause was changed, relaxed, or added after seeing a result.** The 4.2
era-split tables, the no-stop comparison and the calendar-cut trade tables live in
a separate script, `descriptive_addendum.py`, written and run **after**
`premium_study.py` had already produced and written every decision number, and
they are explicitly non-decision-bearing.

---

## 8. What could still be wrong

- **The KILL is of *this rule*, not of the Coinbase premium as information.**
  Section 4.2 shows a monotone, cross-asset-consistent, cross-era-persistent
  relationship between the premium z and forward 48h returns. What dies here is
  the specific construction: a +2 sigma level trigger, a 48-hour hold and a
  2xATR14 stop that half the trades hit. A different construction is not tested
  and is not endorsed by this study.
- **Stop fills are optimistic.** A stop is filled at exactly the stop price with
  no gap-through and no slippage beyond the 18 bp. Real fills would be worse.
  This flatters the result, so it can only strengthen a KILL — but it means the
  positive full-sample numbers in section 2 are an upper bound, not an estimate.
- **Overlapping-window correlations understate their own standard error.** The
  n = 58,225 pearson of +0.072 is computed on 48-hour-overlapping forward
  returns, so the effective sample is ~1,200, not 58,000. The non-overlapping
  figures (+0.126 BTC, +0.063 ETH on ~1,214 bars) are the honest ones, and they
  disagree with each other by 2x — the correlation's magnitude is not well
  determined, only its sign.
- **ETH is out-of-sample for the *parameters*, not for the *era*.** Nothing about
  ETH informed the thresholds, which is what makes C1 meaningful. But ETH's
  premium is ~identical to BTC's every year (section 4.1), so the two assets
  share the same regime shift. ETH is one genuine test, not two.
- **The 2026 sample is 8 months** (25 BTC trades, 30 ETH trades). The 2026
  numbers are directional, not conclusive on their own; the case for decay rests
  on 2024-2026 combined (BTC n = 109 mean R -0.113, ETH n = 114 mean R -0.047).
- **Coinbase data provenance.** `coinbase_spot_1h` was shipped the same day this
  study ran (feed D4, 2026-09-08). Verified independently here: 117,220 rows,
  BTC 58,610 + ETH 58,610, 2020-01-01 -> 2026-09-08 17:00 UTC, zero NULL closes,
  zero off-hour timestamps, 16 missing hours in 6 registered venue-side outage
  windows, and 100% join coverage against `cd_spot_binance` on BTC / 99.94%
  against `eth_1m` on ETH. Rows before 2026-04-14 are seeded from the predecessor
  repo's `trader.db` and were not re-verified bar-by-bar against the live
  Coinbase API in this study.

---

## 9. Recommendation

**Do not build.** Recommendation only — the user decides. No sleeve, bot or
production file was created or modified.

If the premium is revisited, the case to pre-register would be the one this study
accidentally isolated and did **not** test: the *excess over drift* at high z is
still +27 bp (BTC) / +64 bp (ETH) post-ETF against an 18 bp cost, and the 2xATR
stop destroys roughly half the gross expectancy. A rule with no stop, or a stop
far outside the 48-hour noise band, is a different hypothesis and needs a new
untouched asset to be tested on — BTC and ETH are now both spent.

---

### Files

- `README.md` — pre-registration (written first, unedited)
- `premium_study.py` — primary analysis, produces every decision number
- `descriptive_addendum.py` — post-primary descriptives, non-decision-bearing
- `build_notebook.py` -> `coinbase_premium.ipynb` — viewer
- `results/clauses.json` — the clause table as computed
- `results/headline.json`, `results/trades_{BTC,ETH}.csv`, `results/trades_by_year.csv`
- `results/premium_by_year.csv`, `results/era_split.json`, `results/correlation.json`
- `results/z_decile_table.csv`, `results/z_bucket_table.csv`,
  `results/z_decile_by_era.csv`, `results/z_bucket_by_era.csv`
- `results/sensitivities.json`, `results/provenance.json`, `results/descriptive_addendum.json`
