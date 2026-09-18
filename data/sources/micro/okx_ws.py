"""OKX public `liquidation-orders` channel for all SWAP contracts (at most one
update per second per contract).

Frame: {"arg":{"channel":"liquidation-orders","instType":"SWAP"},
        "data":[{"details":[{"bkLoss":"0","bkPx":"0.09859","ccy":"","posSide":"short",
                             "side":"buy","sz":"10975","ts":"1789678209470"}],
                 "instFamily":"MINA-USDT","instId":"MINA-USDT-SWAP","instType":"SWAP",
                 "uly":"MINA-USDT"}]}

`sz` is in CONTRACTS. The contract value map comes from
GET /api/v5/public/instruments?instType=SWAP (fetched at startup, refreshed
daily, stored in okx_instruments):
  ctValCcy == base (e.g. BTC-USDT-SWAP ctVal 0.01 BTC): qty = sz x ctVal x ctMult,
      notional = qty x price
  ctValCcy == "USD" (inverse, e.g. BTC-USD-SWAP ctVal 100): notional = sz x ctVal x ctMult,
      qty = notional / price
  (ctMult is 1 for every current SWAP; absent -> 1)
  unknown instId: qty = sz (contracts), notional NULL, extra "ct_val": null
posSide is "long"/"short", or "net" in one-way mode — then derive it from the
order side (sell -> long was liquidated, buy -> short).

collector.py seeds the map from the okx_instruments table before the socket
starts and, on the very first run, waits for one REST attempt, so rows in
contracts only occur for a swap listed between two daily refreshes. Such rows
(venue='okx' AND notional IS NULL) are self-describing and can be repaired
offline by joining okx_instruments on symbol: qty = extra.sz_contracts x ct_val
x ct_mult and notional = qty x price for base-ccy contracts; for ct_val_ccy =
'USD' notional = sz_contracts x ct_val x ct_mult and qty = notional / price;
rewrite extra.ct_val as well.

Policy: OKX drops idle sockets after 30 s, so send the text frame `ping`
every 20 s; the reply is the text frame `pong` (not JSON, not data).
"""
from __future__ import annotations

import json
import logging
import time

log = logging.getLogger("micro.okx")

URL = "wss://ws.okx.com:8443/ws/v5/public"
INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments?instType=SWAP"
SUBSCRIBE = json.dumps({"op": "subscribe",
                        "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]})
PING = "ping"
PING_INTERVAL_S = 20
INSTRUMENTS_REFRESH_S = 24 * 3600
VENUE = "okx"


def default_http_get(url: str) -> dict:
    import requests
    r = requests.get(url, timeout=30, headers={"User-Agent": "p300-collector/1.0"})
    r.raise_for_status()
    return r.json()


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ─── instruments ──────────────────────────────────────────────────────────────

def parse_instruments(payload: dict) -> dict[str, dict]:
    """instId -> {ct_val, ct_val_ccy, ct_mult, settle_ccy} from the REST reply."""
    out: dict[str, dict] = {}
    for inst in (payload or {}).get("data") or []:
        inst_id = inst.get("instId")
        if not inst_id:
            continue
        out[inst_id] = {"ct_val": _f(inst.get("ctVal")), "ct_val_ccy": inst.get("ctValCcy"),
                        "ct_mult": _f(inst.get("ctMult")) or 1.0, "settle_ccy": inst.get("settleCcy")}
    return out


def instrument_rows(instruments: dict[str, dict], fetched_ms: int) -> list[tuple]:
    return [(inst_id, m["ct_val"], m["ct_val_ccy"], m["ct_mult"], m["settle_ccy"], int(fetched_ms))
            for inst_id, m in instruments.items()]


# ─── liquidations ─────────────────────────────────────────────────────────────

def liquidation_row(inst_id: str, d: dict, instruments: dict[str, dict],
                    received_ms: int) -> tuple | None:
    price = _f(d.get("bkPx"))
    contracts = _f(d.get("sz"))
    if price <= 0 or contracts <= 0:
        return None
    order_side = str(d.get("side", "")).lower()
    pos_side = str(d.get("posSide", "")).lower()
    if pos_side not in ("long", "short"):
        if order_side == "sell":
            pos_side = "long"
        elif order_side == "buy":
            pos_side = "short"
        else:
            return None
    if order_side not in ("buy", "sell"):
        order_side = "sell" if pos_side == "long" else "buy"
    meta = instruments.get(inst_id)
    if meta is None or meta.get("ct_val", 0) <= 0:
        qty, notional, ct_val = contracts, None, None
    elif str(meta.get("ct_val_ccy", "")).upper() == "USD":
        ct_val = meta["ct_val"] * (meta.get("ct_mult") or 1.0)
        notional = contracts * ct_val
        qty = notional / price
    else:
        ct_val = meta["ct_val"] * (meta.get("ct_mult") or 1.0)
        qty = contracts * ct_val
        notional = qty * price
    extra = json.dumps({"bkLoss": d.get("bkLoss"), "sz_contracts": contracts, "ct_val": ct_val,
                        "ct_val_ccy": None if meta is None else meta.get("ct_val_ccy"),
                        "posSide_raw": d.get("posSide")})
    return (VENUE, inst_id, int(d.get("ts") or 0), pos_side, order_side, price, qty, notional,
            extra, int(received_ms))


def parse_message(text: str, instruments: dict[str, dict],
                  received_ms: int | None = None) -> list[tuple]:
    """Rows for the liquidations table; [] for `pong`, subscribe acks, errors."""
    received_ms = int(time.time() * 1000) if received_ms is None else received_ms
    if text == "pong":
        return []
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    if not isinstance(obj, dict):
        return []
    if "event" in obj:                       # subscribe ack / error
        if obj.get("event") == "error":
            log.warning(f"okx error frame: {obj}")
        return []
    if (obj.get("arg") or {}).get("channel") != "liquidation-orders":
        return []
    rows = []
    for item in obj.get("data") or []:
        inst_id = item.get("instId")
        if not inst_id:
            continue
        for d in item.get("details") or []:
            row = liquidation_row(inst_id, d, instruments, received_ms)
            if row is not None:
                rows.append(row)
    return rows
