"""Ingest OKX BTC-USDT-SWAP perp 1H klines via the public REST endpoint
`/api/v5/market/history-candles`. Walks backwards from `--end` using the
`after` cursor.

Table:
    okx_perp_1h(
        timestamp INTEGER PRIMARY KEY,    -- unix seconds (bar open)
        open, high, low, close            REAL,
        volume_contracts                  REAL,    -- contracts traded
        volume_btc                        REAL,    -- BTC traded
        volume_usd                        REAL     -- quote-currency turnover
    )

Idempotent via INSERT OR REPLACE. Rate limit: 20 req/2s. We sleep
`--sleep` (default 0.15s) between calls — well under the limit.

Usage:
    python data/sources/okx_perp.py --start 2021-01-01 --end 2026-05-25
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import date, datetime, UTC
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
BASE_URL = "https://www.okx.com/api/v5/market/history-candles"
CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
DEFAULT_INSTRUMENT = "BTC-USDT-SWAP"

log = logging.getLogger("p300.okx_perp")


_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    timestamp           INTEGER PRIMARY KEY,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    volume_contracts    REAL,
    volume_base         REAL,
    volume_usd          REAL
);
CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);
"""


def ensure_schema(con: sqlite3.Connection, table: str = "okx_perp_1h") -> None:
    con.executescript(_SCHEMA_TEMPLATE.format(table=table))
    con.commit()


def fetch_chunk(inst: str, after_ms: int | None, limit: int = 100,
                  timeout: float = 30.0) -> list[list[str]]:
    """Fetch up to `limit` 1H bars OLDER than `after_ms`. If after_ms is None
    fetches the most recent `limit` bars."""
    params = {'instId': inst, 'bar': '1H', 'limit': str(limit)}
    if after_ms is not None:
        params['after'] = str(after_ms)
    r = requests.get(BASE_URL, params=params, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if body.get('code') != '0':
        raise RuntimeError(f"OKX error: {body.get('msg')} (code {body.get('code')})")
    return body.get('data', [])


def backfill(inst: str, start: date, end: date, *,
              sleep_between: float = 0.15,
              table: str = "okx_perp_1h") -> int:
    """Walk OKX history-candles backwards from `end` to `start`, writing to
    `table`. OKX returns newest-first; we paginate using `after` set to the
    OLDEST timestamp from the previous chunk."""
    start_ms = int(datetime.combine(start, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    end_ms = int(datetime.combine(end, datetime.min.time(), tzinfo=UTC).timestamp() * 1000) + 86_400_000

    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con, table)
        after_ms = end_ms + 3_600_000
        total_written = 0
        empty_streak = 0
        last_oldest = None
        while True:
            try:
                chunk = fetch_chunk(inst, after_ms=after_ms, limit=100)
            except Exception as e:
                log.error(f"  fetch failed at after_ms={after_ms}: {e}")
                time.sleep(2.0)
                continue
            if not chunk:
                empty_streak += 1
                if empty_streak >= 3:
                    log.info("  3 empty chunks in a row, stopping")
                    break
                time.sleep(2.0)
                continue
            empty_streak = 0
            rows = []
            for r in chunk:
                ts_ms = int(r[0])
                ts_sec = ts_ms // 1000
                if ts_ms < start_ms:
                    continue
                rows.append((
                    ts_sec, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                    float(r[5]), float(r[6]), float(r[7]),
                ))
            if rows:
                con.executemany(
                    f"INSERT OR REPLACE INTO {table} "
                    f"(timestamp, open, high, low, close, volume_contracts, "
                    f"volume_base, volume_usd) VALUES (?,?,?,?,?,?,?,?)", rows)
                con.commit()
                total_written += len(rows)
            oldest_ms = int(chunk[-1][0])
            if oldest_ms <= start_ms:
                log.info(f"  [{table}] reached start window (oldest={datetime.fromtimestamp(oldest_ms/1000, UTC).date()})")
                break
            if last_oldest == oldest_ms:
                log.info(f"  [{table}] cursor not advancing, stopping")
                break
            last_oldest = oldest_ms
            log.info(f"  [{table}] fetched {len(chunk):>3d} (last={datetime.fromtimestamp(oldest_ms/1000, UTC).isoformat()[:16]}), "
                      f"written so far {total_written:,}")
            after_ms = oldest_ms
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


def refresh_latest(inst: str = DEFAULT_INSTRUMENT, *,
                    table: str = "okx_perp_1h",
                    timeout: float = 30.0) -> int:
    """Incremental live refresh: pull the most recent 1H bars from the
    regular `/market/candles` endpoint (serves the live edge that
    `history-candles` lags behind) and upsert everything at or after the
    local MAX(timestamp).

    Covers outages up to ~300 hours (~12.5 days, the endpoint's page
    depth). Longer holes need :func:`backfill`; the freshness monitor is
    what surfaces those. Called from ``binance.refresh_all()`` on an
    hourly throttle so the table always has a live writer (2026-07-21
    lesson: manual-backfill-only left it stale and silently gate-locked
    CHENTO_TRIPLE_V3's OKX filter).
    """
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con, table)
        row = con.execute(f"SELECT MAX(timestamp) FROM {table}").fetchone()
        local_max_s = int(row[0]) if row and row[0] is not None else 0

        params = {"instId": inst, "bar": "1H", "limit": "300"}
        r = requests.get(CANDLES_URL, params=params, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != "0":
            raise RuntimeError(
                f"OKX error: {body.get('msg')} (code {body.get('code')})")
        chunk = body.get("data", [])
        rows = []
        for c in chunk:  # newest-first; confirmed bars only (c[8] == "1")
            if len(c) > 8 and c[8] != "1":
                continue
            ts_sec = int(c[0]) // 1000
            if ts_sec < local_max_s:
                continue
            rows.append((ts_sec, float(c[1]), float(c[2]), float(c[3]),
                         float(c[4]), float(c[5]), float(c[6]), float(c[7])))
        if rows:
            con.executemany(
                f"INSERT OR REPLACE INTO {table} "
                f"(timestamp, open, high, low, close, volume_contracts, "
                f"volume_base, volume_usd) VALUES (?,?,?,?,?,?,?,?)", rows)
            con.commit()
        if chunk and local_max_s:
            oldest_fetched_s = int(chunk[-1][0]) // 1000
            if oldest_fetched_s - local_max_s > 3600:
                log.warning(
                    f"[{table}] hole beyond refresh window: local max "
                    f"{datetime.fromtimestamp(local_max_s, UTC).isoformat()[:16]} "
                    f"< oldest fetched "
                    f"{datetime.fromtimestamp(oldest_fetched_s, UTC).isoformat()[:16]}"
                    f" — run the backfill CLI to heal")
        return len(rows)
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inst", default=DEFAULT_INSTRUMENT)
    p.add_argument("--table", default="okx_perp_1h",
                    help="Destination table (default okx_perp_1h)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--sleep", type=float, default=0.15)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(message)s")
    backfill(args.inst,
              date.fromisoformat(args.start),
              date.fromisoformat(args.end),
              sleep_between=args.sleep,
              table=args.table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
