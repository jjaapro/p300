STATUS: CONCLUDED 2026-09-15 — microstructure stage 1, **NONE PROMOTED**. No production change; no stage 2.

# Exit-policy study, microstructure stage 1 — findings

**One-line answer.** On chento, the only strategy with enough events to test, none of three events says anything
detectable about what the rest of the trade is worth: absorption of aggressive flow against the position, rejection of
the prior 24 h extreme, and an order book tilted against the position. Holding after each still paid +0.67 to +0.78 R
on average, so an exit keyed on any of them would have cost R. short_squeeze trades almost never meet these events (median
trade 65 minutes). squeeze_bull meets them too rarely to test.

Pre-registration: [PREREGISTRATION_MICROSTRUCTURE.md](PREREGISTRATION_MICROSTRUCTURE.md), frozen in
`results/microstructure/freeze_F0.json` (2026-09-15 15:41:36 UTC) before any continuation value at an event; outcome run
and verdict 15:41:54 UTC. Review notebook: [03_microstructure_information.ipynb](03_microstructure_information.ipynb)
(recomputes every event and statistic and matches the saved run).

## 1. What was measured

- **Trades:** the same entries as the earlier phases. chento 392 (BTC + ETH, 72 h), squeeze_bull S0 122 (48 h),
  short_squeeze 71 (6 h). All are walked with their shipped stop, target and time exit on the Binance perpetual 1-minute
  path. Every entry equals that path's close. The walker reproduces squeeze_bull's S0 exactly, chento's 15-minute exits on
  392 / 392 trades, and the short_squeeze replay on 68 / 71.
- **Events** (first occurrence inside the trade, known at a minute's close):
  - **E1 absorption against:** 5-minute taker delta at z ≥ 3 on the position's side with no price progress.
  - **E2 rejection:** first break of the prior 24 h high (long) or low (short), closed back inside within 15 minutes.
  - **E3 book against:** ±1 % depth imbalance at z ≤ −3 against the position (bookDepth archive, 2023+).
  - **E4:** E1 within 0.25 % of the 24 h extreme.
  - **Sign controls:** the same patterns pointing with the position.
- **Statistic:** Δ = continuation value at the first event (the move to the shipped exit, in R) minus a matched placebo.
  The placebo is the same quantity for other trades at the same point in their life, in the same 0.25 R profit bin and
  direction, with no event yet and no calendar overlap. With no information, Δ is zero whatever the exit rule and
  the strategy's drift.

## 2. How often the events happen inside trades

| | chento (392) | squeeze_bull (122) | short_squeeze (71) |
|---|---|---|---|
| E1 absorption against | 122 trades (31 %), first after a median 13 h | 15 (12 %) | 0 |
| E2 rejection | 219 (56 %), median 7.9 h | 26 (21 %) | 1 |
| E3 book against (2023+ trades) | 51 of 290 (18 %), median 17 h | 12 of 111 | 0 of 50 |
| E4 absorption at the level | 27 (7 %) | 5 | 0 |
| sign controls: E1 / E2 / E3 supportive | 71 / 35 / 75 | 19 / 11 / 20 | 0 / 0 / 2 |

A decision test needed 30 event trades with at least 3 matched controls, so **the decision family is chento's E1, E2 and
E3** (fixed in the preconditions before any continuation value). Every squeeze_bull and short_squeeze test is
descriptive.

The events are rare by construction. Absorbed buying holds in 0.018 % of all BTC minutes, about one in 5,500, and a
short_squeeze trade lasts a median 65 minutes. Before the freeze, a diagnostic confirmed the short_squeeze zeros are not
a bug: the median short_squeeze trade's largest 5-minute flow z-score is 1.74.

## 3. Results

Δ in each strategy's own R per event trade; 95 % intervals from a 30-day block bootstrap of entry days.

| chento | Event trades | Δ (95 % interval) | Halves | Holding after the event | Matched moments | Holm p | Classification |
|---|---:|---|---|---:|---:|---:|---|
| **E1 absorption against** | 119 | −0.07 (−0.66, +0.56) | +0.80 / −0.96 | +0.78 | +0.85 | 0.83 | UNDETERMINED |
| **E2 rejection** | 215 | −0.20 (−0.68, +0.32) | +0.25 / −0.65 | +0.67 | +0.87 | 0.69 | UNDETERMINED |
| **E3 book against** | 48 | +0.22 (−0.52, +0.93) | +0.84 / −0.40 | +0.73 | +0.51 | 0.83 | UNDETERMINED |
| E4 absorption at the level | 27 | +0.44 (−1.04, +1.81) | +1.78 / −1.00 | +0.97 | +0.53 | — | DESCRIPTIVE |
| sign control E1 supportive | 70 | −0.07 (−0.52, +0.52) | +0.41 / −0.54 | +0.56 | +0.63 | — | — |
| sign control E2 acceptance | 35 | +0.33 (−0.51, +1.05) | +1.43 / −0.85 | +1.04 | +0.71 | — | — |
| sign control E3 supportive | 75 | +0.13 (−0.44, +0.74) | +0.28 / −0.01 | +0.52 | +0.38 | — | — |

Minimum detectable effect (80 %, one-sided 5 %): E1 0.41 R, E2 0.30 R, E3 0.64 R. Secondary Δ (reported, not decided):
time-only placebo E1 +0.22, E2 +0.05, E3 +0.33; continuation to a no-time-exit walk E1 +0.21, E2 −0.17, E3 +0.49.

| squeeze_bull (descriptive) | Event trades | Δ (95 % interval) | Holding after | Matched |
|---|---:|---|---:|---:|
| E1 absorption against | 15 | −0.15 (−0.56, +0.22) | +0.02 | +0.17 |
| E2 rejection | 26 | −0.09 (−0.42, +0.15) | +0.02 | +0.11 |
| E3 book against | 12 | +0.07 (−0.66, +0.79) | +0.17 | +0.10 |
| E1 supportive / E2 acceptance / E3 supportive | 19 / 11 / 19 | −0.07 / −0.18 / +0.43 | +0.13 / −0.02 / +0.61 | +0.20 / +0.16 / +0.18 |

## 4. What this says

- **No information shown on chento.** None of the three decision tests is close (Holm-adjusted p 0.69–0.83). The sign
  controls rule out a directional reading anyway. Absorption with the position (−0.07) scores the same as absorption
  against it (−0.07). An order book tilted *against* the position comes, if anything, before *better* continuation than
  matched moments (+0.22).
- **For an exit, the continuation value itself decides, and it stayed positive.** Holding after every chento event
  returned +0.52 to +1.04 R per event trade. Exiting at the first absorption would have cost 0.24 R per trade across all
  392 trades, and at the first rejection 0.36 R (price only; the exit leg is paid either way). That points the same way
  as the chento arm's 15-minute flow-reversal exit (X1). These moments are not where chento's remaining expected value
  turns negative.
- **Rejection is the closest thing to a lead, and it is not one.** It is the only event whose sign fits its mechanism:
  rejection −0.20 R against acceptance +0.33 R, and −0.16 R against +0.39 R in the era-matched control below. But its
  interval spans −0.68 to +0.32, the effect is below what 215 trades can detect, and holding after it still returned
  +0.67 R.
- **The halves flip is mostly a period effect.** Every chento event, and every sign control, is positive in 2021–2023
  and negative in 2024–2026. The frozen placebo draws controls from all years, so a period in which holding paid more for
  every trade shows up in whichever events fell in it. An exploratory control added after the verdict
  (`micro_explore.py`, `results/microstructure/exploratory_era_matched.json`, not pre-registered) keeps only controls
  entering within ±365 days. The results move to: E1 −0.14, E2 −0.16 (halves −0.08 / −0.24), E3 +0.13, E1 supportive
  +0.01, E2 acceptance +0.39, E3 supportive +0.15. No interval moves away from zero.
- **short_squeeze: the question does not arise.** Its trades end in about an hour (stop 42, target 19, time 10 of 71)
  before an extreme flow or book reading appears. An absorption or rejection exit would never fire. The open question
  for short_squeeze is the no-stop twin's risk (its only loss exit is the 6 h time stop), which is about risk, not
  information.
- **squeeze_bull: too few events.** Holding after absorption against or rejection was worth about +0.02 R against
  +0.1–0.2 R at matched moments. Holding after acceptance, the opposite of a rejection, was worth −0.02 R. So no
  directional content is visible either. With 11–26 events per test this is noise.

## 5. What it permits

Nothing. By the pre-registration, **NONE PROMOTED closes this line for these events at this resolution**: no stage 2
exit arms. It does not show that every microstructure exit is useless. It does not rule out information smaller than
about 0.3–0.6 R per event trade on chento, or anything the public 1-minute archive cannot see: individual prints, the
size of the absorbing order, live depth at seconds, or liquidations. Reopening would need that data and a new
pre-registration. The repository's prior on each is low: the bookDepth imbalance predictor was |t| < 2, level-arrival
effects matched placebo levels, and footprint confirmation was dead.

For the question that started this (*is a time stop just overfitting?*), the evidence now reads as follows:

- **The time-stop value is a dial, and so is every event threshold here.** What separates a good exit from a bad one is
  whether it knows something, and these events do not.
- **The structural stop is still the only invalidation exit that does not damage the payoff** (chento arm, ORB exits,
  here).
- **The time stops that stay are doing measurable work.** chento's 72 h exit beats every shorter one. The squeeze_bull
  twin's 48 h exit is its risk control.

## 6. Limitations

- **Power.** Chento's decision tests could detect effects of 0.30–0.64 R. squeeze_bull and short_squeeze could not be
  tested.
- **Placebo pool.** It spans all years; the era-matched control is exploratory.
- **Data resolution.** 1-minute klines, so taker flow has no trade-size detail and "no price progress" is a
  5-minute close-to-open. Book depth is ±1 % notional in snapshots about 30 s apart, missing five archive days, and it
  is spoofable.
- **Reused trades.** The same trades were used by the earlier phases, and chento's 15-minute absorption exit (X1 / X2)
  was already known.
- **Price-only continuation value.** Funding and costs are ignored, because the exit leg is paid either way.
