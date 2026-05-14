"""S-102 PDO-L-RF signal parameters."""

# Gap threshold: minimum % the bar_day's open must be above the prior day's
# open (PDO) to consider the setup. 2.0% by default.
GAP_THRESHOLD_PCT = 2.0

# Regime filter: BTC 30d trailing return must be >= this value to take entries.
# Below -10% the gap-fill edge degrades materially.
REGIME_THRESHOLD_PCT = -10.0

# Tolerance band around PDO for touch detection (% of price).
TOUCH_TOL_PCT = 0.10

# Maximum hold time per asset (hours). BTC holds longer; ETH closes faster.
HOLD_BARS_BY_ASSET = {"BTC": 24, "ETH": 4}

# Round-trip taker fee estimate (5bp entry + 5bp exit) on BTC/ETH perps.
COST_BP_RT = 10.0
