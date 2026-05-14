"""External data fetchers — populate the local DB from external APIs.

  binance.py     Binance kline + funding-rate + LSR ingester (was binance_feed.py at root).
  coindesk.py    CoinDesk OI / liquidations / DVOL (was services/coindesk_fetcher.py).
  fed_funds.py   NY Fed target-rate XML scraper + phase classifier (was services/fed_funds_service.py).
  news.py        RSS/Atom news ingester (was services/news_fetcher.py).
  polymarket.py  Polymarket "Fed cuts in 2026" market reader (was services/polymarket_service.py).
  sentiment.py   alternative.me Fear & Greed index (was services/sentiment_index_service.py).

Each module exposes a ``refresh()`` (or equivalent) used by the unified
data feed (Proposal.md: data/feed.py).
"""
