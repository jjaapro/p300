# S-003 ADX Regime Flip

Trend-flip signal on BTC 1D. Catches medium-term directional moves;
takes the loss when trend reverses.

## Signal

14-period ADX crosses 25 from prior compression (<20 in last 20 bars).
Direction: LONG when close > EMA(50), SHORT when close < EMA(50).
LONG-only trend filter: LONG additionally requires close > EMA(150).
SHORT has no trend filter (asymmetric, since 2026-05-04 — see [config.py](config.py)).

## Entry / exit

- Entry at the crossover bar (the bar that completes the cross).
- Exit on: opposite ADX flip, ADX < 20 (trend exhaustion), or per-variant
  stop loss (default 10% spot, ~50% margin at k=5×).

## Edge thesis

Catches medium-term BTC trends. Pays the loss when trend reverses.
Asymmetric trend filter protects bull-market LONG whipsaws while
preserving counter-trend SHORT funding income.

## Calibration history

- **Stateful was_low machine** (current) — matches the TradingView Pine
  reference exactly. See [signal.py](signal.py) `_current_signal`.
- **Rolling 20-bar lookback** (deprecated 2026-05-01) — replaced after
  Bitstamp 8.7y backtest showed +175% / DD -89.6% vs stateful +924% /
  DD -40.1% (both with 10% SL).
- **Signal source switch** (2026-05-01) — moved from perp (`cd_futures_ohlcv`)
  to spot (`cd_spot_binance`) to align with TradingView's default BTCUSDT
  1D feed. Perp/spot ADX can diverge 1-2 points on calm tape.
- **Asymmetric trend filter** (2026-05-04) — LONG-only, after the
  symmetric variant lost ~$1.1k vs no-filter on the 2023-09 → 2026-05
  funding-aware replay (counter-trend SHORTs in bull markets earn funding
  AND often pay off on price).

## Files

- [signal.py](signal.py) — signal evaluation + tick handler
- [signal.pine](signal.pine) — Pine Script reference for TradingView cross-validation
- [config.py](config.py) — calibration constants (ADX periods, trend filter, cost)
- `__init__.py` — package marker

Validation script: `studies/notebooks/bitstamp_adx_backtest.py` (moved
from `tools/` in restructure step 8 — pending `.ipynb` conversion).
