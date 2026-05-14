# S-102 PDO-L-RF — Prior-Day-Open Retouch (Long, Regime-Filtered)

LONG on BTC + ETH after an intraday gap-up, when the price retraces back
to the prior day's open. Conditional on BTC not being in a deep bear regime.

## Signal

1. Bar_day's open ≥ `GAP_THRESHOLD_PCT` above the prior day's open (PDO).
2. Regime filter: BTC 30d trailing return ≥ `REGIME_THRESHOLD_PCT` (default -10%).
3. Intraday 1H bar's range contains the PDO (±`TOUCH_TOL_PCT` band).

See [config.py](config.py) for tunable thresholds.

## Entry / exit

- **Entry**: market on the first hourly bar that contains the PDO level.
- **Exit**: `min(hold_hours, end-of-bar_day)`. BTC holds 24h, ETH 4h
  (see `HOLD_BARS_BY_ASSET` in config.py).
- **Allocation**: split equally across BTC and ETH.

## Edge thesis

Gap-fills are a known intraday phenomenon in crypto. Mean-reversion long
after a down-gap, conditional on the broader regime not being in capitulation.

## Caveats

- **Parameters were swept** in upstream research without visible
  walk-forward CV — data-snooping exposure.
- **No per-trade stop loss**: the diagnostic_pdo_retouch_sl_sweep probe
  found no SL config that improved expectancy.
- **Funding not modeled** on close (`apply_funding=False`). BTC's 24h hold
  crosses 3 funding settlements at ~5bp each; the asymmetry is bounded
  and documented in AUDIT_2026_05_04. Worth quantifying once enough
  live PDO trades exist.

## Calibration history

- **TradingView re-validation** (2026-05-11) confirmed Python signal
  matches the Pine reference. TV's 172 trades reduce to 86 real (Pine
  margin-call quirk inflates the count). WR / PF match Python. See
  memory `project_pdo_cpr_tv_revalidation.md`.

## Files

- [signal.py](signal.py) — signal evaluation + tick handler
- [signal.pine](signal.pine) — Pine Script reference for TradingView cross-validation
- [config.py](config.py) — calibration constants (gap, regime, touch tolerance, hold)
- `__init__.py` — package marker

Validation notebooks:
`studies/notebooks/pdo_tv_validate.ipynb`, `studies/notebooks/pdo_tv_validate_sweep.ipynb`,
`studies/notebooks/pdo_tv_validate_dump.ipynb`, `studies/notebooks/pdo_tv_csv_parse.ipynb`.
