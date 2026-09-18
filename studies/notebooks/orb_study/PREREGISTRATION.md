# ORB study pre-registration v1.0

**Written 2026-09-15, before any ORB outcome was computed in p300.** This document turns
[TEST_PLAN.md](TEST_PLAN.md) (draft v1, 2026-09-14) into frozen, executable rules. Where the
draft left a choice open or a detail undefined, the resolution is written here and marked
**Resolved**; where this document departs from the draft it says **Deviation** and why. The
draft stays unchanged as the design record. Code, data and this file are hashed into
`results/freeze_F0.json` before the first development outcome is read.

## 1. What is claimed and what is not

Questions, in the draft's order: does price continue after breaking a completed session-opening
range (Q1); does waiting for the break add value over entering in the opening candle's direction
(Q2); does a recognisable market opening matter more than an arbitrary clock (Q3); does the edge
survive fills, fees, spread and actual funding (Q4); does it persist across years and transfer to
ETH (Q5); is it more than repackaged crypto beta (Q6).

**Primary outcome:** net expectancy per trade, in basis points of entry notional, under the
decision-bearing cost model (section 5). **Also required:** a positive calendar-time mean net
daily return. **Economic hurdle:** +2 bp per trade (a design hurdle, not an estimate).

Nothing in this study authorises paper trading, a bot, or a production change. A positive
verdict is a research claim; deploying anything needs a mechanism argument and the user's go-ahead.

## 2. Data (frozen)

| Item | Frozen choice |
|---|---|
| Instruments | Binance USD-M linear perpetuals BTCUSDT and ETHUSDT, last-trade price |
| Klines | 1-minute archive, `data.binance.vision`, monthly 2020-01..2026-08 plus daily 2026-09-01..13; every zip matches Binance's published SHA-256 |
| Funding | monthly `fundingRate` archive 2020-01..2026-08 plus public REST `fundingRate` for 2026-09-01..13 (saved verbatim) |
| Panel | dense UTC minute grid, 2020-01-01 00:00 to the **exclusive cutoff 2026-09-14 00:00**; built by `orb_data.py build` |
| Missing prices | Zero-volume (tradeless) bars are treated as missing, never as tradable prices: 369 BTC and 302 ETH minutes, mostly exchange-outage filler in runs of 13–99 minutes, plus 24 isolated tradeless ETH minutes in 2020 |
| Tick | BTCUSDT 0.01 before 2022-02-15, 0.10 from 2022-02-15 (the first hour with every price on a 0.1 grid is 2022-02-15 04:00 UTC); ETHUSDT 0.01 throughout. Stops round outward to the tick; resting entries sit one tick beyond the range |
| Calendars | `configs/calendar_XNYS.json`, `configs/calendar_XLON.json` (exchange_calendars 4.13.2), checked against an independently compiled NYSE holiday and early-close list in `tests/test_calendars.py` |
| Mark price | not used; exposure is 1x and no liquidation can occur |

Local spot tables are not used for returns. The local Binance perp tables (`cd_futures_15m`,
`cd_futures_eth_15m`, `screener_klines_1m`, fetched through the REST API) and a trade-level sample
(`aggTrades`, three development days fixed below) serve only as integrity checks in notebook 01.

**Data gate (must pass before outcomes):** unique monotonic timestamps on the minute grid;
finite positive OHLC with low <= open, close <= high; archive-versus-REST 15-minute OHLC agreement
on at least 99.9% of overlapping bars for both assets, with every disagreement listed; 1-minute
agreement with trade-built bars on the BTC `aggTrades` sample days 2020-06-15, 2021-06-15 and
2022-06-15 (13:00–21:30 UTC, chosen by date rule before any outcome) as amended below;
funding settlements at 8-hour spacing with no missing settlement; an eligibility table per anchor
with every exclusion reason. A failure gives `INVALID_DATA_OR_ENGINE` until repaired with an
audit trail.

**Amendment A1 (2026-09-15, before any outcome; evidence in `results/data_gate.json`).** The first
gate run failed the clause as originally written, "exact 1-minute OHLC agreement with trade-built
bars". The check assumed a bar's open is its first trade. Binance sets some bar opens to the previous
bar's close instead: 11, 85 and 111 of 510 minutes on the three days, every one of them equal to the
previous close and one tick from the first trade, which printed 5–105 ms after the boundary. A few
trades on a minute boundary are also assigned to the adjacent bar (highs/lows differ in 0, 4 and 1
minutes, by at most 0.38 bp; window volume is identical). The archive matches the REST API exactly on
all 144,000 overlapping 1-minute bars, so this is how the exchange builds bars, not corruption. The
clause now reads: closes exact; every open equal to the first trade or the previous close; highs/lows
equal on at least 99% of minutes and never more than 1 bp apart; window volume equal within 1e-6.
Consequence for fills: a next-open fill can sit one tick from the first trade of that minute, far
below the cost model's resolution.

## 3. The primary rule P0 (BTC)

| Component | Frozen definition |
|---|---|
| Anchor | 09:30 America/New_York on full-length NYSE sessions; early-close sessions excluded (they are run separately as a diagnostic) |
| Range | high H and low L of the 15 one-minute bars in [09:30, 09:45); W = H − L; any missing range minute or W <= 0 invalidates the session |
| Trigger | the first completed one-minute bar from [09:45, 09:46) on whose close is strictly above H (long) or strictly below L (short) |
| Entry | market order at the open of the bar after the trigger bar (latency 0); **Resolved:** a trigger counts only if that fill is strictly before the deadline, so the last possible trigger bar is [11:28, 11:29) |
| Deadline | 11:30 local (anchor + 120 min) |
| Pre-submission check | skip (and consume the session) if the latest completed close is at or beyond the stop, or if the fill bar is missing |
| Stop | opposite boundary (L for a long, H for a short), live from the fill; a bar opening beyond the stop exits at that open, otherwise a touch exits at the stop |
| Fill through the stop | if the fill itself is at or beyond the stop, the position is closed at the fill price (costs are still charged) |
| Target | none |
| Time exit | open of the first tradable bar at or after 16:00 local (anchor + 390 min) |
| Frequency | the first trigger consumes the session; at most one trade; no re-entry or reversal |
| Size | 1x equity notional per trade in a standalone account; returns compound |
| Funding | every settlement whose minute lies in [fill minute, exit minute] inclusive, `−side × rate × price_at_settlement / fill`, price = that minute's open |

Resting stop entries (the core family's second entry type) place a buy stop at H + tick and a sell
stop at L − tick from anchor + m minutes, one cancels the other, and they expire at the deadline. A
buy stop fills at max(level, bar open); a sell stop at min(level, bar open). A bar touching both
levels walks both paths and keeps the worse outcome. An intrabar fill followed by a stop touch in
the same bar is booked as stopped. Every such case is flagged and counted.

## 4. Policies (46 in the selection family)

Implemented in `orb_policies.py`; the JSON form and its SHA-256 go into the freeze manifest.

- **P0** as above.
- **CORE (23):** anchors {NY 09:30 America/New_York on full NYSE sessions; LDN 08:00 Europe/London on
  full LSE sessions; UTC 00:00 every calendar day} × range {5, 15, 30, 60} min × entry {close,
  resting stop}, minus P0. Deadline anchor + 120, exit anchor + 390, opposite stop for all.
- **EXT (14), one change to P0 each:** buffer 0.05W and 0.10W; opening-direction gate (watch only
  the boundary in the range candle's direction; a doji range skips the session — **Resolved:** a
  close beyond the unwatched boundary is ignored rather than consuming the session); relative
  volume > 1.0 / 1.5 / 2.0 (range volume over the mean of the prior 20 valid same-anchor ranges);
  range width W/range-open inside the prior 60 valid ranges' 20th–80th percentiles; deadline 60 and
  180 min; midpoint stop; fixed targets 1R / 2R / 3R from the actual fill (a resting limit that
  fills at the target when a bar trades through it by one tick; stop and target in one bar books
  the stop); time exit 60 min after entry capped at the session exit.
- **INT (2):** relative volume > 1.0 with a 2R target; relative volume > 1.0 with the direction gate.
- **EXIT (6) — addendum, 2026-09-15.** Added at the user's request to study exit events for each
  strategy, and because the user distrusts time stops ("a time stop just means we do not know if
  the trade was valid"). P0's 16:00 exit is itself a time stop. Each arm changes only the exit:
  - `X_REENTER1M` — invalidation: exit at the next open after a completed one-minute close back
    inside the range (long: close < H; short: close > L), checked from the fill bar's close on.
  - `X_REENTER15M` — the same on completed 15-minute closes aligned to the anchor.
  - `X_VWAP` — exit at the next open after a completed close on the wrong side of the session VWAP
    (cumulative quote volume / base volume from the anchor).
  - `X_TRAIL` — the stop trails one range width behind the best price since entry, ratcheting only
    on completed bars; session time exit kept.
  - `X_NOTIME` — no time exit: hold to the opposite-boundary stop, censored 7 days after entry.
    While a position is open later sessions are skipped and counted as `position_open`.
  - `X_TRAIL_NOTIME` — trailing stop and no time exit (the one predetermined exit interaction).
  The 7-day censoring horizon is a finite-study device, not a proposed exit; censored trades are
  counted and reported.

**Controls (never selectable):** opening momentum (enter at the first post-range open in the
range candle's direction, opposite-boundary stop, session exit); clock-only long and short (same
entry time, opposite-boundary stop); break-fade (reverse the P0 break at the same fill, stop one
range width beyond the broken boundary, target at the range midpoint, session exit); placebo
anchors (P0 shifted −120, −60, +60, +120 min on the same NY dates); random direction (on P0's
trade sessions: P0's fill, a seeded coin-flip side, stop at P0's risk distance, session exit;
200 seeds); random time (on P0's trade sessions: fill at a seeded uniform minute in
[anchor + 15, anchor + 120), coin-flip side, stop one range width away rounded outward, session
exit; 200 seeds); session buy-and-hold (long from the first post-range open to the session exit,
no stop); calendar buy-and-hold (perpetual long, daily, with funding); cash (zero).

**Diagnostics (never selectable):** P0 with 1- and 2-minute submission latency; P0 on early-close
sessions.

## 5. Costs (frozen)

Per-leg charges in bp of the leg's notional; funding is always charged from the ledger except in
the zero-cost signal diagnostic.

| Scenario | Per leg | Round trip | Role |
|---|---|---|---|
| `gross` | 0, no funding | 0 | price-signal diagnostic |
| `rt5` / `rt10` / `rt20` / `rt30` | 2.5 / 5 / 10 / 15 | 5 / 10 / 20 / 30 | sensitivity; `rt10` equals the taker fee floor |
| **`db`** | **5.8** | **11.6** | **decision-bearing:** 5.0 VIP0 taker fee + 0.3 half-spread + 0.5 slippage allowance |
| `db_nonfee_x2` | 6.6 | 13.2 | non-fee friction doubled |
| `db_plus5` | 8.3 | 16.6 | +5 bp per round trip (the validation stress clause) |
| `db_plus10` | 10.8 | 21.6 | +10 bp per round trip |

**Resolved:** the fee is today's 5.0 bp applied to every year (Binance's historical VIP0 taker fee
was lower for part of the sample, so this is conservative). The 0.3 bp half-spread is the execution
study's measurement on other signals; the 0.5 bp slippage is an unmeasured allowance for breakout
entries. No ORB-conditioned spread or depth data exist for the development years, so the study can
at most reach a price-signal-plus-modelled-cost verdict; an execution-validated verdict is withheld.
Break-even friction (the per-leg charge at which mean net expectancy is zero) is reported for every
candidate.

## 6. Chronology

| Block | Dates (session dates, inclusive) | Use |
|---|---|---|
| Development | 2020-01-01 .. 2022-12-31 | BTC only: all 46 policies, controls, selection of at most one challenger |
| Validation | 2023-01-01 .. 2024-12-31 | frozen candidates on BTC (decision) and ETH (transfer), no tuning |
| Lockbox | 2025-01-01 .. 2026-09-13 | one final run of every frozen candidate, both assets |
| Prospective | after this study | not started here |

These years were seen in other p300 studies; they are held out **for this ORB campaign** only.
Rolling filter features use earlier sessions (warm-up across block boundaries is allowed; relative
volume needs 20 and the width band 60 prior valid sessions, so those filters start in 2020-02 and
2020-04). A position still open at a block's last UTC midnight is closed at the last tradable close
before it (`censored_block_end`) and excluded from that block's confirmatory statistics; with intraday
exits this cannot happen. ETH development results are computed only after the challenger freeze and
never influence selection.

## 7. Development selection (frozen)

**Deviation from the draft:** the draft selected on two development "test" folds (2022-H1, 2022-H2).
No policy here fits a parameter, so those folds are only evaluation windows, and selecting on 2022
alone would pick the best rule of one bear year. Selection instead uses the whole development block,
with a per-year consistency condition; the draft's two folds are reported alongside for transparency.

1. Eligible challengers: the 45 family policies other than P0, with at least 100 development trades,
   positive development net expectancy under `db`, and positive net expectancy in at least two of
   the three development years.
2. The challenger is the eligible policy with the highest development net daily Sharpe (365-day
   zero-filled calendar, `db`). Ties within 0.02 Sharpe go to fewer changed components, then the
   lexically smaller ID.
3. It must beat P0's development net daily Sharpe; otherwise P0 is carried alone.
4. The candidates (P0, plus the challenger if any) are written to `results/freeze_F1.json` with the
   development summary before any validation outcome is computed.

Development inference, reported but not a gate: Romano–Wolf StepM over the 46 family daily net return
series against cash (one-sided, alpha 0.05, 10,000 circular block bootstraps of 20 days, seed 42);
DSR of the challenger with N = 46 trials and the family's Sharpe dispersion, with N = 100 and 200 as
sensitivity; minimum detectable per-trade effect at 80% power for the validation and lockbox sample
sizes, scaled from P0's development standard error.

## 8. Validation continuation rule (frozen)

For each frozen candidate on BTC 2023–2024, all must hold:

1. The data gate and engine checks passed.
2. Net expectancy per trade > 0 and mean net daily return > 0 under `db`.
3. Net expectancy > 0 in at least three of the four half-years.
4. Net expectancy under `db_plus5` >= 0.

If at least one candidate passes, the lockbox is opened once for every frozen candidate. If none
passes, the lockbox stays closed and the study reports the development and validation evidence.
ETH validation results are reported but are not part of this rule.

## 9. Lockbox tests and verdicts (frozen)

Primary tests, one-sided, Holm-corrected at family alpha 0.05 across every test that exists (at most
six): per-trade net expectancy > 0 under `db` for each candidate on BTC and on ETH; and the paired
uplift of each BTC candidate over the opening-momentum control (mean net daily return difference > 0
on the common calendar). p-values come from the recentred circular block bootstrap (20-day blocks,
10,000 draws, seed 42): per-trade expectancy resamples each day's trade-net sum and trade count
jointly and divides the resampled totals; paired uplift resamples the daily difference series.

| Verdict | Rule |
|---|---|
| `INVALID_DATA_OR_ENGINE` | data gate, parity, truncation or accounting checks fail |
| `NO_ECONOMIC_EDGE_DEMONSTRATED` | the 95% upper bound of per-trade net expectancy (`db`) is below +2 bp in the block that ended the study (validation if the lockbox stays closed, else lockbox) |
| `INCONCLUSIVE` | the interval spans both 0 and +2 bp, or ambiguity/data prevents a candidate verdict |
| `HISTORICAL_CANDIDATE` | lockbox per-trade net mean >= +2 bp and positive mean daily return under `db`; Holm-corrected BTC test rejects zero; `db_plus5` mean >= 0; at least 3 of 4 validation half-years positive |
| `BREAKOUT_INCREMENT_SUPPORTED` | a historical candidate whose Holm-corrected uplift over opening momentum rejects zero |
| `TRANSFER_SUPPORTED` | the same frozen candidate has positive ETH lockbox net expectancy and its Holm-corrected ETH test rejects zero |
| `PRICE_SIGNAL_ONLY` | always appended while execution costs remain modelled rather than measured |

A candidate status uses the +2 bp point estimate; its interval's relation to 0 and +2 bp is reported
separately. Low-power failures are `INCONCLUSIVE`, not proof of zero edge.

## 10. Diagnostics (reported, never decision-bearing)

Years and half-years; long versus short; before and after the US spot ETF launch (2024-01-11); weekday;
US/UK DST-mismatch weeks; range-width and relative-volume terciles; exit-reason mix; MFE/MAE; best
1/5/10 trade share; leave-one-year-out; cost curve and break-even friction; latency 1 and 2 minutes;
early-close sessions; flagged (ambiguous / gap / delayed) trades; block-length sensitivity 5/10/40
days; beta and alpha against calendar buy-and-hold; a 50/50 BTC+ETH notional portfolio of each
candidate. Regime or filter patterns found here are exploratory and would need a new
pre-registration and new data.

**Post-verdict exploratory run.** Only after `results/freeze_verdict.json` exists, all 46 family
policies and the controls are run on the validation and lockbox blocks for both assets, and on ETH
development. These tables are labelled exploratory: they describe how the entry, filter and exit arms
behave out of sample, they never change a verdict, and nothing selected from them is a finding without
a new pre-registration and data not yet seen.

## 11. Freeze procedure and trial ledger

- **F0** (before any development outcome): SHA-256 of this file, TEST_PLAN.md, the policy registry JSON,
  every study module and test, the calendars, the data manifest and the panels' logical hashes, plus
  the data-gate and engine-check results. Engine checks: 50 synthetic fixture and accounting tests;
  exact field-by-field parity between the reference loop and the independent vectorized implementation
  on every development BTC trade of the 42 policies it covers; the randomized-control walker against the
  reference walk; decisions unchanged when future minutes are deleted; and two end-to-end smoke runs of
  every stage on a synthetic random walk (`tests/test_run_smoke.py`), one of which forces a challenger and
  an open lockbox. Written to `results/freeze_F0.json`.
- **F1** (after development, before validation): the selected candidates and development summary.
- **F2** (after validation, before the lockbox): the continuation decision.
- `trial_ledger.csv` records every policy evaluated, its blocks, cost model, outcome-access time and
  selection use, appended as each run happens.

The repository's convention is a git commit at each freeze. Commits are made only when the user asks,
so the hashes and UTC timestamps in the manifests are the freeze record until then, and each manifest
is written before the next block's outcome is computed. Any change after F0 that is not a bug fix
proven by a failing test is a new exploratory trial with its own ID.

## 12. Information already seen (disclosure)

- No ORB rule has been backtested in p300 before this study (RESEARCH.md section 4).
- Related results known to the author: the Asia-range (00–07 UTC) first-break **fade** had negative gross
  expectancy (range_sanity_2026_09), so continuation of that different break was weakly positive before
  costs; intraday small-move signals on BTC measured −0.2..+0.15 R gross; momentum sweeps found "follow"
  beating "fade" without a cell clearing |t| 4.88; measured taker round trips on other signals of 7–10 bp.
- Data facts from the data gate (coverage, outages, tick change) were seen before this document was
  final. No price path around any ORB trigger was inspected.
- Engine parity and truncation checks on development data report match counts only, never P&L.
