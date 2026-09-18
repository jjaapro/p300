"""Microstructure collector library (2026-09-17): the pieces behind
`collector.py`, the fleet unit that records public liquidation, depth and
Hyperliquid data into its own `data/databases/microstructure.db`.

Research only. Nothing here reads or writes prod.db, and no bot reads this
database. Every module is side-effect free at import: schema, sockets and
files are only touched from functions called by `collector.main()`.

    store.py        MICRO_DB path, schema, batched Writer, status file
    wsclient.py     reconnecting websocket loop (backoff, app pings, max age)
    book.py         Binance local order book + 10 bp bucketing + walls
    binance_ws.py   !forceOrder@arr parser, diff-depth handler + sampler
    bybit_ws.py     allLiquidation parser + subscribe/ping policy
    okx_ws.py       liquidation-orders parser + ping policy + ctVal map
    hyperliquid.py  REST pollers: asset contexts, leaderboard, positions
"""
