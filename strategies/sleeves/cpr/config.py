"""S-101 CPR signal parameters."""

# Round-trip taker fee estimate (5bp entry + 5bp exit) on BTC/ETH perps.
COST_BP_RT = 10.0

# Hard stop loss: 5% from entry price.
STOP_PCT = 0.05

# Time stop: close any open position after this many calendar days.
TIME_STOP_DAYS = 15

# Rolling window for funding / LSR percentile computation (days).
PCTILE_WINDOW = 180

# Percentile threshold for funding and LSR conditions (0.20 = 20th pctile).
PCTILE_THRESHOLD = 0.20
