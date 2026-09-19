# CHENTO_TRIPLE_V3 — calibration log

Single source of truth for "what is calibrated right now" and the provenance
of each change. Update this file in the same commit as any config/sizing
change (definition-of-done rule from the pool plan's A4, carried into the
bot-extraction plan).

## Current state (2026-09-14)

**Strategy params** (`bots/chento_v3/strategy/config.py`) —
signal unchanged from the 2026-06-05 calibration: 5×ATR(14) stop, 6R target,
72h TIF, B1-anchored trigger (`b1_now & b5_w & b7_w`, 24h window), **2 filters
ON for both assets** (no_resist_OB_2R, skip_up_30d_shorts); **no_tilt and the ETH
half-after-loss OFF since 2026-09-19** (decision 15, row below); **okx_aligned OFF since
2026-09-14** (verdict RETIRE, row below), LADDER_ENABLED=False (P1 backward-only
verdict), 6h cooldown. No single-open guard: positions stack, bounded by the
6h cooldown and the 72h TIF (see the 2026-09-14 row for the measured exposure).
**Cost `COST_BP_RT` 10 bp since 2026-09-12** (was 18, the June research
convention): measured on the sleeve's own fires, see the log row. Both the
BTC and ETH legs share the constant.

**Bot-level** (`bots/chento_v3/config.py`, standalone bot since 2026-07-21):
- Variant `bot_chento_v3_v1`, capital $10,000 paper.
- Sizing: fixed-R **2%**/trade over the sleeve's own 5×ATR stop
  (notional = capital × 2% / stop_pct), notional cap 3× capital,
  alloc/leverage expressed via Intent replace — sleeve code untouched.
- Diagnostics permanently ON (`CHENTO_V3_DIAG=1`,
  `bots/chento_v3/logs/diag.jsonl`).
- Stale-input policy: mgmt tables (cd_futures_15m, btc_1m) stale → skip
  tick; entry tables (ca_long_short_ratio; okx_perp_1h until 2026-09-14) stale →
  sweep runs, entries refused loudly. The OKX table must still EXIST — the
  sleeve loads it and computes okx_delta_z every rebuild — but nothing reads
  or records that z any more.

## Replay baseline (shipped config: ladder OFF)

> **SUPERSEDED 2026-09-13 — do NOT use `__replay_p0gate` as a gate, and do not
> diff a new replay against it.** It was recorded while the three feature loaders
> had no upper clock bound, so every tick of that replay could read the whole
> database's future. The OKX gate's sign differs from its peeking value on ~31% of
> bars, and `ret_30d` by 1.4 pp on average (8 pp at p99), so the +$1,043.37 below
> measures a different information set from anything the live bot can see — a new
> baseline can move in EITHER direction by more than noise, and that move is not a
> regression. No dial may be re-tuned to recover the old number. A new baseline
> needs re-recording on the bounded loaders (and, per the 2026-09-12 row, at 10 bp,
> and per the 2026-09-14 row with the OKX gate off).
> Walking replays are ~15x slower since the same fix (replay-only frame rebuild).

`__replay_p0gate` (2026-07-22, window 2025-06-06 → 2025-12-06, 15m ticks,
chento-only): **8 trades, +$1,043.37, WR 62.5%**. Supersedes
`__replay__rgap_fix_a` (+$962.28), which pre-dated the 2026-06-05
LADDER_ENABLED=False ship — all 8 entries byte-identical between the two;
the 3 exit diffs are exactly the baseline's ladder-widened (1.5R) stops
firing where the shipped 1R stop exits earlier.

## Change history

| Date | Change | Why / provenance |
|---|---|---|
| 2026-09-19 | **Post-loss rules retired on both assets** — `FILTER_NO_TILT = False` (BTC no longer skips 48 h after a closed stop) and `TILT_HALF_AFTER_LOSS = False` (ETH no longer halves the next trade's risk). Code paths kept, flags off. Seven BTC goldens re-baselined: `filter_no_tilt` true → false and the `no_tilt` key leaves `_filter_diag`; the ETH goldens are unchanged. Takes effect when chento_v3 and chento_v3_eth restart. | BACKLOG decision 15, operator 2026-09-19. The bots' rules were not the overlay study's (BTC 94 vs 198 of 208 triggers; ETH differs on 54 of 184 trades), the 48 h was never fitted, and on the gate-off pool the effect is noise-sized (MAR none 7.20 / skip 8.16 / half 8.34; skip lowers DSR 0.726 → 0.461). |
| 2026-09-19 | **The OKX-era figures retired** — the ETH kill rule (< +0.3 R after 15 trades), the +0.739 / +0.605 R expectancies, the overlay tilt ranking, the attribution split, the LSR B5 scores and the audit DSRs describe the gated configuration that stopped 2026-09-14. No parameter changed. The paper track's rule is the dashboard's results warnings; the research baselines the dashboard compares against were already built on the gate-off pool (BTC 198 trades, ETH 184). | BACKLOG decision 14, operator 2026-09-19. Re-cut only what a named decision needs. |
| 2026-09-14 | **The scheduled-exit backstop books the sleeve's own cost: 10 bp, no slippage, no funding.** No parameter changed. `botlib.close_due_trades` now closes through `signal._close_paper` instead of the `trades.py` defaults (10 bp fee + 5 bp slippage + funding). This applies to BTC and ETH alike, because the ETH process runs the same tick. **This happened live:** SJ-4243 and SJ-4245 (`bot_chento_v3_v1`, 2026-08-24) were closed by the backstop at 10 + 5 bp with funding −0.086 % / −0.090 %. Their duplicate-incident twins SJ-4244 / SJ-4246 closed `tif_expiry` at the then 18 bp with no funding. Against the sleeve's cost at the time, the error is −$4.81 / −$4.48 (−0.024 / −0.022 R); against today's 10 bp with no funding, −$11.70 / −$10.46 (−0.059 / −0.052 R). **Not restated, record only:** the concluded OKX re-validation reads these rows (`okxlib.py` `P3_IDS`). A de-duplication of the incident rows must note that the `scheduled_exit` twin carries the backstop cost. **Why chento reaches the backstop at all:** on the tick that walks the bar just before the TIF bar, neither the in-walker TIF check (bar open ≥ exit time) nor the zero-walk TIF close fires, so the backstop closes the trade. Before the 90 s settle margin (`c0cdc75`, 2026-09-06) that was the usual path. Under it, about 12.5 % of TIF exits are expected to go through the backstop (only entries 30 s or more after the 15m boundary are exposed). Costs now match; the price source (the tick's quote, not the bar close) and the label (`scheduled_exit`, not `tif_expiry`) still do not. Also: a tick whose `decide()` raises now still runs the backstop and reports heartbeat `error`. Takes effect when chento_v3 and chento_v3_eth restart (outside the 180 s evaluation window). Restarted 2026-09-14 19:06:27Z stop / 19:06:59Z start (commits 1c201c5, 5485899, 5ce3e7e); backstop closes before that booked the old defaults. | BACKLOG 4.4 and 18; operator go-ahead 2026-09-14. `test_chento_bot.py`: backstop cost (90.00 on +$100 of price P&L; the old backstop booked 75.00) and raising decide. Open follow-up: the zero-walk TIF condition, a sleeve and golden change that needs its own go-ahead. |
| 2026-09-14 | **Restart onto the gate-off commit `53d3393`.** chento_v3 stopped 08:48:42 UTC and came back as pid 61200 at ~08:48:52; chento_v3_eth pid 57356 at ~08:49:03; the dashboard (it caches the strategy config) pid 60892 at 08:45:55. Timed after the 08:45 evaluation window closed (08:48:30), so no bar was evaluated twice. **The first bar evaluated gate-off is the 08:45 bar, at the 09:00 boundary**: split paper-track, attribution and the ETH kill rule at 2026-09-14 08:45 (bar open). Checks: 0 open chento positions and no chento stop in the prior 48h (so clearing BTC's in-memory 48h skip lost nothing); fresh heartbeats from the new pids; one unit per fleet member; `health.py` 0; the dashboard reports the OKX filter `off` on both bots. **Diag note:** the 2026-09-14 lines in both `diag.jsonl` files cover the restart to 00:00 UTC only (counters live in memory and are written at day rollover), so they hold gate-off evaluations only; the dashboard's 09-14 diagnostics tile is partial for the same reason, as were the short 09-12 / 09-13 lines after those days' restarts. | Operator go-ahead 2026-09-14; study README §6 names the restart as part of the switch. Suite 1222 passed and the drill (baseline green, 38 caught, 0 missed, 0 skipped) ran on the committed tree before the restart. |
| 2026-09-14 | **OKX gate OFF on both legs** — `FILTER_OKX_ALIGNED = False` (the ETH leg resolves the same config). Also: `okx_perp_1h` / `okx_perp_eth_1h` removed from the two bots' `ENTRY_TABLES`, so a stale OKX table no longer refuses entries it no longer decides. okx_delta_z is still computed into the frame each rebuild but is no longer written to `_filter_diag`, `trades.notes` or the diag near-misses — **the per-row marker of a gated trade is that key: rows with `_filter_diag.okx_delta_z` are gated, rows without are gate-off.** Goldens re-baselined: six BTC documents lose exactly their `okx_delta_z` keys and nothing else; `chento_btc_okx_blocked` became `chento_btc_okx_gate_off` (the same 2026-07-16 06:30 bar now decides long); new `chento_eth_okx_gate_off` (a short at z +0.552 that now decides) and `chento_btc_resist_ob_veto` (the only filter_blocked golden left, a filter-2 veto at 1.35R). The live-ledger z pins now read the feature frame. Drill: the "gate removed" mutation (now dead code) replaced by "gate switched back on" in each process plus an OB-veto mutation; the drill now fails on a red baseline or any skipped entry. **Restart time: recorded in the follow-up row.** | Pre-registered verdict **RETIRE**, 2026-09-13 (`studies/notebooks/okx_gate_revalidation/findings.md`, run commit `2406b6a`): kept beat blocked by +0.30R, but the 90% CI [−0.04, +0.66] includes 0 while the blocked trades earned +111R; §6 fixed the direction in advance. Operator go-ahead to switch off 2026-09-14 ("a gate that cuts so much profit is not a good gate"). **§6 sizing and concurrency review (2026-09-14)**, two independent simulations reconciled, on the study's trades in the bots' own sequence (6h cooldown, BTC 48h skip after a stop loss, ETH half after a loss, fixed 10k capital, 3× per-trade cap), gated → gate-off: trades BTC 90 → 198, ETH 81 → 184 (5.44 y); max open BTC 3 → 5, ETH 4 → 6; peak open risk BTC 6% → 10%, ETH 8% → 12%; peak gross notional BTC 5.53× → 8.53×, ETH 3.40× → 4.39×; worst 72h stop cluster BTC −4.6% → −6.6%, ETH −8.3% both; mark-to-market max DD (additive, day-end + exit marks) BTC 23.2% → 33.4%, ETH 10.9% → 20.9% (both bots as one 20k book 11.1% → 19.7%); total additive return BTC +146% → +290%, ETH +80% → +130%. **Decision: keep RISK_PCT 2% and the 3× cap for paper; accept stacking.** A single-open guard would change which trades are taken for about half the gate-off pool, which RETIRE did not measure. Before real capital: a per-bot open-risk or gross budget, or lower risk (1.5% gives BTC 25% / ETH 16% DD), as its own pre-registered test. Known at the switch: no code caps total exposure (structural max 12 open per bot); BTC starts inside an unrecovered drawdown (peak 2026-02-06); the restart clears BTC's in-memory 48h skip (no BTC stop in the prior 48h); `strategy_health` will show negative gross headroom on bot_chento_* (2.5× default). **Numbers that describe the RETIRED config:** the +0.739 / +0.605 R expectancy row below, the ETH go/no-go and the ETH kill rule (< +0.3R after 15 trades) were set on OKX-gated pools — the ETH leg's first 15 trades will all be gate-off, so that rule needs its own decision before it is applied. The study's report-only gate-off arm: BTC +0.731 R (208), ETH +0.540 R (184), every trade taken. |
| 2026-09-13 | **ETH half-after-loss reads the last ACTUAL close.** `bots/chento_v3/runner.py::_last_closed_was_loss` ordered by `exit_time`, which holds the SCHEDULED time stop and is never overwritten on close (SJ-4248 stopped 2026-08-22 but carries `exit_time` 2026-08-25). An early stop-out therefore sorted as if it closed days later. The bug cut both ways — it could halve the next ETH position after a win, or skip halving after a real loss; both are now tested. Now ordered by `COALESCE(actual_exit_time, exit_time)`. No parameter changed; BTC unaffected (it uses the sleeve's no-tilt skip, not this function). Latent until now: `bot_chento_v3_eth` has no closed trades | BACKLOG 12b, found by the item-11 time-stop census. User go-ahead 2026-09-13. The function had no test before this. Needs a chento_v3_eth restart |
| 2026-09-13 | **Loaders bounded at the clock — no parameter changed, live unchanged, replay and goldens corrected.** `_load_15m_btc` and `_load_lsr_btc` now stop at `now` (still including the forming 15m bar); `_load_okx_1h` stops at `now − 3600`, the last CLOSED hour. Replay-only stale-frame rebuild added. **Live: measured no-op** — at a live clock the BTC and ETH frames are identical to before (8,640 bars, same last bar, all 36 columns and every order block). **Goldens: six BTC re-recorded, one moved.** Every changed line is `okx_delta_z`, and every new value equals what the live bot recorded in `trades.notes`: SJ-4243 1.4609786480, SJ-4245 0.1437340057, SJ-4248 1.6751492272. The old values (−0.7299, 0.4754, 0.9437) matched none. `chento_btc_okx_blocked` asserted the OKX gate blocked 2026-08-21 06:00 — **live traded that signal**; its anchor moved to 2026-07-16 06:30, which blocks on both information sets. A walking replay of 2026-08-21 00:00 → 08-22 06:00 now decides at exactly the three bars live traded (06:00, 19:30, 03:45), where before it blocked 06:00 on 59 minutes of future OKX data. ETH goldens unchanged | BACKLOG 7b. User go-ahead 2026-09-13. The old docstring claimed a trailing-only frame was safe unbounded; `ret_30d` and `okx_delta_z` come off resamples whose last bucket reaches past the bar. The `-3600` is load-bearing: `<= now` pairs a complete OKX hour with a truncated Binance hour and flips the gate on ~36% of bars. **Open consequence:** the OKX gate's study (−25% DD, +34% OOS) used same-hour complete bars on both venues, which live never sees — scheduled for re-validation on the causal information set. Guards: `tests/test_chento_clock_bound.py` (4 arms, both directions) and five drill mutations. Restart of both chento bots needed to put the code on disk into the running processes |
| 2026-09-12 | `COST_BP_RT` 18 → 10 bp (`SLIPPAGE_BP_RT` stays 0) | `studies/notebooks/execution_2026_09/` E6: measured taker round trip on the sleeve's own 2020–2026 fires 9.6 bp BTC [CI90 8.4, 10.8], 10.0 bp ETH [8.0, 11.9]; half-spread < 1 bp, drift −0.5 / −0.2 bp, zero stop gap-throughs in 54 / 33 stops; the pre-registered change rule (> 3 bp and CI excludes the coded value) passed. Net expectancy on the same fires +0.685 → +0.739 R BTC, +0.563 → +0.605 R ETH. Trades closed before this date carry 18 bp, and the 2026-07-22 replay baseline (+$1,043.37) was booked at 18 bp — any later replay-equivalence gate must re-cost. User go-ahead 2026-09-12. |
| 2026-07-22 | **P0 live boundary-eval fix**: live entry path anchors on wall-clock 15m boundaries, evaluates the JUST-CLOSED bar with final values (intraday cache refresh; forming bar never evaluated); `_just_closed_15m_ts` → last fully-closed bar (walker partial-bar protection). Replay path untouched (`clock.is_simulated()` branch). | Day-1 telemetry: 850/850 live evals `boundary_skipped` — entry path was dead (2nd live lockout after OKX). Gate: replay entries 8/8 byte-identical. |
| 2026-07-21 | B5 `compute_lsr_extremes` min_periods `max(8, w//4)` → `w//4` | Byte-equivalence violation vs `validation_B5_lsr_extremes` caught by new `tests/test_chento_parity.py` (NaN-mask diff in warmup rows 7-8; zero live impact). Research semantics are the validated ones. |
| 2026-07-21 | Extracted to standalone bot (`bots/chento_v3/`), fixed-R 2% sizing, diag on | Bot-extraction plan M1. Previous life inside P-300: **zero trades ever — OKX gate was stale-locked since 2026-05-27** (okx_perp_1h had no live writer). Feed now refreshes OKX hourly; runner refuses stale inputs loudly. |
| 2026-06-05 | atr5_t6R + no_tilt + no_resist_OB_2R confirmed; ladder disabled | Triple composite optimization + P1 backward-only Pareto test (memories: chento-triple-optimized-config, chento-v3-p1-ladder-verdict) |
| 2026-06-04 | Fix A intra-bar walking; B1-anchored trigger | chento-v3-b1-anchored memory |
| 2026-05-30 | B7 resample-bucket → rolling-sum + median-z (byte-equivalence fix) | chento-v3-b7-bug memory |

## 2026-08-23 — ETH leg (multi-asset plan Phase B)

- Sleeve asset-parameterized via `CHENTO_V3_ASSET` env (config resolves
  `cd_futures_eth_15m` / `okx_perp_eth_1h` / LSR asset='ETH'); BTC leg
  byte-identical (test_chento_parity.py green through the refactor).
- New bot `bots/chento_v3_eth` (variant `bot_chento_v3_eth`, $10k paper,
  2%/trade, 3x cap) — thin wrapper over the shared runner.
- Per-asset tilt policy per the overlay study + backward-only confirmation:
  BTC keeps FILTER_NO_TILT (skip-after-loss); ETH disables the skip and
  halves risk after a loss at the bot layer (TILT_HALF_AFTER_LOSS).
  **Both retired 2026-09-19 (decision 15): no post-loss rule on either asset.**
- Go/no-go basis: backward-only research pool ETH +0.70R mean / 44% WR /
  n=73 (2021→2026-05), attribution timing +0.70R vs regime −0.10R.
  Underwrite expectancy: ~+0.7R region, NOT the +1.28R research figure.
- Paper gate: **retired 2026-09-19 (decision 14).** It was set on the OKX-gated
  pool, which stopped running 2026-09-14; the dashboard's results warnings
  (AMBER below research from n ≥ 5, RED negative at any n) are the paper
  track's only rule now. For the record it read: ≥2 months or ≥10 trades; kill
  at < +0.3R/trade after 15 trades (plan doc:
  studies/material/plans/multi_asset_chento_plan.md).
