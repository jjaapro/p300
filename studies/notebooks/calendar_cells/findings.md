STATUS: CONCLUDED KILL (all five cells) per pre-registration

Run: 2026-09-08. `python studies/notebooks/calendar_cells/run_cells.py`, read-only on
`data/databases/prod.db`. Cost 18 bp round trip (research convention). N_TRIALS = 5.
Era split 2024-01-11. Every number below comes from `results/`.

---

## 1. The five-row clause table

Decision clauses, all three required to pass: **C1** `t_pre >= 2.5`, **C2** `t_post >= 2.5`,
**C3** `DSR(n_trials=5) >= 0.95`. Per-trade net returns throughout.

| Cell | n | mean/trade net | t (full) | Sharpe/trade | **C1** t_pre | **C2** t_post | **C3** DSR | Verdict |
|---|---:|---:|---:|---:|---|---|---|---|
| `QTR_END` | 26 | **+0.863%** | +0.87 | 0.171 | +0.94 (n=16) FAIL | +0.09 (n=10) FAIL | 0.360 FAIL | **KILL** |
| `NFP_INCREMENTAL` (14:00-16:00) | 81 | -0.192% | -1.37 | -0.152 | -0.54 (n=49) FAIL | -1.49 (n=32) FAIL | 0.009 FAIL | **KILL** |
| `WKND_FADE` | 349 | -0.590% | **-2.92** | -0.156 | -1.93 (n=210) FAIL | -2.59 (n=139) FAIL | 0.000028 FAIL | **KILL** |
| `HIGH52` | 38 | **+1.039%** | +0.80 | 0.130 | -0.09 (n=20) FAIL | +1.49 (n=18) FAIL | 0.336 FAIL | **KILL** |
| `EMA_ETH` | 11 | **+7.069%** | +0.85 | 0.256 | +0.90 (n=6) FAIL | +0.18 (n=5) FAIL | 0.352 FAIL | **KILL** |

**0 of 15 clauses fired. 0 of 5 cells pass.** No cell came close: the best DSR was 0.360
against a 0.95 bar, and no era t-statistic anywhere in the table exceeded +1.49 against a
+2.5 bar.

Context rows (pre-registered as **not** decision-bearing):

| Row | n | mean net | mean gross | t | DSR |
|---|---:|---:|---:|---:|---:|
| `NFP_STANDALONE` 12:30-16:00 | 81 | -0.112% | +0.069% | -0.63 | 0.036 |
| `NFP_R4_COVERED` 12:30-14:00 *(post-hoc decomposition, see section 5)* | 81 | -0.098% | +0.082% | -0.80 | 0.025 |

Supporting statistics (pre-registered as not decision-bearing): bootstrap 90% CI on
per-trade Sharpe, 10,000 iterations, seed 42 -
`QTR_END` [-0.163, +0.474], `NFP_INCREMENTAL` [-0.367, +0.026],
`WKND_FADE` [-0.249, -0.067], `HIGH52` [-0.149, +0.370], `EMA_ETH` [-0.243, +0.875].
**Only `WKND_FADE` has a CI excluding zero, and it excludes it on the wrong side.**
CSCV PBO across the five cells on a common monthly net-return grid (68 months, s=10,
252 combinations): **0.476**.

Zero observations were skipped for missing bars in any cell (all `skipped` tallies 0), so
no result here is a data-availability artefact.

---

## 2. The NFP incremental question - which number is decision-bearing

Pre-registered: the decision-bearing NFP number is the **14:00-16:00 sub-window**, the
part the live R4 V2 Friday leg does not already own.

The script confirms the overlap claim mechanically, and it is total:

> 81 NFP dates in sample, **all 81 are Fridays**, **all 81 have day-of-month <= 14**
> => **every NFP date in the sample is already an R4 V2 Friday fire.**

So `JPLUS_R4_BTC_V2` / `JPLUS_R4_ETH_V2` (Fri 04:00 -> 14:00 UTC, dom <= 14) hold through
12:30-14:00 on 100% of NFP days. The 12:30-14:00 leg of the standalone NFP window is not
an available cell - buying it would be buying risk the fleet already carries. The only
purchasable increment is 14:00 -> 16:00.

**That incremental cell is dead, and it is dead before costs.** Its gross mean is
**-0.012% per fire** - twelve-thousandths of one percent, indistinguishable from zero -
over 81 events. Net of 18 bp it is -0.192%, t = -1.37, DSR = 0.009. There is no
post-release drift left in 14:00-16:00 to buy.

The standalone 12:30-16:00 window is also dead (gross +0.069%/fire, net -0.112%,
t = -0.63), which matters because it means the answer does not depend on which framing
you prefer: NFP day on BTC has no tradeable directional edge in either window, and the
"incremental vs standalone" distinction turns out to be moot rather than decisive.

**Pre-registered sensitivities, both confirming the KILL:**

* Dropping 2025-10-03 / 2025-11-07 / 2025-12-05 (the shutdown-mislabelled dates, 6.1):
  incremental n=78, mean -0.218%, t = -1.51, DSR = 0.007. Slightly *worse*.
* Counting the standalone window as a 6th trial: incremental DSR at `n_trials=6` = 0.007
  (from 0.009); standalone 0.029 (from 0.036). Stricter, same verdict.

---

## 3. The one well-powered result: the weekend gap does not fade - it mildly continues

`WKND_FADE` is the only cell in this study with enough observations to say something
positive, and what it says is that the pre-registered direction was **backwards**.

* n = 349 weekends, 2020-01-01 -> 2026-09-07.
* Net mean **-0.590%/weekend**, t = **-2.92**, DSR = 2.8e-5, win rate 41.3%, cumulative
  **-206%** over the sample.
* It is not a cost artefact: **gross mean is -0.410%/weekend**. 18 bp explains less than a
  third of the loss.
* Both eras agree on the sign (pre -0.573%, post -0.616%) and the post-ETF era is the more
  significant of the two (t = -2.59).
* It is the only cell whose required bar was actually reachable at its realised
  dispersion: it needed a per-trade Sharpe of 0.152 for DSR >= 0.95 and delivered -0.156.

So this is a **strong, well-powered KILL**, not a shrug. Fading the Fri-21:00 -> Sun-22:00
CME-closure move and holding to Mon 22:00 loses money reliably. Mechanically, weekend
moves in BTC mildly *persist* into Monday rather than reverting - the "CME gap always
fills" folk claim is, at this horizon and on this definition, false, and the popular
version of the trade is a systematic money-loser.

**Post-hoc, and explicitly not a finding of this study**: the mechanically-implied
opposite direction (weekend *continuation*) nets +0.230%/trade at t = **+1.14**, DSR
0.479 - because the 18 bp is paid whichever way you face, the flip is not a sign change.
Gross it is +0.410% at t ~ +2.03. **That is still below the +2.5 bar in either era and
would still be a KILL**, and in any case flipping a direction after seeing its sign is
precisely the error a pre-registration exists to prevent. Recorded in
`results/post_hoc_notes.json` with that warning attached, and generated uniformly for all
five cells rather than cherry-picked for the one with an interesting sign. It changes no
verdict. If anyone wants continuation, it needs its own pre-registered study with its own
trial count - and my read of the numbers above is that it would fail that study too.

---

## 4. The other three KILLs are low-power, and I want to be precise about that

`QTR_END`, `HIGH52` and `EMA_ETH` all have **positive point estimates** - +0.863%,
+1.039% and +7.069% net per trade respectively - and all three are killed by the frozen
rule. They are not the same kind of dead as `WKND_FADE`. The honest statement is **"not
demonstrated"**, not **"demonstrated absent"**.

The power columns (`results/cells_summary.csv`: `sr_needed_for_dsr95`,
`mean_needed_pre_for_t2p5`, `mean_needed_post_for_t2p5`) make the gap concrete:

| Cell | realised Sharpe/trade | Sharpe needed for DSR >= 0.95 | mean/trade needed for t >= 2.5, pre (got) | post (got) |
|---|---:|---:|---|---|
| `QTR_END` | 0.171 | 0.567 | 3.520% (+1.327%) | 3.254% (+0.120%) |
| `HIGH52` | 0.130 | 0.466 | 4.986% (-0.175%) | 3.999% (+2.388%) |
| `EMA_ETH` | 0.256 | 0.897 | 30.740% (+11.116%) | 29.904% (+2.213%) |
| `NFP_INCREMENTAL` | -0.152 | 0.317 | 0.448% (-0.097%) | 0.567% (-0.337%) |
| `WKND_FADE` | -0.156 | **0.152** | 0.742% (-0.573%) | 0.595% (-0.616%) |

`EMA_ETH` would have needed a **~31% mean return per trade in each era** to clear C1/C2.
That is not a bar any real trend follower clears; it is what n = 6 and n = 5 does to a
t-statistic. **The binding constraint on these three cells is the calendar, not the
data.** A turn-of-quarter cell generates 4 observations a year, a weekly 5/21 cross
generates ~1.7 closed trades a year, and no amount of additional analysis inside this
sample creates observations the calendar does not produce.

This is a real limitation of the pre-registered rule and I will not pretend otherwise:
**for these three cells the double-era t >= 2.5 test was close to unpassable before the
first price was read.** I considered whether that makes them INCONCLUSIVE rather than
KILL. Under the frozen rule it does not (README section 4: the fewer-than-3-observations
escape hatch is the only one, and every era here has >= 5). But a reader should treat
these three as *"the pre-registered evidence bar was not met, and this design could not
have met it"* rather than *"the effect was shown to be absent"*.

Cell-specific notes:

* **`QTR_END`** - 26 observations, 16 pre / 10 post. The point estimate is decent
  (+0.86%/72h, 61.5% win rate, +22.4% cumulative), and it is essentially all pre-ETF:
  +1.327%/trade pre versus **+0.120%/trade post**, a 91% decay. Whatever this was, the
  post-ETF era shows almost none of it. A turn-of-quarter effect that only exists before
  2024 is exactly what the ETF-era rebalancing story would *not* predict.
* **`HIGH52`** - 96 raw 364-day-closing-high signals, 58 suppressed by the non-overlap
  rule, 38 traded. The era pattern is the opposite of `QTR_END`: -0.175%/trade pre-ETF
  versus **+2.388%/trade post-ETF** (t = +1.49), 47.4% win rate, positive skew (0.91) - a
  few large winners, consistent with breakout continuation being a fat-tail effect rather
  than a reliable one. My prior said "right sign, wrong significance"; that is roughly
  what happened, though I did not predict the pre-ETF era being slightly negative.
* **`EMA_ETH`** - only **11 closed trades in 6.6 years**. Worth flagging: the first trade
  does not open until **2021-07-14**, because the sleeve's rule opens only on a *cross*
  and ETH's weekly 5/21 did not cross between the 22-bar warmup and July 2021. The entire
  2020-21 ETH bull market is therefore un-traded by this cell - the biggest ETH move in
  the sample contributes nothing. The headline +7.07%/trade is carried by three long-held
  positions (short Jan 2022 -> Jan 2023 +50.8%, long Nov 2023 -> Aug 2024 +30.6%, short
  Nov 2025 -> Aug 2026 +28.5%). Six pre-ETF trades averaged +11.1%; five post-ETF trades
  averaged +2.2%. That decay direction matches my prior, but with n = 5 post it means
  nothing statistically.

---

## 5. Deviations from the pre-registration

**One addition, no removals, no changes.** No threshold, window, direction, cost or
decision clause was altered after seeing any result.

1. **`NFP_R4_COVERED` (12:30-14:00) was added after the run** and is not in the README's
   section 3 list of NFP windows. It is a *descriptive decomposition* - 12:30-14:00 plus
   14:00-16:00 is mechanically the standalone window - included so a reader can see where
   the standalone number lives. It is not a candidate, it is marked non-decision-bearing
   everywhere it appears, and it is negative anyway (net -0.098%, t = -0.80), so it cannot
   and does not affect any verdict. Flagged rather than quietly shipped.
2. **The post-hoc direction-flip block** (section 3, `results/post_hoc_notes.json`) is
   likewise post-hoc, generated for all five cells uniformly, and carries an embedded
   warning in the JSON.

Both additions are reported because they were run, not because they help.

---

## 6. What could still be wrong

1. **NFP dates are computed first-Fridays, not BLS publication dates.** Confirmed against
   the DB: all 108 `scheduled_events` NFP rows are first Fridays, so the table is a
   generated calendar. Real releases deviate - most importantly the autumn-2025 US
   shutdown, which (per public reporting) pushed the September 2025 report from 2025-10-03
   to 2025-11-20 and suppressed a standalone October report. The pre-registered
   sensitivity dropping those three dates makes the cell *worse*, so this does not
   threaten the KILL. It would matter for a study trying to *establish* an NFP effect. My
   knowledge of the exact shutdown dates is general knowledge, not sourced from this repo
   - treat that specific claim as the weakest link in this document.
2. **`WKND_FADE` ignores DST.** Fixed 21:00/22:00 UTC is the true CME bell only in EST;
   roughly two-thirds of the year the measurement is one hour late. A real
   closure-boundary effect concentrated in the exact bell hour would be blurred. Given the
   effect is significantly negative rather than marginally positive, an hour of smearing
   is very unlikely to be what turned a winner into a -206% cumulative loser, but I cannot
   rule out that a DST-tracking version behaves differently.
3. **No funding charged.** All cells hold <= 72 h. Funding would make the three positive
   point estimates slightly worse and the two negative ones worse still - it can only push
   toward KILL, never away.
4. **`HIGH52` loses 2020** to the 364-day warmup (single-source lookback from `btc_1m`),
   so its pre-ETF era is 3 years, not 4, and the 2020 bull's 52-week highs are excluded.
   Seeding the lookback from `cd_spot_binance` would recover them at the cost of mixing
   venues inside one rule.
5. **Single venue (Binance) throughout.** A cell that is really an exchange-specific
   microstructure artefact would not be visible here.
6. **The non-overlap rule in `HIGH52` is a modelling choice with teeth** - it discarded 58
   of 96 signals. It is the right choice for a t-test (overlapping 5-day windows in a bull
   leg are one trade counted five times), but a live overlay that stacked signals would
   have a different, more autocorrelated return stream.
7. **PBO = 0.476 is weak evidence either way.** With only N = 5 columns the CSCV rank
   statistic is coarse. As pre-registered, a mid-range PBO across five nulls is what you
   would expect and is not itself evidence of overfitting - there was no search to overfit.

---

## 7. Priors versus outcome

I pre-registered **~10% that at least one of the five would pass**, with per-cell
probabilities of 2% / 5% / 8% / 5% / 5%. **None passed.** The calibration was reasonable,
and the reasoning behind it - that this repo has already ground BTC calendar effects to
death and R4 is the survivor - held up.

Two things I got wrong, recorded because they are the informative part:

* **`WKND_FADE` direction.** I predicted "a weakly positive raw mean and a negative net
  mean after 18 bp". Wrong: the raw mean is **clearly negative** (-0.410% gross). The
  weekend move persists, it does not revert. I had the mechanism backwards, not just the
  magnitude.
* **`QTR_END` sample size.** I predicted the clause would be unreachable at n ~ 26, and it
  was - but I did not anticipate the point estimate being strongly positive pre-ETF
  (+1.33%) and essentially zero post-ETF (+0.12%). The interesting fact about this cell is
  the decay, not the level, and my prior did not frame it that way.

What I got right: `EMA_ETH`'s era decay (pre >> post), `HIGH52`'s "right sign, wrong
significance", `NFP`'s death by cost-per-hour on a short window - though NFP turned out to
be dead *gross* as well, which is deader than I predicted.

---

## 8. Recommendation

**Build nothing.** All five cells KILL. No sleeve, no bot, no config change is proposed,
and none was created - `strategies/**` and `bots/**` are untouched, and the module used
for cell 5 (`strategies/sleeves/ema/math.py`, which moved to
`strategies/support/ema_position.py` on 2026-09-13) was imported read-only.

Concrete takeaways for the research queue:

1. **The weekend-gap-fade idea should be considered closed**, and closed with a real
   negative result rather than a low-power one: n = 349, wrong sign, both eras agreeing,
   DSR 2.8e-5. Add it to the do-not-retest list. The continuation flip is *not* a
   promising lead - it does not clear the same bar either.
2. **NFP adds nothing to R4.** Every NFP date is already an R4 V2 Friday fire, and the
   only unowned slice (14:00-16:00) has a gross mean of -1.2 bp over 81 events. There is
   no case for extending R4's Friday leg past 14:00.
3. **`QTR_END`, `HIGH52` and `EMA_ETH` are not refuted, they are unmeasurable at this
   cadence.** If any is worth revisiting, the honest route is not a longer backtest of the
   same window - it is a design that generates more observations (multi-asset panels, or a
   pooled cross-sectional test) so a double-era t >= 2.5 bar is physically reachable.
   Testing a 4-fires-a-year cell against a two-era significance bar will always return
   this answer, and running it again on more history will not fix it.
4. The one cell whose *point estimate* would justify a follow-up design is **`HIGH52`
   post-ETF** (+2.39%/trade over 18 trades, positive skew) - but that is a subgroup of a
   killed cell chosen after the fact, and it should be treated as an idea for a new
   pre-registration with its own trial count, not as a result from this one.

---

## 9. Artefacts

All numbers in this document come from:

* `results/cells_summary.csv` / `.json` - per-cell stats, era split, DSR, bootstrap CIs,
  power columns, skipped tallies, HIGH52 and EMA_ETH diagnostics.
* `results/trades_*.csv` - every individual trade for all seven rows (5 decision cells +
  2 NFP context rows), with entry/exit timestamps, side, prices, gross and net.
* `results/nfp_sensitivity.json` - shutdown-date and `n_trials=6` sensitivities, plus the
  R4-overlap verification flags.
* `results/pbo.json`, `results/monthly_grid.csv` - CSCV PBO input and output.
* `results/post_hoc_notes.json` - the flagged post-hoc direction flips.

Reproduce with `python studies/notebooks/calendar_cells/run_cells.py` (venv python,
read-only). Viewer notebook: `C:/Python/Python313/python.exe build_notebook.py`, then open
`calendar_cells.ipynb`.
