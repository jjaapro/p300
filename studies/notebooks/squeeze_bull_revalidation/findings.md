STATUS: CONCLUDED BUILD per pre-registration

# S-SqueezeBull - out-of-sample re-validation

**Tag: AUDIT / re-validation. N_TRIALS = 1.** The rules, thresholds, windows and the decision
rule were frozen in [README.md](README.md) before any number was computed. Nothing was tuned,
swept or re-selected here. Written 2026-09-08 from the runs of the same date.

**One-line answer.** The pre-registered rule returns **BUILD**, on numbers that clear both
thresholds by a hair: OOS bull-gated OI-flush n = 10 against a floor of 10, mean R **+0.2021**
against +0.10, full-sample combined MAR **1.60** against 1.50. All four parity checks pass
byte-exactly, so the OOS numbers are decision-bearing. But the margin is thin in a specific,
measurable way - drop the single best of the ten fires and the mean falls to +0.0679 (below the
+0.10 clause) with n = 9 (below the n floor); eight of the ten fires sit inside one 19-day bull
episode in late April / early May 2026; and the deflated Sharpe at N_TRIALS = 1 is 0.72, which
does not reject the null at 0.95. The verdict label is what the frozen rule produces. The
recommendation that goes with it is *not* "ship it now" - see [What happens next](#what-happens-next).

**Nothing was built.** No sleeve, no bot. A BUILD verdict is a recommendation to the user only.

---

## Runs behind these numbers

Re-ran on 2026-09-08 in order with the repo venv (pandas 3.0.0, numpy 2.2.6, Python 3.13.11):
`run_parity.py` -> `run_oos.py` -> `run_combined.py`, then the new `run_fragility.py`.
Everything reads `prod.db` through `file:...?mode=ro`.

Every decision-bearing number reproduced the artefacts a previous session left in `results/`,
**exactly**. Two things did move, both explained:

| what moved | before (2026-09-07 run) | now (2026-09-08 run) | why |
|---|---|---|---|
| OOS window end | 2026-09-06 23:59:59 | **2026-09-07 23:59:59** | the window is defined as "the last full UTC day"; a day passed. No new fire landed in it. |
| combined annual R (handling A, strict) | 8.53 | **8.52** | the strict span denominator grew by one day (4.518 y -> 4.521 y). MAR still rounds to 1.60. |
| regime distribution | 3504 h, 40 bull days, 27.40 % bull | 3528 h, 41 bull days, 27.89 % bull | same one extra day, which was a bull day. |
| `summary.json` bootstrap block | absent | present | `run_combined.py` was edited (07:44) *after* the last `summary.json` was written (07:43); the previous session was cut off before re-running it. The newest run wins. |

n, mean R, sum R, WR, maxDD, exit mix, the fire timestamps, all parity fields and clause (b)'s
MAR are unchanged to the last printed digit.

---

## Parity - P1-P4 (all pass)

Parity runs first and gates everything: per the README, if P1/P2/P4 fail and the delta is not
fully explained by a documented data change, no OOS number is decision-bearing.

| id | what | June reference | measured 2026-09-08 | pass |
|---|---|---|---|---|
| **P1** | OI flush bull-gated, phase3a window (fires <= 2026-02-06 17:30) | n = 104, mean R +0.280, MAR 2.18, annual R +8.10 | n = **104**, mean R **+0.280**, MAR **2.18**, annual R **+8.10**, maxDD -3.72, WR 0.596 | PASS |
| **P2** | OI flush on the table as of the ablation (rows <= 2026-06-05 19:01:47) | `fixed_020` bull n = 112, +0.285, MAR 2.05, maxDD -3.72, cum R 31.92, WR 0.607, June-internal OOS (>=2025-01-01) n = 17 @ +0.562; pooled n = 416, +0.009, maxDD -19.84, OOS n = 100 @ -0.028 | bull n = **112**, **+0.285**, MAR **2.05**, maxDD **-3.72**, cum R **31.92**, WR **0.607**, OOS n = **17** @ **+0.562**; pooled n = **416**, **+0.009**, maxDD **-19.84**, OOS n = **100** @ **-0.028** | PASS |
| **P2b** (extra anchor) | the -3 % threshold at the phase-2 cut (2026-06-05 17:04:40) | n = 221 (bear 49 / flat 106 / bull 66), mean R +0.005, maxDD -16.49, IS 181 / OOS 40 @ +0.069, bull +0.331 MAR 1.51 | identical on every one of those eleven fields | PASS |
| **P3** | OI flush bull-gated, 2022-01-30 -> 2026-04-13 (the caller's window), today's table | n = 104 +/- 3, mean R +0.28 +/- 0.05 | n = **104**, mean R **+0.280**, MAR 2.18; **0** fires between 2026-02-06 17:30 and 2026-04-13, so P1 and P3 are the same set | PASS |
| **P4** | fCVD, 2020-01-01 -> 2026-04-13, June code path | the 21-fire ledger in `funding_cvd_phase2_robustness.json`, mean R +1.08977 | n = **21**, all **21/21** timestamps matched, **max abs dR = 0.0**, mean R **+1.0897659407605873** | PASS |

Supporting reconstruction, all in `results/parity.json`:

- **The June OI table was reconstructed, not assumed.** The 2026-06-19 Binance migration
  back-filled 18 hourly OI rows *inside* the CoinDesk era (2026-06-02 13:00 -> 2026-06-03 06:00).
  Removing them gives 38,080 joined rows through 2026-06-05 15:00 - exactly the `n_bars` the
  June phase-1 JSON recorded (today's table gives 38,098). P1/P2/P2b are computed on that
  reconstruction.
- **Quantified migration delta.** On today's table the *pooled* fire set gains
  2026-06-02 22:00 and 2026-06-04 02:00 and loses 2026-06-03 16:00 (pooled n 416 -> 417,
  mean R +0.009 -> +0.007); max |dR| on the 415 common fires is **0.0**. The **bull-gated** set
  is completely unaffected (112 fires, identical timestamps, max |dR| = 0.0). All three moved
  fires are bear-regime, so **clause (a) is untouched by the backfill.**
- **P2c**: the 2026-07-22 `prod.db` backup and today's `prod.db` are byte-identical over the
  June span for both `cd_open_interest` and `cd_futures_ohlcv` (0 rows only-today, 0 only-backup,
  max value diff 0.0), so nothing else was rewritten under the June numbers.
- **P4b** (informational): handling B on the June window gives 20 fires sharing only 17
  timestamps with June's 21 - the funding-cadence handling genuinely changes which bars fire.
  See the caveats.

**README correction (not a deviation in the rules):** the README's environment note says the venv
runs pandas 3.0.2 / numpy 2.4.4. The measured environment is **pandas 3.0.0 / numpy 2.2.6 /
Python 3.13.11** (`results/parity.json -> env`). The README's purpose for that line - that the
parity check covers version drift - is met either way: P4 matched the June ledger to 0.0.

---

## Decision clauses - measured

Pre-registered rule (README section "Decision rule"), applied verbatim by `run_combined.py`:

| clause | required | measured | fired? |
|---|---|---|---|
| parity gate | P1 and P2 and P3 and P4 all pass | all pass (P2b too) | YES - OOS numbers are decision-bearing |
| **(a) n** | >= 10 resolved bull-gated OOS fires | **10** (0 unresolved) | YES, margin **0** |
| **(a) mean R** | >= +0.10 -> BUILD; <= 0 -> KILL | **+0.2021** | YES, BUILD side, margin **+0.1021** |
| **(b) combined MAR** | >= 1.5, full sample 2022-01-30 -> last full day, strict span | **1.60** | YES, margin **+0.10** |
| KILL clause | n >= 10 and mean R <= 0 | mean R +0.2021 > 0 | no |
| INCONCLUSIVE (n < 10) | n < 10 | n = 10 | no |
| INCONCLUSIVE (0 < mean R < +0.10) | - | +0.2021 | no |
| INCONCLUSIVE ((a) met, (b) failed) | - | (b) met | no |

**Verdict: BUILD** - `(a) n=10, mean R +0.202 >= +0.10 and (b) combined MAR 1.60 >= 1.5`.

Clause (b) alternates, all reported so the reader can see the construction is not cherry-picked:
strict span **1.60**; June formula (span first->last fire, window cut at overlapping fires)
**1.67** against June's own 1.77; fCVD handling B **1.74**.

Supporting OOS detail (`results/oos.json`):

| set | n | mean R | sum R | WR | maxDD | stops / targets / TIF |
|---|---|---|---|---|---|---|
| bull-gated (decision-bearing) | 10 | **+0.2021** | +2.021 | 70 % | -1.59 | 3 / 3 / 4 |
| pooled (all regimes) | 20 | -0.1719 | -3.438 | 45 % | -7.37 | 10 / 5 / 5 |
| bear-regime only | 5 | -0.5900 | -2.950 | 20 % | -3.27 | 4 / 1 / 0 |
| flat-regime only | 5 | -0.5018 | -2.509 | 20 % | -3.92 | 3 / 1 / 1 |

The bull gate is doing all the work, exactly as June said it would (pooled June mean R +0.009 was
a coin flip; pooled OOS is -0.17). The firing *rate* did not decay: 10 bull-gated fires over 41
bull-regime days = **0.244 fires per bull day**, against June's 0.2725 - 90 % of the historical
rate. What decayed is the **target-hit rate**: June bull-gated exits were 32 % stop / 41 % target /
27 % TIF; the OOS ten are 30 % / **30 %** / 40 %. Fewer 3 % follow-throughs, more small TIF scrapes.
That, not fewer signals, is why the mean fell from +0.285 (June full) and +0.562 (June's internal
2025-> slice) to +0.202.

---

## Fragility of the BUILD - how thin is thin

Everything in this section is post-hoc measurement of an already-frozen verdict. It changes no
threshold and no label. Artefacts: `results/fragility.json`, `results/fragility_regime_audit.csv`.

### 1. Drop the best fire, then the best two

The ten OOS bull-gated outcomes, sorted: `-1.090, -1.090, -1.090, +0.160, +0.234, +0.311, +0.355,
+1.410, +1.410, +1.410`. The three positives at exactly +1.410 are the three target exits
(a 3 % move on a 2 % stop, minus 18 bp - the payoff is mechanically identical every time).

| subset | n | mean R | sum R | verdict under the frozen rule |
|---|---|---|---|---|
| all ten (as run) | 10 | **+0.2021** | +2.021 | **BUILD** |
| drop the single best (+1.410) | 9 | **+0.0679** | +0.611 | **INCONCLUSIVE** - fails *both* legs of (a): n = 9 < 10 **and** +0.068 < +0.10 |
| drop the best two | 8 | **-0.0999** | -0.799 | **INCONCLUSIVE** (n = 8 < 10); at n >= 10 that mean would have been a **KILL** |

Full jackknife (every leave-one-out, n = 9 each): mean R ranges **[+0.0679, +0.3456]**; 7 of the
10 subsets still clear +0.10. All 45 leave-two-out pairs (n = 8): **[-0.0999, +0.5251]**, 67 % of
pairs still clear +0.10 and 7 % go non-positive.

Concentration: the three target exits carry **+4.230 R**, more than the whole +2.021 R sum -
i.e. everything else nets **-2.209 R**. The result is three winners paying for seven other trades.

**Read:** the n >= 10 floor is met with margin zero, so the sample cannot lose a single fire without
the study reverting to INCONCLUSIVE. The mean-R clause has more room in absolute terms (+0.102)
but is carried by three identical mechanical payoffs.

### 2. Does the regime gate peek? (yes - and it is not what makes the result)

The June construction is `daily = close.resample('1D').last(); ret_30d = daily.pct_change(30)`
reindexed onto the hourly grid with `method='ffill'`. The daily bar for day D is **stamped at
00:00 of day D but carries the last close of day D** (the 23:00 bar). Forward-filling it means an
hourly bar at HH:00 on day D reads a 30-day return whose numerator is a close that has not
happened yet.

Measured over all 20 OOS fires (`results/fragility_regime_audit.csv`): **20 of 20** fires read a
gate value sourced from a close stamped at or after the fire. Look-ahead ranges **1 h to 21 h**,
mean **10.85 h**; among the 10 bull-gated fires the worst is **20 h** (the 2026-05-11 03:00 fire
reads the 2026-05-11 23:00 close). This is real look-ahead, not a rounding artefact.

`ret_30d_backonly` (the pre-registered sensitivity: daily series shifted one day, so day D uses
the close of D-1) is **strictly causal** - every gate source close lands >= 3 h *before* its fire.

The two constructions disagree on 3 of the 20 fires:

| fire | June gate | backward-only gate | R |
|---|---|---|---|
| 2026-05-13 13:00 | flat (+0.0659) | **bull** (+0.1379) | -0.649 |
| 2026-06-02 22:00 | bear (-0.1503) | flat (-0.0923) | -1.090 |
| 2026-08-21 12:00 | **bull** (+0.1850) | flat (+0.0974) | -1.090 |

So the backward-only bull set is the June set minus 2026-08-21 (-1.090) plus 2026-05-13 (-0.649):
still exactly n = 10, mean R **+0.2462**, sum +2.462, maxDD -1.149.

**Which is decision-bearing?** The **June construction (+0.2021)**, because the README froze it as
the rule and named backward-only a *sensitivity*. That is the number in the clause table and it
stays there. **Which would you actually trade?** The backward-only one, because the June one is
not implementable at the fire - you cannot know today's 23:00 close at 03:00.

The important part is the *direction*: the causal construction scores **higher** (+0.2462 vs
+0.2021), so the look-ahead is **not manufacturing the OOS result** - it is, if anything, costing
it 0.044 R per trade. That is a real reassurance about this specific ten-fire sample. It is *not*
a general clean bill of health: the June study's headline numbers (P1 +0.280, P2 +0.285) were all
computed with the same peeking gate, and this study did not re-derive the full sample under
backward-only. Any production port must use the backward-only gate (or an intraday rolling 30-day
return), and the June reference numbers should be re-derived under it before they are quoted as
the sleeve's expectation.

### 3. The OI source seam

`cd_open_interest` switches source mid-window: CoinDesk OHLC rows through **2026-06-10 08:00**,
native Binance point snapshots from **2026-06-10 09:00** (migration 2026-06-19).

The seam itself is benign for this rule. The 1-hour OI change across it is **+0.0601 %**, which is
**0.35x** the 2026 median absolute hourly OI move (0.1739 %) and far under the p99 (2.14 %). The
rule reads only a 4-row percent change of `oi_close`, so it is source-agnostic. In the OOS window
the OI series has **0 missing hours, 0 off-grid rows, 0 duplicates** (3,545 rows on a 3,545-row
grid), and every fire's 4-row window spans exactly 4 wall-clock hours.

The *fire distribution* across the seam is not benign at all:

| side | span | bull-gated fires | mean R | sum R | pooled fires | pooled mean R |
|---|---|---|---|---|---|---|
| pre-seam (CoinDesk) | 2026-04-14 -> 2026-06-10, **57.3 d** | **8** | **+0.3499** | +2.799 | 14 | -0.0571 |
| post-seam (Binance) | 2026-06-10 -> 2026-09-04, **86.2 d** | **2** | **-0.3893** | -0.779 | 6 | -0.4398 |

**8 of the 10 decision-bearing fires are on the old source; 2 are on the new one, and both of
those together are net negative.** On the post-seam subset alone the frozen rule returns
**INCONCLUSIVE (n = 2 < 10)** - the verdict does not survive there, though at n = 2 nothing could:
that subset is far below the evidentiary floor either way, so this is a statement about sample
size, not a demonstration that the new source breaks the rule.

The honest reading is that the seam and the regime coincide. The bull gate opened for a long
stretch in late April / early May (pre-seam) and again only from 2026-08-21 (post-seam), so the
pre/post split is mostly a split between "the big April-May bull episode" and "everything after".
Section 4 makes that the primary framing.

### 4. Uncertainty on the OOS mean, and the deflated Sharpe

**Why N_TRIALS = 1 is defensible here.** This study fits nothing. Both rules, every threshold, both
windows, the cost convention and the decision rule were written into README.md before a single
outcome number existed, and the analysis code *imports* the June functions rather than
re-implementing them. There is no search over which the maximum could have been taken, so the
multiple-testing penalty for *this* study is zero and `sr_expected_under_H0 = 0`. N = 1 is **not**
defensible for the June studies that produced the rule - see the breakeven below.

All from `studies.lib.validation` (`bootstrap.bootstrap_sharpe`, `bootstrap.dsr_required_sr`,
`bootstrap.circular_block_indices`, `dsr_pbo.dsr_from_returns`), seed 20260908, 20,000 resamples:

| statistic | value |
|---|---|
| point mean R | **+0.2021** |
| iid bootstrap 90 % CI on the mean | **[-0.3054, +0.7052]** |
| iid bootstrap 95 % CI on the mean | [-0.4229, +0.8119] |
| P(mean > 0) | 73.3 % |
| P(mean >= +0.10) - i.e. P(clause (a) mean leg holds) | **60.7 %** |
| circular-block bootstrap (block = 3) 90 % CI | [-0.1190, +0.5350]; P(>0) 83.1 % |
| **episode cluster bootstrap** (resample the 3 firing episodes, sizes 8/1/1) 90 % CI | **[-0.6228, +0.3477]**; P(>0) 74.2 % |
| per-trade Sharpe | +0.1975 (annualised +0.98 at 24.85 trades/yr) |
| skew / full kurtosis | -0.11 / 1.35 (bimodal, not fat-tailed) |
| **Deflated Sharpe, N_TRIALS = 1** | **0.7208** (z = 0.585) - **does not reject the null at 0.95** |
| per-trade Sharpe required for DSR > 0.95 at n = 10 | 0.5483 (N = 1); 0.9458 (N = 5) |

The 90 % CI on the mean straddles zero under every resampling scheme. The DSR is the sharpest
statement available: even with **no** multiple-testing penalty at all, ten fires at this Sharpe
are not enough to reject "no edge" at conventional confidence. The pre-registered clause was
"mean R >= +0.10 at n >= 10", not "DSR > 0.95", and the clause is met - but the reader should know
that the clause is a much weaker bar than significance.

**Clustering.** The ten fires form **3 episodes** under 7-day linkage:

| episode | fires | span | sum R |
|---|---|---|---|
| 2026-04-21 -> 2026-05-11 | **8** | 19.3 d | **+2.799** |
| 2026-08-21 | 1 | - | -1.090 |
| 2026-09-04 | 1 | - | +0.311 |

Two of the nine consecutive fire pairs are closer together than the 48-hour TIF, and no two OOS
trades were ever actually open at the same time (corrected 2026-09-09 — the original text said
nine of ten, from a unit bug; see the corrections section at the end). The fires are still
heavily clustered: eight of the ten fall inside a single 19-day episode, which is why the episode
cluster bootstrap (which resamples whole episodes) widens the interval to [-0.62, +0.35].
Stripped of statistics: **the OOS BUILD rests on one three-week bull run in spring 2026.**

**Context, and the N that would kill it.** The full-sample bull-gated leg (n = 114, mean R +0.273,
per-trade SR +0.2530) has DSR **0.9956** at N_TRIALS = 1 - but N = 1 is wrong for the full sample,
because June *did* search (the ablation swept at least the -2 % / -3 % thresholds across the three
regime gates). The full-sample DSR falls below 0.95 once **n_trials >= 4**. An honest count of
June's effective search is plausibly at or above 4, which means the *historical* leg is not
comfortably significant either once the selection is paid for. This is context, not a clause: the
pre-registration did not gate on DSR, and re-deflating the June studies is a separate job.

### 5. The funding + CVD leg - 2 OOS fires

| fire | R | exit | funding z | CVD z | regime at fire | cutover-mixed? |
|---|---|---|---|---|---|---|
| 2026-06-05 20:30 | +0.678 | TIF | -2.03 | +0.93 | bear (-0.250) | no |
| 2026-06-17 13:15 | -1.127 | stop | -2.35 | +1.02 | bear (-0.162) | no |

n = 2, mean R **-0.2243**, sum -0.449. Handlings A and B produce the **identical** two fires with
identical R, so the funding-cadence handling makes no difference to the OOS sample.

**What this does not tell us.** Nothing about whether the rule works. n = 2. The README gave OOS
fCVD numbers **no decision weight** in advance precisely because the rule fires ~3.5 times a year;
reaching a decision-grade n >= 10 would take roughly three more years at the historical rate. No
bootstrap, no Sharpe and no DSR was computed on two observations, and none should be.

**What it does tell us.** (i) The rule still fires post-cadence-change: two triggers in the 8-hour
settlement regime, both with funding z past -2 and sustained positive CVD z, so the feature
pipeline did not silently die at the cutover. (ii) Both fires were in a **bear** 30-day regime -
the fCVD rule is ungated by design, which is why it diversifies the bull-gated OI leg (monthly
Pearson r = **+0.017** over 36 active months). (iii) The bigger caveat is not these two fires but
the cadence itself: on the June window, handling B fires 20 times against handling A's 21 and
shares only **17** timestamps. The funding z-score is cadence-dependent, so the post-cutover part
of handling A is an 8-hour step function forward-filled onto a 15-minute grid - not quite the
statistic June validated. Live, the series will be handling-B-shaped forever.

**How it enters the combined portfolio (clause b).** As 16 resolved fires from 2022-03-11 onward
contributing +7.40 R - **19 %** of the combined +38.54 R. But it also deepens the drawdown, and it
deepens it faster than it adds return:

| portfolio (handling A, strict span, 2022-01-30 -> 2026-09-07) | n | sum R | maxDD | annual R | MAR |
|---|---|---|---|---|---|
| combined (the clause-(b) object) | 130 | +38.54 | -5.31 | +8.52 | **1.60** |
| OI flush bull-gated alone | 114 | +31.14 | -3.72 | +6.89 | **1.85** |
| funding+CVD alone | 16 | +7.40 | -3.61 | +1.65 | 0.46 |

**Clause (b) passes despite the fCVD leg, not because of it**: adding it moves MAR from 1.85 down
to 1.60, i.e. it consumes 60 % of the +0.10 margin the clause has. Under handling B the fCVD leg
is far better behaved (maxDD -1.49, MAR 1.63) and the combined MAR is 1.74.

### 6. Clause (b) is mostly an in-sample statistic

Worth stating plainly, because "full-sample combined MAR >= 1.5" reads like an OOS test and is not:

| window | n | sum R | maxDD | annual R | MAR |
|---|---|---|---|---|---|
| full sample -> 2026-09-07 | 130 | +38.54 | -5.31 | +8.52 | **1.60** |
| pre-OOS only (-> 2026-04-13) | 118 | +36.97 | -5.31 | +8.98 | **1.69** |

The OOS window contributes **+1.57 R** of +38.54 (4 %). Clause (b) was already true before the OOS
window opened (and June's own figure was 1.77). The unseen data did not *establish* clause (b);
it slightly **eroded** it, 1.69 -> 1.60. Read correctly, clause (b) is a "did the new data break the
historical portfolio" check - answer: dented, not broken - and essentially all the genuinely new
evidence in this study is the ten fires of clause (a).

---

## Data caveats

1. **OI source seam (2026-06-10 08:00 / 09:00).** Quantified in section 3. Seam step +0.0601 % =
   0.35x the 2026 median hourly move; OOS OI series complete (0 missing / 0 off-grid / 0 duplicate
   rows); the rule uses only a 4-row percent change and is source-agnostic. **But 8 of the 10
   decision fires are pre-seam and the 2 post-seam fires are net negative.** The native-Binance era
   has not yet produced a decision-grade bull-gated sample.
2. **Binance backfill inside the CoinDesk era.** 18 hourly rows (2026-06-02 13:00 -> 2026-06-03
   06:00) were added retroactively by the migration and did not exist when June ran. They change
   the *pooled* fire set by +/-1 fire and leave the **bull-gated** set identical. Two of the changed
   fires fall inside the OOS window; both are bear-regime, so clause (a) is unaffected. All June
   parity anchors were recomputed on the table with those rows removed.
3. **Funding cadence change 2026-04-13.** `cd_funding_rate` went from ~24 hourly CoinDesk
   *predicted* rows/day to exactly 3 Binance *settlement* rows/day (measured: 23.93/day in the 30
   days before, 3.00/day in the 30 days after; 0 post-cutover rows off the 00/08/16 UTC grid).
   The level and dispersion are comparable (mean |fr| 3.27e-05 -> 4.11e-05, sd 4.06e-05 ->
   4.22e-05), so the z-score is not obviously rescaled - but the *sampling* changed, and on the
   June window handling A and handling B share only 17 of ~21 fires. The 2 OOS fCVD fires are
   identical under both handlings and neither is flagged `cutover_mixed`. **This caveat bounds only
   the fCVD leg, which carries no decision weight; the OI-flush clause (a) does not touch funding
   data at all.**
4. **Regime-gate look-ahead.** Section 2. The frozen June gate peeks up to 21 hours ahead. The
   causal variant scores better on this sample, so the OOS verdict is not an artefact of it - but
   every June reference number in the parity table was computed with the peeking gate, and a
   production port must not be.
5. **Trade overlap.** 48-hour TIF and a 24-row cooldown mean concurrent positions are possible,
   though in the OOS window none occurred (2 of 9 consecutive pairs are < 48 h apart and both
   resolved before the next entry — corrected 2026-09-09). Per-trade R statistics treat the fires
   as independent, which is defensible for this window but not in general: a real sleeve still
   needs a concurrent-position policy, and neither the June studies nor this one models one.
6. **Cost.** 18 bp round trip, the research convention, applied inside the imported June `replay`
   at entry. Production `strategies/trades.py` charges 10 bp fee + 5 bp slippage **plus funding**,
   which for a long-only 48-hour hold in a bull regime is an extra cost not modelled anywhere here.
7. **Unresolved fires.** None - all 20 OOS fires had their full 48-hour TIF covered by data
   (`cd_futures_ohlcv` runs to 2026-09-08 16:00, the window ends 2026-09-07 23:59:59). No
   mark-to-market estimate enters any decision number.

---

## What happens next

The pre-registered rule returns **BUILD**. That is a recommendation to the user and nothing has
been built - no `bots/squeeze_bull/strategy/`, no `bots/squeeze_bull/`.

What the numbers actually support, in order:

1. **Do not treat this as a green light to size a sleeve.** The BUILD rests on ten fires whose n
   margin is zero, whose mean margin evaporates if any one of three mechanically-identical target
   exits is removed, and whose DSR is 0.72 at the most generous possible trial count. Eight of the
   ten are one three-week episode.
2. **Cheapest decisive next step: keep the frozen rule running and re-cut on a schedule.** The
   rule fires at 0.24-0.27 per bull-regime day. Another ~40 bull-regime days doubles the OOS
   sample. Re-running `run_oos.py` + `run_combined.py` monthly costs minutes and needs no new
   pre-registration, since the rule is frozen - the natural re-cut points are n = 20 and n = 30
   OOS bull-gated fires. The one thing that must not happen is editing a threshold when the next
   fire disappoints.
3. **If a paper sleeve is authorised anyway, three things must change from the June code.**
   (i) the regime gate must be the backward-only construction (or an intraday rolling 30-day
   return) - the June gate is not implementable; (ii) the entry must re-derive `ret_30d` from the
   live hourly table with a stale-data refusal, per the table-freshness contract, because
   `cd_open_interest` is a live-read table; (iii) concurrent-position policy must be decided
   explicitly — no OOS fire actually overlapped a prior open trade, but the rule permits it.
4. **The funding+CVD leg is not ready to be part of anything.** Two OOS fires. It also *lowers*
   the combined MAR (1.85 -> 1.60) under the handling the pre-registration made primary. If a
   sleeve is built, build the OI-flush bull-gated leg alone and revisit fCVD when it has >= 10 OOS
   fires - roughly three years away at its historical rate, or sooner only if the 8-hour cadence
   raises its trigger frequency.
5. **Separately, the June reference numbers deserve a re-derivation under the causal gate**, and
   the full-sample DSR deserves an honest trial count (it drops below 0.95 at n_trials >= 4). That
   is a different study, not a revision of this one.

---

## Deviations from the pre-registration

| item | status |
|---|---|
| Rules, thresholds, windows, cost, decision rule | **unchanged**; README.md was not edited after the first script ran |
| P1-P4 pass conditions | **unchanged**, all four evaluated and passed |
| Any clause unevaluable | **none** - every clause produced a number |
| `run_fragility.py` | **added** after the verdict was fixed. It is post-hoc measurement of an already-frozen result: it computes no new rule, changes no threshold, and its outputs are labelled non-decision-bearing throughout. The verdict label is unchanged by it. |
| Environment note in README | README says pandas 3.0.2 / numpy 2.4.4; measured pandas 3.0.0 / numpy 2.2.6 / Python 3.13.11. Recorded as a README inaccuracy; parity covers version drift and P4 matched to 0.0 |
| OOS window end | advanced 2026-09-06 -> 2026-09-07 between the previous session's run and this one, by the README's own "last full UTC day" definition. No fire landed in the extra day; the newest run is the one reported |

---

## Files

| file | what |
|---|---|
| `README.md` | the pre-registration (written before any outcome number) |
| `squeeze_bull_lib.py` | read-only loaders + imports of the frozen June rule functions |
| `run_parity.py` -> `results/parity.json`, `results/parity_*.csv` | P1-P4, P2b, P2c, data-migration caveats |
| `run_oos.py` -> `results/oos.json`, `results/oos_*_ledger.csv`, `results/oos_regime.json`, `results/per_year.csv`, `results/etf_split.csv` | the OOS window and the informational tables |
| `run_combined.py` -> `results/summary.json`, `results/combined_ledger*.csv`, `results/fig_cum_r.png` | clause (b) and the verdict |
| `run_fragility.py` -> `results/fragility.json`, `results/fragility_regime_audit.csv` | the fragility analysis in this document |
| `build_notebook.py` -> `squeeze_bull_revalidation.ipynb` | executed viewer notebook (system Python) |

---

## 10. Corrections after independent verification (2026-09-09)

This study was re-checked by an independent agent that recomputed every decision number
from `results/` and re-derived all 20 OOS fires from `prod.db` with its own trigger and
replay implementation. **The verdict is unchanged: the pre-registered rule returns BUILD**,
and every decision-bearing figure reproduced (max |dR| vs this study's ledger = 2.2e-16;
n = 10, mean R +0.202084, MAR 1.6040 unrounded). Four things did need correcting, and one
of them materially strengthens the case for waiting rather than building.

### C1 — Trade-overlap claim was wrong (unit bug, fixed)

`run_fragility.py` computed inter-fire gaps as `np.diff(t.view('int64')) / 3.6e12`. Under
pandas 3.0 these timestamps are `datetime64[us]`, so `.view('int64')` yields **microseconds**,
not nanoseconds, and every gap came out ~1000x too small — all nine below the 48-hour TIF.
The true gaps are 39, 91, 61, 112, 52, 27, 82, 2457 and 338 hours: **2 of 9 consecutive pairs
are under 48 h, and replaying the actual exits shows no two OOS trades were ever open at the
same time.** Fixed in `run_fragility.py`, `results/fragility.json` regenerated, and the three
affected sentences corrected in sections 4, "Caveats" and "What happens next".

This makes the fires *more* independent than the study claimed, not less. The episode
clustering — 8 of 10 fires inside one 19-day window — is unaffected and remains the real
concentration problem.

### C2 — The trial count was understated, and this is the important one

Section 4 said an honest N is "plausibly at or above 4". The June artefacts on disk state the
search size explicitly and it is an order of magnitude larger:

- `studies/material/chento/validation/oi_flush_threshold_ablation.json` contains **30 threshold
  variants** (fixed_020 … fixed_050, plus z-score and percentile families over 30/60/90-day
  windows), each scored pooled **and bull-gated**;
- phase 2 additionally swept roughly **80 stop / target / TIF combinations**.

Recomputed on the full-sample bull-gated leg (n = 114, per-trade SR +0.2530):

| N_TRIALS | 1 | 4 | 30 | 80 | 110 |
|---|---|---|---|---|---|
| DSR (full sample) | 0.996 | 0.945 | **0.726** | **0.592** | 0.549 |
| DSR (OOS only, n=10) | 0.721 | — | 0.072 | 0.033 | — |

Worse than the raw count: **the shipped -2% threshold was selected on bull-gated MAR** — the
same statistic clause (a) is built on — so the selection and the decision share a criterion.
The historical leg does not survive its own search space. Only genuinely new fires can fix
this, which is precisely what recommendation 2 (re-cut at n = 20 and n = 30) is for.

### C3 — "The causal gate scores higher" is true of only one causal gate

Section 2 argued the June gate's look-ahead is not manufacturing the result, because the
pre-registered backward-only construction scores **higher** (+0.2462 vs +0.2021). That holds.
But the *other* causal construction this study recommends for production — an intraday rolling
30-day return, `close[i]/close[i-720] - 1` on the hourly grid — scores **+0.1212**, a 40 % haircut
that lands almost exactly on the +0.10 clause floor (n = 10, maxDD -2.24, DSR@1 0.635). Any
production port must pick its causal gate deliberately and re-cut the decision on that gate,
not inherit +0.2021.

### C4 — Clause (b) was compared against a rounded value

`squeeze_bull_lib.window_metrics()` returns `MAR` already rounded to two decimals, and
`run_combined.py` tests `mar >= 1.5` against that rounded number. Not triggered here (raw MAR
is 1.6040), but a raw 1.4951 would have rounded to 1.50 and passed a clause it fails. A
pre-registered threshold should never be clearable by formatting. Left as-is so this run's
artefacts stay reproducible; fix `window_metrics()` to keep a raw field before any re-cut.

### C5 — Recorded, not alleged: the n floor equalled the available sample

The tenth OOS fire is dated 2026-09-04 14:00 and the pre-registration was written 2026-09-06,
with the clause "n >= 10". The floor therefore coincided exactly with the number of fires then
available; a floor of 11 would have returned INCONCLUSIVE. The verifier found no evidence of an
exploratory run preceding the README (README mtime precedes every script and the study lib's
first import) and explicitly does not allege one — but the artefacts cannot rule it out, and an
audit should say so. **Mitigation: fix the re-cut points at n = 20 and n = 30 now, in writing,
before the fires exist.**

### What this changes

Nothing about the verdict label, which the frozen rule determines. Everything about how much
weight it should carry. Taken with the fragility already in section 4 — drop the best fire and
the mean falls to +0.068, drop two and it is negative; 6 of the 10 outcomes are the mechanical
barrier payoffs, so clause (a) is in substance "did 3 of 6 barrier-resolved trades hit +3 %
before -2 %" — the honest summary is: **the rule says BUILD, the evidence says wait for
n = 20.**

---

## Addendum 2026-09-19 — re-cut on the corrected open-interest table (BACKLOG item 30)

**Why.** From 2026-06-10 09:00 UTC the live `cd_open_interest` held the open interest at the *start* of each
hour, one bar stale against the convention these June rules were written on (the close of the hour, CoinDesk's
`CLOSE_SETTLEMENT`). The table was re-stamped on 2026-09-18 21:49 UTC (`data/migrations/2026_09_19_oi_close_of_hour.py`,
verified against Binance Vision's 5-minute archive: 85.9 % of rows closer to the close-of-hour snapshot after,
88.6 % closer to the start-of-hour one before). Every result from this study's 2026-09-08 run that used a fire at
or after that seam was built on the stale alignment. This addendum re-runs the frozen pipeline unchanged
(`run_parity → run_oos → run_combined → run_fragility`) on the corrected table and reports what moved. The
2026-09-08 results are preserved in git at `f0f5abe`; the fire-level diff is `results/recut_2026_09_19.json`
(`recut_oi_table_2026_09_19.py`). The OOS window, defined as "the last full UTC day", ran to 2026-09-17 instead of
09-07; that extension added no fire, so every difference below is the table's.

**Parity.** P1–P4 all pass, unchanged — the anchors sit on rows the migration did not touch.

**OOS OI-flush fires, fire by fire** (20 → 21; 16 unchanged, none with a changed R, nothing before the seam moved):

| 2026-09-08 run (stale alignment) | corrected table | R |
|---|---|---|
| 2026-06-17 21:00 bear | 2026-06-17 20:00 bear | −1.09 → −1.09 |
| 2026-06-23 07:00 bear | 2026-06-23 06:00 bear | −1.09 → −1.09 |
| 2026-07-01 07:00 bear | 2026-07-01 06:00 bear | +1.41 → +1.41 |
| **2026-09-04 14:00 bull** | **2026-09-04 13:00 bull** | **+0.311 → +0.195** |
| — | 2026-06-11 16:00 bear (new) | +1.069 |
| (window ended 09-07) | **no fire on 2026-09-11** | — |

Three bear fires are the same event relabelled one hour earlier with the same outcome. The one bull fire in the
post-seam set is also the same event an hour earlier, entered a bar sooner, worth 0.12 R less. One bear fire exists
only on the corrected alignment. And the bar that fired SJ-4250 in the paper ledger (2026-09-11 17:00) does not fire
here — consistent with the top-anatomy note's −1.79 % at bar closes against the −2 % trigger. That is the evidence
behind decision 8 in the study's own harness.

**Decision (frozen rule, unchanged).** BUILD → **BUILD**, by the same margin:

| clause | 2026-09-08 | corrected table | floor |
|---|---|---|---|
| (a) OOS bull-gated n | 10 | 10 (the same ten fires; only 09-04's R moved) | ≥ 10 |
| (a) OOS bull-gated mean R | +0.202 | **+0.190** | ≥ +0.10 |
| (b) full-sample combined MAR (handling A, strict) | 1.60 | **1.59** | ≥ 1.5 |
| bootstrap P(mean ≥ +0.10), n = 10 | — | 59 % | informational |
| without the best fire | +0.068 | +0.055 | informational |

The correction lowers the decision statistics by 0.012 R and 0.01 MAR and changes no verdict. The margins remain
one fire wide, exactly as the original findings said. Full-sample bull-gated n rises 112 → 114 (the new 06-11 fire
and 09-04), DSR 0.9955 unchanged in substance.

**Caveats carried, not lifted.** The 2026-09-14 review's P0 status stands: the combined MAR is trade R concatenated
by signal time, not a marked, capital-aware portfolio, and funding provenance across the 2026-04-13 cutover is
historical. Nothing here is a new current-policy protocol; it is the old one on the right data.

**Artifacts.** `results/` regenerated 2026-09-19; `squeeze_bull_revalidation.ipynb` re-executed on the corrected
table (the 2026-09-08 execution is in git at `f0f5abe`); `results/recut_2026_09_19.json`.
