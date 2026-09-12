# ADX robustness pack 2026-09 — pre-registration (frozen before any result)

Written 2026-09-12 before any `run_p1*.py` produced a number. Track P1 of the approved brainstorm follow-up
plan. Tag: AUDIT (a, b, d, e) + one policy decision (c). Nothing under `strategies/`, `bots/`, `data/` is
modified; prod.db read-only; the ADX sleeve's shipped Tier-2 parameters are used unchanged (ADX(14) 20/25,
EMA(50) direction, symmetric EMA(150) gate, 10 % SL, ATR(14)×4 ratcheting trail; the funding-crowding veto is
NOT modelled, as in the adx_study parity table).

## Questions

- **(a) Honest backtest = live semantics.** The research harness fills stops at the stop price on *daily*
  bars, updates the trail with the same bar's close before testing that bar's low, enters at the daily close,
  charges 10 bp and no funding. The live sleeve enters at the first 1 m close after the day boundary, checks
  stops minute by minute (`stop_path`: wick → stop, gap → open), applies a day's trail level only from the
  next day, seeds the trail from the anchor close, charges 15 bp and funding. What does the shipped
  configuration return under the live semantics, and how big is the gap? Gate: first reproduce the adx_study
  Tier-2 table with the harness (n = 27, +2483 %, −15.1 %, MAR 3.09 to 2026-06-25).
- **(b) Mark-to-market drawdown across bar phases → sizing.** The Tier-2 MTM maxDD of the live-semantics
  machine at each of the 24 day boundaries; the notional that keeps the worst-phase MTM maxDD inside a
  stated budget (default 25 % of capital), expressed as `RISK_PCT` / `NOTIONAL_MAX_X` for the shipped bot.
- **(c) Phase ensemble.** k ∈ {2, 3} equally spaced boundaries ({0, 12} and {0, 8, 16} UTC) at 1/k size each,
  daily MTM returns averaged; vs the live phase 0 and vs the 24-phase distribution; paired 30-day block
  bootstrap of the Sharpe and MAR differences vs phase 0; the all-24 average as the limit.
- **(d) Late entry.** The sleeve enters any time in the UTC day on which the cross is the newest closed bar.
  For every live-semantics entry: the P&L of the same trade entered h ∈ {0, 1, 2, 6, 12, 23} hours after the
  boundary (same exits), and the share of trades whose stop would already have been hit before a late entry.
- **(e) Funding and venue.** Funding paid by ADX longs (Binance settlements over each hold), per trade and per
  year of long exposure; the share of long-days on which S-078 CARRY was on (7-day average daily funding > 0
  until three negative days), when the two perp legs net and the pair is economically "ADX long on spot".

## Data

`cd_spot_binance` hourly → daily bars at each phase (`bv_lib.daily_from_intraday`), `btc_1m` (spot, the live
stop-check table) via the execution study's cache, Binance settlement funding (`bv_lib.fetch_binance_funding`,
cached), 2018-01-01 → now.

## Decision rules (fixed now)

- (a), (b), (d), (e): audit outputs, no rule; (a) reports the gap in CAGR, MTM Sharpe, MTM maxDD and MAR.
- (c): a phase ensemble is a **BUILD-CANDIDATE** only if its MTM maxDD is at least 5 pp shallower than the
  median single-phase maxDD AND its MTM Sharpe is at least the median single-phase Sharpe, at k ≤ 3;
  otherwise NO CHANGE. Complexity cost (k open sub-positions) is stated alongside.
- (d): if the mean P&L change from a 6-hour-late entry is within ±0.5 pp per trade, the current behaviour is
  fine and only the backtest assumption needs stating; otherwise a grace window is proposed (hours, not
  seconds — skipping forfeits the whole cycle because `was_low` is consumed).
- Trial ledger: 24 phases (all reported), 2 ensembles, 6 entry delays. No cell picking.

## Run order

`run_p1a_live_semantics.py` → `run_p1bc_phases.py` → `run_p1d_late_entry.py` → `run_p1e_funding.py`, then
`C:/Python/Python313/python.exe build_notebook.py` → `adx_robustness.ipynb`.
