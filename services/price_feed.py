"""Read last-close price from the local trader.db.

Replaces `dashboard.services.execution_service._get_current_price` from the
original repo. Only the two assets P-300 touches (BTC, ETH) are supported.

Data provenance:
  BTC: cd_futures_ohlcv  (Binance BTC-USDT perp, hourly)
  ETH: eth_1m            (Binance ETH/USDT spot, 1-min; we take the latest close)

If the table is stale — because binance_feed.py hasn't been running — callers
get whatever the most recent row says. Signal services already guard with
warmup / no-data branches, so a stale read never fires a trade on stale data
unless the staleness is small relative to the service's cadence.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"


def get_current_price(asset: str) -> float | None:
    asset = asset.upper()
    con = sqlite3.connect(str(TRADER_DB))
    try:
        if asset == "BTC":
            row = con.execute(
                "SELECT close FROM cd_futures_ohlcv ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        elif asset == "ETH":
            row = con.execute(
                "SELECT close FROM eth_1m ORDER BY open_time DESC LIMIT 1"
            ).fetchone()
        else:
            return None
    finally:
        con.close()
    return float(row[0]) if row and row[0] is not None else None


# Backward-compatible alias so ported services can keep calling _get_current_price
_get_current_price = get_current_price
