"""S-096 Thu Bear signal parameters."""

# Round-trip taker fee estimate (5bp entry + 5bp exit) on BTC/ETH perps.
COST_BP_RT = 10.0

# V3 enhanced regime filter (matches backtest_thu_bear.py P-200 usage).
V3_REGIMES_ALLOWED = {"bear_trend", "sell_off", "chop"}

# V4 event-conditioned filter: Thursdays within +/-1 day of CPI or NFP,
# excluding +/-1 day of any OPEX event. Motivated by E4 event-purged CPCV
# (2026-04-19) which showed V3's Sharpe is driven by CPI/NFP-adjacent
# Thursdays and hurt by OPEX-adjacent Thursdays.
V4_INCLUDE_EVENT_TYPES = ("CPI", "NFP")
V4_EXCLUDE_EVENT_TYPES = ("OPEX_MONTHLY", "OPEX_QUARTERLY")
V4_WINDOW_DAYS = 1

# UTC hours at which we act. At exactly HH we fire; idempotency keeps us safe
# across the minute-level ticks within that hour.
ENTRY_HOUR = 0     # Thursday 00:00 UTC
EXIT_HOUR = 1      # Friday 01:00 UTC — matches Pine's process_orders_on_close
                   # fill on the Fri-00:00 bar. The dispatcher fires at the first
                   # tick of Fri 01:xx and uses the latest 1m bar's close, which
                   # is the price at ~Fri 01:00 UTC. Holding through the Fri
                   # 00:00 funding settlement is intentional — the recovered
                   # alpha (~+11pp BTC over 25 Thursdays in 2024-2026) dominates
                   # the funding accrual (1-5bp/trade typical).
EXIT_WEEKDAY = 4   # Friday (Mon=0..Sun=6)
