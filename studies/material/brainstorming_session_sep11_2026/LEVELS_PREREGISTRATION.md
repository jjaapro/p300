# Pre-registered: did AI-defined levels carry information?

Registered 2026-09-11. **Evaluation opens 2026-12-11.** Not a study yet — a
frozen experiment awaiting data.

## The claim

Two frontier models at max effort independently defined support levels on a live
spot position, and price action appeared to respect them. The hypothesis that
follows: the edge may be in the AI harness rather than in any single signal.

## Why it cannot be answered today

Every dated level document in this repo was written 2026-09-08 to 09-11:

| document | defined | forward data as of 2026-09-11 |
|---|---|---|
| `archive/PRICE_ACTION_PLAYBOOK_pre_revision` | 2026-09-08 | **3 days** |
| `2026-09-08_session/TAPE_BRIEF.md` | 2026-09-08 | **3 days** |
| `PRICE_ACTION_CAMPAIGN_2026-09-10.md` | 2026-09-10 | **1 day** |
| `PRICE_ACTION_PLAYBOOK.md` (current) | 2026-09-11 | **0 days** |

A level test needs repeated touches. At one to three days there are almost none,
and any answer produced now would be an anecdote dressed as a measurement.

## Why the observation needs a control at all

"Price respected the level" is the most confirmation-prone observation in
trading, because price is always near *some* level. This repo has measured the
size of that illusion twice:

- **Study 17:** a placebo level offset by an arbitrary **$211** scored
  **t = +3.4** — higher than any real level anywhere in that study. Real minus
  placebo, paired by day, reached max |t| = 1.5 across 28 cells.
- **`run_anchors`:** real structural levels barely beat random placement —
  PWO +0.083% (t = +0.40), POC +0.068% (t = +0.86).

So the question is never "did price react at the level." It is **"did price
react at the level more than at a level placed somewhere arbitrary."**

A second control problem specific to this claim: two frontier models agreeing is
**not** independent confirmation. They are trained on overlapping corpora of the
same technical-analysis literature and will tend to identify the same
conventional levels — round numbers, prior swings, VWAP, prior POC. Agreement
between them measures shared training, not market structure.

## The frozen experiment

Level set: [`scalp_lab/levels_frozen.json`](../scalp_lab/levels_frozen.json) —
**14 levels across 2 sets**, frozen 2026-09-11 before the window opens, so the
set cannot be revised with hindsight.

| parameter | value |
|---|---|
| touch | price within **0.15%** of the band |
| respected | reversed **≥0.75%** away without first closing **0.75%** through |
| horizons | 4, 12, 24, 48 hours after first touch |
| control | each level shifted by a random **0.5–3.0%**, 2,000 draws |
| **decision rule** | **real must beat the placebo distribution's 95th percentile on reversal-rate-given-touch** |

Evaluation: `python -m scalp_lab.run_level_test`. It refuses to run before
2026-12-11 — running early is the stopping-rule bias this document exists to
prevent.

## What each outcome would mean

**Real beats placebo p95.** AI-defined levels carry information that arbitrary
levels do not. That would be the first positive result in 24 studies and would
justify a much larger programme.

**Real lands between p50 and p95.** Suggestive, underpowered at 14 levels.
Would justify extending the frozen set rather than trading it.

**Real at or below placebo median.** The levels are conventional TA landmarks
with no more predictive content than an arbitrary price, and the "respect"
observed was the illusion studies 17 and 23 already quantified.

## The broader hypothesis, and where it is better aimed

The harness hypothesis is partly supported — but by *deletion*, not discovery.
Across 24 studies the harness found **zero** new tradeable edges and deleted
**seven** false ones:

| deleted | from | to |
|---|---|---|
| hindsight regime labels | +0.22R | −0.02R |
| position-blocking on future info | +0.38pp | negative |
| overlapping samples | t = 3.46 | not significant |
| averaging-down basis accounting | +863% at t = +4.20 | a wash |
| placebo-tested levels | t = +3.2 | paired \|t\| < 1.5 |
| MTF containing-bar lookahead | t = +9.49 | t = +0.64 |
| pivot lookahead (study 24) | +3.4 to +6 t-units | zero survivors |

Any one of those, traded, would have lost money. That is a real and large edge —
it is simply defensive rather than predictive.

What a harness cannot do is raise the **capture ceiling**. Across 17 prediction
studies, measured capture clustered at 3–13% of sigma with a median near 5%.
That is a property of the market, not of the reader: a model reading the same
OHLCV faces the same ceiling, and model capability does not change what is in
the data. Where a harness plausibly adds real value is breadth (funding, OI,
options, ETF and spot flow read simultaneously), throughput (24 studies in four
days, adversarially verified), and discipline — which is an edge over yourself,
not over the market.
