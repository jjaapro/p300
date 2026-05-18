"""S-103 FOMC signal parameters."""

# Trade window (minutes from FOMC announcement time, which is 14:00 ET).
ENTRY_OFFSET_MIN = -10 * 60   # T-10h
EXIT_OFFSET_MIN = 30           # T+0.5h

# Tolerance for picking up the entry/exit minute. 1 minute is enough since
# the orchestrator ticks every minute.
WINDOW_TOL_MIN = 1

# Round-trip taker fee estimate (5bp entry + 5bp exit) on BTC perps.
COST_BP_RT = 10.0

# FOMC slippage override — 10bp RT vs the 5bp default. Rationale: FOMC
# enters around the announcement bar at 18:00 UTC with 10× leverage,
# which is when BTC/USDT spread widens and impact is largest. Per
# AUDIT_2026_05_13 High-tier execution-cost row, the audit range is
# 5-10bp/RT in normal conditions and "more in volatile windows" — FOMC
# is the canonical volatile window.
SLIPPAGE_BP_RT = 10.0
