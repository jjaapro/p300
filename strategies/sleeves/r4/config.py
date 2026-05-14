"""S-099 R4 sleeve parameters (4 variants share this file)."""

# Strategy names written to trades.strategy.
STRATEGY_R4_BTC = "JPLUS_R4_BTC"
STRATEGY_R4_ETH = "JPLUS_R4_ETH"
STRATEGY_R4_BTC_V2 = "JPLUS_R4_BTC_V2"
STRATEGY_R4_ETH_V2 = "JPLUS_R4_ETH_V2"

# Inner-leverage multiplier applied on top of vol-target leverage when the
# R4 gate has NOT fired. 1.0 (no amplification) when the gate IS active.
R4_INNER_LEV_UNGATED = 2.5
R4_INNER_LEV_GATED = 1.0

# V1 R4_BTC — Mon wk1-2, 06:00 → 18:00 UTC (12h hold).
R4_BTC_ENTRY_HOUR = 6
R4_BTC_EXIT_HOUR = 18

# V1 R4_ETH — Tue 20:00 → Wed 20:00 UTC (24h, where Wed is in days 1-14).
R4_ETH_ENTRY_HOUR = 20
R4_ETH_EXIT_HOUR = 20

# V2 R4_{BTC,ETH}_V2 — Wed+Fri wk1-2, 04:00 → 14:00 UTC (10h hold). The
# era-stable cell from the 2026-05-08 grid search; see
# tools/r4_study/findings.md for the selection methodology.
R4_V2_ENTRY_HOUR = 4
R4_V2_EXIT_HOUR = 14
