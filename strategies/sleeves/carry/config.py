"""S-078 Filtered Carry signal parameters."""

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
