# S-096 Thu Bear V3 (V4 alternative)

Calendar-driven SHORT on BTC + ETH every Thursday 00:00 UTC, closed Friday
01:00 UTC. Conditional on the prior-day regime being one of `bear_trend`,
`sell_off`, or `chop`.

## Signal

- Thursdays only (UTC weekday == 3).
- Wednesday's regime ∈ {bear_trend, sell_off, chop} (NOT bull_trend).
- V4 (alternative): additionally requires CPI or NFP within ±1 day AND
  no OPEX within ±1 day. See [config.py](config.py) for event-type lists
  and rationale.

## Entry / exit

- Entry: Thursday 00:00 UTC (one tick fires; idempotent across ticks).
- Exit: Friday 01:00 UTC (matches Pine's `process_orders_on_close` fill on
  the Fri-00:00 bar). Holding through the Fri-00:00 funding settlement is
  intentional — the recovered alpha (~+11pp BTC over 25 Thursdays in
  2024-2026) dominates the funding accrual (1-5bp/trade typical).
- Stop loss: -1% spot (5% margin at k=5×) by default.
- Allocation: split equally across both legs (BTC, ETH).

## Edge thesis

Weekly Thursday selling pressure during macro-event-adjacent periods,
conditioned on being already in a non-bull regime.

## Caveats

- **V4 event filter was derived post-hoc** from V3's Thursday attribution
  (2026-04-19 E4 event-purged CPCV). Any V4 backtest that reuses the same
  CPI/NFP/OPEX series is curve-fit by construction — first genuine
  out-of-sample is paper live.
- **V4 fails closed**: if `scheduled_events` is missing, V4 skips rather
  than silently degrading to V1 unconditional shorts. Repopulate via
  `fetch_events.py` to re-enable.

## Calibration history

- **V1** unconditional (all regimes) — too noisy.
- **V2** bear_trend + sell_off only — too restrictive.
- **V3** bear_trend + sell_off + chop (used by P-200).
- **V4** bear-only + Wk1+Wk2 only — alternative, not selected.

Exit-hour change (Thu 23:00 → Fri 01:00) was made to match the Pine
reference exactly; earlier Thu-23 exit sacrificed the post-23:00 Thursday
move on big-down weeks (e.g. 2025-03-06: Pine +5.0% vs Live +0.4%, almost
entirely the missed last-hour move).

## Files

- [signal.py](signal.py) — signal evaluation + tick handler
- [signal.pine](signal.pine) — Pine Script reference for TradingView cross-validation
- [config.py](config.py) — calibration constants (regime filter, V4 events, hours)
- `__init__.py` — package marker

Validation: `tools/bitstamp_thu_bear_backtest.py` (to move to
`studies/notebooks/` in restructure step 8).
