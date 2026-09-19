# SQUEEZE_BULL on ETH 2026-09 — pre-registration (frozen before any result)

Written 2026-09-19 before `run_sqb_eth.py` produced a number; frozen by `run_sqb_eth.py freeze0` (sha256 of this
file, the library, the run script, the fixtures and every input file in `results/freeze_F0.json`). Roadmap §3
research item 1, second assets: *"SQUEEZE_BULL on ETH is the one second-asset study left: the same open-interest
route, the revalidation study's hourly engine, and no live ETH feed."* The operator allotted the ETH flushes to this
study over the top-anatomy's stage B on 2026-09-19. Tag: AUDIT of a shipped rule on a new asset + one
recommendation. Nothing under `bots/` or `strategies/` changes; a BUILD is a proposal for an ETH paper twin, which
needs a live ETH open-interest feed (coverage plan item 11: an ETHUSDT arm of `binance.py::fetch_open_interest`
into an asset-keyed table) and the operator's go-ahead.

## What is already known (disclosure)

- The BTC sleeve (S-107: a −2 % 4-hour open-interest drop with price down ≥ 0.5 %, in a bull regime — 30-day
  return above +10 % as of the previous day — long at the bar's close, stop −2 %, target +3 %, 48-hour time stop,
  24-hour cooldown) was re-validated **BUILD** on 2026-09-08 and again on the corrected open-interest table on
  2026-09-19: OOS (2026-04-14 → last full day) bull-gated mean R +0.190 at n 10 (clause (a) ≥ +0.10 with n ≥ 10),
  full-sample combined MAR 1.59 (clause (b) ≥ 1.5), margins one fire wide. Full sample (2022-01-30 →) bull-gated:
  114 fires, mean +0.27 R, MAR 1.83 at the June 18 bp. It fires ~25 times a year in bull tape and never in bear.
- Its research engine is frozen June code imported unchanged by `squeeze_bull_revalidation/squeeze_bull_lib.py`:
  `oi_flush.phase2_backtest.identify_long_flush_events` and `replay` (hourly bars, 18 bp charged inside the R).
- ETH data exists on disk but no ETH open-interest table exists in prod.db. The SHORT_SQUEEZE-on-ETH study
  (2026-09-19) built close-of-hour open interest from the 5-minute Binance archive (`ss_eth_lib.oi_hourly`: hour
  H = the snapshot stamped H + 1 h, the convention item 30 restored) and hourly bars from the perpetual 1-minute
  panel, and showed the same route reproduces prod's BTC tables bar for bar (15-minute) and the sleeve's own
  prod-table replay (Jaccard 0.986, identical fills). The archive's open interest is a different series from
  prod's `cd_open_interest` (CoinDesk until 2026-06-10, native Binance since), within about 0.05 % but not equal:
  on a rule whose trigger IS a −2 % open-interest change, some fires will sit on different sides of the threshold
  in the two series. That is why this study carries a fidelity gate and not a parity gate.
- No ETH flush has been looked at by anyone (the top-anatomy protocol kept them untouched; the ETH/BTC spread study
  read daily closes only). No ETH number of any kind has been computed.

## Data

| input | source |
|---|---|
| ETH hourly OHLC | `orb_study/cache/ETHUSDT_perp_1m.npz` aggregated to 60 min (`ss_eth_lib.bars_from_minutes`) — Binance USD-M perpetual, the venue of prod's `cd_futures_ohlcv` |
| ETH close-of-hour open interest | `exit_policy_2026_09/cache/ETHUSDT_metrics_5m_full.npz` (2021-12-01 →), `ss_eth_lib.oi_hourly` |
| BTC, for the fidelity run | `BTCUSDT_perp_1m.npz` and `BTCUSDT_metrics_5m_full.npz` (2020-09-01 →), the same builders |
| BTC reference ledger | `squeeze_bull_revalidation/results/full_oi_flush_ledger.csv`, the corrected-table run of 2026-09-19 (424 fires, 122 bull-gated) |

The frame is `squeeze_bull_lib.load_oi_frame`'s recipe on the panel-built tables: an inner join of hourly bars and
open interest, `oi_chg_4h` and `px_chg_4h` as 4-bar percentage changes, `ret_30d` from the daily resampled close
(June's same-day form, reported) and `ret_30d_backonly` (shifted one day — **the shipped bot's causal regime**,
which decides). Fires before **2022-01-30** are dropped so the ETH sample starts where the BTC revalidation's does
(its `FULL_SAMPLE_START`); the sample ends at the last full UTC day the panels cover (2026-09-13). Every regime's
fires are listed; only the bull-gated ones decide.

## Engine (verbatim)

`squeeze_bull_lib.oi_ledger(df)`: June's detector (−2 % / −0.5 % / 24 h cooldown) and replay (stop 2 %, target
3 %, 48 bars, **18 bp** charged inside `r_outcome`), unchanged, on the ETH frame. Reported beside it: `r_10bp`,
the same trades re-costed at 10 bp (`r_outcome + 0.04 R`, exactly, because the stop is 2 % of entry): 10 bp is the
measured taker round trip on ETHUSDT for chento's entries; the BTC sleeve's own measured 6.7 bp was not measured on
ETH. The regime label is `classify(ret_30d_backonly)`; a fire is *resolved* when its 48 bars lie inside the sample.

## Fidelity gate (P1, before any ETH number is decision-bearing)

The same builder and engine on the BTC panels, against the corrected-table reference ledger, over their common
span (2022-01-30 → 2026-09-13): the share of the reference's bull-gated fires the panel run reproduces (same trigger
hour) and the reverse, as a **Jaccard ≥ 0.80** on the bull-gated fire sets; and on the shared fires `r_outcome`
must agree to 1e-9 (the same bars, the same replay). Below either, the ETH numbers are **DESCRIPTIVE** and the
builder is at fault. The open-interest source difference is the only expected cause of non-shared fires; the count
and the sign pattern of the differences are reported.

## Statistics (ETH, bull-gated by `ret_30d_backonly`, resolved fires)

Full sample: n, mean R, win rate, cumulative R, max drawdown (R), annual R, **MAR** = annual R ÷ |max drawdown|
(June's `stats`), at 18 bp and at 10 bp; **halves** by fire order; per calendar year; exit mix; DSR
(`dsr_from_returns`, n_trials 1, on `r_outcome`). The **OOS window** of the BTC study, 2026-04-14 00:00 → the last
full day: n, mean R, win rate. Pooled (every regime) and per-regime ledgers reported. The BTC panel run's numbers
beside them for scale, and the reference ledger's as the anchor.

## Decision rule (fixed now)

The revalidation's clauses, applied to the ETH bull-gated OI-flush ledger at the June 18 bp:

- **BUILD** iff **(a)** OOS bull-gated mean R ≥ **+0.10** with **n ≥ 10** resolved fires, **(b)** full-sample
  bull-gated **MAR ≥ 1.5** (the shipped sleeve alone — ETH has no fCVD arm to combine), **(c)** full-sample
  bull-gated mean R > 0 in **both halves** by fire order (added here: a twin must not rest on one era), and
  **(d)** the fidelity gate passed.
- **KILL** iff OOS bull-gated mean R ≤ 0 with n ≥ 10 resolved fires, or full-sample bull-gated mean R ≤ 0 with
  n ≥ 10.
- **INCONCLUSIVE** otherwise, with the failing clause named and, when OOS n < 10, the date by which n ≥ 10 is
  expected at ETH's own bull-gated firing rate per bull-regime day.
- If (d) fails: **DESCRIPTIVE** (fix the builder first).

BUILD is a proposal: an ETH paper twin needs the ETH open-interest feed first (`cd_open_interest` is BTC-only), its
own paper variant, and the operator's go-ahead; it would join the re-cut at n = 20 / 30 on the BTC sleeve's terms.

## Trial ledger

One rule, one asset, no sweep. n_trials = 1.

## Run order

`python run_sqb_eth.py freeze0`, `python run_sqb_eth.py outcomes`, then `C:/Python/Python313/python.exe
build_notebook.py` → `squeeze_bull_eth.ipynb`. Findings in `findings.md`, written after the run. Fixtures:
`tests/test_sqb_eth.py`.
