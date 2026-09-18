"""Bybit linear `allLiquidation.<SYMBOL>` stream (pushed every 500 ms).

Frame: {"topic":"allLiquidation.BTCUSDT","type":"snapshot","ts":..,
        "data":[{"T":1739502302929,"s":"BTCUSDT","S":"Sell","v":"0.001","p":"96176.50"}]}

Bybit's `S` is the side of the POSITION that was liquidated ("Buy: a long
position was liquidated"), the opposite convention from Binance:
  S == "Buy"  -> pos_side long,  order_side sell
  S == "Sell" -> pos_side short, order_side buy
v = qty (base), p = bankruptcy price, T = ts_ms.

Policy: subscribe in batches of <= 10 topics; send {"op":"ping"} every 20 s;
pongs and subscribe acks carry no data. A rejected topic is logged and the
rest keep streaming.
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("micro.bybit")

URL = "wss://stream.bybit.com/v5/public/linear"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
                   "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT", "LTCUSDT")
PING = json.dumps({"op": "ping"})
PING_INTERVAL_S = 20
SUBSCRIBE_BATCH = 10
VENUE = "bybit"


def subscribe_messages(symbols=DEFAULT_SYMBOLS, batch: int = SUBSCRIBE_BATCH) -> list[str]:
    topics = [f"allLiquidation.{s}" for s in symbols]
    return [json.dumps({"op": "subscribe", "args": topics[i:i + batch]})
            for i in range(0, len(topics), batch)]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def liquidation_row(item: dict, received_ms: int) -> tuple | None:
    side = str(item.get("S", "")).lower()
    if side == "buy":
        pos_side, order_side = "long", "sell"
    elif side == "sell":
        pos_side, order_side = "short", "buy"
    else:
        return None
    price, qty = _f(item.get("p")), _f(item.get("v"))
    if price <= 0 or qty <= 0:
        return None
    return (VENUE, item.get("s"), int(item.get("T") or 0), pos_side, order_side,
            price, qty, price * qty, None, int(received_ms))


def parse_message(text: str, received_ms: int | None = None) -> list[tuple]:
    """Rows for the liquidations table; [] for pongs, acks and errors."""
    received_ms = int(time.time() * 1000) if received_ms is None else received_ms
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    if not isinstance(obj, dict):
        return []
    if obj.get("op") == "pong" or obj.get("ret_msg") == "pong":
        return []
    if "success" in obj or obj.get("op") == "subscribe":
        if obj.get("success") is False:
            log.warning(f"bybit subscribe rejected: {obj.get('ret_msg')!r} ({obj})")
        return []
    if not str(obj.get("topic", "")).startswith("allLiquidation."):
        return []
    rows = []
    for item in obj.get("data") or []:
        row = liquidation_row(item, received_ms)
        if row is not None:
            rows.append(row)
    return rows
