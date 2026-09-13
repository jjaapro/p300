"""Calendar/clock substrategies, kept only until BACKLOG step 4 archives them.

The TIMING_ANOMALIES meta-sleeve that dispatched these was retired on
2026-09-13 with the rest of the legacy orchestrator path, and with it went the
`_RESOLVERS` registry, `get_dispatch`, and the `ALLOCATOR_KEY` weight table.
R4 — the one substrategy a live bot also ran — moved to bots/r4/strategy/ in
step 2, so the dormant -> application import that registry carried is gone too.

What remains under this package is fomc/, thu_bear/, pdo/ and cpr/: dormant,
dispatched by nothing, and still imported by three surviving callers —
bootstrap.py (fomc.init_schema), dashboard/market.py (cpr's percentile
constants, read on a live display) and strategies/support/gate.py. Step 4
archives them and has to resolve those three edges first.
"""
