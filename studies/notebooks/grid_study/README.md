# Grid trading (S-080) — pre-registered design (2026-09-06, before any data)

Closes the one item the trader repo built four harnesses for and never adjudicated
(`backtest_grid*.py`, status still "IDEA" in its registry). The harness ported here is
`backtest_grid_realistic.py`: a long-only limit ladder on BTC — buy a level when price
touches it from above, sell that lot one level up, `2n+1` levels of equal size, close
everything and recentre when price drifts more than `2 × n × spacing` from the centre.
No signal, always on.

Data: `btc_1m` in `prod.db`, 2020-01-01 → present, 1-minute bars as ground truth for
touches. Fills are limit orders: a touch fills with probability `fill_rate` (50%, the
trader's "realistic" assumption) at the level ± 0.02% slippage. Recentre closes are market.

## Fixed grid (no additions after seeing results)

| knob | values |
|---|---|
| grid spacing | 0.2%, 0.5% of centre |
| levels | 10 each side (21 total) |
| fill rate | 50% |
| slippage | 0.02% |
| cost model A "maker" | grid fills 2 bp/side (Binance maker), recentre closes 7.5 bp/side (taker + slip) |
| cost model B "taker" | 7.5 bp/side everywhere (p300's 15 bp round trip) |
| capital | 1× (unlevered; leverage only scales both return and drawdown) |

Metrics on the daily P&L series (realized + mark-to-market of open lots, % of capital):
annualised Sharpe per half (2020-01-01 → 2023-04-30 and 2023-05-01 → present),
annual return / max drawdown (MAR) over the full period, per-year returns, cycles/year.

## Priors (stated before running)

`project_range_strategy_sanity_2026_09`: BTC shows almost no intraday mean reversion
(15m lag-1 autocorr −0.02, 4h +0.01). A grid earns the round-trip spread only from
oscillation that returns to the fill level; on a drifting asset the ladder accumulates
inventory into declines (2022) and pays for it in drawdown, while the upside is capped at
one spacing per lot. Expected: positive realized cycle income, drawdown dominated by the
long inventory in 2022, MAR well under 0.5, and the second half not better than the first.
Expected verdict: KILL.

## Decision rule (fixed)

Evaluate cost model A (the friendlier one) first. KILL if annualised Sharpe ≤ 0 in either
half OR full-period MAR < 0.5. Only if A survives is B evaluated with the same rule; both
must pass for the item to move to a sleeve-design discussion. No parameter search beyond the
four cells above; the cells are reported, not selected from.

Tag: ALPHA-SEARCH, 4 trials against the "range/grid" family (ledger).
