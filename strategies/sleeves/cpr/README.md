# S-101 CPR — Contrarian Positioning Reversal

LONG on BTC + ETH when persistent negative funding + crowded shorts coincide
with a still-intact uptrend → expected short squeeze.

## Signal (all four must agree)

1. 3-day mean funding rate ≤ 20th percentile of trailing 180-day window.
2. L/S ratio ≤ 20th percentile of trailing 180-day window.
3. Daily close > EMA(20).
4. EMA(20) > EMA(50).

## Entry / exit

- **Entry**: at the next 1m bar after signal trigger, LONG at market.
- **Target**: BB upper band (20, 2σ) at signal day — fixed level.
- **Stop**: -5% from entry (hard).
- **Time stop**: 15 calendar days.
- **Allocation**: split equally across BTC and ETH.

## Edge thesis

Contrarian-position-with-trend setup: persistent negative funding + crowd
already short + price still in uptrend → short squeeze expected. Combines
sentiment extremes with momentum confirmation to avoid catching falling
knives.

## Caveats

- **Thin historical sample** (12 BTC + 9 ETH events upstream, n=21 total).
  Per-trade backtest: +129bp BTC, +69bp ETH; win rate 83/67%. Statistical
  power is limited — live data accumulation is the actual validation.
- **Funding accrual** is applied on close (since 2026-05-04 audit). Earlier
  comment ("CPR holds are intraday so funding is negligible") was incorrect
  for the 15-day time stop and inflated historical LONG P&L by ~75bp/trade.

## Files

- [signal.py](signal.py) — signal evaluation + tick handler
- [config.py](config.py) — calibration constants (stop, time-stop, percentile window)
- `__init__.py` — package marker

Source probe: `probes/diagnostic_contrarian_positioning.py` (2026-04-22).
TradingView re-validation is still pending (see memory `project_pdo_cpr_tv_revalidation.md`).
