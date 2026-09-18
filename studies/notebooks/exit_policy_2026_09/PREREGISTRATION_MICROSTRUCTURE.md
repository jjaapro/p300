# Exit-policy study, microstructure stage 1 — PRE-REGISTRATION v1.0

**Written 2026-09-15, before any event below was computed on any trade.** An information test, not an exit test:
it asks whether absorption, rejection and order-book events seen during a trade say anything about what the rest of
the trade is worth. **Nothing here permits a production change.** A positive result permits one thing: writing a
separate stage 2 pre-registration of exit arms that use exactly these event definitions.

The prompt was the user's, after the chento and squeeze_bull arms (2026-09-15): *"isn't time simply just statistical
overfitting? ... I still feel there is something in absorption, rejection and liquidity that could help to define exits
better."* The reply recorded before this document: the value of a time stop is a dial, but the thing a better exit
needs, information that the trade has gone wrong, is measurable, and that is measured first.

## 1. Why an information test before exit arms

An exit arm's result mixes two things: whatever the trigger knows, and the mechanical effect of leaving early. On
trades with positive drift, leaving early costs R whatever triggers it: chento's shorter time stops lost 0.13 to
0.57 R, and its flow-reversal exit (X1) lost 0.24 R against no time stop while firing on 141 of 392 trades. A
trigger can carry real information and still lose as an exit arm, or carry none and win by cutting exposure.

The test here separates the two. At the first event in a trade it measures the **continuation value**, what holding
from that minute to the shipped exit added in R. It compares that value with the continuation value of other trades
at the same point in their life, in the same profit bin and the same direction, with no event yet. With no
information, the difference is zero, whatever the exit rule and whatever the strategy's drift.

An exit also does not have to pay a round trip. The brainstorm session's microstructure features failed as entries
because they could not pay the fee. An exit signal pays nothing extra: the exit leg is paid either way. A small
informational edge can be worth having in an exit when it is worthless as an entry.

## 2. Mechanisms (what each event claims)

| Event | Claim | Against the position (tested) | Sign control (same pattern pointing with the position) |
|---|---|---|---|
| **E1 absorption** | Aggressive orders on the position's side are met by a passive participant who absorbs them; price does not advance, so a large player is leaning the other way | long: extreme taker **buying** in 5 min while price does not rise; short: extreme selling while price does not fall | long: extreme selling while price does not fall (buyers defending) |
| **E2 rejection** | Price runs through the prior 24 h extreme, where resting stops sit, finds no follow-through and closes back inside within 15 minutes: a failed breakout or liquidity grab. The move the trade needs is less likely | long: breaks the 24 h high, closes back below within 15 min; short: the 24 h low, mirrored | acceptance: the same first break holds for 15 minutes |
| **E3 book liquidity** | Resting depth within 1 % of mid is unusually tilted against the position: the path of least resistance points the other way | long: book unusually ask-heavy; short: bid-heavy | the book unusually tilted with the position |
| **E4 absorption at the level** | E1 happening at the 24 h extreme: a defended level | long: E1 against within 0.25 % of the 24 h high | none |

If a sign control shows the same result as its event, the pattern is activity or volatility, not the claimed
mechanism. Section 8 builds that into the decision.

## 3. Populations and the one price path (frozen)

| Population | Trades | Entry | Stop / target / time exit | R |
|---|---|---|---|---|
| **chento** | the 392 gate-off entries of the chento arm (`okx_gate_revalidation/results/features_{BTC,ETH}.csv`, `in_off == True`; BTC 208, ETH 184; long 226, short 166; 2021-04-17 → 2026-08-22) | signal bar open + 15 min | stop 1 R, target 6 R, **72 h** | the features `risk` (ATR-based) |
| **squeeze_bull** | the 122 bull fires of the squeeze_bull arm (`squeeze_bull_revalidation/results/full_oi_flush_ledger.csv`, `regime_backonly == "bull_30d"`), long | trigger hour open + 1 h | −2 %, +3 %, **48 h** (S0, shipped) | 2 % of entry |
| **short_squeeze** | the 71 triggers of the execution study's notebook replay (`execution_2026_09/results/e0_short_squeeze_notebook_replay.csv`, committed), long, 2022-05-06 → 2026-05-18 | trigger 15 m bar open + 15 min | the replay's stop (trigger low × 0.999) and target (3 R), **6 h** | entry − stop |

**Price path, all three:** Binance USD-M perpetual 1-minute bars, BTCUSDT and ETHUSDT, from the ORB study's
checksum-verified archive panel (`orb_study/cache/<SYMBOL>_perp_1m.npz`, logical sha256 in each meta file). Every
recorded entry equals the close of the minute before the entry minute on this path (measured before this document:
392 / 392, 122 / 122, 71 / 71, maximum difference 0.0). A minute with zero volume is missing: no fill, no mark, no
event.

**Out of scope:** the no-stop twins (the same entries and the same information; their exits come later), ADX, CARRY
and R4 (their exits define the strategy).

### 3.1 The shipped exit, re-walked on the 1-minute path (one walker for all three)

Entry minute `i0` = the minute opening at the entry time; horizon `H` hours. For minutes `i0 … i0 + 60H − 1`, in order,
missing minutes skipped:

1. **Stop:** a minute opening at or beyond the stop exits at its open; otherwise a low (long) / high (short) at or beyond
   the stop exits at the stop.
2. **Target:** a high (long) / low (short) at or beyond the target exits at the target. Stop before target within a minute.
3. **Time exit:** otherwise, at the open of the first non-missing minute opening at or after entry + `H`.

The exit minute is `x`. **A trade is open after the close of minute `b` exactly when `b < x`.** This is the squeeze_bull
arm's walker; for squeeze_bull it must reproduce that arm's S0 walks exactly (M4).

**Secondary walk (reported, not decided):** the same with no time exit (stop and target only, censored at 720 h or at
the panel end).

## 4. Data added for this stage

- **bookDepth**, Binance's daily archive for BTCUSDT and ETHUSDT, 2023-01-01 → 2026-09-13, every zip verified against
  its published CHECKSUM (`micro_data.py`, manifest `data/raw/bookDepth/manifest.json`). Only the ±1 % rows are used, the
  nearest level present across the whole archive. Per minute: bid and ask notional of the **last snapshot stamped
  inside the minute** (snapshots are about 30 s apart); no snapshot in the minute means missing. Days the archive does
  not serve are recorded, never filled.
- Taker flow is the 1-minute panel's `taker_buy_volume` (base units). No prod.db access anywhere in this stage.

## 5. Event definitions

`s = +1` long, `−1` short. Minute `b` is the bar opening at `b`, known at its close. Every event requires: the trade is
open after `b` (`b < x`), `close_b` is not missing, and every minute the event's window uses lies at or after `i0`.
Only the **first** event of each kind in a trade is used.

### E1 — absorption (5-minute taker flow)

- `d_b = 2 × taker_buy_volume_b − volume_b`; `D_b = d_{b−4} + … + d_b` (missing if any of the five is missing).
- `z_b = (D_b − mean) / sd`, mean and sd over `D` at minutes `b − 10084 … b − 5` (the 7 days of 5-minute sums ending just
  before the window starts), at least 5,040 present, else missing. Computed per asset on the whole panel.
- `ΔP_b = close_b − open_{b−4}`.
- **E1 against:** `s·z_b ≥ 3` and `s·ΔP_b ≤ 0`. **E1 supportive (sign control):** `s·z_b ≤ −3` and `s·ΔP_b ≥ 0`.
- Window: minutes `b−4 … b` all at or after `i0`.

### E2 — rejection of the prior 24 h extreme

- **Level** `L`: long, the highest high over the 1,440 minutes before `i0`; short, the lowest low (at least 1,000
  present). **Valid** only if `s·(L − entry) / entry ≥ 0.001`. Trades without a valid level have no E2 or E4 and are not
  in those tests.
- **First break** `b0`: the first minute at or after `i0` with high `> L` (long) / low `< L` (short), with `b0 < x`.
- **E2 rejection:** the first minute `b` in `b0 … b0 + 14` with `s·(close_b − L) < 0`.
- **E2 acceptance (sign control):** at `b0 + 14`, when no minute in `b0 … b0 + 14` closed back inside (missing minutes
  skipped).
- Only the first break counts. Each trade has at most one of rejection or acceptance, or neither when the trade exits
  before the answer.

### E3 — order-book tilt

- `q_b = (bid_b − ask_b) / (bid_b + ask_b)` from the minute's ±1 % notional; `Q_b` = mean of `q` over `b−4 … b`
  (at least 3 present).
- `zq_b = (Q_b − mean) / sd` over `Q` at minutes `b − 10084 … b − 5` (at least 5,040 present), per asset.
- **E3 against:** `s·zq_b ≤ −3`. **E3 supportive (sign control):** `s·zq_b ≥ 3`.
- Only trades entering on or after **2023-01-08 00:00 UTC** (seven days of book history) are in E3 tests.

### E4 — absorption at the level

- The first minute with E1 against **and** a valid level **and** `|close_b − L| ≤ 0.0025 × L`.

### Provenance of the numbers (chosen here, before any count)

`z ≥ 3` is the conventional "extreme" and is not tuned. Five minutes is the shortest window that holds a burst of
flow; chento's own 15-minute rule (X1) is already known. Seven days covers the weekly cycle while adapting to regime.
The 24 h extreme is the standard prior-day level, rolled so UTC midnight does not matter. Fifteen minutes is one bar
of chento's and short_squeeze's signal timeframe. The 0.1 % minimum puts the level beyond entry, not at it. The
0.25 % "at the level" band is a judgment. No value was tried and discarded.

## 6. Statistic: continuation value against a matched placebo

For trade `j` open after minute `m` (elapsed `e = m − i0_j`): mark `M_j(e) = s·(close_m − entry) / R` and **continuation
value** `CV_j(e) = s·(exit_price − close_m) / R`, price only (the exit leg costs the same either way; funding
ignored). Profit bin `k = floor(M / 0.25)`.

For event kind `E`, event trade `i` with first event at elapsed `e_i`:

- **At-risk controls:** trades `j ≠ i` in the same population and test (valid level for E2/E4; E3 date rule),
  **same direction**, whose interval `[entry_j, exit_j]` does **not overlap** `[entry_i, exit_i]` in calendar time. A
  control contributes its minutes `e′` with `|e′ − e_i| ≤ W` where it is open, its close is present, it has had **no
  `E` event at or before `e′`**, and its bin equals `k_i`. `W` = 5 % of the horizon: 216 min (chento), 144 (squeeze_bull),
  18 (short_squeeze).
- `c_j` = mean `CV_j` over its contributing minutes; **placebo** `P_i` = mean of `c_j` over contributing controls.
  At least **3 contributing controls**, otherwise trade `i` is dropped from the test (counted).
- `Δ_i = CV_i(e_i) − P_i`. The test statistic is the mean `Δ̄` over included event trades.

**Inference:** `Δ_i` assigned to trade `i`'s entry day. Day axis from the population's first to last entry day.
Circular block bootstrap, 30-day blocks, 10,000 draws, seed 42 (the squeeze_bull arm's `block_indices`). Each draw is
Σ Δ / Σ count over the drawn days, and draws with no trades are dropped. 95 % percentile interval. One-sided `p` for
`Δ̄ < 0`: `(1 + #{draws: boot − Δ̄ ≤ Δ̄}) / (draws + 1)`. **Halves:** the included event trades in entry order, earlier
half (the extra trade when odd) and later half.

## 7. Tests

**Primary (12):** each population × {**E1 against, E2 rejection, E3 against, E4**}.
**Sign controls (not in the family):** each population × {E1 supportive, E2 acceptance, E3 supportive}, computed identically.

A primary test is **in the family** only if it has **at least 30 included event trades**. Section 9's preconditions
count this before any continuation value is computed. The family is then fixed.

## 8. Decision per test

| Classification | Rule |
|---|---|
| **INFORMATIVE** (promoted to stage 2) | in the family; **Holm-adjusted p < 0.05** over the family; **both halves Δ̄ < 0**; and for E1–E3, **Δ̄ against < Δ̄ of its sign control**. If the sign control has fewer than 10 included trades, the label is *INFORMATIVE, sign control unavailable* |
| **NO INFORMATION ≥ 0.10 R** | in the family, 95 % interval inside (−0.10, +0.10) R |
| **CONTRARY** | in the family, 95 % interval above 0: the event comes before *better* continuation than matched moments |
| **UNDETERMINED** | in the family, none of the above |
| **DESCRIPTIVE** | fewer than 30 included event trades: numbers shown, no classification |

**Stage 1 verdict:** the list of INFORMATIVE tests, or **NONE PROMOTED**. R is each strategy's own R. No pooling across
populations.

**What a promotion permits:** a separate stage 2 pre-registration of exit arms on that population using the event
**exactly as defined here**: exit at the first event, or at the first event while losing, paired against the shipped
exit, with drawdown. Stage 2 may add aggTrades footprint detail only as a reported refinement, never to re-pick
thresholds. Because stage 2 re-uses these trades, its paired result is not independent confirmation. Any production
proposal would also need forward paper evidence or replication on another population, and the user's go-ahead.
**NONE PROMOTED** closes this line for the tested events at this resolution. It does not show that every
microstructure exit is useless.

## 9. Preconditions (before F0; any failure stops the stage)

| # | Check |
|---|---|
| M1 | Inputs match their hashes: ORB 1-minute panels (logical and file sha256 against their meta), chento features files (the chento arm's frozen hashes), squeeze_bull ledger, short_squeeze replay; every bookDepth zip verified against its CHECKSUM; the built book arrays' logical hashes recorded |
| M2 | Population identity: chento (asset, signal time, direction) set equals the chento arm's A0 rows (392); squeeze_bull equals its S0 rows (122); short_squeeze 71 distinct triggers, all in London / NY hours |
| M3 | Venue identity: every entry equals the close of the minute before its entry minute (difference ≤ 1e-9 relative) |
| M4 | Walker: reproduces the squeeze_bull arm's S0 walks exactly (kind, exit time, exit price ≤ 1e-9) on 122 / 122; chento's 1-minute exit kind agrees with the arm's 15-minute A0 on at least 90 %; short_squeeze's perp exit kind agrees with the replay's (spot path) on at least 80 %. Differences listed |
| M5 | Fixtures pass (`tests/test_micro_*.py`): each event rule, the window-inside-trade rule, the level and break logic, the book minute mapping, the placebo exclusions (overlap, direction, prior event, bin, window, minimum controls), the continuation value and the one-sided p |
| M6 | Book coverage: share of minutes with a snapshot per asset and year. If fewer than 90 % of in-trade minutes of E3-eligible trades have one, the E3 tests are DESCRIPTIVE |
| M7 | Causality on the real series: masking every minute after a cut leaves every `z`, `zq`, level and event at or before the cut unchanged (5 cuts per asset) |
| M8 | Counts per test (event trades, included trades with ≥ 3 controls, the family), event share, median elapsed at first event, events per 24 h in trade, and a power line: MDE(80 %, one-sided 5 %) ≈ 2.49 × sd(CV at one random open minute per trade, seed 42) / √n. No continuation value at an event is computed |

## 10. What is reported (secondary, never decided)

Per test and sign control: `Δ̄` with interval, halves, and by year; mean `CV` at the event and mean placebo, as
absolute values (holding after the event adds or loses R); `Δ̄` against a **time-only placebo** (no bin match); `Δ̄`
with continuation values from the **no-time-exit walk** (same event minutes and control minutes); for chento, `Δ̄` per
asset and direction. Event base rates from M8.

## 11. Information already seen (disclosure)

- The shipped outcomes of all three populations: chento A0 on 15-minute bars (+0.634 R net), squeeze_bull S0 on
  this path (+0.259 R), the short_squeeze replay (spot 1-minute path; execution study port n = 70, +0.39 R) and the
  sizing study's short_squeeze rows. The entry-venue identity in M3 was measured before writing this.
- **Chento arm X1 / X2:** the bot's 15-minute B1 absorption rule against the position (k = 3) as an exit, without a time
  stop, lost 0.24 R against no time stop firing on 141 trades (X1). Only while losing (X2), it fired on 21 trades,
  −0.05 R. These are exit arms on 15-minute bars. No continuation value against a placebo was computed.
- **Squeeze_bull arm:** "flush resumed" as an exit, 25 events, −0.02 R.
- **Prior microstructure results, all as entries or unconditional predictors, none inside trades:** bookDepth
  imbalance |t| < 2 at every horizon, 173k snapshots (brainstorm session 2026-09-11). Order arrival at levels scored
  the same at placebo levels. 5-minute absorption (C3) KILL on validation. 15-minute whale absorption no edge. Footprint
  level-delta confirmation dead. ORB: invalidation exits lowered gross.
- For this stage, before this document was finished: the bookDepth download was started, and its parser was run on
  two sample days to check format only (columns, levels present, ~30 s snapshot spacing on 2024-06-15). No minute
  flow statistic, level, break, book imbalance or event count has been computed on any trade or any day.

## 12. Freeze

F0 after the preconditions and before any continuation value at an event: this file, `micro_data.py`, `micro_lib.py`,
`micro_run.py`, the tests, `preconditions.json`, the book manifest and array hashes, and the ORB panel hashes, written
to `results/microstructure/freeze_F0.json` with a UTC timestamp. Then one outcome run writes `report.json` and
`verdict.json`. Neither step reruns. A bug found afterwards is fixed by a dated amendment below, and both results are
reported. Manifests, not commits, unless the user asks.
