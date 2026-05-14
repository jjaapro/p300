# S-078 Filtered Carry — Delta-neutral BTC funding harvest

LONG BTC spot + SHORT BTC perp of equal notional. The short-perp leg
collects funding; the basis stays near zero over the open period. P&L
is dominated by funding accrual, not price.

## Signal

- **Entry**: 7-day rolling average daily BTC funding rate > 0 (see
  `FR_ENTRY_THRESHOLD` in [config.py](config.py)).
- **Exit**: 3 consecutive days of negative daily funding
  (`EXIT_NEG_DAYS`).
- No regime filter — funding regime IS the signal.

## Structure

- Asset: BTC.
- Direction tagged as `LONG` in the trade ledger (the spot leg is the
  defining side for accounting). The short-perp hedge is implicit —
  P&L is funding accrual minus 20bp round-trip fees on the synthetic
  spot+perp position.
- Daily funding = sum of 3 settlements per day (00:00 / 08:00 / 16:00 UTC)
  from `cd_funding_rate` (Binance BTC perp).

## Entry / exit semantics

- **Idempotent per UTC day**: at most one CARRY action (open or close)
  per variant per day. Same-day exit→re-entry is blocked (would burn
  fees without funding-regime change).
- **Single-open invariant**: no new entry while an existing CARRY trade
  is open.
- No scheduled time stop — held until funding regime breaks.

## Edge thesis

Structurally positive funding in bullish regimes is paid to delta-neutral
holders for free if you can hedge cheaply. P&L is dominated by funding
accrual; price movement nets to zero by construction.

## Calibration

Source: `backtest_tail_harvester.py` (mode='filtered').

## Files

- [signal.py](signal.py) — signal evaluation + tick handler
- [config.py](config.py) — calibration constants (window, threshold, exit, cost)
- `__init__.py` — package marker

No .pine reference: CARRY is funding-driven, not chart-pattern based.
