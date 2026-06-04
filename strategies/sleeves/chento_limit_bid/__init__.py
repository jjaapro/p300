"""S-106 CHENTO_LIMIT_BID — long-side swing-base entries with MTF + confluence filters.

A first-pass replication of chento's "limit-bid at the swing low" pattern
(see studies/notebooks/chento_journal/strategy_spec.md). The setup:

  1. A recent 36h swing-low base in BTC perp
  2. Price approaching from above (within 1.2%)
  3. Multi-timeframe bias mildly bullish or counter-trend reversal
     (mtf_net ∈ {+1, +2} OR signature == '--+++')
  4. Confluence score ≥ 3 across {basis≤−2bp, funding<0, OI flushed,
     spot CVD positive}
  5. NY-overlap session (12:00–17:00 UTC)
  6. Mon / Tue / Wed only (weekend liquidity excluded)

Entry: MARKET on the first 15m bar after all gates pass.
Stop: swing_low × (1 − 0.020) (2% below the structural low)
Target: entry + 3 × (entry − stop)  → fixed 3R
TIF: 14 days time-stop

v1 is single-position, single-exit. The multi-tier ladder + partial scale-outs
documented in the spec are v2 work pending partial-close infrastructure.

See README.md for the calibration story and chento_journal/strategy_spec.md
for the full discretionary playbook this sleeve approximates.
"""
from .signal import (
    try_decide_for_variant,
    execute_for_variant,
    try_fire_for_variant,
)

__all__ = [
    "try_decide_for_variant",
    "execute_for_variant",
    "try_fire_for_variant",
]
