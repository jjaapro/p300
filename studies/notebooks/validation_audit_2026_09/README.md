# Track V4 — validation audit of p300's own track record (PRE-REGISTRATION)

*Written and saved 2026-09-08, BEFORE any outcome statistic was computed.*

---

## 0. Scope — this is an AUDIT, not an alpha search

The statistical validation toolkit (`studies/lib/validation/`) was ported on
2026-09-07. This study points it at **p300's own numbers** and asks one
question:

> What do p300's Sharpe ratios look like after deflation, bootstrap
> uncertainty and multiple-testing correction?

Explicit scope rules, fixed now:

1. **`N_TRIALS = 1` for every paper series.** Nothing in the live paper
   ledgers was selected by searching over live paper data. The bots were
   configured from research, then switched on. There is exactly one paper
   realisation per bot, so there is no selection to deflate. Applying a
   multiple-testing penalty to a forward paper record would be wrong.
   (Backtests are different — Part B deflates at the documented grid size.)
2. **There are NO KILL rules.** Nothing is switched off, disabled, resized or
   recommended for disabling as a result of this study. If a number comes out
   ugly, the deliverable is the ugly number, not an action.
3. **The deliverable is a table**, not a decision: one row per series, honest
   Sharpe after each correction, plus a plain-language verdict per row.
4. **No production code is touched.** `strategies/**` and `bots/**` are read
   and imported only. The only file edited outside this folder is
   `PORTFOLIO.md` §9 (Part D), a docs-only correction of two caveats.

### Scope facts already established (not outcomes)

To write the series list below I ran `SELECT ... GROUP BY` counts against
`trades` and `variants`. Those row counts and date spans are *scope facts* —
they define what can be measured. **No Sharpe, drawdown, DSR, bootstrap CI,
alpha or t-statistic has been computed at the time this file is saved.**

---

## 1. Series list

### Part A — paper ledgers (read-only `prod.db`)

`SELECT id, enabled FROM variants WHERE id LIKE 'bot_%'` returns **five** rows,
not six: `bot_adx_v1`, `bot_carry_v1`, `bot_chento_v3_v1`,
`bot_chento_v3_eth`, `bot_short_squeeze_v1`. The sixth bot variant,
**`bot_r4_v1`**, is declared in code (`bots/r4/config.py: VARIANT_ID`) but has
**not been registered in the `variants` table** — the runner registers on first
start and the operator has not started it (per
`memory/project_r4_bot_shipped.md`: "operator must start it"). It is carried in
the table as a code-declared series with n=0, **not dropped**.

| # | series | source | closed trades (scope fact) |
|---|---|---|---|
| A1 | `bot_adx_v1` | trades, variant | 0 closed (1 open) |
| A2 | `bot_carry_v1` | trades, variant | 0 closed (1 open) |
| A3 | `bot_chento_v3_v1` | trades, variant | 6 closed |
| A4 | `bot_chento_v3_eth` | trades, variant | 0 |
| A5 | `bot_short_squeeze_v1` | trades, variant | 0 |
| A6 | `bot_r4_v1` | **code-declared only, unregistered** | 0 |
| A7 | legacy R4 paper — `strategy_variant='p300_aggressive_v2_v1_0'` AND `strategy LIKE 'JPLUS_R4_%'` | trades | 13 closed |
| A8 | legacy variant, all sleeves — `p300_aggressive_v2_v1_0` | trades (context row) | 27 closed |

A7 is the **only R4 paper record that exists**. A8 is included as a context
row so the reader can see the whole paper corpus in one place; it is a
mixed-sleeve aggregate, not a strategy.

### Part B — the two big backtests

| # | series | source | pool |
|---|---|---|---|
| B1 | chento Triple v3 BTC, backward-only | `studies/notebooks/overlay_study/results_backonly/trades_BTC.csv` | OKX-aligned subset, base exits |
| B2 | chento Triple v3 ETH, backward-only | `.../trades_ETH.csv` | OKX-aligned subset, base exits |
| B3 | chento combined BTC+ETH (common window) | both CSVs | as B1+B2 |
| B4 | ADX S-003 baseline (live-2026-05 semantics) | `studies/notebooks/adx_study/harness.py: run()` defaults | 2018-01-01 → today |
| B5 | ADX S-003 Tier-2 (shipped: symmetric short<EMA150 filter + ADX-or-ATR×4 exit) | same harness, `entry_gate` + `exit_mode='adx_or_atr', atr_mult=4.0` | same window |

Shipped-config rows are B1 (BTC keeps `no_tilt` = skip-after-loss), B2 (ETH
keeps half-after-loss per `docs/calibration/chento_triple_v3.md` 2026-08-23),
and B5 (per `docs/calibration/adx.md` 2026-07-22). Un-tilted / baseline rows
are reported alongside so the reader sees what the overlay buys.

---

## 2. Frozen method — exact rules, windows, costs, data sources

### 2.1 Loading (Part A)

`strategies/support/strategy_health.py` exposes `_load_closed_trades(variant_id,
strategy, start, end)` and `trades_daily_returns(variant_id, start, end,
capital_usdt, *, zero_fill=False)`. Both were read before writing this file.
**They do not fit**, for two reasons fixed now:

* Both call `sqlite3.connect(str(db.DASH_DB))` — and `DASH_DB is PROD_DB`. That
  opens the production database **read-write**, which this study's standing
  rules forbid.
* `_load_closed_trades` filters on a single `strategy` string; series A7 needs
  `strategy LIKE 'JPLUS_R4_%'`, and A3/A8 need a variant-level aggregate.

Therefore: **a minimal loader is written in `audit_paper_track.py`** that opens
`file:<PROD_DB>?mode=ro` via URI and issues the *same SQL predicates*
`strategy_health` uses — `status='closed'`, grouping on
`date(actual_exit_time)`, `SUM(pnl_usdt)/capital*100`, calendar zero-fill
between first and last exit date. This is a read-only transcription, not a
different definition. It is recorded as a deviation in `findings.md`.

Capital: `variants.capital_usdt` (10,000 USDT for every bot variant and for the
legacy variant). For the unregistered A6, capital is taken from
`bots/r4/config.py`; it is moot at n=0.

### 2.2 Per-series statistics (Part A)

For each series, from the closed-trade list and the zero-filled daily series:

| statistic | function | convention |
|---|---|---|
| n closed trades, date span | loader | — |
| mean R | **not available** — the `trades` table has no risk-unit column. Reported as `n/a`; mean % of capital is the substitute. | see §4 deviation |
| mean %, win rate | loader | `pnl_usdt / capital × 100` per trade |
| per-trade Sharpe | `metrics.trade_sharpe` / `bootstrap._per_trade_sharpe` | mean/sd of per-trade % of capital |
| daily Sharpe | `metrics.daily_sharpe(r, 365)` | rf = 0, 365 d/yr (repo convention) |
| max drawdown | `metrics.max_drawdown` | on the zero-filled daily series |
| drawdown durations | `dd_duration.equity_curve_from_trades` → `drawdown_durations` → `summarize_drawdowns` | calendar days |
| iid bootstrap CI on per-trade Sharpe | `bootstrap.bootstrap_sharpe(rets, n_iter=10000, seed=42)` | 5/50/95 percentile |
| circular-block bootstrap CI on daily Sharpe | `bootstrap.block_bootstrap_sharpe(daily, block=20, n_iter=5000, seed=42, periods_per_year=365)` | 5/50/95 percentile |
| deflated Sharpe | `dsr_pbo.dsr_from_returns(daily, n_trials=1, periods_per_year=365)` | **N_TRIALS = 1** |
| Fundamental Law required IC | `fundamental_law.breadth_verdict(sharpe_ann, bets_per_year)` | bets/yr = realised closed trades × 365 / span_days |
| flat-max | `flat_max.flat_max_1d` | only where a parameter surface exists on disk — **paper series have no parameter surface, so flat-max is `n/a` for Part A** and is run in Part B only |
| CPCV | `cpcv.cpcv_score(daily, daily_sharpe, n_groups=10, k_test=2, embargo_days=5)` | **ONLY when the daily series has ≥ 200 rows**; otherwise print `insufficient` |

Bootstrap seeds are fixed at 42 so the script is deterministic and re-runnable.

### 2.3 Alpha vs BTC (both parts)

BTC daily closes are built from `cd_futures_ohlcv` (hourly BTC perp,
2019-09 → 2026-09; last close of each UTC day; the still-forming current day is
dropped). Daily BTC return = simple pct change.

Regression, per series, on the overlapping calendar dates of the zero-filled
daily series:

```
r_series[t] = alpha + beta * r_btc[t] + e[t]
```

OLS via `numpy.linalg.lstsq`. Reported: annualised alpha (`alpha_daily × 365`,
in percentage points), beta, R², and the alpha t-statistic using
**Newey-West** standard errors with lag 5 (hand-rolled; no scipy), because
daily crypto returns are autocorrelated and an OLS t would be optimistic. The
two-sided normal approximation is used for significance (no scipy `t` CDF).

Threshold fixed now: **|t_alpha| ≥ 2.0** counts as "alpha distinguishable from
zero"; **R² ≥ 0.20** counts as "materially beta-driven".

### 2.4 The paper-record caveat (Part A)

`memory/project_paper_trade_loss_recovery_2026_06_05.md` records that paper
trades **before 2026-05-16 are not fully trustworthy** (the ledger-coherence +
`unique_key` hardening landed 2026-06-05; the record is trustworthy from
2026-05-16 onward). Series A7 and A8 straddle that date. **Every statistic for
a straddling series is reported twice**: full span, and restricted to
`date(actual_exit_time) >= '2026-05-16'`. Both go in the results CSV and in the
findings table.

`PORTFOLIO.md` §9.12 additionally records that pre-2026-05-13 paper PnL was net
of fees only (no slippage). Rows before that date therefore also carry an
optimistic-cost bias; this is noted, not corrected.

### 2.5 Backtest cost models (Part B) — stated, not re-chosen

* **chento (B1–B3)**: the `r_outcome` column in `trades_{BTC,ETH}.csv` already
  carries the study's cost model — **18 bp round trip scaled by stop distance**
  (`cost_R = 0.0018 × entry / risk`), per
  `memory/project_chento_triple_optimized_config.md`. No further cost is
  applied. R units, so the "return" series is R per trade.
* **ADX (B4–B5)**: `harness.py` charges `COST_BP_RT = 10.0` (5 bp per leg,
  spot-only, **no funding**) inside `close_pos`. `net_pct` is used as-is. The
  live sleeve charges 10 bp fee + 5 bp slippage + funding
  (`strategies/trades.py`), so the harness is optimistic by ~5 bp/trade plus
  the funding stream; noted, not corrected.

Both differ from the research default of 18 bp RT because each source study
documents its own model, which the standing convention says to honour.

### 2.6 Backtest statistics (Part B)

Same statistic set as §2.2 where it applies, plus:

* **Deflated Sharpe at the documented trial count** — `dsr_pbo.dsr_from_returns`
  on the per-trade series, at both a conservative and an aggressive N (§3).
* **Harvey-Liu haircut** — `haircut.haircut_triple(observed_sharpe_ann, n_obs,
  n_trials, freq_per_year)` at the same two trial counts; Bonferroni / Holm /
  BHY all reported, **Holm** taken as the headline (the middle of the three).
* **Flat-max** — `flat_max.flat_max_1d` on the one parameter surface that
  exists on disk without re-running a sweep: for chento, the tilt-policy axis
  reconstructed from `r_outcome`; for ADX, the ATR-multiplier axis, which
  `harness.run()` recomputes cheaply and deterministically from candles
  (`atr_mult ∈ {2.5, 3.0, 3.5, 4.0, 4.5, 5.0}`, chosen = 4.0). Re-running
  `harness.run()` on one axis is a *re-evaluation of an already-documented
  lever*, not a new parameter search, and no threshold is changed as a result.
* **PBO via CSCV** — `dsr_pbo.cscv_pbo(returns_matrix, s=10)`. **Condition
  fixed now:** run it only if per-variant series can be rebuilt from files
  already on disk. `results_backonly/overlay_summary.csv` holds **aggregate
  metrics only** (120 rows: 5 exit × 4 tilt × 2 H-tag × 3 scopes) — no
  per-variant trade series. The 5 wick-exit variants need a bar-by-bar replay
  and the H-tag needs the events table; **re-running either would be a
  parameter sweep and is forbidden here.** The 4 tilt variants ARE pure
  post-processing of the on-disk `r_outcome` column (`tilt_sizes` is a
  deterministic function of the outcome sequence), so PBO is run over the
  **tilt sub-family only (4 variants per asset)** and labelled as partial. Full
  40-variant PBO is **skipped and reported as skipped**.

---

## 3. Trial counts for deflation — where each number comes from

`N_TRIALS = 1` for all of Part A (§0).

For Part B the trial count matters more than any other input, so both a
conservative and an aggressive figure are computed and both are shown.

### chento (B1–B3)

* **Conservative N = 40.** The overlay grid actually documented in
  `studies/notebooks/overlay_study/run_overlays.py:183–201` — `exit_variants`
  (5: base, wick_p0.5, wick_p1, wick_p2, wick_p3) × `tilt` (4: none,
  skip_after_loss, half_after_loss, half_after_2stops_7d) × `htag` (2) = 40
  per scope. Confirmed by `results_backonly/overlay_summary.csv`: 120 data rows
  = 40 × 3 scopes.
* **Aggressive N = 120.** The overlay grid is the *last* search in a chain; the
  trade pool it scores was itself selected. Adding the documented upstream
  searches:
  - 30 — the stop/target grid `for atr_mult in (2,3,4,5,6) × for target_r in
    (2,3,4,5,6,8)` in
    `studies/notebooks/chento_journal/validation_target_sweep_5y.py:64–65`.
  - ~25 — the B1–B13 feature screen (`validation_B*.py`, 25 files on disk in
    `chento_journal/`), of which 3 blocks survived into the composite.
  - 7 — ladder tiers T1 / T2 / T3 / H_A / H_B / H_E / none, tabulated in
    `memory/project_chento_triple_optimized_config.md`.
  - ~18 — TIF variants, OKX z-thresholds (z≥0 / 0.5 / 1.0 / 1.5), regime-filter
    forms (symmetric vs asymmetric), and the no_tilt / no_resist_OB filter
    combinations tabulated in the same memory and in
    `memory/project_chento_regime_filter.md`, `memory/project_tif_72h_optimal.md`,
    `memory/project_cross_exchange_okx_gate.md`.
  - 40 — the overlay grid above.
  Sum ≈ 120. **This is a stated assumption, not a documented count**: the
  chento work never recorded a single "we tried N configurations" number. The
  sum-not-product form is deliberate — the searches were sequential, not joint;
  the product (≈ 2.7 × 10⁵) would be an over-count.

### ADX (B4–B5)

* **Conservative N = 17.** The variants literally on disk in
  `studies/notebooks/adx_study/experiments.py:64–91`: baseline, +short<EMA150,
  +short<EMA200, dir=DI, dir=DI&EMA50, ATR_trail ×2.5/×3.0/×4.0,
  ADX-or-ATR×3, ADX-or-ATR×4, rearm<22, rearm<23, SL=8%, SL=12%, and 3 combos.
* **Aggressive N = 40.** 17 above, plus the funding-crowding-veto z-threshold
  sweep in the 2026-06-27 addendum of `adx_study/findings.md` (the sweep script
  is **not on disk** — only the chosen z > 1.5 is written down), plus the
  earlier calibration rounds recorded in `docs/calibration/adx.md`
  (2026-05-04 asymmetric-filter derivation, 2026-05-01 signal-source switch
  `cd_futures_ohlcv → cd_spot_binance`) and the SL / re-arm probes in
  `memory/project_s003_calibration.md`. **Stated assumption**, same caveat.

The shipped ADX config also includes the funding-crowding LONG veto, which is
**not reproduced here** — the veto sweep is not on disk and re-deriving it
would be new search. B5 audits the Tier-2 configuration, which is the one
`docs/calibration/adx.md` quotes numbers for (n=27, MAR 3.09). Recorded as a
deviation.

---

## 4. Known deviations, declared in advance

1. **Six bot variants, five registered.** `bot_r4_v1` is code-declared and
   unregistered; carried as n=0.
2. **Minimal read-only loader** replaces `strategy_health`'s helpers (§2.1).
3. **Mean R is unavailable for Part A** — the `trades` table stores
   `pnl_usdt` / `pnl_pct` / `size_usdt` but no risk unit. Mean % of capital and
   mean % of notional are reported instead; the R column reads `n/a`.
4. **Flat-max is `n/a` for Part A** — no parameter surface exists for a
   realised paper ledger.
5. **ADX funding veto not reproduced** (§3).
6. **Full 40-variant PBO skipped**; partial 4-variant tilt-family PBO reported
   and labelled (§2.6).

Any further deviation discovered while running is added to `findings.md`
prominently, with the original clause left visible.

---

## 5. Classification clauses — the verdict rules, fixed now

There are no KILL clauses. These clauses assign the **plain-language verdict**
in the honest-Sharpe table, and each one is reported with its measured number
and whether it fired.

| clause | rule | verdict it assigns |
|---|---|---|
| **C0** | Is any paper series' closed-trade count ≥ 20? | If NO for all of A1–A8: "p300's forward paper record cannot support any statistical claim as of 2026-09-08." |
| **C1** | `n_trades < 20` OR `n_daily_rows < 60` | **"too few observations to say"** — this verdict pre-empts C2–C4 for that row. All statistics are still printed. |
| **C2** | `DSR ≥ 0.95` at the row's stated N_TRIALS **AND** bootstrap `sr_p05 > 0` | **"survives deflation"** |
| **C3** | (C1 not fired) AND NOT C2 | **"does not survive deflation"** |
| **C4** | `|t_alpha| < 2.0` **AND** `R² ≥ 0.20` | **"indistinguishable from beta"** — overrides C2/C3 for that row's headline verdict |
| **C5** | Holm-adjusted haircut Sharpe > 0 at the aggressive N | flag: "survives Holm at aggressive N" |
| **C6** | Fundamental-Law required IC > 0.7 | flag: "breadth-implausible — required IC in the suspicious/impossible band" |
| **C7** | (daily rows ≥ 200 only) CPCV `decay_ratio < 0.5` | flag: "degrades out of fold" |
| **C8** | (partial tilt-family only) `PBO > 0.5` | flag: "overfit-prone on the reconstructible axis" |

Where a clause cannot be evaluated the cell reads `unevaluable` and the reason
is named. An audit row can be `unevaluable` without making the whole study
INCONCLUSIVE — the study is INCONCLUSIVE only if the audit itself cannot be
carried out.

---

## 6. Priors — what I expect the honest numbers to look like

Stated now so the reader can score my calibration afterwards.

**Part A (paper).** I expect the audit's headline to be *negative about the
evidence, not about the strategies*: with 6 closed trades on the largest live
bot series and 13 on the legacy R4 record, **C1 will fire on every single Part-A
row** and C0 will answer NO. Specifically:

* A3 (`bot_chento_v3_v1`, 6 trades): iid bootstrap 5–95 CI on the per-trade
  Sharpe will span zero by a wide margin. DSR at N=1 will land somewhere in
  0.5–0.9 and will be meaningless at n=6 regardless of value. Daily-series
  rows ≈ 3 (2026-08-22 → 08-24) — the block bootstrap and CPCV will both be
  unusable; I expect to print `insufficient` for CPCV on every Part-A row.
* A7 (legacy R4, 13 trades over ~5 weeks): I expect a *positive* mean (R4 was
  a positive-expectancy sleeve in research) but a bootstrap CI that includes
  zero, and an alpha t-stat under 2. Splitting at 2026-05-16 will leave ~4–6
  trades on the trustworthy side — I expect that split to be the single most
  damning number in Part A.
* Alpha vs BTC on Part A: R² will be low simply because the daily series are
  mostly zeros (flat days), not because the sleeves are market-neutral. I
  expect C4 to *not* fire on Part A rows, and I expect that to be an artifact
  of sparsity rather than evidence of neutrality. I will say so.
* Fundamental Law: with 13 trades in 5 weeks the annualised bets/yr is
  extrapolated from a tiny window and the required IC will look absurd in one
  direction or the other. Low confidence in that column for Part A.

**Part B (backtests).**

* B1 (chento BTC backward-only, 101 OKX-aligned trades, +0.80 R mean): the
  per-trade t-stat is roughly `mean/sd × √101`. With mean R +0.80 and an R
  distribution whose sd is plausibly ~2.2 (a 6R right tail against −1R stops),
  I expect a per-trade Sharpe near 0.35, t ≈ 3.5, and **DSR at N=40 to survive
  (> 0.95) but DSR at N=120 to be marginal — I'd put it near 0.90–0.97**. That
  marginality is the honest headline for chento, and it is exactly what the
  study's own "production ceiling 50–70% of research R" caveat predicts.
* Harvey-Liu Holm at N=120 I expect to cut the annualised Sharpe by roughly
  40–60%, leaving it positive but modest.
* B3 (combined) should be the strongest row — the overlay study's central claim
  is that BTC+ETH diversify — so I expect combined DSR to beat both legs.
* B4/B5 (ADX): 34 / 27 trades over 8.5 years is very low breadth. I expect the
  annualised daily Sharpe to come out far below the harness's headline "Sharpe
  2.09" (which is a per-trade t-statistic, `mean/pstdev × √n`, not an
  annualised Sharpe — I expect to have to say so explicitly). I expect
  **ADX to be the row where C4 fires**: it is a long-biased BTC trend-follower
  over the largest bull market in the asset's history, so beta should explain a
  lot, R² should clear 0.20, and the alpha t-stat should land between 1 and 2.
  My prior is genuinely uncertain here — a 50/50 between "indistinguishable
  from beta" and "alpha survives at t ≈ 2.2".
* Flat-max: chento's tilt axis I expect FLAT-to-OK (the overlay study found the
  ordering stable across two pools). ADX's ATR-multiplier axis I expect OK or
  MARGINAL, not SHARP_PEAK, since the study reported ×2.5/×3/×4 all improving
  drawdown.
* Partial PBO on the 4-variant tilt family: with only 4 columns CSCV is weak;
  I expect a PBO around 0.2–0.5 with wide uncertainty and will label it as
  approximately uninformative at that width.

**Overall prior on the audit's conclusion:** p300's *research* survives
deflation in the chento family and probably not cleanly in ADX; p300's *paper
forward record* is, as of today, statistically empty. I expect the honest
one-line summary to be: "the backtests are the evidence; the paper ledgers are
not yet evidence of anything."

---

## 7. Files

| file | role |
|---|---|
| `README.md` | this pre-registration |
| `audit_paper_track.py` | Part A — paper ledgers, read-only `prod.db` |
| `audit_backtests.py` | Part B — chento backward-only + ADX harness |
| `results/` | every CSV/JSON that a number in `findings.md` comes from |
| `findings.md` | the honest-Sharpe table + clause table + discussion |
| `build_notebook.py` | writes the viewer notebook (run with `C:/Python/Python313/python.exe`) |
