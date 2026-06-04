"""Backfill ETH and OP perp data needed for multi-asset cross-validation
of the optimized Triple composite stack:
  - Binance perp 15m (with taker buy/sell split) -> cd_futures_eth_15m,
    cd_futures_op_15m
  - OKX -USDT-SWAP 1h -> okx_perp_eth_1h, okx_perp_op_1h
  - Bybit linear-perp 1h -> bybit_perp_eth_1h, bybit_perp_op_1h
  - ca_long_short_ratio for OP (already has BTC + ETH; needs OP)

Also migrates the existing BTC OKX/Bybit tables to rename `volume_btc` to
the more generic `volume_base` column for consistency.

Usage:
    python pipelines/backfill_eth_op.py
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.support import db as _db   # noqa: E402

DB_PATH = _db.PROD_DB

log = logging.getLogger("p300.backfill_eth_op")


# -------------------- Step 0: migrate BTC tables ---------------------------

def migrate_btc_perp_tables() -> None:
    """Rename `volume_btc` -> `volume_base` on okx_perp_1h and bybit_perp_1h
    so they share a column name with the new per-asset tables."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        for table in ("okx_perp_1h", "bybit_perp_1h"):
            cols = [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]
            if "volume_btc" in cols and "volume_base" not in cols:
                log.info(f"  renaming {table}.volume_btc -> volume_base")
                con.execute(f"ALTER TABLE {table} RENAME COLUMN volume_btc TO volume_base")
                con.commit()
            elif "volume_base" in cols:
                log.info(f"  {table}.volume_base already present")
            else:
                log.info(f"  {table}: no volume_btc or volume_base column found, skipping")
    finally:
        con.close()


# -------------------- Step 1: Binance perp 15m for ETH + OP ---------------

def _ensure_futures_15m_table(con: sqlite3.Connection, table: str) -> None:
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            timestamp           INTEGER PRIMARY KEY,
            open                REAL,
            high                REAL,
            low                 REAL,
            close               REAL,
            volume              REAL,
            quote_volume        REAL,
            volume_buy          REAL,
            quote_volume_buy    REAL,
            volume_sell         REAL,
            quote_volume_sell   REAL,
            total_trades        INTEGER,
            trades_buy          INTEGER,
            trades_sell         INTEGER
        );
        CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);
    """)
    con.commit()


def backfill_binance_perp_15m(symbol: str, table: str,
                                start: date, end: date,
                                sleep_between: float = 0.20) -> int:
    """Backfill 15m perp klines from Binance fapi into `table` with full
    taker-split schema (matching cd_futures_15m)."""
    import requests
    api_base = "https://fapi.binance.com/fapi/v1"
    cadence_s = 900
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000

    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_futures_15m_table(con, table)
        cursor_ms = start_ms
        total = 0
        log.info(f"  [{table}] backfill {symbol} 15m {start} -> {end}")
        while cursor_ms < end_ms:
            params = {
                "symbol": symbol, "interval": "15m",
                "startTime": cursor_ms, "endTime": end_ms,
                "limit": 1000,
            }
            try:
                r = requests.get(f"{api_base}/klines", params=params, timeout=30)
                r.raise_for_status()
                rows = r.json()
            except Exception as e:
                log.warning(f"  [{table}] fetch fail at cursor={cursor_ms}: {e}")
                time.sleep(2.0)
                continue
            if not rows:
                break
            con.executemany(
                f"INSERT OR REPLACE INTO {table} "
                f"(timestamp, open, high, low, close, volume, quote_volume, "
                f" volume_buy, quote_volume_buy, volume_sell, quote_volume_sell, "
                f" total_trades, trades_buy, trades_sell) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                [(int(r0[0]) // 1000, float(r0[1]), float(r0[2]), float(r0[3]),
                  float(r0[4]), float(r0[5]), float(r0[7]),
                  float(r0[9]), float(r0[10]),
                  float(r0[5]) - float(r0[9]), float(r0[7]) - float(r0[10]),
                  int(r0[8]))
                 for r0 in rows],
            )
            con.commit()
            total += len(rows)
            last_ms = int(rows[-1][0])
            cursor_ms = last_ms + cadence_s * 1000
            if total % 10_000 < 1000:
                log.info(f"  [{table}] {total:,} rows, "
                          f"now at {datetime.fromtimestamp(last_ms/1000, timezone.utc).isoformat()[:16]}")
            if len(rows) < 1000:
                break
            time.sleep(sleep_between)

        span = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}"
        ).fetchone()
        if span[1]:
            log.info(f"  [{table}] final: {span[0]:,} bars  "
                      f"{datetime.fromtimestamp(span[1], timezone.utc).date()} -> "
                      f"{datetime.fromtimestamp(span[2], timezone.utc).date()}")
    finally:
        con.close()
    return total


# -------------------- Step 2: ca_long_short_ratio for OP ------------------

def backfill_op_long_short_ratio(start: date, end: date) -> int:
    """Backfill globalLongShortAccountRatio for OPUSDT into ca_long_short_ratio
    with asset='OP'."""
    sys.path.insert(0, str(ROOT))
    from data.sources.binance import backfill_long_short_ratio
    # The binance.py module has a helper; check if it exists
    try:
        return backfill_long_short_ratio(
            symbol="OPUSDT", asset="OP",
            start=start.isoformat(), end=end.isoformat())
    except (ImportError, AttributeError):
        # Inline implementation
        import requests
        log.info(f"  backfill ca_long_short_ratio OP {start} -> {end} (inline)")
        api_base = "https://fapi.binance.com/futures/data"
        start_ms = int(datetime.combine(start, datetime.min.time(),
                                         tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(datetime.combine(end, datetime.min.time(),
                                       tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000
        con = sqlite3.connect(str(DB_PATH))
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS ca_long_short_ratio (
                    asset TEXT, timestamp INTEGER, ratio REAL,
                    long_pct REAL, short_pct REAL,
                    PRIMARY KEY (asset, timestamp)
                );
            """)
            con.commit()
            cursor = start_ms
            total = 0
            while cursor < end_ms:
                params = {"symbol": "OPUSDT", "period": "4h",
                           "startTime": cursor, "endTime": end_ms, "limit": 500}
                r = requests.get(f"{api_base}/globalLongShortAccountRatio",
                                  params=params, timeout=30)
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    break
                con.executemany(
                    "INSERT OR REPLACE INTO ca_long_short_ratio "
                    "(asset, timestamp, ratio, long_pct, short_pct) VALUES (?,?,?,?,?)",
                    [("OP", int(r0["timestamp"]) // 1000, float(r0["longShortRatio"]),
                      float(r0["longAccount"]), float(r0["shortAccount"]))
                     for r0 in rows],
                )
                con.commit()
                total += len(rows)
                last_ms = int(rows[-1]["timestamp"])
                if last_ms <= cursor:
                    break
                cursor = last_ms + 4 * 3600 * 1000
                if len(rows) < 500:
                    break
                time.sleep(0.2)
            log.info(f"  [ca_long_short_ratio] OP: {total:,} rows written")
            return total
        finally:
            con.close()


# -------------------- Step 3: OKX + Bybit per asset ------------------------

def backfill_okx(asset: str, inst: str, start: date, end: date) -> int:
    from data.sources.okx_perp import backfill as okx_backfill
    table = f"okx_perp_{asset.lower()}_1h"
    log.info(f"  backfill {table} ({inst}) {start} -> {end}")
    return okx_backfill(inst, start, end, table=table)


def backfill_bybit(asset: str, symbol: str, start: date, end: date) -> int:
    from data.sources.bybit_perp import backfill as bybit_backfill
    table = f"bybit_perp_{asset.lower()}_1h"
    log.info(f"  backfill {table} ({symbol}) {start} -> {end}")
    return bybit_backfill(symbol, start, end, table=table)


# -------------------- Main -------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2021-01-01")
    p.add_argument("--end", default="2026-05-25")
    p.add_argument("--skip-binance", action="store_true")
    p.add_argument("--skip-okx", action="store_true")
    p.add_argument("--skip-bybit", action="store_true")
    p.add_argument("--skip-op-lsr", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(message)s")
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    log.info("=== Step 0: migrate BTC OKX/Bybit tables ===")
    migrate_btc_perp_tables()

    if not args.skip_binance:
        log.info("\n=== Step 1: Binance perp 15m ETH + OP ===")
        backfill_binance_perp_15m("ETHUSDT", "cd_futures_eth_15m", start, end)
        # OP perp launched ~2022-08
        op_start = max(start, date(2022, 8, 1))
        backfill_binance_perp_15m("OPUSDT", "cd_futures_op_15m", op_start, end)

    if not args.skip_op_lsr:
        log.info("\n=== Step 2: ca_long_short_ratio OP ===")
        try:
            backfill_op_long_short_ratio(max(start, date(2022, 8, 1)), end)
        except Exception as e:
            log.warning(f"  OP LSR backfill failed: {e}")

    if not args.skip_okx:
        log.info("\n=== Step 3a: OKX ETH + OP 1h ===")
        backfill_okx("eth", "ETH-USDT-SWAP", start, end)
        backfill_okx("op",  "OP-USDT-SWAP",  max(start, date(2022, 8, 1)), end)

    if not args.skip_bybit:
        log.info("\n=== Step 3b: Bybit ETH + OP 1h ===")
        backfill_bybit("eth", "ETHUSDT", start, end)
        backfill_bybit("op",  "OPUSDT",  max(start, date(2022, 8, 1)), end)

    return 0


if __name__ == "__main__":
    sys.exit(main())
