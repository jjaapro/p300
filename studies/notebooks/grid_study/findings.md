# Grid trading (S-080) — findings

STATUS: CONCLUDED — KILL per pre-registration (2026-09-07). No maker-cost cell has
annualised Sharpe > 0 in both halves AND full-period MAR ≥ 0.5; cost model B was therefore
not adjudicated, but is reported (every taker cell is negative).

Data: `btc_1m`, 2020-01-01 → 2026-09-06, 3,512,461 bars (`results/run_1m.log`). Harness:
`grid_harness.py`, a port of the trader repo's `backtest_grid_realistic.py` with p300 costs;
four pre-registered cells, run once, no parameter search. Unlevered, P&L as additive % of
capital (a −662% total means the ladder lost 6.6× its capital cumulatively, i.e. ruin).

## Result table (`results/grid_summary.csv`)

| cell | spacing | fees grid / close (per side) | total | ann. | max DD | MAR | Sharpe h1 | Sharpe h2 | round trips / yr | recentres |
|---|---|---|---|---|---|---|---|---|---|---|
| A_maker_0.2 | 0.2 % | 2 bp / 7.5 bp | −662 % | −99 %/yr | 689 % | −0.14 | −3.06 | −2.02 | 31,351 | 1,599 |
| **A_maker_0.5** | 0.5 % | 2 bp / 7.5 bp | **+122 %** | +18.3 %/yr | 61.9 % | **0.30** | **+0.53** | **+0.84** | 6,402 | 255 |
| B_taker_0.2 | 0.2 % | 7.5 bp / 7.5 bp | −1815 % | −272 %/yr | 1818 % | −0.15 | −6.90 | −6.19 | 31,351 | 1,599 |
| B_taker_0.5 | 0.5 % | 7.5 bp / 7.5 bp | −114 % | −17 %/yr | 137 % | −0.12 | −0.78 | −0.18 | 6,402 | 255 |

Halves: h1 = 2020-01-01 → 2023-04-30, h2 = 2023-05-01 → 2026-09-06.

Per-year returns (% of capital):

| cell | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| A_maker_0.2 | −81 | −278 | −146 | −6 | −57 | −54 | −40 |
| A_maker_0.5 | +42 | +40 | −39 | +34 | +27 | +9 | +9 |
| B_taker_0.2 | −291 | −635 | −340 | −100 | −198 | −152 | −99 |
| B_taker_0.5 | −5 | −38 | −77 | +18 | +0 | −10 | −3 |

## Decision-rule mapping

| clause | measured | outcome |
|---|---|---|
| A cell with Sharpe > 0 in both halves | A_maker_0.5 only (+0.53 / +0.84); A_maker_0.2 negative in both | one candidate |
| … AND MAR ≥ 0.5 | A_maker_0.5 MAR 0.30 (18.3 %/yr over a 61.9 % drawdown) | **fails → KILL** |
| B evaluated only if A survives | not required; both B cells have negative Sharpe in both halves | — |

## What the numbers say

- **The 0.2 % ladder is ruinous under any cost model.** A 4 % drift from centre (the fixed
  recentre rule) happens ~240 times a year on BTC; each recentre dumps up to ten lots at a
  market loss. 1,599 recentres × ~0.4 % ≈ −660 % even with 2 bp grid fills. The round-trip
  income (31k trips/yr × 0.2 % × 1/20 lot) never covers it.
- **The 0.5 % maker ladder is the only positive cell** and it is a long-BTC exposure with
  extra churn: positive in every year except 2022 (−39 %). Its equity is 0.6 Sharpe on a 62 %
  drawdown; p300's ADX sleeve runs at MAR ≈ 3 on the same asset.
  *Corrected 2026-09-09:* the 61.87 % max drawdown is **not** a 2022 inventory bleed, as the
  original text said. It is a 22-day COVID-crash event, running from a +15.70 % equity peak on
  2020-02-23 to −46.17 % on 2020-03-16. 2022 is the second-worst episode (49.30 % intra-year
  drawdown, −38.9 % for the year). The distinction matters for a future reader: a slow
  bear-market inventory problem sounds like something a regime filter or an inventory cap could
  fix, whereas a 22-day vertical crash against a fully-laddered book is the ladder's defining
  exposure and cannot be filtered away.
- **Taker costs flip it negative** (−17 %/yr): the spread earned per trip (0.5 % on 1/20 of
  capital ≈ 2.5 bp of capital) is eaten by 15 bp round-trip fees on every lot. Maker
  execution on Binance USDⓈ-M is not something the paper stack models today, and even with it
  the cell fails MAR.
- Consistent with the prior recorded in the README and with
  `project_range_strategy_sanity_2026_09`: BTC has no intraday mean reversion to sell; a grid
  is a leveraged bet on oscillation that the tape does not provide.

## Port defect found in verification (2026-09-09)

The harness does not implement the ladder the README froze. `grid_harness.py` fills a buy when
a level falls inside the bar's range, with **no directional test**: `if not lv["has_inv"] and
l <= p <= h`. And at every centre and re-centre the ten levels *above* the new mid are created
empty, i.e. above market, so they are bought as price rallies up into them. A real limit ladder
cannot do that, because a buy limit placed above market is immediately marketable.

So the simulated strategy is "buy every gridline crossed in either direction, sell one spacing
up", which is a strictly easier strategy than the pre-registered one: it collects the up-leg of
every oscillation as well as the down-leg. The surviving cell's returns are therefore driven in
part by rally-buying that a real ladder would not capture.

This **strengthens the KILL**. The friendliest cell fails MAR at 0.30 while enjoying fills a
correctly-specified ladder would never get; a directional re-run can only be worse. The verdict
is left as recorded rather than re-run, and this note stands in place of the re-run. Anyone
reviving the item must fix the directional test first.

## Side notes

- Harness defect fixed after the run: `simulate()` returned the round-trip count of the last
  grid instance only (the count is reset at every recentre). P&L, drawdown and Sharpe were
  unaffected; `results/grid_summary.csv` was regenerated from the saved daily CSVs with the
  summed count (`round trips / yr` above). `results/run_1m.log` still shows the old, wrong
  `cycles/yr` column.
- Fill assumption is generous (50 % of touches fill at the level ± 0.02 %); a stricter
  queue-position model can only lower every cell.
- Ledger: 4 trials against the "range / grid" family; no cell selected, nothing to deflate.

Closes the trader-repo item S-080 (four harnesses, never adjudicated) and the last untested
entry of the intraday-range playbook. No follow-up.
