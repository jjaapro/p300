# Exit-policy study, chento arm — PRE-REGISTRATION v1.0

**Written 2026-09-15, before any outcome of any exit arm in this document was computed.**
BACKLOG item 11 (are the time stops worth anything?) merged with research queue 2 (exits that fire when a
trade is shown wrong), chento arm first, as the roadmap orders it. The squeeze arms are a later phase with
their own pre-registration and stay report-only until their n = 20 / 30 re-cuts.

The user's hypothesis, on record before any number (2026-09-14): *"time stop overall feels a bad idea, it just
means that we do not actually know if the trade was valid or not."* This study tests it, and the alternative it
implies: exits that fire when the trade is contradicted.

Nothing under `bots/`, `strategies/` or `data/` changes inside this study. The only database read is the frozen
OKX-study snapshot, opened read-only.

## 1. Questions

- **Q1 (the hypothesis).** On identical entries, does removing the 72 h time stop (arm A1) raise net R per trade
  compared with the shipped exit (A0)?
- **Q2.** Does an invalidation exit on top of A1 beat A0: a flow reversal at any time (X1), a flow reversal only
  while the trade is losing (X2), or the opposite chento signal (X3)?
- **Q3.** Does another time stop from the historical grid (6, 12, 24, 48 or 168 h) beat 72 h?
- **Mechanism (report-only).** How chento's edge accrues after entry, and what the trades A0 closes on its time
  stop do when held.

Mechanism statement written before outcomes: chento fades crowd-positioning extremes when taker flow diverges
from price. If the reversion completes within about three days, a 72 h exit costs little; if it is slower or
path-dependent, holding to the structural stop or target captures more. A fresh absorption against the position
(passive flow absorbing aggressive flow in the trade's direction) or the full composite firing the other way says
the reason for the trade has reversed. The "1–3 days to resolve" story for 72 h was written after that value was
picked in-sample (BACKLOG item 11), so it is a hypothesis here, not support.

## 2. Inputs (frozen)

| Item | Frozen value |
|---|---|
| Snapshot | `C:\Source\Repos\p300-study-snapshots\okx_gate_revalidation\snapshot.db`, sha256 `f3decffe3e5d740bf2678a9f6db29997ace4731ea0655a25274e71aa56b75c81`, rows `< 2026-09-12 00:00 UTC`, opened `mode=ro` |
| Entries | The OKX re-validation's gate-off arm: `okx_gate_revalidation/results/features_{BTC,ETH}.csv` (sha256 `c1762d57…`, `83663d81…`) through `okxlib.membership` (`in_off`: valid ATR, filter 2 `dist_R > 2`, filter 4 no shorts when `ret_30d > +10%`); trigger bar open `t` in [2021-04-01, 2026-09-08). **BTC 208, ETH 184, 392 trades.** Every trade is taken (no cooldown or tilt at pool level) |
| Trade geometry | As the bot: entry = trigger bar close; risk = 5 × ATR(14, 15m); stop = entry ∓ risk; target = entry ± 6R |
| Price path | Snapshot `cd_futures_15m` (BTC) and `cd_futures_eth_15m` (ETH), bar-open stamped |
| Flow features | `math.compute_moneyflow_signal(df, cvd_window_bars=2880, velocity_window_bars=4)` (the bot's own function and constants) on each asset's full snapshot 15m history: `cvd_z`, `vel_z` |
| Opposite-signal stream | `okx_gate_revalidation/results/pool_{BTC,ETH}.csv` (sha256 `1063f2f5…`, `15f41e5a…`): every backward-only Triple trigger in the window, before filters (pool fidelity to the bot's anchors 0.987) |
| Funding | Binance USD-M settlement history from the ORB study's checksum-verified archive: `orb_study/cache/{BTCUSDT,ETHUSDT}_funding.npz` (logical sha256 in their `.meta.json`), 8-hourly, complete 2020-01-01 → 2026-09-13 |
| Cost | 10 bp round trip exactly as the bot (`cost_R = 10/10000 × entry/risk`); 18 bp secondary |

## 3. Walker (every arm)

For a trade with trigger bar `t` (entry at the bar's close, `entry_ts = t + 15m`), bars `b = t + 15m, t + 30m, …`
in order; a missing bar is skipped and counted:

1. **Time exit** (arms that have one): if `b ≥ t + TIF`, exit at the close of bar `b`. Checked before anything
   else on the bar, as the bot does; this is the OKX study's frozen convention, measured against the live ledger.
2. **Stop / target**: `math.evaluate_position_step` (stop before target within a bar, fills at the level).
3. **Condition exit** (X arms), decided on bar `b`'s close and filled at that close.
4. **Censoring** (arms without a time exit): at `b ≥ t + 720 h` exit at the close of bar `b`
   (`censored_horizon`); if the snapshot ends first, exit at the close of its last bar (`censored_data_end`).
   The 30-day horizon is a device to finish the study, not a proposed exit; both counts are reported.

`R_price = side × (exit − entry) / risk − cost_R`.
`funding_R = Σ −side × rate_s × mark_s / risk` over settlements `s` with `entry_ts < s` and, for stop and target
exits, `s ≤` the exit bar's open (the exit happens inside that bar), for close exits `s <` the exit bar's close.
`mark_s` = close of the 15m bar ending at `s`.
**Net R = R_price + funding_R** (decision-bearing). **Bot-faithful R = R_price** (the bot books no funding) is
reported alongside.

## 4. Arms

| ID | Exit rule |
|---|---|
| **A0** | shipped: stop, 6R target, time exit 72 h |
| A2_6, A2_12, A2_24, A2_48, A2_168 | stop, target, time exit 6 / 12 / 24 / 48 / 168 h (the historical grid, reused so the dial's trial count does not grow) |
| **A1** | stop, target, no time exit (censored at 720 h) |
| **X1** | A1 + exit at the first bar close with ABS_AGAINST(k) |
| **X2** | A1 + exit at the first bar close with ABS_AGAINST(k) **and** that close on the losing side of entry (long: close < entry; short: close > entry) |
| **X3** | A1 + exit at the close of the first opposite-direction trigger bar `t' ≥ t + 15m` in the pool stream |

**ABS_AGAINST(k)** at bar `b` is the bot's own B1 absorption rule pointed against the position with a stricter
flow threshold: for a long, `cvd_z > k` and `|vel_z| < 1.0`; for a short, `cvd_z < −k` and `|vel_z| < 1.0`
(B1 enters at 0.5; `B1_VEL_Z_MAX = 1.0`).

Candidate family against A0: **M = 9** (A2 × 5, A1, X1, X2, X3).

## 5. Step 0 — freeze k before any R

On the 392 entries, for k ∈ {1, 2, 3}: hours from `entry_ts` to the close of the first bar with ABS_AGAINST(k)
within 720 h (censored at 720 h), with median, quartiles, the share firing within 24 h and 72 h, and the per-bar
fire rate. **Rule: k = the smallest value whose median time to first fire is at least 72 h; if none qualifies,
k = 3 and X1/X2 are reported as exits faster than the shipped time stop.** Step 0 reads only `cvd_z` and `vel_z` —
no price relative to entry, no R — and is written to `results/chento/step0.json` and hashed into F0. The reason for
the rule: at B1's live threshold the signal could fire within hours and act as a hidden shorter time stop, which is
not what the hypothesis asks.

## 6. Statistics

- Per-trade paired difference `d_i(C) = netR_i(C) − netR_i(A0)`; `d̄` = mean over the 392 trades.
- Also by asset, by half (entry day before / from **2023-12-20**, the window's midpoint) and by year.
- **Bootstrap:** calendar entry-day axis 2021-04-01 → 2026-09-08 (1,987 days); per day the sum of `d` and the
  trade count; circular blocks of **30 days**, **10,000** draws, seed **42**; each draw's mean is its summed `d`
  over its summed count. 95% percentile interval. One-sided p for `d̄ > 0` from the recentred draws:
  `(1 + #{draw − d̄ ≥ d̄}) / (10,000 + 1)`. **Holm** across the nine candidates at family α = 0.05.

## 7. Decision rule

A candidate C **passes** when all hold:

- **D1** effect: pooled `d̄ ≥ +0.10 R`.
- **D2** significance: Holm-adjusted one-sided p ≤ 0.05.
- **D3** stability: `d̄ > 0` on BTC and on ETH, and in both halves.
- **D4** walk-forward: folds `gates.walk_forward_folds(entry days, 730, 365, 365)`. In each fold the arm with the
  highest mean net R over the fit window's trades is selected among A0 and the nine candidates (ties: A0, then the
  order of section 4). The stitched out-of-sample mean of (selected − A0) is > 0, and C is selected in at least
  half the folds.

Verdicts, evaluated in this order:

| Verdict | Rule |
|---|---|
| `INVALID` | a precondition of section 8 fails after F0; one repair through a dated addendum before any rerun |
| `CHANGE_SUPPORTED(C*)` | at least one candidate passes; C* is the passing candidate with the largest `d̄` (within 0.02 R the earlier of A1, X3, X1, X2, A2_168, A2_48, A2_24, A2_12, A2_6) |
| `KEEP_72H` | no candidate passes and every candidate's 95% upper bound of `d̄` is below +0.10 R |
| `INCONCLUSIVE` | otherwise |

The hypothesis gets its own sentence, whatever the verdict: **supported** if A1 passes; **contradicted** if A1's
95% upper bound of `d̄` is below 0; **not settled** otherwise.

## 8. Preconditions (checked before F0)

| # | Check |
|---|---|
| P1 | Snapshot sha256; OKX input files match their recorded sha256 (features, pool) and the committed trade files |
| P2 | **A0 parity:** the walker's A0 with no funding at 10 bp reproduces `okx_gate_revalidation/results/trades_{BTC,ETH}.csv` for all 392 trades — exit kind, exit bar, exit price and R (≤ 1e-9) |
| P3 | **Flow-feature parity:** for the first 20 gate-off trades per asset, at the trigger bar and at `t + 24 h` and `t + 48 h` (60 bars per asset), `cvd_z` and `vel_z` from the full-history frame equal the bot's own frame rebuilt at that clock (≤ 1e-9), and ABS_AGAINST(k) is identical for k = 1, 2, 3 |
| P4 | Funding: no gap longer than 8 h 5 min in the settlement history over any walk path |
| P5 | Bars: missing bars on walk paths counted; none expected (the OKX study measured 0 over 72 h paths) |
| P6 | Synthetic walker fixtures pass (`tests/test_chento_walk.py`), and the whole outcome path runs end to end on synthetic random-walk markets (`tests/test_chento_outcomes_smoke.py`, opt-in with `EXIT_SMOKE=1`) |
| P7 | Truncation: deleting every bar after a cut leaves every exit completed before the cut unchanged, for every arm |

## 9. Report-only

- Exit mix, holding time and funding by arm; bot-faithful R; 18 bp.
- The trades A0 closes on its time stop, followed under A1: exit kinds, R, hours to resolution.
- **Forward-return profile** with no exit at all: mean signed move in R at 6, 12, 24, 48, 72, 120, 168, 336 and
  720 h after entry, with 95% day-block intervals; the horizon of the peak and the first horizon at half of it.
- **The bots' own sequence per arm**, per asset: 6 h cooldown on entries; BTC skips entries for 48 h after a losing
  stop exit; ETH halves risk after a losing close; only exits completed before a trigger can change it. Fixed
  $10,000, 2% risk, 3× notional cap. Trades taken, total R, mark-to-market maximum drawdown (day-end and exit
  marks), maximum concurrent positions, peak open risk.
- DSR of the best arm's daily net R at N = 21 (the dial's 12 historical values plus these 9) and N = 40.
- `d̄` by year for A1, X1, X2 and X3.

## 10. Power (planning, before outcomes)

The design is paired, so only trades whose exits differ contribute. Under A1 about 159 of 392 trades change (the
share A0 closes on its time stop, disclosed below). With a planning standard deviation of 2.5 R for their
differences, the standard error of `d̄` is about 0.08 R, about 0.10 R after day clustering; with Holm at M = 9 and
80% power the minimum detectable effect is about **0.36 R per trade**. Smaller true effects will usually end
`INCONCLUSIVE`, not `CHANGE_SUPPORTED`.

## 11. Information already seen (disclosure)

- **The A0 outcomes of these exact entries**: the OKX study's gate-off arm, BTC +0.731 R (208), ETH +0.540 R (184),
  pooled +0.641 R; exit mix pooled stop 194, target 39, time stop 159. Its production-sequence approximation and the
  §6 sizing review (gate-off mark-to-market drawdown BTC 33.4%, ETH 20.9%; up to 5 / 6 concurrent positions).
- **The time-stop grid** from 2026-05-26 (in-sample, on a pool with ±24 h look-ahead and a same-hour OKX z): mean R
  6 h +0.49, 12 h +0.86, 24 h +1.35, 48 h +1.80, 72 h +2.16, 168 h +2.85; the 2026-06-05 backward-only rerun
  re-picked 72 h as MAR-optimal. Neither is on this pool, cost or information set.
- Attribution layer: chento's exit component +0.03 R (BTC) / +0.10 R (ETH) on older pools. Every chento early-exit
  or take-profit variant tried (A7/A8/A9, wick exit, RSI scale-out) was killed for cutting the 6R tail.
- The ORB study (2026-09-15): on breakout trades, early and invalidation exits lowered gross and dropping the time
  exit raised it. A different payoff type; disclosed, not evidence here.
- SJ-4250 (squeeze_bull, a different bot) closed on its time stop and later rose 2.7%; one hindsight trade.
- BACKLOG's subjective prior that X2 beats A0 by a fixed rule: about 0.15.
- While writing this document: `cvd_z` / `vel_z` were not computed on any post-entry bar, and no exit of any arm
  other than A0's recorded outcomes was known.

## 12. What each verdict permits

- `CHANGE_SUPPORTED(C*)`: a proposal to the user to change chento's exit to C* on both legs, with that arm's
  exposure numbers from section 9 (longer holds stack more positions; BACKLOG 13). No change without the go-ahead;
  if approved it is its own commit with a calibration-log row, golden re-baseline and restart, and the paper track
  splits at the restart bar.
- `KEEP_72H`: keep the shipped exit and record that no tested alternative is worth +0.10 R per trade.
- `INCONCLUSIVE`: keep the shipped exit (this study does not justify a change) and record what the interval
  excludes.
- `INVALID`: no verdict and no change.

## 13. Freeze procedure

F0 (before any arm outcome): SHA-256 of this file, the study code and tests, `step0.json`, the precondition
results and every input hash, written to `results/chento/freeze_F0.json`. Then the outcome run, with the verdict
file written last. The freezes are manifests with UTC timestamps; they become git commits only if the user asks.
