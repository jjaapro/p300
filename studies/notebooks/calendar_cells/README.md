# S-calendar-cells — PRE-REGISTRATION

**Written and frozen before any outcome number was computed.** Date: 2026-09-08.
Author: research agent, p300 repo. Read-only on `data/databases/prod.db`.

---

## 1. Purpose

Five — exactly five — pre-registered calendar cells on BTC/ETH, each tested **once**,
with the multiple-comparison correction done honestly at the true trial count.

This is deliberately a *small* multiple-comparison exercise. The repo has already
ground through very large intraday calendar grids (`studies/notebooks/r4_study/`,
`studies/notebooks/range_sanity_2026_09/`, `studies/notebooks/grid_study/`). The point
here is not to search. It is to take five specific, externally-motivated calendar
claims, state them completely in advance, and run them once.

**N_TRIALS = 5.** Deflated Sharpe is computed at `n_trials=5` for every cell.

No cell will be re-specified, re-parameterised, re-windowed or re-directioned after a
result is seen. If a definition has a free choice, it is made and frozen **below**,
with the reason, before the script is run.

---

## 2. Common frozen conventions (apply to all five cells)

| Item | Frozen value |
|---|---|
| Price source, BTC | `btc_1m` in `prod.db` (Binance 1-minute klines, UTC) |
| Price source, ETH | `eth_1m` in `prod.db` (Binance 1-minute klines, UTC) |
| DB access | `sqlite3.connect("file:"+str(db.PROD_DB)+"?mode=ro", uri=True)` — read-only |
| Fill convention | **open of the entry minute bar** and **open of the exit minute bar** (matches `studies/notebooks/r4_study/r4_lib.py` and the live R4 sleeve, which fill on the open of the entry hour and close on the open of the exit hour) |
| Sample start | 2020-01-01 00:00 UTC (first `btc_1m` / `eth_1m` bar) |
| Sample end | 2026-09-07 23:59 UTC (last complete UTC day; data runs to 2026-09-08 16:59 and the partial day is discarded) |
| Cost | **18 bp round trip**, research convention. `r_net = side * (p_exit/p_entry - 1) - 0.0018` |
| Funding | Not charged. Every cell holds ≤ 72 h and most hold ≤ 24 h; funding is a second-order term at these horizons and adding it would require a per-cell 8h-settlement alignment that is not part of any of the five source claims. Stated as a limitation, not silently dropped. |
| Era split | `pre_etf` = entry date < **2024-01-11**; `post_etf` = entry date ≥ 2024-01-11 (BTC spot-ETF launch, the repo's standard boundary, `metrics.era_split` default) |
| Missing bars | If the entry **or** exit minute bar is absent, the observation is **skipped** and counted in a per-cell `skipped` tally reported in `results/`. No forward-filling, no nearest-bar substitution. |
| Statistic `t` | One-sample t on per-trade net returns, `t = mean / (sd / sqrt(n))`, sd with `n-1` |
| Statistic `Sharpe` | **Per-observation (per-trade)** Sharpe `mean/sd`, NOT annualised — this is the unit `dsr_pbo.deflated_sharpe` requires |
| DSR | `studies.lib.validation.dsr_pbo.dsr_from_returns(net_returns, n_trials=5)` on the **full pooled sample** (both eras), full-kurtosis convention |
| Bootstrap | `studies.lib.validation.bootstrap.bootstrap_sharpe(..., n_iter=10000, seed=42)`, 90% percentile CI on per-trade Sharpe. **Supporting evidence, not a decision clause.** |

---

## 3. The five cells — complete frozen definitions

### Cell 1 — `QTR_END`: turn-of-quarter, BTC, LONG

* **Entry**: open of the 00:00 UTC minute bar on the **last calendar day of March,
  June, September, December**.
* **Exit**: open of the 00:00 UTC minute bar on the **3rd calendar day of the new
  quarter** (i.e. 1 April / 1 July / 1 October / 1 January + 2 days).
* **Hold**: 72 h. The window is calendar day −1 through +2 around the quarter turn.
* **Direction**: LONG.
* **Free choice frozen — the window length.** The equity turn-of-month/turn-of-quarter
  literature (Lakonishok & Smidt 1988; Ogden 1990) buys at the close of the last
  *trading* day and holds ~3–4 *trading* days into the new period, the mechanism being
  month-end institutional cash flows and rebalancing. Crypto trades continuously, so
  there are no trading days to count: I take the direct calendar analogue, −1 to +2
  calendar days = 72 h, and freeze it. I am not sweeping 24/48/72/96 h; picking one is
  the whole point of a five-trial study.
* **Expected n**: 26 (2020-03-31 … 2026-06-30), roughly 16 pre-ETF / 10 post-ETF.
  This is a very small sample and the pass rule will be hard to clear — that is a
  property of the claim, not a reason to widen the window.

### Cell 2 — `NFP`: US non-farm payrolls day, BTC, LONG

* **Event dates**: `SELECT date FROM scheduled_events WHERE event_type='NFP'` in
  `prod.db`. **Checked before writing this file**: the table holds 108 NFP rows
  spanning 2019-01-04 … 2027-12-03, and **all 108 are first Fridays** (weekday == Fri
  and day-of-month ≤ 7). So the table is a *computed* first-Friday calendar, not a
  scrape of BLS publication dates. The rule and its exceptions are documented in §5.
* **Direction**: LONG. Frozen because R4 — the incumbent this cell must beat — is
  long-only; a short NFP cell could not be composed with R4's Friday leg, so a long
  cell is the only version that is decision-relevant.
* **Standalone window**: enter at the open of the 12:30 UTC minute bar, exit at the
  open of the 16:00 UTC minute bar. 3.5 h. (13:30 UTC is the release; 12:30–16:00 is
  the one-hour-before to 2.5-hours-after window given in the task.)
* **Incremental window** — *this is the decision-bearing one*: enter at the open of the
  **14:00** UTC minute bar, exit at the open of the **16:00** UTC minute bar. 2 h.
  * Why: the live `JPLUS_R4_BTC_V2` / `JPLUS_R4_ETH_V2` windows are **Fri 04:00 → 14:00
    UTC with day-of-month ≤ 14** (`docs/calibration/r4.md`, `bots/r4/windows.py`).
    Every NFP date is a first Friday, so **day-of-month ≤ 7 ⇒ every single NFP date is
    already an R4 V2 Friday fire**. The 12:30 → 14:00 portion of the standalone NFP
    window is therefore 100% owned by R4 already; buying it again is buying the same
    risk twice, not adding a cell. The only part of the NFP window R4 does not hold is
    **14:00 → 16:00**.
  * **Decision rule applies to the 14:00–16:00 incremental cell.** The 12:30–16:00
    standalone number is reported for context and is explicitly *not* decision-bearing.
* **Trial accounting**: the incremental cell is the trial. N_TRIALS stays 5. As a
  sensitivity I will additionally report DSR at `n_trials=6` for both NFP rows, in case
  a reader prefers to count the standalone window as a sixth trial. The sensitivity
  cannot rescue a cell, only make it stricter.
* **Expected n**: ~80 NFP dates inside 2020-01-01 … 2026-09-07, ~48 pre-ETF / ~32 post.

### Cell 3 — `WKND_FADE`: weekend gap fade, BTC

* **Gap measurement window**: open of the **Fri 21:00 UTC** minute bar → open of the
  **Sun 22:00 UTC** minute bar. `gap = P_sun2200 / P_fri2100 - 1`.
* **Entry**: open of the Sun 22:00 UTC minute bar (the same bar that ends the gap
  measurement — no look-ahead, the gap is fully known at that price).
* **Direction**: `side = -sign(gap)`. Fade. If `gap == 0.0` exactly, the observation is
  skipped.
* **Exit**: open of the **Mon 22:00 UTC** minute bar. 24 h hold.
* **Free choice 1 frozen — what "the gap" is.** BTC trades 24/7, so there is no literal
  Friday-close-to-Monday-open gap in spot. The only real closure is **CME Bitcoin
  futures**, shut Fri 16:00 ET → Sun 17:00 ET; the "CME gap" folk claim is defined on
  exactly that interval. 16:00 ET / 17:00 ET map to 21:00 / 22:00 UTC in EST. I freeze
  fixed UTC hours (21:00 / 22:00) rather than DST-tracking ET, so that summer
  observations are measured one hour off the true CME bell. Using fixed UTC keeps the
  cell a single rule; DST-tracking would be a second variant and I am not running two.
  This is a stated approximation, listed again in §5.
* **Free choice 2 frozen — no magnitude threshold.** Every weekend is traded, however
  small the gap. A `|gap| ≥ x%` filter is a free parameter, and sweeping x is exactly
  the search this study exists to avoid. n is large enough that a threshold is not
  needed to get a readable statistic.
* **Free choice 3 frozen — 24 h hold.** The claim is that the gap fills when US desks
  return on Monday; one full day covers the Monday session end to end.
* **Expected n**: ~348 weekends.

### Cell 4 — `HIGH52`: BTC after a new 52-week high, LONG

* **Daily bars**: built from `btc_1m` — day open = open of that day's 00:00 bar, day
  close = close of that day's 23:59 bar, UTC days.
* **Signal on day D**: `close(D) > max(close(D-364) … close(D-1))` — a new 364-day
  closing high. 364 = 52 × 7, closes only, prior window strictly excludes D.
* **Entry**: open of the 00:00 UTC bar on **D+1** (the signal is a close, so the first
  tradeable price is the next day's open — no look-ahead).
* **Exit**: open of the 00:00 UTC bar on **D+6**. 5-day / 120 h hold.
* **Direction**: LONG (breakout continuation).
* **Non-overlap rule**: while a trade is open, further 52-week-high signals are
  **ignored**. New trades may only start after the previous one has exited. This is
  required for the t-statistic to mean anything — overlapping 5-day windows during a
  bull leg are the same trade counted five times.
* **Free choice 1 frozen — 5-day hold.** The framing in the task is an *overlay* (a
  tilt on an existing book), so I take the shortest horizon on which a breakout claim
  is normally made — one week — rather than a multi-week position trade. Frozen; no
  sweep over 3/5/10/20 days.
* **Free choice 2 frozen — lookback seeded from `btc_1m` only.** `btc_1m` starts
  2020-01-01, so the 364-day lookback is not complete until 2020-12-30. **Signals are
  only evaluated from 2021-01-01 onward.** I could have seeded the lookback from
  `cd_spot_binance` (which reaches back to 2017-08) and bought back the whole of 2020,
  but that mixes two venues' price series inside one rule. Single source, shorter
  sample, documented. The pre-ETF era for this cell is therefore 2021-01-01 …
  2024-01-10, not the full pre-ETF period.
* **Expected n**: unknown in advance; non-overlap should hold it to a few tens.

### Cell 5 — `EMA_ETH`: weekly EMA(5/21) cross on ETH, LONG/SHORT

* **Algorithm**: exactly the shipped `strategies/support/ema_position.py` algorithm,
  applied to **ETH** instead of BTC. Imported, not reimplemented:
  `strategies.support.ema_position.aggregate_weekly` for the 168 h fixed-size buckets and
  `strategies.support.regime_jplus.ema_calc` for the EMAs. (Nothing under
  `strategies/**` is modified — the module is imported read-only.)

  This module was `strategies/sleeves/ema/math.py` when the study ran. It moved to the
  support layer on 2026-09-13 — the EMA sleeve was archived, but this arithmetic sizes
  the live r4 bot through `jplus_inputs`, so it stayed. The algorithm is unchanged.
* **Bars**: ETH hourly aggregated from `eth_1m` (hour open = first minute's open, hour
  close = last minute's close, high/low from the minutes), then bucketed 168 h.
* **Signal**: EMA(5) vs EMA(21) on weekly **closes**. Cross up ⇒ LONG, cross down ⇒
  SHORT. `long_short` mode: always in the market after the first cross.
* **Entry**: open of the **next** weekly bar after the cross bar (sleeve convention).
* **Exit**: on the opposing cross, at the open of the **next** weekly bar after that
  cross bar — the same bar on which the reversed position is opened.
* **Warmup**: `slow + 1 = 22` weekly bars, per the sleeve.
* **Open trade at the data edge is EXCLUDED** — no exit price exists, so it is not an
  observation. Frozen.
* **Direction**: per signal (both sides traded).
* **Era**: assigned by **entry date**.
* **Free choice frozen — trade-level returns, not daily.** The sleeve emits a daily
  position map; I convert to one observation per closed trade, because the pass rule is
  a t-test on independent observations and daily returns inside a multi-week trend
  position are strongly autocorrelated. Cost is charged once per trade (18 bp round
  trip), not per day.
* **Expected n**: ~25–45 closed trades.

---

## 4. Decision rule — frozen

A cell **PASSES** only if **all three** of the following hold:

| Clause | Condition |
|---|---|
| **C1** | `t_pre_etf ≥ 2.5` (per-trade net returns, entries before 2024-01-11) |
| **C2** | `t_post_etf ≥ 2.5` (per-trade net returns, entries on/after 2024-01-11) |
| **C3** | `DSR(n_trials=5, full pooled sample) ≥ 0.95` |

Anything else is a **KILL for that cell**. There is no "promising, needs more work"
tier and no partial credit. A cell with an era containing fewer than 3 observations has
that era's t-statistic reported as undefined, which fails the clause (it cannot be
`≥ 2.5`), and I will say so explicitly rather than quietly dropping the era.

A PASS is a **recommendation to consider only**. No sleeve, no bot, no config change is
produced by this study under any outcome. The user decides.

**Supporting statistics computed but explicitly NOT decision clauses**: full-sample t,
bootstrap 90% CI on per-trade Sharpe, win rate, cumulative net return, CSCV PBO across
the five cells on a common monthly grid, and the `n_trials=6` DSR sensitivity for NFP.
None of these can flip a KILL to a PASS.

---

## 5. Known data limitations, declared in advance

1. **NFP dates are computed, not published.** All 108 rows in `scheduled_events` are
   first Fridays. Real BLS releases deviate from "first Friday" in known cases: when the
   first Friday falls very early in the month the release can slip to the second Friday,
   and government shutdowns delay releases outright. Inside this sample the relevant
   episode is the **autumn 2025 US federal shutdown**: per public reporting the
   September 2025 employment report was delayed from 2025-10-03 to 2025-11-20, the
   October report was not published on its own, and Oct/Nov were released together in
   mid-December. That makes the first-Fridays **2025-10-03, 2025-11-07 and 2025-12-05**
   near-certainly *non-event* days mislabelled as NFP in the table. I pre-register a
   **sensitivity run** of the incremental NFP cell with those three dates dropped,
   reported alongside the main number. It is a robustness note, **not** a decision
   clause, and the main clause table uses the unmodified table dates. I am flagging that
   my confidence in the exact shutdown dates is from general knowledge, not from a
   source in this repo.
2. **Weekend cell ignores DST.** Fixed 21:00/22:00 UTC is the true CME bell only in
   EST; from March to November it is one hour late.
3. **No funding charged** (see §2).
4. **HIGH52 loses 2020** to the 364-day warmup; its pre-ETF era is 3 years, not 4.
5. **Single venue.** All five cells are Binance prices. A cell that is really an
   exchange-specific microstructure artefact would not be caught here.
6. **`btc_1m`/`eth_1m` had a repair commit** (`cff6726 fix(data): repair mislabeled
   minute candles with verified archives`). I use the tables as they now stand and
   report the skipped-bar tally so any residual holes are visible.

---

## 6. Priors — what I expect before running

Stated so the results can embarrass me.

**Overall prior: I expect all five cells to KILL.** Probability that ≥ 1 of the 5
passes all three clauses: **~10%**.

The reason is structural, not statistical squeamishness: this repo has already measured
BTC intraday and day-of-week calendar effects exhaustively. `r4_study` swept the
weekday × week-of-month × entry-hour × hold-length grid, and **R4 is the single
survivor**; `range_sanity_2026_09` then killed the intraday range playbook outright
(band/Asia/basis/funding-timing/LVN, dead even at zero cost). A genuinely new calendar
cell that clears t ≥ 2.5 in *both* eras would have had to hide from that grid. The
double-era t ≥ 2.5 bar is the hard part — it is roughly "the effect survived the
2022 bear, the 2023 chop, the ETF regime change and the 2024–26 institutional era with
essentially the same magnitude". Very few real effects do that; R4 barely does.

Per cell:

| Cell | P(pass) | Why |
|---|---|---|
| `QTR_END` | **~2%** | n ≈ 26 total, ≈ 10 post-ETF. To get t ≥ 2.5 on n = 10 you need a mean of ~0.8 sd. Even a real turn-of-quarter effect would not show that. The clause is essentially unreachable at this sample size and I expect the honest answer to be "too small to tell", which under the frozen rule is a KILL. I would rather report that than widen the window until n cooperates. |
| `NFP` (incremental 14:00–16:00) | **~5%** | Two hours of post-release drift, three years after the release stopped being news to crypto. Also the residual after R4 already took 04:00–14:00, and R4's own edge is a drift harvest — the 14:00–16:00 tail is the part R4's grid search *rejected*. I expect a small positive mean drowned by 18 bp on a 2 h hold: the cost alone is ~9 bp/hour of holding, brutal for a 2 h window. |
| `WKND_FADE` | **~8%** | Best n of the five (~348), so the statistic will at least be readable. But "the CME gap always fills" is the single most-repeated crypto folk claim, which is exactly the profile of something already arbitraged. I expect a weakly positive raw mean and a negative net mean after 18 bp, possibly with a real pre-ETF effect that has decayed post-ETF. |
| `HIGH52` | **~5%** | I expect a *positive* mean — momentum after breakouts is a real, widely-documented cross-asset effect and BTC 52-week highs cluster in bull legs. But I expect n ≈ 20–40 after non-overlap and t nowhere near 2.5 in both eras, because the pre-ETF era's highs sit in the 2021 blowoff and the post-ETF era's in a different regime. Most likely outcome: right sign, wrong significance. |
| `EMA_ETH` | **~5%** | A weekly 5/21 trend follower. I expect the pre-ETF era (the 2020–21 ETH bull) to look excellent and the post-ETF era to be flat or negative — and with only ~30 trades total, ~10 of them post-ETF, C2 is very unlikely to clear. The repo has already found ETH's regime behaviour to be era-dependent (`project_eth_regime_thesis_validated`), which cuts against era-stability here. |

Secondary prior: **PBO across the five will be high (> 0.5)** simply because none of them
are expected to have real out-of-sample persistence; a high PBO here would be
*consistent* with five nulls, not evidence of overfitting a search I did not run.

If a cell does pass, my first suspicion will be the NFP standalone (which is
contaminated by R4's already-owned window) or a small-n fluke in `QTR_END`, and I will
say so in `findings.md` rather than celebrating.

---

## 7. Files

* `README.md` — this file. Frozen before any outcome number.
* `run_cells.py` — the analysis script. Deterministic, read-only on prod.db.
* `results/` — `cells_summary.csv`, `cells_summary.json`, per-cell trade CSVs,
  `pbo.json`, `nfp_sensitivity.json`.
* `findings.md` — verdict, five-row clause table, honest discussion.
* `build_notebook.py` — writes `calendar_cells.ipynb`; run with
  `C:/Python/Python313/python.exe`.
