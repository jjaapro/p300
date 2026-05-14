"""S-078 Filtered Carry signal parameters."""

# Rolling window (days) for the funding-rate average that gates entry.
FR_WINDOW_DAYS = 7

# Entry triggers when the FR_WINDOW_DAYS rolling average daily funding is
# strictly above this threshold (default 0 → any positive funding regime).
FR_ENTRY_THRESHOLD = 0.0

# Exit triggers after this many consecutive days of negative daily funding.
EXIT_NEG_DAYS = 3

# Total round-trip cost: 5bp spot + 5bp perp per leg, both sides = 20bp.
# Expressed as a percentage of notional (0.20 = 0.20%).
ENTRY_EXIT_COST_PCT = 0.20
