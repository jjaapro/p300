# Exit-policy study, squeeze_bull arm — PRE-REGISTRATION v1.0 (REPORT-ONLY)

**Written 2026-09-15, before any outcome of any arm below was computed on this price path.** Phase 2 of the
exit-policy study (BACKLOG item 11 + research queue 2). **Report-only by standing decision:** squeeze_bull's exits
may not change before its pre-registered n = 20 / 30 re-cuts (`docs/calibration/squeeze_bull.md`). This document
therefore fixes what is measured and how it is reported; **no result here permits a change**, whatever it shows.

The prompt for the whole study was SJ-4250, a squeeze_bull trade that closed on its time stop, and the user's
hypothesis that a time stop "just means that we do not actually know if the trade was valid or not".

## 1. Questions (all report-only)

1. **Time stop.** Would squeeze_bull have done better with no time stop, or with another value from its own
   historical grid (12, 24, 72 h), than with the shipped 48 h?
2. **Target.** How far do the bounces run? Would a wider target (+4 %, +5 %, +6 %) or no target have done better
   than +3 %? (+3 % was the largest value of the grid that picked it.)
3. **Invalidation.** Does "the flush resumed" — a new long-flush signal after entry — identify trades that fail?
4. **The no-stop twin.** Is its 48 h time stop, its only loss exit, doing work? What if it held to its target with a
   catastrophe stop instead?
5. **A clean re-test of the twin's evidence.** The sizing study's S1, which justified the no-stop twin, averaged
   gross R labelled net and walked spot prices (study-validation audit, P0). What is the twin's paired difference
   against the incumbent in net R, on the traded venue, with actual funding?

## 2. Inputs (frozen)

| Item | Frozen value |
|---|---|
| Fires | `squeeze_bull_revalidation/results/full_oi_flush_ledger.csv` (sha256 recorded in F0), rows with `regime_backonly == "bull_30d"`: **122 fires**, trigger bars 2022-03-25 → 2026-09-04, the live bot's own causal gate (the parity test pins them to the sleeve's math) |
| Entry | the ledger's `entry`, the trigger hour's close; verified equal to the Binance perpetual 1-minute close at the end of that hour on all 122 fires (max relative difference 0.0) |
| Price path | Binance USD-M BTCUSDT perpetual 1-minute bars from the ORB study's checksum-verified archive panel (`orb_study/cache/BTCUSDT_perp_1m.npz`, logical sha256 in its meta); zero-volume outage minutes are missing |
| Hourly flush inputs | study snapshot `p300-study-snapshots/exit_policy_2026_09/squeeze_bull_hourly.npz` (`cd_futures_ohlcv` ⋈ `cd_open_interest`, captured 2026-09-15 13:20 UTC, rows before 2026-09-15; hashes in `results/squeeze_bull/hourly_snapshot.json`) |
| Funding | ORB study archive `BTCUSDT_funding.npz` (actual 8-hourly settlements) |
| Units | **R = 2 % of entry for every arm**, the bot's reference distance (both live variants are measured against it) |
| Cost | the booked 7 bp round trip (measured, `PAPER_COST_BP_RT`): `cost_R = 7/10000 × entry / risk = 0.035 R` |

## 3. Walker (1-minute, every arm)

Entry at `entry_ts` = the trigger bar's open + 1 h. Minutes `m` from `entry_ts`, in order, missing minutes skipped:

1. **Time exit** (arms with one): at the first tradable minute opening at or after `entry_ts + H`, exit at its open.
   **Censoring** (arms without one): the same at `entry_ts + 720 h` (`censored_horizon`); at the panel's end,
   the last close (`censored_data_end`).
2. **Stop or catastrophe stop**: a minute opening at or below the level exits at its open, otherwise a low at or
   below the level exits at the level.
3. **Target**: a high at or above the level exits at the level (the execution study found every target touch
   traded through). The stop is checked before the target within a minute.
4. **Flush resumed** (arm C_REFLUSH): at the close of each hourly bar whose 4-bar window lies wholly after the trigger
   bar (bar open ≥ trigger open + 4 h), the sleeve's own `is_flush(oi_chg_4h, px_chg_4h)` on the snapshot's joined
   hourly frame (positional 4-bar changes, as the sleeve computes them); exit at the open of the next minute.

`R_price = (exit − entry) / risk − cost_R`. `funding_R = Σ −rate_s × mark_s / risk` over settlements after `entry_ts`
and up to the exit minute's open (included for stop / target exits, excluded for exits at an open); `mark_s` = the
open of the minute at `s`. **Net R = R_price + funding_R.**

## 4. Arms

| Family | ID | Stop | Target | Time exit | Other |
|---|---|---|---|---|---|
| incumbent | **S0** (shipped) | −2 % | +3 % | 48 h | |
| incumbent | S_T12, S_T24, S_T72 | −2 % | +3 % | 12 / 24 / 72 h | the grid's other holds |
| incumbent | **S_NOTIME** | −2 % | +3 % | none (720 h) | |
| incumbent | S_TGT4, S_TGT5, S_TGT6 | −2 % | +4 / +5 / +6 % | 48 h | beyond the grid |
| incumbent | S_NOTGT | −2 % | none | 48 h | |
| incumbent | **C_REFLUSH** | −2 % | +3 % | none (720 h) | exit when the flush resumes |
| twin | **N0** (shipped twin) | none | +3 % | 48 h | |
| twin | N_NOTIME | catastrophe −10 % | +3 % | none (720 h) | |
| twin | N_NOTGT | none | none | 48 h | the sizing study's P1 |

## 5. What is reported

- Per arm: mean net R, win rate, exit mix, holding time, funding; bot-faithful R (no funding) beside it.
- Paired differences on the 122 fires: incumbent arms minus S0, twin arms minus N0, and **N0 minus S0**. Mean and 95%
  interval from a 30-day circular block bootstrap of entry days (10,000 draws, seed 42); by half (entry before / from
  **2024-06-14**, the calendar midpoint of the fire span; 81 fires before, 41 from) and by year. **No Holm correction and no decision rule: nothing is decided.**
- The trades S0 closes on its time stop, followed under S_NOTIME.
- **How far bounces run:** maximum favourable and adverse excursion within 48 h and 7 days; the share of fires that
  reach +3 / +4 / +5 / +6 % before −2 %; mean move at 1, 2, 4, 8, 12, 24, 48, 72, 96 and 168 h with 95% intervals.
- The live sequence per arm (one open position per variant, as the bot enforces), fixed $10,000, 1 % risk
  (0.5× notional): trades taken, total return and maximum drawdown against the fixed capital with day-end and exit marks.

## 6. Preconditions (before F0)

| # | Check |
|---|---|
| Q1 | Ledger, price panel, funding and hourly snapshot match their recorded hashes |
| Q2 | Venue identity: ledger entry = perp 1-minute close at the end of the trigger hour on all 122 fires (measured before this document: 122 / 122, max difference 0.0) |
| Q3 | Research parity: the sleeve's own `replay_bracket` on the snapshot's hourly bars reproduces the ledger's `r_outcome` and `exit_kind` on all 122 fires (≤ 1e-9) |
| Q4 | Funding complete over every walk path; missing minutes counted |
| Q5 | Synthetic walker fixtures pass (`tests/test_sqb_walk.py`) |
| Q6 | Truncation: deleting every minute after a cut leaves every exit completed before it unchanged, every arm |

## 7. Information already seen (disclosure)

- The ledger's shipped-exit outcomes on the hourly research replay (18 bp); the revalidation's full-sample and OOS
  numbers (114 / 122 bull fires, +0.273 R, OOS n = 10 +0.202 / +0.246 R causal).
- The S1 sizing study on these fires (1-minute spot path, gross R): shipped +0.334 R, no stop with target and 48 h
  +0.526 R, no stop no target +0.566 R, worst trade −4.17 R; its halves and drawdowns; and the research baseline JSON
  for the results warning (incumbent +0.284, twin +0.426 % of capital per trade after single-open, 7 bp and funding).
- The exit grid's provenance (80 combinations, picked on full-sample MAR with the out-of-sample in the selection);
  exit-kind shares from phase 2 and the revalidation (target 33–44 %).
- SJ-4250's path (rose +2.68 % after its time stop, target not touched as of 2026-09-15 08:13 UTC).
- The chento arm of this study (2026-09-15): shorter time stops worse, no time stop not reliably better. A different
  strategy; disclosed, not evidence here.
- No excursion statistic, forward-move profile, flush-resumed timing or perp-path outcome of these fires had been
  computed when this was written.

## 8. Freeze

F0 (before any arm outcome): this file, the code, the tests, `hourly_snapshot.json`, the precondition results and
input hashes, written to `results/squeeze_bull/freeze_F0.json`. Then one outcome run writing `report.json`. Manifests,
not commits, unless the user asks.
