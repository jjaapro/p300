"""Keep data/trader.db up-to-date with Binance market data the live services need.

Handles 4 feeds via Binance public REST (no API key needed):
  btc_1m           — BTCUSDT spot 1-min klines (for PDO, CPR)
  eth_1m           — ETHUSDT spot 1-min klines (for PDO, CPR)
  cd_futures_ohlcv — BTCUSDT perp 1-hour klines (for ADX, carry, regime, price_feed)
  cd_funding_rate  — BTCUSDT perp funding rate history (for carry, cpr)

Does NOT maintain (manually seed + periodically re-seed from upstream):
  cd_spot_binance      — seeded once, not refreshed (hourly, rarely read)
  ca_long_short_ratio  — seeded once, daily cadence; could be extended via
                         /futures/data/globalLongShortAccountRatio
  scheduled_events     — static calendar (CPI/NFP/OPEX), seeded once

Runs in two modes:
  python binance_feed.py --once   — fetch latest bars and exit
  python binance_feed.py          — loop forever, refresh every 60s

Uses stdlib urllib (no requests/ccxt dependency)."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DB_PATH = Path(__file__).resolve().parent / "data" / "trader.db"
SPOT_API = "https://api.binance.com/api/v3"
FAPI = "https://fapi.binance.com/fapi/v1"

log = logging.getLogger("binance_feed")


# ─── HTTP ────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, timeout: float = 20.0) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ─── Kline upserts ────────────────────────────────────────────────────────────

def _latest_open_time(con: sqlite3.Connection, table: str, ts_col: str) -> int:
    row = con.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_spot_klines_1m(symbol: str, table: str) -> int:
    """Fetch spot 1-minute klines and upsert into (btc_1m | eth_1m).
    open_time is in ms (matches the original schema). Returns rows inserted."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        last_ms = _latest_open_time(con, table, "open_time")
        start_ms = last_ms + 60_000 if last_ms else None  # start from next minute
        params: dict = {"symbol": symbol, "interval": "1m", "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{SPOT_API}/klines", params)
        inserted = 0
        for r in rows:
            ot = int(r[0])
            if ot == last_ms:
                continue
            con.execute(
                f"INSERT OR REPLACE INTO {table} "
                f"(open_time, open, high, low, close, volume, num_trades) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ot, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5]), int(r[8])),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def fetch_futures_klines_1h() -> int:
    """Fetch BTCUSDT perp 1h klines, upsert into cd_futures_ohlcv.
    timestamp is seconds (matches original CoinDesk schema)."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        last_s = _latest_open_time(con, "cd_futures_ohlcv", "timestamp")
        start_ms = (last_s + 3600) * 1000 if last_s else None
        params: dict = {"symbol": "BTCUSDT", "interval": "1h", "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{FAPI}/klines", params)
        inserted = 0
        for r in rows:
            ts_s = int(r[0]) // 1000
            if ts_s == last_s:
                continue
            con.execute(
                "INSERT OR REPLACE INTO cd_futures_ohlcv "
                "(timestamp, open, high, low, close, volume, quote_volume, "
                " volume_buy, quote_volume_buy, volume_sell, quote_volume_sell, "
                " total_trades, trades_buy, trades_sell) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, NULL)",
                (ts_s, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5]), float(r[7]), int(r[8])),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def fetch_funding_rate() -> int:
    """Fetch BTCUSDT funding history, upsert into cd_funding_rate.
    timestamp is seconds. fr_close stores the settlement rate."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        last_s = _latest_open_time(con, "cd_funding_rate", "timestamp")
        start_ms = (last_s + 3600) * 1000 if last_s else None
        params: dict = {"symbol": "BTCUSDT", "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{FAPI}/fundingRate", params)
        inserted = 0
        for r in rows:
            ts_s = int(r["fundingTime"]) // 1000
            if ts_s == last_s:
                continue
            rate = float(r["fundingRate"])
            con.execute(
                "INSERT OR REPLACE INTO cd_funding_rate "
                "(timestamp, fr_open, fr_high, fr_low, fr_close) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts_s, rate, rate, rate, rate),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def refresh_all() -> dict[str, int]:
    results: dict[str, int] = {}
    for sym, table in [("BTCUSDT", "btc_1m"), ("ETHUSDT", "eth_1m")]:
        try:
            n = fetch_spot_klines_1m(sym, table)
            results[table] = n
        except Exception as e:
            log.warning(f"{table} fetch failed: {e}")
            results[table] = -1
    try:
        results["cd_futures_ohlcv"] = fetch_futures_klines_1h()
    except Exception as e:
        log.warning(f"cd_futures_ohlcv fetch failed: {e}")
        results["cd_futures_ohlcv"] = -1
    try:
        results["cd_funding_rate"] = fetch_funding_rate()
    except Exception as e:
        log.warning(f"cd_funding_rate fetch failed: {e}")
        results["cd_funding_rate"] = -1
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Binance feed updater for trader.db")
    ap.add_argument("--once", action="store_true",
                    help="Fetch once and exit (default: loop every 60s)")
    ap.add_argument("--interval", type=int, default=60,
                    help="Loop interval seconds (default 60)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if not DB_PATH.exists():
        log.error(f"{DB_PATH} not found — run `python seed_data.py` first")
        return 1

    while True:
        r = refresh_all()
        summary = ", ".join(f"{k}:{v}" for k, v in r.items())
        log.info(f"feed tick — {summary}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
