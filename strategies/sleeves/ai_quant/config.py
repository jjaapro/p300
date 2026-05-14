"""AI_QUANT sleeve parameters."""

SLEEVE_NAME = "AI_QUANT"
DEFAULT_ASSET = "BTC"

# Daily entry window in UTC. Fires on the first minute within this window
# that passes all four gates (kill-switch / time-window /
# per-day-already-fired / daily-cost-cap).
ENTRY_WINDOW_HOURS_UTC = 0          # 00:xx UTC
ENTRY_WINDOW_START_MIN = 5          # 00:05
ENTRY_WINDOW_END_MIN = 15           # 00:15 (inclusive)

# Default daily API spend ceiling (USD). Overridable via
# AI_QUANT_DAILY_COST_CAP_USD env var, bounded to [0.01, 50.0] in signal.py
# so a typo like "50" instead of "5.0" can't 10× the intended cap.
DEFAULT_DAILY_COST_CAP_USD = 5.0

# Conviction floor: model output with conviction < this value is forced to
# FLAT regardless of stated direction.
MIN_CONVICTION_FOR_TRADE = 30

# Defer feature: max defers per UTC day before the defer tool is stripped
# from the next API call, forcing a LONG/SHORT/FLAT commitment.
MAX_DEFERS_PER_DAY = 3

# Latest absolute clock-time a deferred re-fire may land on within today's
# UTC date. Past this, defer targets are clamped down so they still execute
# today (avoids the next-day's 00:05 window swallowing the deferred call).
DEFER_LATEST_HOUR = 23
DEFER_LATEST_MIN = 55
