# How ADX S-003 T2 works

**One line:** enter when a dead market wakes up trending, direction from price vs
EMA(50), ride the trend on a wide trail.

## Entry
Daily bars (BTC spot for signal, perp for execution). Fire when **ADX(14) crosses
from below 20 to ≥ 25** — compression breaking into trend. Direction: **LONG** if
close > EMA(50), **SHORT** if close < EMA(50). One decision per UTC day.

## Filters (Tier-2 calibration, 2026-07-22)
- **Symmetric trend filter** — LONGs additionally need close > EMA(150), SHORTs
  close < EMA(150). Kills counter-trend whipsaws on both sides (maxDD −27% → −15%,
  MAR 1.78 → 3.09 on 2018→2026).
- **Funding-crowding LONG veto** — skip LONGs when the 30d funding z-score > 1.5
  ("don't long over-crowded leverage"; avoided the 2025-10-05 ATH long).

## Exits & sizing
Three exits race: **ADX drops back below 20** (trend died), **ATR(14)×4 trailing
stop**, or the fixed **10% stop-loss**. No profit target and no timed stop — a
trend runs as long as it runs (that's why the chart shows no TP line and "no timed
stop" for this bot). Fixed-R 2% of capital over the effective initial stop.

## Character
A few entries per year; losers historically cluster in funding-harvest shorts that
end ~net-flat after funding. The trade you see open for weeks with a far-away trail
is this bot behaving normally.
