STATUS: CONCLUDED 2026-09-18 — **NONE PROMOTED**. No production change; no stage 2.

# Exit-policy study, spot-led versus perp-led extremes (item F) — findings

**One-line answer.** A new high carried by perpetual takers and a rising premium while Binance spot takers lag says
nothing useful about what the rest of the trade is worth, and on chento it points the wrong way for an exit: holding
after one of those highs returned **more** than holding at matched highs, not less. On squeeze_bull it is flat. The
brainstorm's item F is dead as an exit signal.

Pre-registration: [PREREGISTRATION_SPOT_PERP.md](PREREGISTRATION_SPOT_PERP.md) v1.1 plus amendment A26, frozen in
`results/spot_perp/freeze_F0.json` (2026-09-18 07:07:47 UTC) before any continuation value at an event; outcome run
and verdict 07:11:32 UTC.

## 1. What was asked and what was measured

BACKLOG research item 2, row F of the exhaustion brainstorm: *"exit a long when a new high is carried by perp taker
buying and a rising premium while spot buying lags."* The mechanism: cash buyers pay in full, leveraged perpetual
buyers must be financed and can be forced out, so a high made by the second kind is borrowed demand.

This is an information test in the form of microstructure stage 1, not an exit arm. At the first qualifying minute in
a trade it measures the **continuation value**, what holding from that minute to the shipped exit added in R, and
compares it with the same quantity for other trades at the same point in their life, in the same profit bin, the same
direction, within a year of it, and **at a new running extreme of their own**. With no information the difference is
zero whatever the strategy's drift.

**The event (F1 against).** At a new running high since entry (a new low for a short), with at least an hour of the
trade elapsed: the premium index is higher than 60 minutes earlier, the perpetual's 60-minute taker delta is positive,
and Binance spot's is not. Every leg is a sign, so there is no free number. **The decision control (F1
spot-confirmed)** holds the premium and perpetual legs and flips only the spot leg, because at a perpetual-made
extreme the other two legs are close to automatic.

## 2. The family, fixed on counts before the freeze

| Population | Trades | Event trades | Median elapsed at the first event | Included at rung 1 | In the family |
|---|---:|---:|---:|---:|---|
| chento BTC | 208 | 78 (38 %) | 2.7 h (4 % of the horizon) | 59 | yes |
| squeeze_bull | 122 | 50 (41 %) | 3.0 h (6 %) | 42 | yes |
| chento ETH | 184 | 59 (32 %) | 4.0 h (6 %) | 42 | replication line |
| short_squeeze | 71 | 5 (7 %) | 1.7 h (28 %) | 0 | descriptive |

The burst form (F2, a 5-minute perpetual taker z of 3 or more with spot at or below its own mean) fires on 13, 5, 21
and 0 trades and reaches 30 included trades under no rung, so it is **descriptive everywhere and never entered the
family**. The family was therefore two tests, Holm over two.

## 3. Results

Δ is the continuation value at the first event minus the matched placebo, in each strategy's own R. Intervals are a
30-day circular block bootstrap of entry days, 10,000 draws.

| Test | n | Δ (95 %) | Halves | Holding after the event | At matched highs | Holm p | Label |
|---|---:|---|---|---:|---:|---:|---|
| **chento BTC** | 59 | **+1.068 (+0.100, +2.034)** | +1.27 / +0.86 | +1.307 R | +0.239 R | 0.99 | **CONTRARY** |
| **squeeze_bull** | 42 | −0.036 (−0.261, +0.206) | +0.03 / −0.10 | +0.338 R | +0.374 R | 0.74 | UNDETERMINED |
| chento ETH (replication) | 42 | +0.617 (−0.357, +1.532) | +0.06 / +1.17 | +1.201 R | +0.584 R | — | descriptive |

The Holm p values are for the one-sided test that Δ is **negative**, which is what an exit signal needs. Neither is
close. On chento BTC the interval sits above zero instead, which is the CONTRARY label: the event comes before
*better* continuation than matched extremes.

**Every placebo agrees in sign.** chento BTC: rung 1 +1.068, rung 2 +0.916, rung 3 +0.653, era-and-bin without the
extreme match +0.811, stage 1's all-years form +0.655, time-only +1.148, no-time-exit walk +1.340, outcome-free overlap
+1.044, volume-matched +0.987, session-matched +0.986 (n = 20). squeeze_bull stays within ±0.25 R on all of them.

**The venue leg is the one that separates, and it separates the wrong way.** On chento BTC, taking the flow legs alone
and dropping the premium leg: spot lagging **+1.046 (+0.220, +1.888)** against spot confirming **−1.064 (−1.761,
−0.347)**. The premium leg alone carries nothing (−0.070 against +0.124 for its mirror). So the contrast is real and it
is in the spot leg, exactly where the pre-registration predicted the content would be, and its sign is the opposite of
the hypothesis. The shared-pool contrast, which compares the two groups against one common reference rather than
against each other, gives the same answer: +0.706 (+0.075, +1.329) in favour of the perpetual-led highs.

Consistent with that, on chento BTC the composition line that adds the zero-fee BTC/FDUSD pair to the spot leg scores
+1.642 (+0.574, +2.679) on its 32 trades, and the external site's "leveraged blow-off" parameterisation (F3, reported
only, 14 trades) scores +1.820 (+0.180, +3.112). Both point the same way as F1.

## 4. What this settles

- **Item F is dead as an exit.** Neither family test is INFORMATIVE, so by the pre-registration the stage verdict is
  **NONE PROMOTED** and no stage 2 exit arm may be written on these events. Exiting a chento long at the first
  perpetual-led high would have given up about 1.3 R of continuation per event trade.
- **The direction is the interesting part, and it is a hypothesis, not a result.** On chento, a high where Binance spot
  takers are net sellers while perpetual takers buy is followed by *better* continuation, and a high where spot
  confirms is followed by worse. Read it with three cautions. The CONTRARY interval is only just above zero
  (+0.100). The label is not protected by the family correction, which tested the other tail. And squeeze_bull, on the
  same asset and the same arrays, shows nothing at all, so this is not a general fact about Binance highs; it is
  something about chento's population.
- **A fourth strike for fine-grained flow as exit information.** Absorption, rejection of the prior day's extreme and
  order-book tilt carried none on these same trades (stage 1). The premium level and the perpetual taker share at a
  high carried none at squeeze_bull tops (top anatomy). The venue split now joins them.
- **short_squeeze cannot be asked.** Five event trades, none with three matched controls, because the 60-minute window
  rarely fits a trade whose median life is 65 minutes.

## 4a. The two reported tables (computed after the verdict, `spotperp_explore.py`)

**The target-minute channel is empirically absent.** The worry was that the event is defined at a new running extreme,
which is the kind of minute that fills a target, and that a trade whose *first* such minute is its exit minute would be
silently moved into the control pool with its earlier, higher-continuation extremes. The pattern holds at the exit
minute on 0 chento BTC trades, 0 chento ETH, 1 squeeze_bull and 1 short_squeeze, and in every one of those the trade
had already had an in-trade event, so **no trade was moved**. The channel that motivated the withdrawn `Δ_x` statistic
is not there to bias anything.

**Covariate balance.** Event minutes against their contributing control minutes, under each decision rung:

| Test | volume ratio | \|hour return\| | ordinal of the extreme | weekend share |
|---|---|---|---|---|
| chento BTC, against | 0.73 / 0.72 | 0.0039 / 0.0036 | 10 / 10 | 0.42 / 0.34 |
| chento BTC, spot-confirmed | 0.66 / 0.65 | 0.0023 / 0.0028 | 8 / 8 | 0.40 / 0.42 |
| squeeze_bull, against | **0.55 / 0.73** | 0.0019 / 0.0045 | 11 / 12 | 0.31 / 0.27 |
| squeeze_bull, spot-confirmed | **0.75 / 0.58** | 0.0026 / 0.0036 | 10 / 8 | 0.26 / 0.36 |

chento's pools are well matched on every covariate. squeeze_bull's are not: its perpetual-led event minutes sit at
noticeably **lower** volume than their controls and its spot-confirmed minutes at higher, which is worth knowing given
that its Δ is flat. The volume-matched robustness line is the one that removes this, and it gives −0.122 on
squeeze_bull, the same near-zero answer. The ordinals are close everywhere, so the contrast is not "first push against
sustained trend".

## 5. What it does not settle

- **Power.** With 42 to 59 included trades and continuation-value spreads of 0.86 to 1.78 R, the detectable effect is
  roughly 0.3 to 0.6 R. The NO INFORMATION label was unreachable by construction and the document said so in advance,
  so "UNDETERMINED" on squeeze_bull means *not measured to be anything*, not *shown to be nothing*.
- **Where the first event sits.** Its median is 2.7 to 4.0 hours into the trade, a few per cent of the horizon, so this
  tests the first push after the window fits, not the top of a move. The ordinal and elapsed cuts are in
  `exploratory_reported_tables.json`.
- **One venue's spot.** The spot leg is Binance BTCUSDT and ETHUSDT only, while the premium is measured against a
  multi-exchange index. A Coinbase-led move can look spot-lagging here. The composition line addresses the
  BTC/FDUSD share of that problem and nothing else.
- **The intervals are optimistic.** Placebo controls are shared between events, which the block bootstrap over entry
  days does not see.

## 6. Data and checks

Five minute-aligned panels were built from 422 checksum-verified Binance archive files (spot 1-minute klines for
BTCUSDT, ETHUSDT and BTCFDUSD; premium index for BTCUSDT and ETHUSDT; 2020-01-01 to 2026-09-14). Zero off-grid rows,
zero outside-panel rows, zero duplicate rows and zero conflicting duplicates on all five. Spot coverage is 99.93 %;
the F1 inputs are finite on 99.0 to 99.9 % of in-trade minutes after the gate.

All eleven preconditions pass. Two worth naming:

- **The new premium panel is byte-identical to the top anatomy stage's cache** across all 2,472,480 shared minutes, so
  this build reproduces that one exactly where they overlap.
- **The walker reproduces stage 1's saved walks** on all 585 trades, entry price, exit price, exit kind and all.

**Amendment A26 (made after seeing the check fail, section 12.2 of the pre-registration).** The alignment precondition
originally correlated the premium *level* against the perpetual-over-spot *level*. Two autocorrelated level series
correlate about 0.92 at every lag from −3 to +3, so the winning lag is noise, and four of its 28 lines peaked off zero.
The half computed on returns was decisive everywhere (lag 0 scoring 0.974 against about 0.00 elsewhere). The premium
half now uses differences too. That fixes both 2023 lines and leaves a genuine one-minute offset in the **2020**
premium archive on both assets, which is recorded rather than repaired: no population reads 2020, since the earliest
minute any window reaches is 2021-04-16 23:15 UTC. The pre-amendment result is kept as
`results/spot_perp/preconditions_pre_A26.json`.

## 7. Files

| File | Role |
|---|---|
| [PREREGISTRATION_SPOT_PERP.md](PREREGISTRATION_SPOT_PERP.md) | Frozen events, placebos, decision rules, preconditions, disclosure, the two review rounds and amendment A26 |
| `spotperp_download.py`, `spotperp_data.py` | The checksum-verified archive download and the panel build |
| `spotperp_lib.py` | Per-minute inputs, the event kinds, the matched placebos, the labels |
| `spotperp_run.py` | `smoke`, `checks` (P1 to P11), `freeze0`, `outcomes` |
| `spotperp_explore.py` | The two reported tables computed after the verdict; exploratory, decides nothing |
| `tests/test_spotperp_data.py`, `tests/test_spotperp_events.py` | 57 fixtures, one twin per gate |
| `results/spot_perp/` | `smoke_counts.json`, `preconditions.json` (and `_pre_A26`), `freeze_F0.json`, `events.csv.gz`, `trades.csv.gz`, `report.json`, `verdict.json`, `exploratory_reported_tables.json` |

Rebuild: `spotperp_download.py`, then `spotperp_data.py build`, then `spotperp_run.py checks`. The freeze and the
outcome run refuse to repeat.
