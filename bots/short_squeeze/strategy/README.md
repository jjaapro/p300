# S-105 SHORT_SQUEEZE — fade short-covers at swept lows

First mechanistically-grounded sleeve in the portfolio (added 2026-05-18). The
trader-described pattern: shorts pile up during the Asia session, get swept at
a London/NY low, then squeeze on perp/spot CVD divergence as forced covers
hit. We long the squeeze with a tight stop and fixed-R target.

See [studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb](../../../studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb)
for the discovery and calibration.

## Signal

A bar-level LONG trigger fires when:

1. **Session gate** — the bar is in London or NY (07:00-21:00 UTC)
2. **Macro context** — today's Asia session was a *short-squeeze setup*:
   - Asia OI rose by ≥ 0.5%
   - Asia mean funding < 0
   - Asia closed below its open
3. **Sweep gate** — the bar's low pierces the lowest low of the prior
   6 hours (24 × 15m bars)
4. **Percentile gates** (rolling 90-day distribution of London/NY bars):
   - `perp_cvd_pct < 0.15` (bar in bottom 15% of perp_cvd — strong perp
     selling intensity)
   - `divergence_pct > 0.70` (bar's `spot_cvd - perp_cvd` in top 30% —
     perp selling harder than spot, the "bullish divergence")
5. **Reversal gate** — `close_in_range ≥ 0.10` (any meaningful reversal
   off the bar's low)
6. **Cooldown** — at least 4 hours since the last trigger on this variant

## Execution

- Entry at the trigger bar's close (next 15m bar open in practice)
- **Fixed stop** at `trigger_bar.low × (1 - 0.001)` (10 bp slippage buffer)
- **Fixed target** at `entry + 3 × (entry - stop)` (3R)
- **Time stop** at 6 hours from entry if neither stop nor target hits
- No trailing — backtest shows TSL underperforms fixed TP for this signal
  type (mean-reversion-to-a-level, not trend continuation)

## Sleeve config

Live variant config (`spec_json.sleeves.SHORT_SQUEEZE`) should specify:

| Field | Recommendation | Why |
|---|---|---|
| `weight_pct` | 5.0 | Pre-leverage allocation of NAV per trade |
| `leverage` | 20-100 | High leverage matches the tight stop |
| `priority` | 50 | Lower than FOMC (100), higher than EMA (200) |
| `params.cooldown_min` | 240 | 4h cooldown matches backtest |

Per-trade risk math:
- Stops are typically 0.4-0.7% from entry
- At 100x leverage with 5% NAV allocation → ~0.5% NAV per stop
- Target +1-2% NAV per win (3R risk)

## Asymmetric edge

The mirror SHORT-side signal (fade longs at a swept high) **does not work**
at any tested configuration despite the daily long-squeeze pattern being
~4.6× more common than the short-squeeze pattern. Don't add a SHORT variant.

## Backtest summary (2022-01-30 → 2026-05-18)

| Metric | Value |
|---|---|
| Trades | 70 |
| Win % @ 3R | 44.3% |
| Avg R | +0.40 |
| PF | 1.65 |
| Sum R / 4 yrs | +27.8 |
| Year-to-year std of avg R | 0.13 |

Conservative net annualized: **+2-3% NAV/year** at 0.5% NAV risk per trade
after slippage haircut. Modest — this is intended to stack with other
mechanistic setups, not stand alone.

## Data dependencies

- `cd_futures_15m` and `cd_spot_15m` in prod.db (real taker buy/sell volume
  split, populated back to 2019-09 — the "added 2026-05-18" previously
  claimed here was the sleeve/schema add date, not a data floor; corrected
  2026-07-21. Maintained by `data/sources/binance.py`)
- `cd_open_interest` (hourly)
- `cd_funding_rate` (hourly)
- `btc_1m` (for the sweep loop's current-price lookup; via the standard
  `strategies.support.price_feed.get_current_price`)

## Related artifacts

- Research: [discovery.ipynb](../../../studies/notebooks/short_squeeze_sessions/discovery.ipynb)
- Backtest: [strategy_backtest.ipynb](../../../studies/notebooks/short_squeeze_sessions/strategy_backtest.ipynb)
- Exit-rule study & threshold sweep & walk-forward live inside the backtest
  notebook.
