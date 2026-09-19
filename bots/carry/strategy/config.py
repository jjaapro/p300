"""S-078 Filtered Carry signal parameters."""
import os

# The traded asset. One process trades one asset: the ETH wrapper (bots/carry_eth)
# sets CARRY_ASSET before the sleeve is imported, the way chento's ETH leg does.
# Read at call time by signal.py (`_cfg.ASSET`), so a test can point it at ETH.
ASSET = os.environ.get("CARRY_ASSET", "BTC").upper()

# Where the daily spot and perp closes come from, per asset: (table, timestamp
# column, timestamp units per second, optional row filter). BTC keeps its hourly
# tables; ETH has no spot hourly table, so its spot close is the last minute of
# each UTC day in eth_1m (the filter keeps the fetch to the day's last 10 minutes)
# and its perp close the last 15-minute bar of the day. Funding comes from
# strategies.support.funding, which maps the asset to its settlement table.
PRICE_SOURCES = {
    "BTC": {"spot": ("cd_spot_binance", "timestamp", 1, None),
            "perp": ("cd_futures_ohlcv", "timestamp", 1, None)},
    "ETH": {"spot": ("eth_1m", "open_time", 1000, "((open_time / 60000) % 1440) >= 1430"),
            "perp": ("cd_futures_eth_15m", "timestamp", 1, None)},
}

# Rolling window (days) for the funding-rate average that gates entry.
FR_WINDOW_DAYS = 7

# Entry triggers when the FR_WINDOW_DAYS rolling average daily funding is
# strictly above this threshold (default 0 → any positive funding regime).
FR_ENTRY_THRESHOLD = 0.0

# Exit triggers when the trailing EXIT_CUM_DAYS-day cumulative daily funding
# (sum of the daily sums, % of notional) is below EXIT_CUM_THRESHOLD_PCT.
#
# Replaced the three-consecutive-negative-days exit on 2026-09-12
# (studies/notebooks/carry_exit_rule_2026_09/, pre-registered): over the
# full 2019-2026 settlement history the streak exit cost 0.85 %/yr against
# never exiting and lost MORE than never exiting in the two longest
# negative stretches, because it reacted after the damage and paid 0.24 %
# to leave and 0.24 % to return. This rule kept a defined exit for a
# sustained negative regime (5 toggles in 6.7 years) at 0.11 %/yr.
EXIT_CUM_DAYS = 30
EXIT_CUM_THRESHOLD_PCT = -0.5

# Total round-trip cost: 5bp spot + 5bp perp per leg, both sides = 20bp.
# Expressed as a percentage of notional (0.20 = 0.20%).
ENTRY_EXIT_COST_PCT = 0.20
