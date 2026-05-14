"""Data layer — local DB accessors and external fetchers.

  loaders.py   Read functions for the local trader.db (BTC/ETH OHLC, LS ratio).
  sources/     External data fetchers (Binance feed, news RSS, F&G, Fed XML,
               Polymarket) — populated in restructure step 6e.
  feed.py      Unified scheduler that calls the sources every minute
               (populated later — see Proposal.md).

Sub-directories that hold non-Python state:
  databases/   SQLite files (prod.db, sim.db).
  csvs/        Temporary downloaded CSV/XML data.
  archives/    Per-sleeve persistent artifacts.
"""
