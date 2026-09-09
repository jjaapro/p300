# S-SqueezeBull — out-of-sample re-validation (pre-registered 2026-09-06, before any data run)

**Tag: AUDIT / re-validation. N_TRIALS = 1 — the rules below are frozen copies of
the June-2026 studies; nothing is tuned, swept or re-selected in this study.**

## Question

Two ingredients of the unbuilt "Squeeze Bull" sleeve were validated in June 2026
(memory `project_oi_flush_findings`, `project_funding_cvd_divergence_findings`)
on data that ended 2026-04-13 (funding) / 2026-06-05 (OI). Do they survive the
first genuinely unseen window — **2026-04-14 → the latest full UTC day** — and
does the full-sample combined portfolio still clear MAR ≥ 1.5?

## Frozen rules (imported from the June scripts, not re-implemented)

**Rule 1 — OI flush long** (`studies/notebooks/oi_flush/phase2_backtest.py`, threshold
per `threshold_ablation.py`):

- data: `cd_open_interest.oi_close` (hourly) inner-joined on timestamp to
  `cd_futures_ohlcv` (BTC perp, hourly);
- trigger: `oi_chg_4h = oi_close.pct_change(4 rows) <= -0.02` AND
  `px_chg_4h = close.pct_change(4 rows) <= -0.005`; a kept fire silences the next
  24 rows (`identify_long_flush_events`);
- trade: long at the trigger bar's close; stop 2 % below, target 3 % above; TIF 48
  bars; within a bar the stop is checked before the target; cost 18 bp converted
  to R at entry (`replay`, `COST_BP = 18`);
- regime: `ret_30d` = daily-close `pct_change(30)` forward-filled onto the hourly
  grid (`load_data`); **bull-gated = fires with `ret_30d > +0.10`**, the only
  variant with decision weight (June: pooled is a coin flip).

**Rule 2 — funding + CVD divergence long** (`studies/notebooks/funding_cvd_divergence/research.py`
with `phase2_robustness.WINNING`):

- data: `cd_futures_15m` (`quote_volume_buy - quote_volume_sell` per bar = CVD;
  OHLC for ATR/replay) and `cd_funding_rate.fr_close` forward-filled onto the 15m
  grid;
- features: 14-day window = 1344 bars, `cvd_z` and `funding_z` = (x − rolling
  mean) / rolling std with `min_periods = 336`; ATR(14) Wilder;
- trigger: `funding_z < -2.0` AND rolling-4-bar min of `cvd_z > +0.5`; cooldown 96
  bars (24 h); long only;
- trade: stop = entry − 5×ATR, target = entry + 6R, TIF 288 bars (72 h), 18 bp cost
  (`generate_triggers`, `replay_trigger`).

Both rules are called through the June functions; the only local code is the
read-only DB loader (`file:…?mode=ro`, byte-for-byte the June SQL), windowing,
and reporting.

## Windows (all UTC)

| name | span | used for |
|---|---|---|
| June/parity window, OI | 2022-01-30 → 2026-04-13 23:59:59 | parity P3 |
| June/parity window, fCVD | 2020-01-01 → 2026-04-13 00:00 (the June `--end`) | parity P4 |
| **OOS window** | fires with `2026-04-14 00:00 <= ts <= end of the last full UTC day` in `cd_futures_ohlcv` at run time (recorded in `results/summary.json`) | decision clause (a); informational tables |
| full sample | 2022-01-30 → last full day | decision clause (b) |

Features are computed on the full series (they need trailing history); only the
*fires* are windowed. A fire is **resolved** if its full TIF (48 h / 72 h) is
covered by the data; unresolved fires (mark-to-market at the last bar) are
listed but excluded from every decision statistic.

## Parity check (mandatory; runs first; goes in findings.md)

| id | what | June reference | pass condition |
|---|---|---|---|
| P1 | OI flush bull-gated, window = phase3a overlap (first OI fire → last June fCVD fire 2026-02-06 17:30) | n = 104, mean R +0.280, MAR 2.18, +8.10 R/yr | n equal, mean R within ±0.005 |
| P2 | OI flush, bars ≤ 2026-06-05 19:01 (the ablation's `generated_at`) | `fixed_020` bull n = 112, mean R +0.285, MAR 2.05, maxDD −3.72; June-internal OOS (≥ 2025-01-01) n = 17, +0.562; pooled n = 416 | n equal, mean R within ±0.005 |
| P3 | OI flush bull-gated, 2022-01-30 → 2026-04-13 (the caller's window) | n = 104 ± 3, mean R +0.28 ± 0.05 | within tolerance, OR the excess is exactly the fires dated 2026-02-06 17:30 → 2026-04-13 (listed) |
| P4 | fCVD, 2020-01-01 → 2026-04-13, June code path | the 21-fire ledger in `funding_cvd_phase2_robustness.json` | same 21 timestamps, each R within 1e-6 |

If P1, P2 or P4 fail and the delta is not fully explained by a documented data
change (OI source migration 2026-06-19; funding cadence change 2026-04-13;
pandas/numpy version), the verdict is **INCONCLUSIVE (parity)** and no OOS
number is decision-bearing. If the migrated OI table changes the numbers, the
delta is quantified per fire and explained (columns, cadence, source).

## Decision rule (fixed now)

- **BUILD** iff (a) OOS bull-gated OI-flush mean R ≥ +0.10 with n ≥ 10 resolved
  fires, AND (b) the full-sample combined portfolio (bull-gated OI flush + fCVD,
  2022-01-30 → last full day) has MAR ≥ 1.5.
- **KILL** iff OOS bull-gated OI-flush mean R ≤ 0 with n ≥ 10 resolved fires.
- **INCONCLUSIVE** iff OOS bull-gated n < 10 — then report the numbers and the
  date by which n ≥ 10 is expected at the historical firing rate (two
  projections: the unconditional June rate of bull-gated fires per calendar
  year, and the rate per bull-regime day, which is the honest one when the
  window has not been in a bull regime).
- Any other outcome (n ≥ 10 with 0 < mean R < +0.10; or (a) met but (b) failed)
  is reported as **INCONCLUSIVE** with the failing clause named. Verdict labels
  are exactly BUILD | KILL | INCONCLUSIVE.

Combined-portfolio construction for (b): the phase3a recipe — bull-gated OI
fires and fCVD fires concatenated and sorted by `ts`, cumulative R, maxDD, annual
R = sum R / span years, MAR = annual R / |maxDD| — with one stricter change:
span runs from the first combined fire to the **end of the full-sample window**
(the last full day), not to the last fire, so a sleeve going quiet cannot shorten
the denominator. The June-formula number (span to the last fire, window cut at
`min(last fires)`) is reported alongside for direct comparison with June's 1.77.

The fCVD ledger entering (b) is the **June code path on the table as stored**
(handling A below), because its pre-cutover part is byte-identical to the June
ledger and the MAR comparison stays apples-to-apples.

## Funding cadence handling (pre-registered)

`cd_funding_rate` switched on 2026-04-13 00:00 from hourly CoinDesk *predicted*
rates to 8 h Binance *settlement* rates. Two handlings, both with the frozen
thresholds:

- **A — as stored** (June code path): whatever rows exist are forward-filled
  onto the 15m grid. Post-cutover the series is an 8 h step function; the 14-day
  z-score keeps its meaning ("current funding vs its trailing two weeks").
  Fires inside **2026-04-13 → 2026-04-27** (rolling window straddles the cutover)
  are flagged `cutover_mixed`.
- **B — 8 h-consistent**: the whole history is put on the settlement grid first
  (pre-cutover: the hourly row stamped 00:00 / 08:00 / 16:00 UTC; post-cutover:
  the stored settlements), then forward-filled onto 15m as in A.

A is the primary series for parity and clause (b); B is reported for every
table as a sensitivity. OOS fCVD numbers carry **no decision weight** either
way.

## Informational outputs (no decision weight)

- OOS fCVD fires and R (A and B); OOS pooled OI fires per regime.
- Pre-ETF vs post-ETF split at 2024-01-11 00:00 UTC (spot-ETF approval
  2024-01-10), both rules, full sample.
- Per-year table: OI pooled, OI bull-gated, fCVD.
- OOS regime distribution: share of hours with `ret_30d > +0.10`, `< −0.10`,
  between; if the bull share is 0 the bull gate makes n = 0 and the report says
  so explicitly.
- Sensitivities: regime gate backward-only (daily `ret_30d` shifted one day,
  so the day-D value uses the close of D−1 — the June construction lets the
  hourly bars of day D see the close of day D); OI fires whose 4-row window spans
  more than 4 wall-clock hours (missing hourly rows).

## Data-migration caveats to verify and report

- `cd_open_interest`: CoinDesk rows through 2026-06-10 08:00, native Binance rows
  since (migration 2026-06-19). Same column `oi_close`; Binance rows are point
  snapshots (`oi_open = oi_high = oi_low = oi_close`); values agree < 0.1 % at
  the seam per the migration note. The rule uses only a 4-row percent change of
  `oi_close`, so it is source-agnostic; the seam delta, missing hours, and
  duplicate timestamps in the OOS window are measured and reported.
- `cd_funding_rate`: see handlings A / B.
- Environment: the venv now runs pandas 3.0.2 / numpy 2.4.4; the parity check
  covers version drift.

## Files and re-run order (all from the repo root with `venv\Scripts\python`)

1. `squeeze_bull_lib.py` — read-only loaders, imports of the frozen rule
   functions, ledger builders, metrics.
2. `run_parity.py` → `results/parity.json`, `results/parity_*.csv`
3. `run_oos.py` → `results/oos_oi_flush_ledger.csv`, `results/oos_funding_cvd_ledger.csv`,
   `results/oos_regime.json`, `results/per_year.csv`, `results/oos.json`
4. `run_combined.py` → `results/combined_ledger.csv`, `results/summary.json` (verdict)
5. `build_notebook.py` (system `C:\Python\Python313\python.exe`, has nbformat) →
   `squeeze_bull_revalidation.ipynb`
6. `findings.md` — written after the runs. This README is not edited after the
   first script runs.

Nothing in `strategies/`, `bots/` or `data/` is touched; prod.db is opened
`mode=ro` only; nothing is committed.
