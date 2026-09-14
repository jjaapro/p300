"""Coinbase Exchange hourly spot candles (public API, no key, stdlib only).

Feeds `coinbase_spot_1h(asset, timestamp, open, high, low, close, volume)`
with PRIMARY KEY (asset, timestamp) — timestamps are epoch SECONDS at the top
of each hour, the same convention as `cd_spot_binance`. Track D4 (2026-09-08),
the Coinbase-premium study's US-venue leg: Coinbase-vs-Binance spot basis is
the classic US-demand proxy, and it needs a venue Binance cannot provide.

Source: https://api.exchange.coinbase.com/products/<PRODUCT>/candles
        ?granularity=3600&start=<iso>&end=<iso>
which returns bare arrays of [time, low, high, open, close, volume] (note the
low/high before open/close), newest first, at most 300 candles per request,
and requires a User-Agent header.

History: the predecessor repo's `coinbase_btc_hourly` / `coinbase_eth_hourly`
(2020-01-01 → 2026-04-14, 55,070 rows each) are copied in once with
`--seed-from`; `--backfill` then walks the live API forward from the last
stored bar so the series is continuous. Seeding uses INSERT OR IGNORE and the
live path INSERT OR REPLACE, so live rows always win over seed rows without
the table needing a `source` column.

Cadence: `refresh()` is called every feed cycle and throttles itself to one
pull per (asset, UTC hour). Each pull starts at the last stored bar or 6h
back, whichever is earlier, so the bar that was still forming last hour is
corrected on the next tick and an outage of any length fills itself in.

CLI:
  python data/sources/coinbase.py --refresh
  python data/sources/coinbase.py --seed-from C:/Source/Repos/trader/data/trader.db
  python data/sources/coinbase.py --backfill              # gap -> now
  python data/sources/coinbase.py --backfill --since 2026-04-14
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import clock, db as _db  # noqa: E402

log = logging.getLogger("coinbase")

TABLE = "coinbase_spot_1h"
API = "https://api.exchange.coinbase.com/products/{product}/candles?{query}"
PRODUCTS: dict[str, str] = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
GRANULARITY = 3600
MAX_CANDLES = 300                 # hard Coinbase limit per request
REQUEST_GAP_S = 0.35              # public rate limit is ~10 req/s; stay well under
REFRESH_WINDOW_HOURS = 6          # trailing re-pull: fixes the last partial bar
# The predecessor snapshot ends here (2020-01-01 → 2026-04-14 00:00 UTC), so a
# bare --backfill on an unseeded table starts from the beginning of Coinbase's
# BTC-USD history instead.
SEED_START_TS = 1577836800        # 2020-01-01 00:00 UTC
DEFAULT_BACKFILL_START = SEED_START_TS
# Predecessor tables, one per asset (timestamp INTEGER PK, open..volume).
SEED_TABLES: dict[str, str] = {"BTC": "coinbase_btc_hourly",
                               "ETH": "coinbase_eth_hourly"}

DDL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        asset     TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY (asset, timestamp)
    )
"""

# Per-process throttle state: {asset}:{YYYY-MM-DD}:{hour} already pulled.
_done: set[str] = set()


def _http_get(url: str) -> list:
    """Three attempts — Coinbase 429s and 5xxs under load."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "p300-feed/1.0"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError("coinbase fetch failed")


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(DDL)
    con.commit()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_candles(asset: str, start_ts: int, end_ts: int,
                  http_get: Callable[[str], list] = _http_get) -> list[tuple]:
    """One request → [(timestamp, open, high, low, close, volume)] ascending.

    Coinbase orders the payload newest-first and packs it as
    [time, low, high, open, close, volume]; rows that are not on an exact hour
    boundary (or are malformed) are dropped rather than trusted."""
    url = API.format(product=PRODUCTS[asset], query=urlencode({
        "granularity": GRANULARITY, "start": _iso(start_ts), "end": _iso(end_ts)}))
    payload = http_get(url)
    if not isinstance(payload, list):          # error bodies come back as {"message": ...}
        raise RuntimeError(f"coinbase {asset}: {payload}")
    rows = []
    for c in payload:
        if not isinstance(c, (list, tuple)) or len(c) < 6:
            continue
        t, low, high, opn, close, vol = c[:6]
        t = int(t)
        if t % GRANULARITY:
            continue
        rows.append((t, opn, high, low, close, vol))
    rows.sort()
    return rows


def store(con: sqlite3.Connection, asset: str, rows: list[tuple]) -> int:
    """INSERT OR REPLACE — the live API is authoritative over seed rows and
    over its own earlier partial bar."""
    con.executemany(
        f"INSERT OR REPLACE INTO {TABLE} (asset, timestamp, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", [(asset, *r) for r in rows])
    con.commit()
    return len(rows)


def _pull(con: sqlite3.Connection, asset: str, start_ts: int, end_ts: int,
          http_get: Callable[[str], list]) -> int:
    """Walk [start_ts, end_ts] forward in 300-candle pages, storing each page
    as it arrives — a failure part-way keeps the pages already written."""
    total = 0
    cursor = start_ts
    while cursor <= end_ts:
        stop = min(cursor + (MAX_CANDLES - 1) * GRANULARITY, end_ts)
        total += store(con, asset, fetch_candles(asset, cursor, stop, http_get))
        cursor = stop + GRANULARITY
        time.sleep(REQUEST_GAP_S)
    return total


def refresh_asset(asset: str, *, now: datetime | None = None,
                  hours: int = REFRESH_WINDOW_HOURS,
                  http_get: Callable[[str], list] = _http_get) -> int:
    """Pull one asset from its last stored bar (or `hours` back, whichever is
    earlier) to now and upsert it.

    Starting at the last stored bar is what lets an outage heal: with only a
    trailing window, a feed down longer than `hours` restarted past the
    missing bars and never asked for them again (the 2026-09-08 hole). An
    empty table gets the trailing window only — full history is the CLI's
    job, not the feed loop's."""
    now = now or clock.now_utc()
    end_ts = int(now.timestamp())
    start_ts = end_ts - hours * GRANULARITY
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        last = _last_ts(con, asset)
        if last is not None:
            start_ts = min(start_ts, last + GRANULARITY)
        return _pull(con, asset, start_ts, end_ts, http_get)
    finally:
        con.close()


def refresh(*, now: datetime | None = None, force: bool = False,
            http_get: Callable[[str], list] = _http_get) -> dict[str, int]:
    """Throttled entry point for the feed loop: at most one pull per asset per
    UTC hour. Returns {asset: rows_upserted}, -1 for a failed asset; keys are
    absent when nothing was due."""
    now = now or clock.now_utc()
    if clock.is_simulated() and not force:
        return {}
    bucket = f"{now.date().isoformat()}:{now.hour}"
    out: dict[str, int] = {}
    for asset in PRODUCTS:
        key = f"{asset}:{bucket}"
        if not force and key in _done:
            continue
        try:
            out[asset] = refresh_asset(asset, now=now, http_get=http_get)
            _done.add(key)
        except Exception as e:  # noqa: BLE001 — one asset must not kill the other
            log.warning(f"{TABLE} {asset} refresh failed: {e}")
            out[asset] = -1
        time.sleep(REQUEST_GAP_S)
    return out


def _last_ts(con: sqlite3.Connection, asset: str) -> int | None:
    try:
        row = con.execute(f"SELECT MAX(timestamp) FROM {TABLE} WHERE asset=?",
                          (asset,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row and row[0] is not None else None


def backfill(since: int | None = None, *, now: datetime | None = None,
             http_get: Callable[[str], list] = _http_get) -> dict[str, int]:
    """CLI only — never called by refresh(). Walk forward in 300-candle pages
    from `since` (default: the asset's last stored bar, else 2020-01-01) to
    now, so a freshly seeded table becomes continuous to the present."""
    now = now or clock.now_utc()
    end_all = int(now.timestamp())
    out: dict[str, int] = {}
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        for asset in PRODUCTS:
            start = since if since is not None else (
                (_last_ts(con, asset) or DEFAULT_BACKFILL_START - GRANULARITY) + GRANULARITY)
            try:
                out[asset] = _pull(con, asset, start, end_all, http_get)
            except Exception as e:  # noqa: BLE001 — one asset must not kill the other
                log.warning(f"{TABLE} {asset} backfill failed (pages already "
                            f"stored are kept): {e}")
                out[asset] = -1
    finally:
        con.close()
    return out


def seed_from(source_db: Path) -> dict[str, int]:
    """Copy the predecessor per-asset hourly tables into the unified table
    (INSERT OR IGNORE — anything the live API already wrote wins)."""
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    con = sqlite3.connect(str(_db.PROD_DB))
    out: dict[str, int] = {}
    try:
        ensure_schema(con)
        for asset, table in SEED_TABLES.items():
            try:
                rows = src.execute(
                    f"SELECT timestamp, open, high, low, close, volume FROM {table}").fetchall()
            except sqlite3.OperationalError as e:
                log.warning(f"seed {table} unavailable: {e}")
                out[asset] = -1
                continue
            con.executemany(
                f"INSERT OR IGNORE INTO {TABLE} (asset, timestamp, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", [(asset, *r) for r in rows])
            con.commit()
            out[asset] = len(rows)
    finally:
        src.close()
        con.close()
    return out


def latest(asset: str) -> tuple[int, float] | None:
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        row = con.execute(
            f"SELECT timestamp, close FROM {TABLE} WHERE asset=? "
            "ORDER BY timestamp DESC LIMIT 1", (asset,)).fetchone()
        return (int(row[0]), float(row[1])) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Coinbase Exchange hourly spot feed")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="walk forward from the last stored bar to now")
    ap.add_argument("--since", default=None,
                    help="backfill start, YYYY-MM-DD or epoch seconds")
    ap.add_argument("--seed-from", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.seed_from:
        print("seeded:", seed_from(args.seed_from))
    if args.backfill:
        since = None
        if args.since:
            since = (int(args.since) if args.since.isdigit() else
                     int(datetime.strptime(args.since, "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc).timestamp()))
        print("backfill:", backfill(since))
    if args.refresh:
        print("refresh:", refresh())
    for a in PRODUCTS:
        row = latest(a)
        stamp = _iso(row[0]) if row else "-"
        print(f"  {a:4s} latest {row} {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
