STATUS: CONCLUDED KILL per pre-registration

The frozen Coinbase-premium rule -- 14-day z-score of the hourly
Coinbase-minus-Binance **spot** premium, fire at z >= +2.0, long 48h, stop at
2xATR14, 18 bp round trip, one fire per 48h -- fires pre-registered KILL clause
**C2** decisively. C1 does not fire in this run, but §3 shows why that must not
be leaned on and why it changes nothing.

Run 2026-09-09 (**Run B**). Every number below traces to a file in `results/`
produced by `analysis.py`. Pre-registration: `README.md`.

> **Read §2 first if you read nothing else.** This study folder already contained
> a complete, independent run of the same frozen rule from 2026-09-08 (**Run A**).
> I did not know that when I started, and my first write destroyed Run A's
> pre-registration file. Full disclosure and reconciliation in §2.

---

## 1. Clause table

| ID | Clause (as frozen in the pre-registration) | Measured (Run B) | Fired? |
|---|---|---|---|
| **C1** | **ETH** 95% two-sided percentile bootstrap CI on mean R **includes zero or lies entirely below zero** (`ci95_lo <= 0`) | n = **310**, mean R **+0.2524**, CI95 **[+0.0157, +0.5051]** -> `ci95_lo = +0.0157 > 0` | **NO** |
| **C2** | **BTC** entries on/after 2025-01-01: **n >= 20** AND **mean R <= 0** | n = **68**, mean R **-0.3191**, total **-21.70 R** | **YES -> KILL** |
| *support* | ETH deflated Sharpe at N_TRIALS = 1 must exceed 0.95 for a BUILD verdict | DSR = **0.9898** (0.9567 at the conservative N = 2) | passes |

**Verdict: KILL.** C2 fires; per the pre-registration, either clause firing kills
it. Source: `results/report.json` -> `clauses`.

Bootstrap spec as frozen: iid percentile bootstrap of the mean R, 10,000
resamples, seed 42, 95% two-sided primary. ETH's 90% interval is
[+0.0532, +0.4629].

---

## 2. Disclosure: a duplicate run, and a file I destroyed

`studies/notebooks/coinbase_premium/` already existed, created 2026-09-08
20:18-20:26, containing a finished study of this identical frozen rule
(`premium_study.py`, `descriptive_addendum.py`, `findings.md`,
`build_notebook.py`, `results/*`). I was told to create the folder and did not
check for prior contents first.

**What I destroyed.** My write of `README.md` overwrote Run A's
pre-registration. The folder is untracked in git
(`?? studies/notebooks/coinbase_premium/`), so there is no committed copy and
**it is not recoverable**. This is a real loss: it was the file that proved Run
A's clauses were frozen before its numbers existed. The best surviving evidence
of its content is Run A's own `findings.md`, which quotes its clauses verbatim --
and those clauses (C1, C2, plus an extra evaluability clause C0) are
substantively identical to the ones I was given and pre-registered.

**What I preserved.** Before writing anything else I copied Run A's outputs to
dated names, and left its scripts untouched:

| Run A file | status |
|---|---|
| `README.md` | **DESTROYED** by my write. Not recoverable. |
| `findings.md` | preserved as **`findings_run_A_2026_09_08.md`** |
| `build_notebook.py` | preserved as **`build_notebook_run_A_2026_09_08.py`** |
| `coinbase_premium.ipynb` | preserved as **`coinbase_premium_run_A_2026_09_08.ipynb`** |
| `premium_study.py`, `descriptive_addendum.py` | untouched (no name collision) |
| `results/*.json`, `results/trades_{BTC,ETH}.csv`, `z_*`, `sensitivities`, `provenance` | untouched (no name collision) |

Run A's numbers below are read from those preserved files; everything else is
Run B's.

### 2.1 Reconciliation -- Run A vs Run B

Two fully independent implementations of the same frozen rule, written without
sight of each other.

| | Run A (2026-09-08) | Run B (2026-09-09) | agree? |
|---|---|---|---|
| BTC fires | 302 | 302 | **identical** |
| BTC mean R | +0.1549 | +0.1549 | **identical** |
| BTC CI95 | [-0.083, +0.416] | [-0.0829, +0.4164] | **identical** |
| BTC 2025+ | n = 68, mean R **-0.3191** | n = 68, mean R **-0.3191** | **identical** |
| BTC DSR (N = 24) | 0.194 | 0.1935 | identical |
| ETH bars used | 58,571 | 58,579 | **differ by 8** |
| ETH fires | 312 | 310 | differ by 2 |
| ETH mean R | +0.2187 | +0.2524 | differ |
| ETH CI95 | **[-0.0052, +0.4587]** | **[+0.0157, +0.5051]** | **differ in sign of `ci_lo`** |
| **C1** | **FIRES** | does not fire | **flips** |
| **C2** | **FIRES** | **FIRES** | **identical** |
| **Verdict** | **KILL** | **KILL** | **identical** |

**Cause of the ETH difference, exactly.** Both runs build the Binance ETH spot
leg from `eth_1m` (there is no ETH counterpart to `cd_spot_binance` -- see §7
D1). They aggregate to hourly slightly differently: Run A requires the hour's
`:59` minute bar to exist for the close and drops the final forming bar
(7 + 1 = 8 bars dropped, 58,571 kept, per its `results/provenance.json`); Run B
keeps any hour holding at least 1 minute bar (58,579 kept). **8 bars out of
58,579 -- 0.014% of the panel.**

That 0.014% moves ETH's `ci95_lo` from -0.0052 to +0.0157 and flips clause C1.

**This is the single most important methodological result in the folder.** C1 is
not a measurement; it is a coin toss decided by an arbitrary, immaterial
data-hygiene choice that neither pre-registration thought worth specifying.
Anyone who quotes "ETH's CI excludes zero" (Run B) or "ETH's CI includes zero"
(Run A) as evidence is quoting noise. The verdict does not depend on it: **C2 is
bit-for-bit identical across two independent implementations and fires with room
to spare.**

---

## 3. Headline numbers (Run B)

Hourly panel 2020-01-01 00:00 -> 2026-09-08 17:00 UTC. Sources:
`results/report.json`, `results/trades.csv`, `results/per_year.csv`,
`results/trade_era_split.csv`.

| | BTC (carry-over, contaminated) | ETH (**genuine OOS**) |
|---|---|---|
| Panel bars | 58,610 | 58,579 |
| Fires | 302 | 310 |
| Trades / year | 45.7 | 46.9 |
| Mean R | +0.1549 | +0.2524 |
| Total R | +46.77 | +78.25 |
| Max drawdown | 30.83 R | 23.76 R |
| Win rate | 36.4% | 39.7% |
| Stopped out | 57.6% | 56.8% |
| Profit factor | 1.23 | 1.40 |
| Mean net return / trade | +0.178% | +0.498% |
| Per-trade Sharpe (CI90) | +0.0688 [-0.026, +0.149] | +0.1134 [+0.025, +0.189] |
| Bootstrap mean R, CI95 | [-0.0829, +0.4164] | [+0.0157, +0.5051] |
| P(mean R > 0) | 0.889 | 0.982 |
| Skew / kurtosis of R | 2.73 / 12.4 | 2.62 / 12.5 |
| **DSR at its honest N_TRIALS** | **0.194** (N = 24) | **0.990** (N = 1) |

BTC's full-sample result does not survive its own trial count. At the
predecessor's declared budget of 24 configurations the deflated Sharpe is
**0.194** -- the observed per-observation Sharpe (0.0688) is *below* the expected
maximum from 24 draws of pure noise (0.1141). BTC contributes no evidence for the
rule even before C2.

### 3.1 The edge is entirely pre-2024, on both assets

`results/trade_era_split.csv`, `results/per_year.csv`. Split at the BTC spot-ETF
approval, 2024-01-11, via `studies.lib.validation.metrics.era_split`.

| per-trade R | BTC n | BTC mean R | BTC CI95 | ETH n | ETH mean R | ETH CI95 |
|---|---|---|---|---|---|---|
| Pre-ETF (< 2024-01-11) | 195 | **+0.2919** | [-0.026, +0.639] | 198 | **+0.4175** | [+0.096, +0.770] |
| Post-ETF (>= 2024-01-11) | 107 | **-0.0948** | [-0.432, +0.316] | 112 | **-0.0394** | [-0.345, +0.294] |
| 2025 onward | 68 | **-0.3191** | [-0.622, +0.038] | 75 | **-0.0414** | [-0.407, +0.371] |

Mean R by calendar year:

| | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| BTC | +0.599 (49) | -0.096 (47) | +0.160 (50) | +0.559 (47) | +0.228 (41) | **-0.267** (43) | **-0.408** (25) |
| ETH | +0.269 (50) | +0.416 (50) | +0.349 (56) | **+0.744** (40) | -0.057 (39) | **-0.122** (45) | +0.080 (30) |

**This is the finding.** ETH -- the asset that was never fitted, on which no
threshold was ever chosen -- is positive in 2020-2023 and dead from 2024. It dies
in the same year BTC dies. The rule's survival of C1 in Run B is a full-sample
artefact of a regime that ended two years ago; ETH's post-ETF `P(mean R > 0)` is
**0.396**, i.e. indistinguishable from a coin flip. Run A's C1 firing and Run B's
C1 not firing are two readings of the same dead signal.

---

## 4. Descriptive results (requested)

### 4.1 Premium mean and sd by year -- it has decayed twice over

`results/premium_by_year.csv`, `results/premium_era_decay.csv`. Basis points of
the Binance price.

| Year | BTC mean | BTC sd | ETH mean | ETH sd | BTC % hours positive |
|---|---|---|---|---|---|
| 2020 | +4.58 | 16.55 | +4.46 | 17.10 | 69.2% |
| 2021 | +5.56 | 8.74 | +5.55 | 8.80 | 85.4% |
| 2022 | -1.26 | 8.77 | -1.39 | 8.73 | 58.2% |
| 2023 | +2.62 | 10.40 | +2.29 | 10.43 | 69.2% |
| 2024 | -0.38 | 6.73 | -0.70 | 6.45 | 48.5% |
| 2025 | +0.32 | 4.72 | +0.32 | 4.68 | 59.0% |
| 2026 (to 09-08) | **-6.03** | 6.08 | **-5.84** | 5.78 | **17.8%** |

**Yes, it decayed -- in two independent ways, and the ETF era is where both
bite.**

1. **Dispersion collapse.** Standard deviation fell from ~16.6 bp (2020) to
   4.7 bp (2025), a **3.5x compression**; era-wise, 11.83 bp pre-ETF ->
   **6.42 bp** post (BTC). Cross-venue arbitrage has closed the gap. This is
   mechanically fatal for a *z-score* rule: a z-score is scale-free, so the rule
   keeps firing at a near-constant rate (BTC 3.22% of bars pre-ETF, 3.69% post)
   on events that are half the size in real terms, while the 18 bp cost does not
   shrink with them.
2. **Level flip.** Mean premium +2.90 bp pre-ETF -> **-1.64 bp** post (BTC;
   ETH +2.75 -> -1.71), and in 2026 **-6.0 bp with only 17.8% of hours
   positive**. The "US demand proxy" now reads as a persistent US *discount*.
   The mechanism is the obvious one: the marginal US institutional buyer
   post-January-2024 transacts through the ETF creation/redemption basket and
   Coinbase Prime/OTC, none of which lifts offers on the public Coinbase order
   book that this premium measures.

BTC and ETH premiums track each other to within 0.3 bp every single year. They
are **not two independent observations of the premium** -- only two applications
of one trading rule to what is essentially one series.

### 4.2 The monotonicity check -- the red flag is ABSENT

`results/z_decile_forward48h.csv`, `results/monotonicity.csv`. Forward 48h return
by z-decile, every bar, no threshold, no stop, no cost, mean bp:

| Decile | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | -28.9 | -13.3 | -6.0 | +20.0 | +31.6 | +43.2 | +44.8 | +52.0 | +59.4 | **+73.6** |
| ETH | -25.6 | -37.1 | +5.3 | +19.3 | +53.9 | +67.2 | +71.4 | +62.8 | **+88.7** | +85.3 |

Correlations of z with forward 48h return, no threshold:

| | Pearson | Spearman | Pearson (non-overlapping) | Spearman (non-overlapping) |
|---|---|---|---|---|
| BTC | +0.0722 (n = 58,226) | +0.0557 | **+0.1261** (n = 1,214) | +0.0807 |
| ETH | +0.0639 (n = 58,195) | +0.0605 | **+0.0700** (n = 1,213) | +0.0653 |

The pre-registration flagged in advance that a signal working only at the +2 cut
with no monotone relationship would be a red flag. **That red flag does not
appear.** The relationship is monotone across essentially the whole z range on
BTC (one inversion at decile 3) and on ETH (one inversion at decile 2), positive
in sign on both assets, and it holds on the honest non-overlapping sample. So the
premium z-score **does** carry weak, real, forward-looking information.

That makes the KILL a *sharper* result, not a softer one: the information is
present and the frozen rule still cannot monetise it in the modern era. Two
measured reasons:

**Reason 1 -- the excess over drift collapsed post-ETF.** `results/report.json`
-> `monotonicity`. The decile table sits on a bull-market drift, so the number
that matters is the z >= 2 bucket's *excess* over the unconditional forward
return:

| | BTC pre-ETF | BTC post-ETF | ETH pre-ETF | ETH post-ETF |
|---|---|---|---|---|
| Unconditional fwd 48h | +34.7 bp | +17.0 bp | +57.7 bp | +11.3 bp |
| fwd 48h when z >= 2 | +167.3 bp | +44.3 bp | +143.6 bp | +76.9 bp |
| **Excess** | **+132.6 bp** | **+27.3 bp** | **+86.0 bp** | **+65.6 bp** |
| fire bars | 1,135 | 860 | 1,122 | 894 |

BTC's edge over drift fell **4.9x**, to +27 bp gross against an 18 bp round trip
-- nothing left worth trading. ETH's fell less (-24%) but its trades still lost
money post-ETF, which points to Reason 2.

**Reason 2 -- the 2xATR stop sits inside the 48-hour noise band.**
`results/report.json` -> `diag_nostop`. Same fires, same 48h window, same 18 bp,
stop removed:

| | mean R with stop | mean R, stop removed | stop hit rate | win rate with / without |
|---|---|---|---|---|
| BTC | +0.155 | **+0.397** | 57.6% | 36.4% / 51.7% |
| ETH | +0.252 | **+0.404** | 56.8% | 39.7% / 58.1% |

A 2xATR14 stop over a 48-hour hold is hit by a majority of trades and costs
roughly **60% of gross expectancy**. **This is an observation, not a
recommendation, and it is explicitly not a decision input** -- it is flagged as
such in the code (`diag_nostop`, "NOT the pre-registered rule"). "Remove the
stop" is an unregistered post-hoc parameter change to a rule that has already
been through a 24-cell selection in the predecessor repo; it would need its own
pre-registration and its own untouched asset. It is recorded only because it
explains the gap between "the signal has information" and "the rule loses
money".

---

## 5. Multiple-testing accounting

BTC is a carry-over, and the contamination is documentable rather than suspected.
The exact cell `(thr = 2.0, hold = 48h, lookback = 336h)` is the **training-set
argmax** of the trend hypothesis in
`C:/Source/Repos/trader/probes/diagnostic_coinbase_premium_zscore.py`
(2026-04-21), whose recorded output
(`trader/data/experiments/coinbase_premium_zscore/results_20260421_140646.json`)
names `winner_cfg = [2.0, 48, 336]`. That probe declared a budget of 24
configurations (2 hypotheses x 3 thresholds x 2 holds x 2 lookbacks).

| leg | N_TRIALS | DSR |
|---|---|---|
| BTC | 24 (predecessor's declared budget) | **0.194** |
| ETH | 1 (never tested on this rule anywhere) | **0.990** |
| ETH | 2 (conservative: this study ran the rule on 2 assets) | 0.957 |

ETH's 0.990 is the only number in this study that argues *for* the rule, and
§3.1 shows it is sourced entirely from 2020-2023.

**PBO is not reported.** CSCV needs a matrix of competing configurations; this
study ran exactly one configuration per asset with no sweep, so there is nothing
to cross-validate combinatorially. Multiple-testing is carried by the DSR.

**Predecessor cross-check.** Run B's BTC per-year mean net returns
(+0.67%, +0.08%, +0.06%, +0.67%, +0.12%, **-0.28%**, **-0.44%** for 2020...2026)
match the predecessor's published series for the same cell
(+0.46%, +1.33%, +0.13%, +0.73%, +0.48%, **-0.10%**, **-0.17%**) in sign every
year except 2021, and reproduce its 2025-2026 decay. Level differences are
expected: this study uses a *level* trigger with a 48-bar cooldown where the
predecessor used a *cross* trigger with a 4-bar cooldown (302 fires vs its 213),
and 18 bp of cost against its 10 bp. The decay is a property of the data, not of
the reimplementation.

---

## 6. Verdict logic

Per the pre-registration: either clause firing -> KILL.

* **C2 fires** (BTC 2025+, n = 68 >= 20, mean R = -0.3191 <= 0). -> **KILL.**
* C1 does not fire in Run B, fires in Run A, and §2.1 shows the difference is 8
  bars out of 58,579. C1 is not informative in either direction.
* Even setting both clauses aside, the BUILD branch was unreachable: it required
  ETH's DSR *and* the absence of both KILL clauses, and §3.1 shows ETH's
  post-ETF mean R is -0.0394 with P(mean R > 0) = 0.396.

---

## 7. Deviations from the pre-registration

| # | Deviation | Status |
|---|---|---|
| **D1** | **The Binance ETH spot leg is not `cd_spot_binance`.** That table is BTC-only -- a single series, no `asset` column, no ETH row. ETH's Binance spot leg is `eth_1m` (Binance ETHUSDT **spot** 1-minute klines, per `data/sources/binance.py` line 5) aggregated to hourly. Spot-against-spot is preserved; the perps (`cd_futures_eth_15m`, `bybit_perp_eth_1h`, `okx_perp_eth_1h`) were refused. | **Declared in the pre-registration §3 before any number was computed.** Not a post-hoc change. |
| **D2** | Hours are kept if they hold at least 1 minute bar (14 of 58,579 ETH hours hold fewer than 60). Run A instead required a `:59` close. | Pre-registered in §3. §2.1 shows this 8-bar choice flips C1 -- recorded as the study's main methodological caveat, not hidden. |
| **D3** | Holds and the cooldown are **index-based** (48 bars), not calendar-based, matching the predecessor. Across the 16 registered Coinbase outage hours a handful of trades span 49-51 calendar hours. | Immaterial: ~1.5% of trades. |
| **D4** | PBO not computed. | Undefined for a single-configuration study (§5). Not a clause. |
| **D5** | An era-split key bug (`metrics.era_split` returns `pre_etf`/`post_etf`, not `pre`/`post`) made the first execution report n = 0 for both eras. Fixed and re-run before any era number was quoted. | Mechanical bug in my code, not a rule change. No clause number was affected (C1 and C2 do not use `era_split`). |
| **D6** | `README.md` carries an appendix added **after** the run, disclosing the destroyed Run A pre-registration (§2). | Provenance only. **No hypothesis, rule, threshold, clause, trial count or prior was altered after seeing a result.** |

---

## 8. What could still be wrong

- **This kills *this rule*, not the Coinbase premium as information.** §4.2
  shows a monotone, cross-asset-consistent relationship between premium z and
  forward 48h returns that survives into the post-ETF era at +27 bp (BTC) /
  +66 bp (ETH) excess over drift. What dies is the specific construction: a
  +2 sigma level trigger, a 48-hour hold, and a 2xATR14 stop that a majority of
  trades hit.
- **C1 is not reliable evidence, in either direction.** §2.1. Any future study
  of this premium must pre-register the hourly-bar construction explicitly.
- **Stop fills are optimistic.** Stops fill at exactly the stop price -- no
  gapping, no slippage beyond the 18 bp. Real fills are worse, so the positive
  full-sample numbers in §3 are an **upper bound**. This can only strengthen a
  KILL.
- **Overlapping windows understate their own standard error.** The n = 58,226
  correlation uses 48h-overlapping forward returns; effective n is ~1,200. The
  non-overlapping figures (+0.126 BTC, +0.070 ETH) differ from each other by
  1.8x, so the correlation's *magnitude* is not well determined -- only its sign.
- **ETH is out-of-sample for the parameters, not for the era.** Nothing about ETH
  informed any threshold, which is what makes it a real test. But §4.1 shows
  ETH's premium is near-identical to BTC's every year, so both assets share one
  regime shift. This is **one** genuine test, not two.
- **The quote-currency confound is uncorrected.** Coinbase quotes USD, Binance
  quotes USDT, so the "premium" contains the USDT/USD basis. A stablecoin
  dislocation registers as a premium spike with zero US-demand content.
  Pre-registered as a known limitation, not adjusted for.
- **2026 is 8 months** (25 BTC / 30 ETH trades) -- directional only. The decay
  case rests on 2024-2026 combined (BTC n = 107, mean R -0.095; ETH n = 112,
  -0.039).
- **Coinbase data provenance.** `coinbase_spot_1h` shipped 2026-09-08 (feed D4).
  Rows before 2026-04-14 are seeded from the predecessor's `trader.db` and were
  **not** re-verified bar-by-bar against the live Coinbase API here.
  Independently verified in this study: 117,220 rows, BTC 58,610 + ETH 58,610,
  2020-01-01 -> 2026-09-08 17:00 UTC, spans and counts exactly as the feed agent
  claimed, 100% join coverage against `cd_spot_binance` on BTC and 99.95%
  against `eth_1m` on ETH.

---

## 9. Recommendation

**Do not build.** Recommendation only -- the user decides. No sleeve, bot, or
production file was created or modified; nothing under `strategies/**` or
`bots/**` was touched, and `prod.db` was opened read-only throughout.

If the premium is ever revisited, the hypothesis this study isolated but did
**not** test is: the z >= 2 excess over drift is still positive post-ETF (+27 bp
BTC / +66 bp ETH) and the 2xATR stop destroys ~60% of gross expectancy. A
stop-free or far-wider-stop variant is a *different hypothesis* that needs its
own pre-registration -- and BTC and ETH are now both spent as test assets for it.

---

### Files

Run B (this run):
- `README.md` -- pre-registration (+ §10 appendix disclosing the Run A loss)
- `analysis.py` -- the whole study; produces every number above
- `build_notebook.py` -> `coinbase_premium.ipynb` -- viewer
- `results/report.json` -- clauses, per-leg stats, era splits, monotonicity,
  diagnostics
- `results/trades.csv`, `results/equity_curve.csv`, `results/per_year.csv`,
  `results/trade_era_split.csv`
- `results/premium_by_year.csv`, `results/premium_era_decay.csv`,
  `results/z_decile_forward48h.csv`, `results/monotonicity.csv`

Run A (2026-09-08, preserved):
- `findings_run_A_2026_09_08.md`, `build_notebook_run_A_2026_09_08.py`,
  `coinbase_premium_run_A_2026_09_08.ipynb`
- `premium_study.py`, `descriptive_addendum.py`
- `results/clauses.json`, `results/headline.json`, `results/era_split.json`,
  `results/correlation.json`, `results/sensitivities.json`,
  `results/provenance.json`, `results/descriptive_addendum.json`,
  `results/trades_{BTC,ETH}.csv`, `results/trades_by_year.csv`,
  `results/z_decile_table.csv`, `results/z_bucket_table.csv`,
  `results/z_decile_by_era.csv`, `results/z_bucket_by_era.csv`
- `README.md` -- **destroyed, not recoverable** (§2)
