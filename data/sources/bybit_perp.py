"""Ingest Bybit BTCUSDT linear-perp 1H klines via the public REST endpoint
`/v5/market/kline`. Walks forward in chunks of 1000 bars.

Table:
    bybit_perp_1h(
        timestamp INTEGER PRIMARY KEY,    -- unix seconds (bar open)
        open, high, low, close            REAL,
        volume_btc                        REAL,
        turnover_usd                      REAL
    )

Idempotent via INSERT OR REPLACE. Public endpoint, no auth, generous rate
limits (effective limit is ~10 req/s, well under their stated 120 RPS).

Usage:
    python data/sources/bybit_perp.py --start 2021-01-01 --end 2026-05-25
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, UTC, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
BASE_URL = "https://api.bybit.com/v5/market/kline"
DEFAULT_SYMBOL = "BTCUSDT"

log = logging.getLogger("p300.bybit_perp")


_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    timestamp           INTEGER PRIMARY KEY,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    volume_base         REAL,
    turnover_usd        REAL
);
CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);
"""


def ensure_schema(con: sqlite3.Connection, table: str = "bybit_perp_1h") -> None:
    con.executescript(_SCHEMA_TEMPLATE.format(table=table))
    con.commit()


def fetch_chunk(symbol: str, start_ms: int, end_ms: int, limit: int = 1000,
                  timeout: float = 30.0) -> list[list[str]]:
    """Fetch up to `limit` 1H bars in [start_ms, end_ms]. Bybit returns
    newest-first."""
    params = {
        'category': 'linear', 'symbol': symbol, 'interval': '60',
        'start': str(start_ms), 'end': str(end_ms), 'limit': str(limit),
    }
    r = requests.get(BASE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if body.get('retCode') != 0:
        raise RuntimeError(f"Bybit error: {body.get('retMsg')} (retCode {body.get('retCode')})")
    return body.get('result', {}).get('list', [])


def backfill(symbol: str, start: date, end: date, *,
              sleep_between: float = 0.10,
              table: str = "bybit_perp_1h") -> int:
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp() * 1000) + 86_400_000

    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con, table)
        chunk_ms = 1000 * 3600 * 1000
        cur_start = start_ms
        total_written = 0
        while cur_start < end_ms:
            cur_end = min(cur_start + chunk_ms - 1, end_ms)
            try:
                chunk = fetch_chunk(symbol, cur_start, cur_end, limit=1000)
            except Exception as e:
                log.error(f"  fetch failed at {cur_start}: {e}")
                time.sleep(2.0)
                continue
            if not chunk:
                log.info(f"  [{table}] empty chunk at {datetime.fromtimestamp(cur_start/1000, UTC).date()}, advancing")
                cur_start += chunk_ms
                time.sleep(sleep_between)
                continue
            rows = []
            for r in chunk:
                ts_ms = int(r[0])
                ts_sec = ts_ms // 1000
                rows.append((
                    ts_sec, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                    float(r[5]), float(r[6]),
                ))
            con.executemany(
                f"INSERT OR REPLACE INTO {table} "
                f"(timestamp, open, high, low, close, volume_base, turnover_usd) "
                f"VALUES (?,?,?,?,?,?,?)", rows)
            con.commit()
            total_written += len(rows)
            oldest_ms = min(int(r[0]) for r in chunk)
            newest_ms = max(int(r[0]) for r in chunk)
            log.info(f"  [{table}] fetched {len(chunk):>4d}  "
                      f"({datetime.fromtimestamp(oldest_ms/1000, UTC).isoformat()[:16]} -> "
                      f"{datetime.fromtimestamp(newest_ms/1000, UTC).isoformat()[:16]})  "
                      f"total {total_written:,}")
            cur_start = newest_ms + 3_600_000
            time.sleep(sleep_between)

        row = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}"
        ).fetchone()
        if row[0]:
            log.info(f"\n{table} total: {row[0]:,} bars  "
                      f"({datetime.fromtimestamp(row[1], UTC).isoformat()[:16]} -> "
                      f"{datetime.fromtimestamp(row[2], UTC).isoformat()[:16]})")
        return total_written
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default=DEFAULT_SYMBOL)
    p.add_argument("--table", default="bybit_perp_1h")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--sleep", type=float, default=0.10)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(message)s")
    backfill(args.symbol,
              date.fromisoformat(args.start),
              date.fromisoformat(args.end),
              sleep_between=args.sleep,
              table=args.table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
