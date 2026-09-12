# Brainstorm session 2026-09-11 — independent validation (pre-registered 2026-09-11, before any run)

**Tag: AUDIT / replication. Nothing here is tuned. Every threshold, window and decision
rule below was written before a single number was computed. N_TRIALS for the primary
tests is 1 (the rules are frozen copies of the brainstorm definitions); sensitivity
grids are reported but never used to pick a winner, and their size is recorded.**

## Why this study exists

`studies/material/brainstorming_session_sep11_2026/` holds 26 studies produced in
another session (2026-09-08 → 09-11) by the `C:\Source\Repos\ai_trading\scalp_lab`
harness on Binance-REST caches. The user's instruction: *treat every result as
unvalidated and assume it is full of mistakes.* The session's own scoreboard leaves a
handful of positive or decision-bearing claims. Those are what a validation has to
attack; the ~20 negatives are not deployable either way and are not re-run here.

Method, in one sentence: **reproduce the number on their data with their definition
(parity), then re-derive it from p300's own database with an independent implementation,
then push it through data the original never saw (other eras, other assets) and through
p300's standing statistical bar (deflated Sharpe, block bootstrap, walk-forward).**

Data never leaves read-only mode: `prod.db` is opened `file:…?mode=ro`; the
`ai_trading` caches are read as pickles and never rewritten (their loader would refetch
and overwrite stale caches, so it is not called). Nothing in `strategies/`, `bots/` or
`data/` is touched.

## Claims selected (and why these)

| id | claim (source doc) | why it is worth a validation |
|---|---|---|
| **C1** | **Big-bar daily momentum.** Daily bar with range z-score (60-bar window) > 1.0 → trade the bar's own direction, hold 7 bars. +193.5 bp drift-adjusted, t = +3.04, episode-clustered t = +3.99, n = 189, win 55.6 %, capture 21.4 % of σ ([ABSORPTION §5](../../material/brainstorming_session_sep11_2026/ABSORPTION_FINDINGS.md)); at 100 %-of-equity sizing "beats buy-and-hold on both axes", 20.3 % vs 19.9 % CAGR, −63 % vs −83 % maxDD ([SIM_TRADING_STYLE §3b](../../material/brainstorming_session_sep11_2026/SIM_TRADING_STYLE.md)) | The only prediction cell in 26 studies said to clear its own Bonferroni bar, and the session's stronger "live candidate" |
| **C2** | **Funding-carry quartile timing.** Lag-1 autocorrelation of BTC funding +0.75; next-print yield by current-print quartile −2.2 / +2.5 / +5.5 / +7.7 %; "holding only the upper half roughly doubles the yield" (+7.7 vs +3.4 %/yr) ([CARRY §2](../../material/brainstorming_session_sep11_2026/CARRY_FINDINGS.md), reusable piece #1 in STATUS) | Top of the session's edge/noise ranking, and it maps onto p300's live **S-078 CARRY** sleeve (7-day-average entry, 3-negative-day exit, 20 bp round trip). The claim was measured on ≈ 400 days of prints; p300 has 7 years |
| **C3** | **Absorption, 5 m bars, 2 h hold.** Volume z > 1, \|imbalance\| z > 1, \|return\| z < 0.5 on a 288-bar window → fade the aggressor, hold 24 bars. +4.8 bp, t = +2.91, clustered t = +2.16, n = 1,620, net +0.7 bp at 4.1 bp; the 2 h peak of a measured decay curve ([ABSORPTION §2–3b](../../material/brainstorming_session_sep11_2026/ABSORPTION_FINDINGS.md)) | The session's second "live candidate"; called "the best-behaved short-horizon signal in the repo" |
| **C4** | **Claims about the ADX regime machine** ([S005_MATRIX](../../material/brainstorming_session_sep11_2026/S005_MATRIX_FINDINGS.md) "three results that outrank the matrix"): (i) the EMA-consensus veto's benefit is a stop-fill artefact — under intrabar stop fills S-003 alone (Sh 1.08 / 43.6 % / −37.5 %) beats S-005 (1.01 / 39.4 % / −41.4 %); (ii) closing the daily bar at 00/04/08/12/16/20 UTC moves the Sharpe over 0.85–1.16, a ±0.15 noise floor; (iii) S-005 does not beat buy-and-hold on return (41.4 % vs 42.3 %); (iv) the short leg is +1.0 % over 21 trades vs +26.1 % over 25 longs and a data-free long-1.00 / short-0.25 tilt is worth +0.19 Sharpe | p300 runs **S-003 ADX live** (`strategies/sleeves/adx/`, TV-validated harness in `studies/notebooks/adx_study/`). If (ii) holds on p300's machine, every tuning delta below ±0.15 Sharpe in the ADX study is noise; (iv) bears directly on the pending Tier-1/Tier-2 short-filter decision |
| **C5** | **Two residual classic-TA cells** ([TA_SWEEP §5](../../material/brainstorming_session_sep11_2026/TA_SWEEP_FINDINGS.md)): Bollinger(20,2) bandwidth squeeze (126-bar percentile-rank < 0.20) released downward, short, 1 d, cluster-robust t 4.77 / 4.77 / 4.19 at H = 4 / 12 / 24; and RSI(14) crossing up through 75, long, 1 d, t 3.78 with a plateau over lengths 7/14/21 and thresholds 65/70/75 | Both were left as "candidates needing their own pre-registered OOS test". This is that test |

**Not run, and why.** Vol-premium / covered call: p300's own `vrp_study` (2026-09-07) already
falsified the strangle on real option marks and the session's three reviewers refuted the
covered-call alpha (t 1.3–1.6, dies from a 2023 start); nothing left to decide. Passive
execution (~1.0 bp/leg): needs tick data p300 does not hold and the session itself says only a
live quoting test settles it. Stop displacement off the 24 h extreme: a hypothesis, not a
result. The ~20 negatives: not deployable either way; their methodological traps are adopted
here, not re-litigated. Levels pre-registration: evaluates 2026-12-11 by its own rule.

## Data

| series | p300 source (read-only) | brainstorm source (read-only pickle) | notes |
|---|---|---|---|
| BTC daily | `cd_spot_binance` 1 h (open-stamped, verified against `btc_1m`) → UTC daily; 2017-08 → | `scalp_lab/cache/spot_BTCUSDT_1d_3200d.pkl` (Binance spot REST) | same venue; differences would expose data faults |
| BTC daily, pre-Binance | `studies/material/bitstamp_btcusd_daily.json` (2011-08 → 2026-05) | `bitstamp_btcusd_86400_*.pkl` (newest), `bitstamp_btcusd_14400_*.pkl` (4 h) | 2012-01 → 2017-08 is **out-of-sample in time** for C1 (their panel began 2017-12) |
| BTC 5 m with taker split | `cd_spot_5s` → 5 m (2025-06-08 → 2026-06-07) | `spot_BTCUSDT_5m_1177d.pkl` | exact-resolution replication on one year |
| BTC 15 m with taker split | `cd_spot_15m` (spot) and `cd_futures_15m` (perp), 2019-09 → | — | 7-year independent panel for C3 at a coarser bar |
| ETH daily / 15 m | `eth_1m` → daily (2020-01 →); `cd_futures_eth_15m` (2021 →) | — | out-of-sample in asset |
| alt daily panel | `screener_klines_daily`, assets with ≥ 800 daily bars, BTC and ETH excluded (2023-05 → 2026-04, up to 64 assets) | — | out-of-sample in asset; pooled with **date-clustered** SEs because alts co-move |
| BTC funding, 8 h settlements | fetched from `fapi.binance.com/fapi/v1/fundingRate` (public) into `cache/`, full history 2019-09 → | `funding_BTCUSDT_full.pkl` | `cd_funding_rate` is **not** used pre-2026-04-13: those rows are CoinDesk hourly *predicted* rates (memory `funding-cadence-change-2026-04-13`); the 08:00 rows do not match Binance settlements. Post-cutover prod rows and `bybit_funding` are cross-checks only |

C0 (data parity) runs first and is a gate: if the brainstorm's cached bars disagree with
p300's tables by more than rounding on the overlap, the affected replication is reported
as INCONCLUSIVE (data), not as a verdict on the idea.

## Tests and decision rules (frozen)

Common conventions, all copied from the brainstorm's own scripts so the parity step is
meaningful: forward return = close[i+H]/close[i] − 1 (entry and exit at the close);
greedy non-overlap (`i − last ≥ H`); drift adjustment = subtract the panel's
unconditional mean H-bar return, sign-adjusted; episode-clustered t with clusters =
runs separated by < 5·H bars; capture = edge / (σ_bar·√H). Independent implementations
are vectorised numpy/pandas and are checked against an exact port of their loop code on
their own cache (parity must agree to the printed digit or the port is wrong).

Deflated Sharpe (`studies/lib/validation/dsr_pbo`) is computed on the per-trade adjusted
series at the trial count named per claim. Block bootstrap: circular blocks
(`studies/lib/validation/bootstrap.circular_block_indices`), 5,000 draws, seed 42.

### C1 — big-bar momentum

1. **Parity**: their cache, their `zroll` + `stats` port → n = 189, +193.5 bp, t 3.04, tc 3.99.
2. **Same rule, p300 daily** (`cd_spot_binance` → daily, 2017-08 → last full day); lookahead
   truncation test (prefix run equals full run on the prefix).
3. **Out-of-sample in time**: Bitstamp 2012-01-01 → 2017-08-16.
4. **Out-of-sample in asset**: ETH daily 2020 →; alt panel pooled with week-of-entry-clustered
   SE and the share of assets with a positive mean.
5. **Robustness on BTC** (reported, not selected): per-year, pre/post spot-ETF split
   (2024-01-11), long vs short leg, causal trailing-365-bar drift instead of full-sample drift,
   raw (no drift adjustment), and a 4 × 5 × 3 grid (z ∈ {0.5, 1, 1.5, 2} × H ∈ {3, 5, 7, 10, 14} ×
   W ∈ {30, 60, 120}) to see whether the cell sits on a plateau or a spike. Grid size 60 goes
   into the trial ledger.
6. **DSR** on the p300 primary series at N = 1, 24 (the control family: 2 thresholds × 3
   horizons × 4 panels), 96 (their run 2) and **348 (their study total — decision-bearing)**.
7. **Sizing claim**: exact port of `run_leverage_sim2.simulate` (cross margin, MEXC maker
   fees, 8 h funding, ≤ 3 concurrent, notional = 100 % of equity) on their cache and on p300
   data; record **average and peak gross exposure** (three concurrent 100 % positions is 3×
   leverage, which is not "comparable sizing" to 1× buy-and-hold); compare against
   buy-and-hold scaled to the strategy's average exposure and against a random-direction,
   frequency-matched control; block-bootstrap (block 30 days, paired) the CAGR and maxDD
   differences.

**Decision**: CONFIRMED-SIGNAL iff (a) parity passes, (b) drift-adjusted mean > 0 with t ≥ 2.0
in at least two of the three unseen samples {Bitstamp 2012-17, ETH, alt panel (clustered t)},
(c) DSR at N = 348 ≥ 0.95 on the p300 primary series. BEATS-HOLD additionally iff the
exposure-matched CAGR difference has a 90 % bootstrap CI above zero. KILL iff (b) fails.
INCONCLUSIVE iff (b) passes and (c) fails (real but under-powered against its own search).

### C2 — funding-carry quartile timing

Series: Binance BTCUSDT settlement rates, 3/day, 2019-09-10 → last settlement.

1. **Parity**: the last 400 days' lag-1 autocorrelation and quartile table (their sample) from the
   fresh fetch; agreement with `funding_BTCUSDT_full.pkl` on every common timestamp.
2. **Persistence**: lag-1/2/3 autocorrelation by calendar year; quartile-conditional next-print
   table (a) with full-sample quartiles (their construction) and (b) with **causal** trailing
   1,095-print (1-year) quantiles, by year and pre/post-ETF.
3. **The trade, net of its own turnover** (the question CARRY left open): at each settlement
   decide from the just-settled print whether to hold the spot-long/perp-short pair through
   the next interval. Rules: ALWAYS-ON; **UPPER-HALF** (print > causal median — their rule,
   decision-bearing); TOP-QUARTILE; **S-078** (p300's live rule: 7-day average of daily
   funding > 0 to enter, 3 consecutive negative days to exit, evaluated daily). Each entry/exit
   pair costs 0.20 % of notional (the sleeve's `ENTRY_EXIT_COST_PCT`); basis is taken as zero,
   as the sleeve does. Report gross yield, round trips per year, net yield, per year, pre/post
   ETF; block-bootstrap (block = 90 prints) the annualised net yield difference
   UPPER-HALF − ALWAYS-ON. Sensitivities (ledger, not selected): hysteresis enter > p60 / exit
   < p40; minimum hold 63 prints (21 days).

**Decision**: CONFIRMED iff UPPER-HALF net yield beats ALWAYS-ON on the full sample, in ≥ 5 of
the 7 calendar years 2020–2026, and the 90 % bootstrap CI of the difference excludes zero.
KILL (as a net claim) iff it fails on the full sample or the CI includes zero. The gross
persistence finding is reported separately as a data fact, not a decision.

### C3 — absorption

1. **Parity**: their 5 m cache, exact port → dense-horizon table (1 h … 2 d) with the 2 h cell
   at n = 1,620, +4.8 bp, t 2.91, tc 2.16.
2. **Data check**: their 5 m cache aggregated to 15 m vs `cd_spot_15m` on the overlap (close,
   volume, taker-buy volume): max and mean relative difference.
3. **Exact-resolution replication**: `cd_spot_5s` → 5 m, 2025-06-08 → 2026-06-07, same rule;
   the same window in their cache for a like-for-like comparison.
4. **Independent 7-year panel at 15 m** (`cd_spot_15m`, W = 96 bars = 1 day, H = 8 = 2 h, and the
   decay curve H ∈ {4, 8, 12, 16, 24, 32, 64, 96}); the same on `cd_futures_15m` (the venue it
   would trade) and on `cd_futures_eth_15m`.
5. Per-year, pre/post ETF, long vs short leg, truncation test, DSR at N = 1 / 348 on the
   15 m spot primary series, net at 4.1 / 5 / 8 bp.

**Decision**: SIGNAL-CONFIRMED iff the 15 m spot 7-year replication has the same sign with
t ≥ 2.0 (drift-adjusted, non-overlapping) and ≥ 5 of 7 years positive, and the perp panel has
the same sign. TRADABLE additionally iff net at 4.1 bp > 0 with DSR(348) ≥ 0.95 (the session
itself expects this to fail). KILL iff the 15 m spot replication has \|t\| < 2 or the wrong sign.

### C4 — ADX regime machine

Two machines, deliberately: their `scalp_lab.s005` engine on Bitstamp daily (parity of the
claims as stated) and **p300's live machine** (`studies/notebooks/adx_study/harness.run`,
TV-validated, `cd_spot_binance` → daily, 2018-01-01 → last full day; fixed 25/20 thresholds,
EMA50 direction, asymmetric EMA150 long filter, 10 % stop filled at the stop price).

1. **Parity (their engine, their data)**: S-003 alone and S-005 20/100 under the study fill
   (breach on the range, exit at the close) and the live fill (exit at the stop) →
   Sh 1.02 / 1.05 and 1.08 / 1.01, CAGR and maxDD as printed; measurement 2014-01-01 → 2026-08-10.
2. **Bar-phase noise floor**: (a) their engine on Bitstamp 4 h aggregated to 6 daily phases →
   Sh 0.85–1.16; (b) **p300's machine on 24 daily phases** (day boundary at every hour, built
   from `cd_spot_binance` 1 h). Report the range of return, maxDD, MAR, per-trade t and the
   annualised Sharpe of the daily mark-to-market curve.
3. **Stop-fill semantics on p300's machine**: the ledger with SL exits re-priced at the breach
   bar's close (the "study fill") vs the live fill; both against buy-and-hold on the same span,
   plus buy-and-hold scaled to the strategy's time-in-market.
4. **Short leg** (their proposed "single cheapest next test", run here as written): p300 machine,
   short weight w ∈ {0, 0.25, 0.5, 0.75, 1.0} applied to the short trades' returns, longs at 1×;
   shorts carry realised Binance funding (shorts receive positive funding) because the live sleeve
   trades the perp. Drift controls: the short leg alone inside the 2018 and 2022 bear years, and
   the whole machine on ETH daily (2020 →). Block-bootstrap the short-leg mean (price+funding).
   DSR of the live machine's daily curve at N = 1 and at the ADX study's documented sweep size
   (17 configurations, `validation_audit_2026_09`).

**Decision**: (i) and (iii) are reproductions — PASS/FAIL against the printed digits.
(ii) NOISE-FLOOR-CONFIRMED iff the 24-phase range of annualised Sharpe on p300's machine
is ≥ 0.20; then any ADX-study delta smaller than that range is declared inside noise.
(iv) NO-SHORT-EDGE-DEMONSTRATED iff the 90 % bootstrap CI of the short-leg mean (price +
funding) includes zero; the w that maximises MAR is reported but **not** recommended — sizing
is the user's call and the sample is ~20 trades.

### C5 — residual TA cells

Definitions frozen from `run_bands_vol.py` and `run_oscillators.py`:
`BBsqz20_fire_dn`: BB(20, 2) bandwidth (ub − lb)/mid, 126-bar percentile rank < 0.20 on the
prior bar, bandwidth expands on this bar, close < mid → **short**; H ∈ {4, 12, 24}.
`RSI14_enter_OB75`: Wilder RSI(14) crosses up through 75 → **long**; H ∈ {1, 4, 12, 24};
plateau cells RSI{7,14,21} × {65,70,75} reported.

1. **Parity** on Bitstamp daily (their panel) → t 4.77 / 4.77 / 4.19 (squeeze) and 3.78 (RSI).
2. **BTC p300 daily** 2017-08 → (largely the same data, a code check).
3. **Out-of-sample in asset**: ETH daily and the alt panel (pooled, week-clustered SE, share of
   assets positive).
4. DSR on the Bitstamp cell at N = 7,908 (the sweep it was selected from) and at N = 1 on ETH.

**Decision**: KEEP-FOR-STUDY iff ETH t ≥ 2.0 with the same sign AND the alt-panel clustered
t ≥ 2.0 with ≥ 60 % of assets positive. KILL otherwise. (These cells were selected out of 7,908;
the in-sample number cannot promote them, only unseen assets can.)

## Trial ledger for this study

| family | primary (decision-bearing) | sensitivity cells (reported only) |
|---|---|---|
| C1 | 1 | 60 grid + 4 drift/leg variants |
| C2 | 1 (UPPER-HALF) | 2 (hysteresis, min-hold) + TOP-QUARTILE |
| C3 | 1 | 8 horizons × 3 panels |
| C4 | 0 (reproductions and a pre-stated sweep of w) | 5 (w) + 24 phases |
| C5 | 2 | 9 plateau cells |

## Files and run order (repo root, `venv\Scripts\python`)

1. `bv_lib.py` — read-only loaders, exact ports of the brainstorm statistics, independent
   implementations, sizing simulator, bootstrap/DSR wrappers.
2. `run_c0_data_parity.py` → `results/c0_*.json` (gate)
3. `run_c1_momentum.py` → `results/c1_*.json|csv|png`
4. `run_c2_carry_timing.py` → `results/c2_*`
5. `run_c3_absorption.py` → `results/c3_*`
6. `run_c4_adx_engine.py` → `results/c4_*`
7. `run_c5_ta_residuals.py` → `results/c5_*`
8. `build_notebook.py` (system `C:\Python\Python313\python.exe`, has nbformat/nbclient; cells
   execute in the repo venv through a temporary kernelspec) → `brainstorm_validation.ipynb`
9. `findings.md` — written after the runs. This README is not edited after the first run.

`cache/` holds the funding fetch and the 5 s → 5 m aggregation (gitignored). `results/` is
committed with the notebook.
