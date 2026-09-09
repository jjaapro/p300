# S-Coinbase-premium — PRE-REGISTRATION

**Written and frozen 2026-09-09, before any outcome number was computed.**
Nothing below is edited after results exist. Deviations, if any, are recorded in
`findings.md` with the original clause left visible here.

---

## 1. Hypothesis

The Coinbase premium — the price of an asset on Coinbase (a US retail and
institutional venue, quoted in USD) relative to Binance (offshore, quoted in
USDT) — is the classic proxy for US demand. A persistently positive premium is
read as US buying pressure. The trend-continuation form of the claim is:

> When the premium reaches an unusually high level relative to its own recent
> history, US demand is arriving, and the asset continues higher over the next
> two days.

That is the hypothesis under test. It is a **trend-confirmation** reading, not a
contrarian one.

## 2. Provenance — and why ETH is the only real test

This rule is a **carry-over from the predecessor repo** (`C:/Source/Repos/trader`).
The relevant artefacts, read while writing this pre-registration:

* `trader/probes/diagnostic_coinbase_premium_zscore.py` — a probe dated
  2026-04-21 that ran **two hypotheses** (contrarian-reclaim and
  trend-confirmation) over a grid of `thr ∈ {1.0, 1.5, 2.0} × hold ∈ {24, 48} ×
  lookback ∈ {168, 336}` = 12 configs each, **24 configs in total**, on BTC
  only, at 10 bp round trip.
* `trader/data/experiments/coinbase_premium_zscore/results_20260421_140646.json`
  — its recorded output. The `trend` hypothesis's **winner** was
  `[thr=2.0, hold=48, lookback=336]`, i.e. **exactly the rule this study is
  asked to test**.

So on BTC this is not an independent test of anything. It is a re-run of the
single best cell of a 24-cell search, on the same venue pair, over a data span
that almost entirely overlaps the search span. **N_TRIALS for BTC is 24, not 1.**
The task brief said "N_TRIALS = 1 at best and arguably contaminated"; the
predecessor's own source file lets us count the budget exactly, and 24 is the
honest number. BTC evidence here is therefore **confirmatory at best and
selection-biased at worst**, and the verdict must not lean on it.

**ETH has never been tested on this rule anywhere.** The predecessor's probe
says so in its own header comment ("BTC-only — no multi-asset bar here because
CB doesn't have ETH at same quality"), and no ETH premium backtest exists in
either repo. The Coinbase ETH-USD hourly series now in `coinbase_spot_1h` is the
first time this rule can be evaluated on a second asset.

> **ETH IS THE GENUINE OUT-OF-SAMPLE TEST. The verdict is weighted on ETH.**
> BTC is reported for completeness and for the era-decay clause, and is treated
> as contaminated evidence throughout.

## 3. Data

Read-only from `data/databases/prod.db`
(`sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)`).

Verified before this file was written:

| table | rows | span (UTC) |
|---|---|---|
| `coinbase_spot_1h` | 117,220 (BTC 58,610 / ETH 58,610) | 2020-01-01 00:00 → 2026-09-08 17:00 |
| `cd_spot_binance` | 79,445 | 2017-08-17 04:00 → 2026-09-09 08:00 |
| `eth_1m` | 3,516,082 | 2020-01-01 00:00 → 2026-09-09 08:07 |

**Venue legs.**

* Coinbase leg, both assets: `coinbase_spot_1h` (`BTC-USD`, `ETH-USD`,
  Coinbase Exchange, hourly, epoch-second hour-open timestamps).
* Binance leg, BTC: `cd_spot_binance` — BTCUSDT **spot** 1h. This is the table
  the task named.
* Binance leg, ETH: **`cd_spot_binance` has no ETH counterpart in `prod.db`.**
  There is no `cd_spot_binance_eth`. The only Binance ETH **spot** series in the
  database is `eth_1m` (ETHUSDT spot 1-minute klines, per
  `data/sources/binance.py` line 5). It is therefore aggregated to the hourly
  grid here: `open` = first minute's open, `high` = max, `low` = min,
  `close` = last minute's close, bucketed by `open_time // 3600000`. This is
  pre-registered now, before results. It keeps the study **spot-against-spot**,
  which is the constraint the task actually cares about; the alternative
  (`cd_futures_eth_15m`, `bybit_perp_eth_1h`, `okx_perp_eth_1h`) would mix spot
  with perp and is refused. Coverage check run before writing this file:
  58,610 hourly ETH bars, exactly matching Coinbase ETH's 58,610; only 15 hours
  out of 58,610 contain fewer than 60 minute bars. An hour is kept if it has
  ≥ 1 minute bar; the count of short hours is written to `results/`.

**Known data limitation, recorded up front:** `coinbase_spot_1h` has 6 gap
windows per asset (16 missing hours) registered in `data/known_unfillable.json`
as genuine Coinbase venue outages — 2020-01-30 16:00, 2020-09-04 22:00,
2020-10-20 19:00, 2023-03-04 17:00 (3h), 2025-10-25 15:00 (5h), 2026-05-08 01:00
(5h). Missing hours are simply absent from the inner join; no interpolation.

**Quote-currency confound, recorded up front:** Coinbase quotes USD, Binance
quotes USDT. The "premium" therefore contains the USDT/USD basis as well as the
venue demand imbalance. This is true of every published Coinbase Premium Index
and is not corrected for here, but it means a USDT depeg or a stablecoin funding
squeeze registers as a premium spike with no US-demand content at all.

## 4. Frozen rules — every threshold

| item | value |
|---|---|
| grid | hourly, inner join on `timestamp` (both legs hour-open, epoch seconds) |
| premium | `(coinbase_close − binance_close) / binance_close` |
| z-score | rolling, lookback **336 hours (14 days)**, computed from the **336 bars strictly before** the current bar (mean and population sd of `premium[i−336 … i−1]`), then `z[i] = (premium[i] − mean) / sd`. No lookahead. `sd ≤ 0` → `z = 0`. |
| fire | `z[i] ≥ +2.0` (a **level**, not a crossing) |
| cooldown | **one fire per 48 hours** — a new fire is only accepted if the previous entry was ≥ 48 bars earlier, so trades never overlap |
| side | long only |
| entry | close of the firing bar, on the **Binance** leg |
| ATR | Wilder RMA, length **14**, on Binance hourly high/low/close, seeded as the mean of the first 14 true ranges; the value at the firing bar (uses bars up to and including the fire, no future data) |
| stop | `entry − 2.0 × ATR14`. A trade is skipped if `stop ≥ entry` or `ATR = 0`. |
| exit | first of: an hourly **low ≤ stop** at any bar after entry → exit filled **at the stop price**; or the close of the bar 48 hours after entry (`bars_held = 48`) |
| warm-up | trading starts at bar index `336 + 14 + 5` |
| cost | **18 bp round trip**, the p300 research convention, charged as a return deduction. (The predecessor used 10 bp; this study is 8 bp stricter.) |
| R unit | `R = 2 × ATR14` at entry — the stop distance. `trade_R = (exit_price − entry) / (2 × ATR14) − (0.0018 × entry) / (2 × ATR14)`. A stop-out is therefore −1R minus the cost in R. Percent returns are reported alongside. |
| period | full overlap: 2020-01-01 → 2026-09-08 (Coinbase-limited) |

The rule is run **twice and only twice**: once on BTC, once on ETH. No grid, no
variants, no re-tuning.

## 5. Decision clauses — KILL rule

Stated as clauses that map to numbers. Both are KILL clauses; **either one
firing kills the strategy.**

| # | clause | KILL when |
|---|---|---|
| **C1** | ETH bootstrap CI on mean R | the 95% two-sided percentile bootstrap CI on ETH mean R **includes zero or lies entirely below zero** (i.e. `ci_lo ≤ 0`) |
| **C2** | BTC post-2024 persistence | BTC trades with entry timestamp on or after **2025-01-01** have **mean R ≤ 0** with **n ≥ 20** fires |

Bootstrap specification, frozen: iid percentile bootstrap of the mean of the
per-trade R series, **10,000 resamples, seed 42**, 95% two-sided
(2.5th / 97.5th percentile) as the **primary** interval. The 90% interval
(5th / 95th) is reported as a secondary number but does **not** decide C1.

If C2 has fewer than 20 BTC fires from 2025-01-01 onward, C2 is **unevaluable**;
per the honesty rules that alone makes the verdict INCONCLUSIVE on C2, and the
named clause is reported as such. C1 is evaluable at any n ≥ 2.

**Verdict mapping**

* Either C1 or C2 fires → `CONCLUDED KILL per pre-registration`.
* Neither fires → `CONCLUDED BUILD per pre-registration` **only if** ETH also
  clears the supporting evidence in §6 (deflated Sharpe at N_TRIALS = 1 above
  0.95). If ETH survives C1 but its DSR is weak, the verdict is
  `CONCLUDED KILL` — an ETH result that cannot survive its own single-trial
  deflation is not a strategy.
* A clause that cannot be computed → `INCONCLUSIVE`, naming the clause.

## 6. Deflation / trial accounting

| leg | N_TRIALS | justification |
|---|---|---|
| ETH | **1** | never tested on this rule in either repo; one frozen rule, run once |
| ETH (conservative) | **2** | this study runs the same frozen rule on 2 assets; reported as a robustness number, does not decide anything |
| BTC | **24** | the predecessor's own declared budget: 2 hypotheses × 3 thresholds × 2 holds × 2 lookbacks. This re-run is the 25th evaluation; 24 is used as the pre-registered figure because it is the number the source file declares. |

Deflated Sharpe via `studies.lib.validation.dsr_pbo.dsr_from_returns` on the
per-trade R series, in per-observation Sharpe units. Bootstrap CIs on the Sharpe
via `studies.lib.validation.bootstrap.bootstrap_sharpe` (its own 90% p05/p95
convention) alongside the hand-rolled mean-R bootstrap that decides C1. Era
split via `studies.lib.validation.metrics.era_split` at the BTC spot-ETF
approval, **2024-01-11**.

## 7. Descriptive outputs required regardless of verdict

1. Premium **mean and standard deviation by calendar year**, both assets, in bp.
2. **Decay test**: pre- vs post-2024-01-11 (spot-ETF era) premium level,
   dispersion, and fire count. The ETF era moved US spot demand into a wrapper
   that does not print on Coinbase's order book, so the prior is that the signal
   decays.
3. **Monotonicity**: Pearson and Spearman correlation between the premium
   z-score and the forward 48-hour return, on **every** bar, with no threshold —
   plus a z-decile table of mean forward 48h return.
   *A signal that only works at the +2 cut but has no monotone relationship
   across the z-range is a red flag and is reported as one.*

## 8. Priors (stated before computing)

* P(C2 fires — BTC 2025+ mean R ≤ 0 with ≥ 20 fires) ≈ **0.8**. The
  predecessor's own recorded per-year table already shows 2025 mean return
  −0.10%/trade (n=51) and 2026 −0.17%/trade (n=15) at 10 bp; at 18 bp both get
  worse. I read those numbers while writing this file and disclose it here
  rather than pretending to a blind prior. They do not change any rule above.
* P(C1 fires — ETH CI includes zero) ≈ **0.7**. ETH has no US-institutional
  bid of the Coinbase-BTC kind, its Coinbase share is smaller, and the
  premium there is more of a stablecoin-basis artefact.
* P(overall KILL) ≈ **0.85**.
* Even in the surviving branch the honest ceiling is low: the predecessor's own
  full-sample per-trade Sharpe was 0.12 at 10 bp with a 29% max drawdown, and it
  failed its own DSR check (0.940 vs a 0.95 bar) at 12 trials.

## 9. Files

* `analysis.py` — the whole study, deterministic, read-only on `prod.db`.
* `results/` — every CSV/JSON that `findings.md` quotes.
* `findings.md` — verdict, clause table, honest discussion.
* `build_notebook.py` — viewer notebook, run with `C:/Python/Python313/python.exe`.

---

## 10. APPENDIX — added AFTER the run (provenance only, no rule changed)

**This appendix was appended on 2026-09-09 after the analysis had been run.
It is disclosure, not tuning. Nothing in sections 1–9 above was edited after any
outcome number existed: no hypothesis, rule, threshold, clause, trial count or
prior was altered.**

This study folder was **not empty** when I was told to create it. It already held
a complete, independent run of this same frozen rule dated 2026-09-08 ("Run A":
`premium_study.py`, `descriptive_addendum.py`, its own `findings.md`,
`build_notebook.py` and a populated `results/`). I did not check for prior
contents before writing, and **my write of this `README.md` overwrote Run A's
pre-registration file.** The folder is untracked in git, so there is no committed
copy and **Run A's README is not recoverable.**

Everything else of Run A's was preserved before I wrote anything further:

* `findings_run_A_2026_09_08.md`
* `build_notebook_run_A_2026_09_08.py`
* `coinbase_premium_run_A_2026_09_08.ipynb`
* `premium_study.py`, `descriptive_addendum.py` and all of Run A's
  `results/*.json` / `results/*.csv` (no filename collisions).

Run A's surviving `findings.md` quotes its own clauses verbatim, and they are
substantively identical to the clauses in §5 above (its C1 and C2 match; it
carried one extra evaluability clause, C0: "either asset produces < 2 fires").

The two runs agree bit-for-bit on BTC and on the deciding clause C2, and differ
on ETH by 8 hourly bars out of 58,579 — enough to flip clause C1. That
reconciliation is §2.1 of `findings.md` and is the study's main methodological
caveat.
