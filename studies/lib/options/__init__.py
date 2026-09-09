"""Study-local options toolkit (S-VRP, 2026-09): Black-Scholes pricing and
implied vol (`bs`), chain selection on p300's Deribit tables (`chain`), and
the delta-hedged short-strangle P&L engine (`pnl_engine`). Ported from the
trader repo's research/probe_vrp_straddle{,_v2}.py so the Phase-3 numbers
can be reproduced byte-for-byte before any new claim is made.

Not production code: nothing under strategies/ or bots/ imports this.
"""
