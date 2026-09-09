"""OKX + Bybit perpetual funding-rate history (public APIs, no key, stdlib only).

Track D6 (2026-09-08) — the cross-venue leg of the delta-neutral
funding-dispersion study. p300 already stores Binance funding
(`cd_funding_rate` BTC, `cd_funding_rate_eth` ETH); dispersion needs at least
two more venues on the *same settlement grid* so the three can be joined
timestamp-to-timestamp.

Tables (explicit PKs, classified in botlib.py in the same change):
  okx_funding   (inst_id, timestamp)  funding_rate, realized_rate
  bybit_funding (symbol,  timestamp)  funding_rate

`timestamp` is epoch SECONDS at the settlement instant (00:00 / 08:00 / 16:00
UTC), exactly the convention `cd_funding_rate` uses after the 2026-04-13
cadence change (1h CoinDesk *predicted* rates -> 8h Binance *settlements*).
Joining on the settlement timestamp is the whole point: pre-cutover Binance
rows are hourly predictions and simply have no counterpart on these venues,
so any dispersion study must inner-join on the 8h grid and start from the
cutover (or use only the post-cutover slice of `cd_funding_rate`).

Sources
  OKX   https://www.okx.com/api/v5/public/funding-rate-history
        ?instId=BTC-USDT-SWAP&limit=100[&after=<ms>]
        -> {"code":"0","data":[{"fundingRate":..,"realizedRate":..,
                                "fundingTime":"<ms>",...}]}
        `after=<ms>` pages to rows OLDER than that fundingTime. Newest first.
  Bybit https://api.bybit.com/v5/market/funding/history
        ?category=linear&symbol=BTCUSDT&limit=200[&endTime=<ms>]
        -> {"retCode":0,"result":{"list":[{"symbol":..,"fundingRate":..,
                                           "fundingRateTimestamp":"<ms>"}]}}
        `endTime=<ms>` pages to rows at/older than that instant. Newest first.

History actually served (measured 2026-09-08, not a doc claim):
  OKX   BTC-USDT-SWAP  278 rows back to 2026-06-08 08:00 UTC  (~92 days)
  OKX   ETH-USDT-SWAP  278 rows back to 2026-06-08 08:00 UTC  (~92 days)
  Bybit BTCUSDT       7075 rows back to 2020-03-25 16:00 UTC
  Bybit ETHUSDT       6446 rows back to 2020-10-21 08:00 UTC
Re-measured 2026-09-09 (paging the OKX endpoint to exhaustion): 280 rows in
4 pages per instrument, still 2026-06-08 08:00 -> latest = 93.0 days. The OKX
window therefore moves forward with the newest settlement but keeps the same
floor day to day, so stored rows age out of the API and only survive because
they are already in the table.
OKX serves a rolling ~3-month window and nothing older, from this or any
other public endpoint, and there is no OKX/Bybit funding in the predecessor
database to seed from (it has Binance only). So the OKX series grows forward
one settlement at a time from 2026-06-08: any study that needs all three
venues is capped at that start date until the live feed accumulates more.
That is a hard sample-length bound, not a fetcher bug.

Cadence: `refresh()` runs every feed cycle and throttles itself to one pull
per (item, UTC hour) — settlements are 8-hourly, and an hourly poll bounds
detection lag at 1h, which is what the 10h freshness contract is sized for.
Each pull re-requests a trailing 24-settlement (~8 day) window with INSERT OR
REPLACE, so a short outage self-heals without a backfill.

CLI:
  python data/sources/venue_funding.py --refresh
  python data/sources/venue_funding.py --backfill              # as deep as each API goes
  python data/sources/venue_funding.py --backfill --since 2026-01-01
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

log = logging.getLogger("venue_funding")

OKX_TABLE = "okx_funding"
BYBIT_TABLE = "bybit_funding"
OKX_API = "https://www.okx.com/api/v5/public/funding-rate-history?{query}"
BYBIT_API = "https://api.bybit.com/v5/market/funding/history?{query}"
OKX_INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
BYBIT_SYMBOLS = ("BTCUSDT", "ETHUSDT")

SETTLEMENT_S = 28800              # 8h — the grid both venues (and Binance) settle on
OKX_MAX_LIMIT = 100               # hard API cap
BYBIT_MAX_LIMIT = 200             # hard API cap
REFRESH_LIMIT = 24                # trailing ~8 days per poll: cheap, self-healing
REQUEST_GAP_S = 0.25
MAX_BACKFILL_PAGES = 400          # ~40k OKX rows / ~80k Bybit rows: far past exhaustion

DDL = [
    f"""CREATE TABLE IF NOT EXISTS {OKX_TABLE} (
        inst_id       TEXT NOT NULL,
        timestamp     INTEGER NOT NULL,
        funding_rate  REAL,
        realized_rate REAL,
        PRIMARY KEY (inst_id, timestamp)
    )""",
    f"""CREATE TABLE IF NOT EXISTS {BYBIT_TABLE} (
        symbol       TEXT NOT NULL,
        timestamp    INTEGER NOT NULL,
        funding_rate REAL,
        PRIMARY KEY (symbol, timestamp)
    )""",
]

# Per-process throttle state: "<venue>:<item>:<YYYY-MM-DD>:<hour>" already pulled.
_done: set[str] = set()


def _http_get(url: str) -> dict:
    """Three attempts — both venues 5xx/429 under load."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "p300-feed/1.0"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError("venue_funding fetch failed")


def ensure_schema(con: sqlite3.Connection) -> None:
    for ddl in DDL:
        con.execute(ddl)
    con.commit()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _num(v) -> float | None:
    """Venue rates arrive as strings; '' means 'not published for this row'."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── OKX ──────────────────────────────────────────────────────────────────────

def fetch_okx(inst_id: str, after_ms: int | None = None, limit: int = OKX_MAX_LIMIT,
              http_get: Callable[[str], dict] = _http_get) -> list[tuple]:
    """One request -> [(timestamp_s, funding_rate, realized_rate)] ascending.

    `after_ms` asks OKX for settlements strictly older than that instant.
    Rows without a usable fundingTime/fundingRate are dropped rather than
    written as NULL-keyed garbage."""
    q = {"instId": inst_id, "limit": min(limit, OKX_MAX_LIMIT)}
    if after_ms is not None:
        q["after"] = int(after_ms)
    payload = http_get(OKX_API.format(query=urlencode(q)))
    if str(payload.get("code", "")) != "0":
        raise RuntimeError(f"okx funding {inst_id}: {payload.get('code')} "
                           f"{payload.get('msg')}")
    rows = []
    for r in payload.get("data") or []:
        ts_ms, rate = r.get("fundingTime"), _num(r.get("fundingRate"))
        if ts_ms is None or rate is None:
            continue
        try:
            ts = int(ts_ms) // 1000
        except (TypeError, ValueError):
            continue
        rows.append((ts, rate, _num(r.get("realizedRate"))))
    rows.sort()
    return rows


def store_okx(con: sqlite3.Connection, inst_id: str, rows: list[tuple]) -> int:
    con.executemany(
        f"INSERT OR REPLACE INTO {OKX_TABLE} (inst_id, timestamp, funding_rate, realized_rate) "
        "VALUES (?, ?, ?, ?)", [(inst_id, *r) for r in rows])
    con.commit()
    return len(rows)


# ─── Bybit ────────────────────────────────────────────────────────────────────

def fetch_bybit(symbol: str, end_ms: int | None = None, start_ms: int | None = None,
                limit: int = BYBIT_MAX_LIMIT,
                http_get: Callable[[str], dict] = _http_get) -> list[tuple]:
    """One request -> [(timestamp_s, funding_rate)] ascending.

    `end_ms` walks backwards through history (Bybit returns newest first)."""
    q = {"category": "linear", "symbol": symbol, "limit": min(limit, BYBIT_MAX_LIMIT)}
    if end_ms is not None:
        q["endTime"] = int(end_ms)
    if start_ms is not None:
        q["startTime"] = int(start_ms)
    payload = http_get(BYBIT_API.format(query=urlencode(q)))
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"bybit funding {symbol}: {payload.get('retCode')} "
                           f"{payload.get('retMsg')}")
    rows = []
    for r in (payload.get("result") or {}).get("list") or []:
        ts_ms, rate = r.get("fundingRateTimestamp"), _num(r.get("fundingRate"))
        if ts_ms is None or rate is None:
            continue
        try:
            ts = int(ts_ms) // 1000
        except (TypeError, ValueError):
            continue
        rows.append((ts, rate))
    rows.sort()
    return rows


def store_bybit(con: sqlite3.Connection, symbol: str, rows: list[tuple]) -> int:
    con.executemany(
        f"INSERT OR REPLACE INTO {BYBIT_TABLE} (symbol, timestamp, funding_rate) "
        "VALUES (?, ?, ?)", [(symbol, *r) for r in rows])
    con.commit()
    return len(rows)


# ─── Refresh ──────────────────────────────────────────────────────────────────

def refresh_okx(inst_id: str, *, limit: int = REFRESH_LIMIT,
                http_get: Callable[[str], dict] = _http_get) -> int:
    rows = fetch_okx(inst_id, limit=limit, http_get=http_get)
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        return store_okx(con, inst_id, rows)
    finally:
        con.close()


def refresh_bybit(symbol: str, *, limit: int = REFRESH_LIMIT,
                  http_get: Callable[[str], dict] = _http_get) -> int:
    rows = fetch_bybit(symbol, limit=limit, http_get=http_get)
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        return store_bybit(con, symbol, rows)
    finally:
        con.close()


def refresh(*, now: datetime | None = None, force: bool = False,
            http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """Throttled entry point for the feed loop: at most one pull per item per
    UTC hour. Returns {"okx_<inst>"|"bybit_<symbol>": rows_upserted}, -1 for a
    failed item; keys are absent when nothing was due. A failure never marks
    the item done, so the next tick retries it, and one venue failing cannot
    stop the other."""
    now = now or clock.now_utc()
    if clock.is_simulated() and not force:
        return {}
    bucket = f"{now.date().isoformat()}:{now.hour}"
    out: dict[str, int] = {}
    items: list[tuple[str, str, Callable[..., int]]] = (
        [("okx", i, refresh_okx) for i in OKX_INSTRUMENTS]
        + [("bybit", s, refresh_bybit) for s in BYBIT_SYMBOLS])
    for venue, item, fn in items:
        key = f"{venue}:{item}:{bucket}"
        if not force and key in _done:
            continue
        try:
            out[f"{venue}_{item}"] = fn(item, http_get=http_get)
            _done.add(key)
        except Exception as e:  # noqa: BLE001 — one item must not kill the rest
            log.warning(f"{venue}_funding {item} refresh failed: {e}")
            out[f"{venue}_{item}"] = -1
        time.sleep(REQUEST_GAP_S)
    return out


# ─── Backfill (CLI only — never called by refresh) ────────────────────────────

def backfill(since: int | None = None,
             http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """Page backwards through both venues until the API stops serving rows (or
    `since`, epoch seconds, is reached). OKX exhausts after ~3 pages; Bybit
    after ~35. Per-item try/except: a venue outage costs that item only."""
    out: dict[str, int] = {}
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        for inst in OKX_INSTRUMENTS:
            total, cursor_ms, pages = 0, None, 0
            try:
                while pages < MAX_BACKFILL_PAGES:
                    rows = fetch_okx(inst, after_ms=cursor_ms, http_get=http_get)
                    pages += 1
                    if not rows:
                        break
                    oldest = rows[0][0]           # rows are ascending
                    if since is not None:
                        rows = [r for r in rows if r[0] >= since]
                    total += store_okx(con, inst, rows)
                    if since is not None and oldest <= since:
                        break
                    cursor_ms = oldest * 1000
                    time.sleep(REQUEST_GAP_S)
                out[f"okx_{inst}"] = total
            except Exception as e:  # noqa: BLE001
                log.warning(f"okx_funding {inst} backfill failed after {total} rows: {e}")
                out[f"okx_{inst}"] = -1
        for sym in BYBIT_SYMBOLS:
            total, end_ms, pages = 0, None, 0
            try:
                while pages < MAX_BACKFILL_PAGES:
                    rows = fetch_bybit(sym, end_ms=end_ms, http_get=http_get)
                    pages += 1
                    if not rows:
                        break
                    oldest = rows[0][0]
                    if since is not None:
                        rows = [r for r in rows if r[0] >= since]
                    total += store_bybit(con, sym, rows)
                    if since is not None and oldest <= since:
                        break
                    end_ms = oldest * 1000 - 1
                    time.sleep(REQUEST_GAP_S)
                out[f"bybit_{sym}"] = total
            except Exception as e:  # noqa: BLE001
                log.warning(f"bybit_funding {sym} backfill failed after {total} rows: {e}")
                out[f"bybit_{sym}"] = -1
    finally:
        con.close()
    return out


# ─── Introspection ────────────────────────────────────────────────────────────

def latest(venue: str, item: str) -> tuple[int, float] | None:
    """(timestamp, funding_rate) of the newest stored settlement, or None."""
    table, col = ((OKX_TABLE, "inst_id") if venue == "okx" else (BYBIT_TABLE, "symbol"))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        row = con.execute(
            f"SELECT timestamp, funding_rate FROM {table} WHERE {col}=? "
            "ORDER BY timestamp DESC LIMIT 1", (item,)).fetchone()
        return (int(row[0]), float(row[1])) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def span(venue: str, item: str) -> tuple[int, int, int] | None:
    """(rows, first_ts, last_ts) for one instrument, or None if absent."""
    table, col = ((OKX_TABLE, "inst_id") if venue == "okx" else (BYBIT_TABLE, "symbol"))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        n, lo, hi = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table} "
            f"WHERE {col}=?", (item,)).fetchone()
        return (int(n), int(lo), int(hi)) if n else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OKX + Bybit funding-rate history feed")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="page back as far as each public API serves")
    ap.add_argument("--since", default=None,
                    help="backfill floor, YYYY-MM-DD or epoch seconds")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill:
        since = None
        if args.since:
            since = (int(args.since) if args.since.isdigit() else
                     int(datetime.strptime(args.since, "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc).timestamp()))
        print("backfill:", backfill(since))
    if args.refresh:
        print("refresh:", refresh())
    for venue, items in (("okx", OKX_INSTRUMENTS), ("bybit", BYBIT_SYMBOLS)):
        for item in items:
            s = span(venue, item)
            if s:
                print(f"  {venue:5s} {item:14s} {s[0]:>6,} rows  "
                      f"{_iso(s[1])} -> {_iso(s[2])}")
            else:
                print(f"  {venue:5s} {item:14s} (empty)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
