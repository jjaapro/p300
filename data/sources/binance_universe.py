"""Refresh the screener universe — top-N USDT-perp pairs by 24h quote volume.

Writes to `screener_universe` table in prod.db. Run daily.

Usage:
    python data/sources/binance_universe.py [--top-n 150] [--once]

Schema:
    screener_universe(
      asset TEXT PRIMARY KEY,    -- e.g. "BTCUSDT"
      base  TEXT,                -- e.g. "BTC"
      quote TEXT,                -- always "USDT" for now
      listing_ts INTEGER,        -- from exchangeInfo, seconds
      median_quote_volume_30d REAL,  -- approximated by current 24h until backfill
      last_refreshed_ts INTEGER
    )
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
FAPI = "https://fapi.binance.com/fapi/v1"

log = logging.getLogger("binance_universe")


def _get(url: str, params: dict | None = None, timeout: float = 20.0):
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


SCHEMA = """
CREATE TABLE IF NOT EXISTS screener_universe (
    asset                   TEXT PRIMARY KEY,
    base                    TEXT,
    quote                   TEXT,
    listing_ts              INTEGER,
    median_quote_volume_30d REAL,
    last_refreshed_ts       INTEGER
);
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def refresh_universe(top_n: int = 150) -> int:
    """Pull live ticker + exchangeInfo, write top-N by 24h quote volume.

    Filters:
      - PERPETUAL contractType (excludes delivery/quarterly futures)
      - Quote asset = USDT (drops BUSD/USDC perps)
      - TRADING status (excludes BREAK/AUCTION_MATCH)
      - Drops anything with a hard-coded blacklist marker (`_DOWN`, `_UP` legacy)

    Returns count written.
    """
    ex_info = _get(f"{FAPI}/exchangeInfo")
    listing_map: dict[str, tuple[str, str, int]] = {}
    for s in ex_info.get("symbols", []):
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s["symbol"]
        if "_" in sym:  # legacy DOWN/UP variants
            continue
        onboard_ms = s.get("onboardDate")
        onboard_s = int(onboard_ms) // 1000 if onboard_ms else None
        listing_map[sym] = (s["baseAsset"], "USDT", onboard_s)

    ticker = _get(f"{FAPI}/ticker/24hr")
    rows = []
    for t in ticker:
        sym = t["symbol"]
        if sym not in listing_map:
            continue
        try:
            qvol = float(t["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((sym, qvol))

    rows.sort(key=lambda r: r[1], reverse=True)
    top = rows[:top_n]

    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        now_s = int(time.time())
        for sym, qvol in top:
            base, quote, listing_ts = listing_map[sym]
            con.execute(
                "INSERT INTO screener_universe "
                "(asset, base, quote, listing_ts, median_quote_volume_30d, "
                " last_refreshed_ts) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(asset) DO UPDATE SET "
                "  base=excluded.base, quote=excluded.quote, "
                "  listing_ts=excluded.listing_ts, "
                "  median_quote_volume_30d=excluded.median_quote_volume_30d, "
                "  last_refreshed_ts=excluded.last_refreshed_ts",
                (sym, base, quote, listing_ts, qvol, now_s),
            )
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM screener_universe").fetchone()[0]
    finally:
        con.close()
    log.info(f"refreshed universe — wrote/updated top {len(top)} of "
             f"{len(rows)} eligible USDT perps; total rows now: {n}")
    return len(top)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top-n", type=int, default=150)
    p.add_argument("--once", action="store_true",
                   help="ignored — this script always runs once")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    n = refresh_universe(top_n=args.top_n)
    print(f"wrote top-{n} universe to screener_universe")


if __name__ == "__main__":
    main()
