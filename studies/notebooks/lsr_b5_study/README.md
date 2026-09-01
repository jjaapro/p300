# LSR B5 study — pre-registered design (2026-09-01, before any data)

**Question.** Does chento_triple_v3's B5 gate (Binance global long/short *account*
ratio: `long_pct` vs its rolling p10 / p90 over 30 daily rows) earn its place in
BOTH directions at chento's 72h horizon, and does the window length matter?

**Why now.** A 2026-09-01 quick check (memory `project_lsr_contrarian_quick_check`)
found the ratio's rolling-1y percentile carries a contrarian tilt on 20-day
forward returns: bottom decile (crowd short) +4.9 %/20d hit 62 %, top decile
−2.4 %; the crowd-SHORT extreme is robust in both halves and on ETH, the
crowd-LONG extreme flipped sign in 2024–26. The B5 *code* is contrarian and
consistent with that (`math.b5_fires`: `< p10 → LONG`, `> p90 → SHORT`); the
config comments were inverted and have been fixed (commit `c582e33`). What is
NOT settled: (1) whether the short leg contributes anything — live has fired
`triple_short_anchor` **0 times** since 2026-07-21; (2) the window (30d vs
longer); (3) the **stamp semantics** of the daily LSR rows, which decide whether
every LSR backtest (B5, CPR, regime-CB) peeks up to 24h ahead. The lookahead
audit listed "B5 percentiles & rolling-days" as robust *without* a backward-only
re-score.

## Priors (stated before running)

- B5 alone is negative (−0.204R post-cost, NOTEBOOK_2_SUMMARY.md); its value is
  as a positioning filter inside B1∩B5∩B7, not a timer. Removing B5 from the
  short side should ADD lower-quality B1∩B7 shorts, so "long-only B5" is
  expected worse-or-equal on shorts, not better.
- The quick-check tilt lives at a 20d horizon; chento's TIF is 72h, where the
  same check showed nothing at 1d and little at 5d. Expected: 90d / 365d windows
  do NOT improve chento (horizon mismatch) and arm less often (n drops).
- Expected short-leg finding: small n, ≈0R — "insufficient evidence" is the
  likely verdict, not "remove the short leg".
- Live evidence (3 long fires, 0 short) is too thin to use for anything.

## Test 0 — LSR stamp semantics (runs first; fixes the alignment for every later test)

Data: `ca_long_short_ratio` is written two ways — Coinalyze daily backfill
(2021-01-01→) and the live feed's Binance `globalLongShortAccountRatio period=1d`
refresh, which `INSERT OR REPLACE`s the trailing ~30 rows every 60 s. If the
value stored under stamp D is the *end-of-day* snapshot, a backtest that uses
it from D 00:00 (the sleeve forward-fills from the stamp) sees the future.

Procedure (read-only GETs; nothing written to prod.db):
1. Binance `globalLongShortAccountRatio` BTCUSDT for `period=1d` (limit 30),
   `period=1h` (limit 500 ≈ 20 days) and `period=5m` (limit 500 ≈ 42 h), via
   `data.sources.binance._get` + `FAPI_DATA` (never `fetch_long_short_ratio`).
2. Stored rows for the same span via a `mode=ro` connection.
3. Coinalyze `long-short-ratio-history` interval=daily for the same 40 days via
   `fetch_coinalyze._api_get` (key from `.env`; never `fetch_lsr`).
4. For every day D with full hourly coverage compute
   `a = |v1d(D) − v1h(D 00:00)|`, `b = |v1d(D) − v1h(D 23:00)|`,
   `c = |v1d(D) − mean(v1h over D)|`, `d = |v1d(D) − v1h(D+1 00:00)|`
   (the first hourly row of the next day = the period-close snapshot).

Decision rule (fixed now):
- `a` smallest on ≥ 80 % of days → **PERIOD-START**: the value under stamp D is
  known at D 00:00; the sleeve's forward-fill is causal; study `SHIFT_DAYS = 0`.
- `b`, `c` or `d` smallest on ≥ 80 % of days → **PERIOD-END / aggregate**: known
  only at D+1 00:00; study `SHIFT_DAYS = +1` (the stamp-D value becomes usable
  from D+1 00:00).
- otherwise **AMBIGUOUS** → Test 1 runs under both shifts; `SHIFT_DAYS = +1` is
  the primary table.
- Forming-row check: if today's stored row equals the latest 5m value rather
  than the 00:00 value, the newest row is live/forming → also implies `+1` for
  backtests.
- Coinalyze cross-check: correlation and mean |diff| of `coinalyze(D)` against
  `stored(D)`, `stored(D−1)`, `stored(D+1)`; a 1-day offset means the backfilled
  history and the Binance-refreshed rows sit on different conventions.

Consequence for CPR and the regime J+ circuit breaker (same rows): **reported,
not fixed, in this study.**

## Test 1 — pre-registered ablation on the backward-only Triple pool (BTC, then ETH)

Pool: the `studies/notebooks/overlay_study/gen_trades.py` pipeline with
`intersect_backward` ([−24h, 0], the production window), replay 5×ATR stop /
6R fixed target / 72h TIF, `no_resist_OB_within_2R`, then the production filter
stack applied at scoring: OKX alignment (z ≥ 0 long / z ≤ 0 short), skip shorts
when the 30d return > +10 %, tilt = skip-after-loss (BTC) / half-after-loss
(ETH). **Parity gate:** variant V0 must reproduce
`overlay_study/results_backonly/trades_{BTC,ETH}.csv` on `ts ≤ 2026-07-20`
(later rows may differ: Binance has since overwritten the trailing 30d of LSR
and the tables extended). If parity fails the study stops until explained.

Variants — these six and no others:

| id | B5 window | B5 required for | shorts come from |
|---|---|---|---|
| V0 sym30 (current) | 30 rows | long and short | B1∩B5∩B7 |
| V1 long30 | 30 rows | long only | B1∩B7 |
| V2 noB5 | — | neither | B1∩B7 (both sides) |
| V3 sym90 | 90 rows | long and short | B1∩B5∩B7 |
| V4 sym365 | 365 rows | long and short | B1∩B5∩B7 |
| V5 long365 | 365 rows | long only | B1∩B7 |

Metrics per variant × asset: n, n_long / n_short, mean R (all / long / short),
total R, max drawdown (R), MAR, win rate, and IS (≤ 2024-12-31) vs OOS
(2025-01-01 →) mean R and n (`run_overlays.IS_END`). Primary table uses the
production tilt; a secondary table uses tilt = none (pool level, no sequence
effect).

**Success criterion (adoption proposal, decided now):** a variant may be
proposed for production only if, on BOTH assets, OOS mean R ≥ V0 + 0.10R AND
MAR ≥ 1.1 × V0's MAR AND n_OOS ≥ 20 per asset AND IS mean R ≥ V0 − 0.05R.
**KILL** otherwise: keep the current config; this study then produces
documentation only.

**Short-leg statement:** report V0's short leg (n, mean R, total R) in IS and
OOS separately. If `n_short_OOS < 10` the verdict on the short leg is
"insufficient evidence" and no production change may be proposed from this
study regardless of the numbers.

No parameter changes after seeing results.

## Test 2 — attribution (gated)

Run ONLY if |total R(V0) − total R(V1)| on the short side ≥ 5R on either asset.
`studies/notebooks/attribution/attribution.py::attribute()` on three pools —
V0 shorts, V1 shorts, V0 longs — using the recipe of `chento_backonly()`
(OKX-aligned, `actual_r` from `_walk`, 72h hold) to show whether the short leg
loses via timing or via regime.

## Data and scope

- Everything reads prod.db through `file:…?mode=ro`. Research modules only
  (`chento_journal/validation_*`, `overlay_study`, `attribution`); the
  production sleeve's `signal.py` is never imported. `chento_triple_v3.math`
  is imported only in a parity assertion (research `compute_lsr_extremes`
  vs the sleeve at 30 / 90 / 365 rows — the `tests/test_chento_parity.py`
  pattern).
- Outputs are study-local: `results/`. Nothing in `strategies/` or `bots/`
  changes as part of this study.

## Files and re-run order

1. `test0_stamp_semantics.py` → `results/test0_stamp_semantics.json`, `results/test0.md`
2. `gen_variant_pools.py` → `results/pools/trades_{BTC,ETH}_{V0..V5}.csv`
3. `score_variants.py` → `results/variant_summary.csv`, `results/variant_summary.md`
4. `attribution_shorts.py` (only if Test 2 is gated in) → `results/attribution.csv`
5. `figures.py` → `results/*.png`
6. `findings.md` — appended after the runs; this pre-registration is never edited.
