# Attribution — the layer the repo was missing (2026-08-23)

*Born from Paladin H14. Per trade: decompose actual R into **regime** (random-time
72h holds at the trade's own risk scale — what the market gave anyone), **timing**
(same-entry-time hold minus regime — the value of choosing the moment), and **exit**
(actual minus same-time hold — the value of the exit machinery). `attribution.py`
is reusable: any trade list with symbol/side/entry/stop/entry-time in.*

## Chento Triple, backward-only pools (the honest research book)

| | n | actual R | = regime | + timing | + exit |
|---|---|---|---|---|---|
| BTC | 101 | +0.78 | +0.10 | **+0.65** | +0.03 |
| ETH | 73 | +0.70 | −0.10 | **+0.70** | +0.10 |

**The chento edge is real selection alpha, not disguised beta.** Timing carries
~85–100% of the expectancy on both assets; ETH earns +0.70R of timing against a
*negative* regime. The 6R-target exit machinery adds little on its own — consistent
with the wick-overlay finding that the tail must not be amputated, but the tail isn't
where the edge originates either: it's *when it enters*.

## Live paper fleet since 2026-07-21 (n=7 — direction, not proof)

- CHENTO_TRIPLE_V3 (6 trades incl. open @ mark): actual +0.17R = regime **−0.17**
  + timing **+0.45** + exit −0.11. The live bot is beating same-window holds by a
  healthy margin; the *market* was the drag. "Hard time earning their keep" is, on
  this early sample, a regime statement, not a selection failure.
- ADX (1 trade): too early to read.

Re-run cadence: monthly, or after every 10 fleet trades. Wire into the study
workflow before any sleeve go/no-go: a sleeve whose timing ≈ 0 is charging fees
for beta regardless of its headline R.

## Paladin entries-completion (companion run, `../paladin_study/entries_completion.py`)

- **H1 round levels: ILLUSION.** His 97%-within-0.25% is matched by controls at 96%
  (entry, TP1, and stop alike). The grid is fine enough that everything is "near" a
  level — same artifact class as fib density. The pack's flagship entry claim is dead.
- **H14: he was NOT just long-beta.** On the R-computable sample: his +1.64R/trade =
  +0.42 passive + **+1.21 alpha** (beta share 26%). With entries proven structureless,
  that alpha is exit-side — third independent confirmation, now with attribution math.
- **H6 outcomes: BTC does not explain his alt results** (corr +0.12) — his
  "BTC drives every alt" was belief, not mechanism.
- **H8 weekends: his one live context claim.** Market seasonality mild (Sun +0.48%,
  2024→), but his weekend trades ran **30/31 = 97% WR** vs 73% weekday (n=31 — small,
  and possibly weekend-chop suiting his small-target style; flagged, not overclaimed).
- **H9 macro calendar: narration.** No frequency drop pre-event (2.62/day vs 2.05
  overall), no half-risk clustering (29% vs 28%), no post-event edge.
- **H15 front-running: none.** Post-signal drift ±3bp ≈ control; the −17bp *pre*-signal
  drift against his direction just confirms he buys dips in real time.

Pack scorecard final: 16 hypotheses — 10 tested earlier, 6 completed here. Entries:
structureless across every claim incl. his own stated reasons. Exits + risk skeleton:
where all the value was.
