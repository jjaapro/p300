"""Binance USDⓈ-M quarterly futures, hourly (public API, no key, stdlib only).

Track D5 (2026-09-08), the perp-versus-quarterly basis study's leg: the
annualised spread between the dated quarterly contract and the perp is the
cleanest read on leveraged-long demand that funding alone cannot give
(funding is a rate, basis is a term structure).

Tables
------
`binance_quarterly_1h(pair, contract_type, timestamp, open, high, low, close,
volume)` with PRIMARY KEY (pair, contract_type, timestamp) — epoch SECONDS at
the top of each hour, the same convention as `cd_futures_ohlcv`, so a basis
series is one join away.

Source is the CONTINUOUS-contract endpoint

    https://fapi.binance.com/fapi/v1/continuousKlines
        ?pair=BTCUSDT&contractType=CURRENT_QUARTER&interval=1h&limit=1500

which stitches each contract-type *slot* (CURRENT_QUARTER / NEXT_QUARTER)
across expiries. That is the right shape for a basis study: the slot always
holds "the front quarterly", so the series does not die every 90 days the way
a per-expiry symbol does. The cost is that the roll is invisible in the price
series itself — which contract a bar belongs to is answered by

`binance_quarterly_contracts(symbol)` with PRIMARY KEY (symbol), the listed
contracts from /fapi/v1/exchangeInfo (deliveryDate, onboardDate) plus
first/last-seen stamps. exchangeInfo only ever shows the CURRENTLY listed
contracts, so this table accumulates the roll calendar going forward; it does
not reconstruct expiries from before this feed existed. Delivery is 08:00 UTC
on the last Friday of the quarter, so historical rolls are recoverable by rule
if a study needs them.

`series` on the klines table is a VIRTUAL generated column, `pair ||
'-' || contract_type` (e.g. "BTCUSDT-CURRENT_QUARTER"). It exists for one
reason: data/check_gaps.py groups by a single column, and this table's natural
group is the (pair, contract_type) pair. Nothing writes it and it costs no
storage.

History: continuousKlines serves from the launch of USDⓈ-M quarterlies —
2021-02-03 (BTCUSDT/ETHUSDT CURRENT_QUARTER) and 2021-03-16 (NEXT_QUARTER).
No predecessor seed exists, so `--backfill` walks the live endpoint forward
from 2021-01-01 and stops where the data does.

Cadence: `refresh()` is called every feed cycle and throttles itself to one
pull per (pair, contract_type) per UTC hour, plus one exchangeInfo pull per
UTC day. Each pull re-requests a trailing window, so the bar that was still
forming last hour is corrected on the next tick.

CLI:
  python data/sources/binance_quarterly.py --refresh
  python data/sources/binance_quarterly.py --backfill              # gap -> now
  python data/sources/binance_quarterly.py --backfill --since 2021-01-01
  python data/sources/binance_quarterly.py --contracts             # force one pull
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
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import clock, db as _db  # noqa: E402

log = logging.getLogger("binance_quarterly")

TABLE = "binance_quarterly_1h"
CONTRACTS_TABLE = "binance_quarterly_contracts"
FAPI = "https://fapi.binance.com/fapi/v1"
KLINES_URL = FAPI + "/continuousKlines?{query}"
EXCHANGE_INFO_URL = FAPI + "/exchangeInfo"

PAIRS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")
CONTRACT_TYPES: tuple[str, ...] = ("CURRENT_QUARTER", "NEXT_QUARTER")
SERIES: tuple[tuple[str, str], ...] = tuple(
    (p, c) for p in PAIRS for c in CONTRACT_TYPES)

INTERVAL = "1h"
INTERVAL_S = 3600
MAX_LIMIT = 1500                  # hard Binance limit per continuousKlines call
REQUEST_GAP_S = 0.4               # limit=1500 costs weight 10; stay well under 2400/min
REFRESH_WINDOW_HOURS = 6          # trailing re-pull: fixes the last partial bar
# USDⓈ-M quarterlies listed 2021-02-03 (CURRENT_QUARTER) / 2021-03-16
# (NEXT_QUARTER); start before both and let the endpoint decide.
DEFAULT_BACKFILL_START = 1609459200        # 2021-01-01 00:00 UTC

DDL_KLINES = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        pair          TEXT NOT NULL,
        contract_type TEXT NOT NULL,
        timestamp     INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        series TEXT GENERATED ALWAYS AS (pair || '-' || contract_type) VIRTUAL,
        PRIMARY KEY (pair, contract_type, timestamp)
    )
"""
DDL_CONTRACTS = f"""
    CREATE TABLE IF NOT EXISTS {CONTRACTS_TABLE} (
        symbol        TEXT NOT NULL,
        pair          TEXT NOT NULL,
        contract_type TEXT NOT NULL,
        delivery_ts   INTEGER,
        onboard_ts    INTEGER,
        first_seen_ts INTEGER,
        last_seen_ts  INTEGER,
        PRIMARY KEY (symbol)
    )
"""

# Per-process throttle state: "{pair}-{ct}:{YYYY-MM-DD}:{hour}" and
# "contracts:{YYYY-MM-DD}" already pulled.
_done: set[str] = set()


def _http_get(url: str) -> Any:
    """Three attempts — Binance 418/429s and 5xxs under load."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "p300-feed/1.0"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError("binance continuousKlines fetch failed")


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(DDL_KLINES)
    con.execute(DDL_CONTRACTS)
    con.commit()


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# ─── Klines ───────────────────────────────────────────────────────────────────

def fetch_klines(pair: str, contract_type: str, *, start_ts: int | None = None,
                 end_ts: int | None = None, limit: int = MAX_LIMIT,
                 http_get: Callable[[str], Any] = _http_get) -> list[tuple]:
    """One request → [(timestamp, open, high, low, close, volume)] ascending.

    Binance packs a kline as [openTime_ms, o, h, l, c, volume, closeTime_ms,
    quoteVolume, trades, takerBuyBase, takerBuyQuote, ignore] with numbers as
    strings. Rows that are not on an exact hour boundary (or are malformed)
    are dropped rather than trusted; an error body (a dict, e.g.
    {"code": -1121, "msg": "Invalid symbol."}) raises instead of writing
    garbage."""
    params: dict[str, Any] = {"pair": pair, "contractType": contract_type,
                              "interval": INTERVAL, "limit": limit}
    if start_ts is not None:
        params["startTime"] = int(start_ts) * 1000
    if end_ts is not None:
        params["endTime"] = int(end_ts) * 1000
    payload = http_get(KLINES_URL.format(query=urlencode(params)))
    if not isinstance(payload, list):
        raise RuntimeError(f"continuousKlines {pair}/{contract_type}: {payload}")
    rows = []
    for k in payload:
        if not isinstance(k, (list, tuple)) or len(k) < 6:
            continue
        ts = int(k[0]) // 1000
        if ts % INTERVAL_S:
            continue
        rows.append((ts, float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                     float(k[5])))
    rows.sort()
    return rows


def store(con: sqlite3.Connection, pair: str, contract_type: str,
          rows: list[tuple]) -> int:
    """INSERT OR REPLACE — the endpoint is authoritative over its own earlier
    partial bar."""
    con.executemany(
        f"INSERT OR REPLACE INTO {TABLE} "
        "(pair, contract_type, timestamp, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(pair, contract_type, *r) for r in rows])
    con.commit()
    return len(rows)


def refresh_series(pair: str, contract_type: str, *, now: datetime | None = None,
                   hours: int = REFRESH_WINDOW_HOURS,
                   http_get: Callable[[str], Any] = _http_get) -> int:
    """Pull the trailing `hours` window for one (pair, contract_type) slot."""
    now = now or clock.now_utc()
    start_ts = int(now.timestamp()) - hours * INTERVAL_S
    rows = fetch_klines(pair, contract_type, start_ts=start_ts,
                        limit=hours + 2, http_get=http_get)
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        return store(con, pair, contract_type, rows)
    finally:
        con.close()


# ─── Listed contracts ─────────────────────────────────────────────────────────

def fetch_contracts(*, now: datetime | None = None,
                    http_get: Callable[[str], Any] = _http_get) -> int:
    """exchangeInfo → the currently listed CURRENT_QUARTER / NEXT_QUARTER
    contracts for our pairs. first_seen_ts is set once and never moved;
    last_seen_ts advances on every pull, so a delisted (delivered) contract
    keeps the stamp of the last day it was listed."""
    now = now or clock.now_utc()
    now_ts = int(now.timestamp())
    payload = http_get(EXCHANGE_INFO_URL)
    if not isinstance(payload, dict) or "symbols" not in payload:
        raise RuntimeError(f"exchangeInfo: {payload}")
    rows = []
    for s in payload["symbols"]:
        if s.get("contractType") not in CONTRACT_TYPES:
            continue
        if s.get("pair") not in PAIRS:
            continue
        rows.append((s["symbol"], s["pair"], s["contractType"],
                     int(s.get("deliveryDate") or 0) // 1000 or None,
                     int(s.get("onboardDate") or 0) // 1000 or None,
                     now_ts, now_ts))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        con.executemany(
            f"INSERT INTO {CONTRACTS_TABLE} (symbol, pair, contract_type, "
            "delivery_ts, onboard_ts, first_seen_ts, last_seen_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET last_seen_ts = excluded.last_seen_ts, "
            "delivery_ts = COALESCE(excluded.delivery_ts, "
            f"{CONTRACTS_TABLE}.delivery_ts), "
            "onboard_ts = COALESCE(excluded.onboard_ts, "
            f"{CONTRACTS_TABLE}.onboard_ts)", rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


# ─── Orchestration ────────────────────────────────────────────────────────────

def refresh(*, now: datetime | None = None, force: bool = False,
            http_get: Callable[[str], Any] = _http_get) -> dict[str, int]:
    """Throttled entry point for the feed loop: at most one kline pull per
    (pair, contract_type) per UTC hour and one exchangeInfo pull per UTC day.
    Returns {series_or_'contracts': rows_upserted}, -1 for a failed item;
    keys are absent when nothing was due."""
    now = now or clock.now_utc()
    if clock.is_simulated() and not force:
        return {}
    day = now.date().isoformat()
    bucket = f"{day}:{now.hour}"
    out: dict[str, int] = {}
    for pair, ct in SERIES:
        name = f"{pair}-{ct}"
        key = f"{name}:{bucket}"
        if not force and key in _done:
            continue
        try:
            out[name] = refresh_series(pair, ct, now=now, http_get=http_get)
            _done.add(key)
        except Exception as e:  # noqa: BLE001 — one slot must not kill the rest
            log.warning(f"{TABLE} {name} refresh failed: {e}")
            out[name] = -1
        time.sleep(REQUEST_GAP_S)
    ckey = f"contracts:{day}"
    if force or ckey not in _done:
        try:
            out["contracts"] = fetch_contracts(now=now, http_get=http_get)
            _done.add(ckey)
        except Exception as e:  # noqa: BLE001 — contracts must not kill the klines
            log.warning(f"{CONTRACTS_TABLE} refresh failed: {e}")
            out["contracts"] = -1
    return out


def _last_ts(con: sqlite3.Connection, pair: str, contract_type: str) -> int | None:
    try:
        row = con.execute(
            f"SELECT MAX(timestamp) FROM {TABLE} WHERE pair=? AND contract_type=?",
            (pair, contract_type)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row and row[0] is not None else None


def backfill(since: int | None = None, *, now: datetime | None = None,
             http_get: Callable[[str], Any] = _http_get) -> dict[str, int]:
    """CLI only — never called by refresh(). Walk forward in 1500-bar pages
    from `since` (default: the slot's last stored bar, else 2021-01-01) to now.
    A page that comes back empty ends that slot: continuousKlines returns []
    once the request window is past the last bar it holds."""
    now = now or clock.now_utc()
    end_all = int(now.timestamp())
    out: dict[str, int] = {}
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        for pair, ct in SERIES:
            name = f"{pair}-{ct}"
            start = since if since is not None else (
                (_last_ts(con, pair, ct) or DEFAULT_BACKFILL_START - INTERVAL_S)
                + INTERVAL_S)
            total = 0
            try:
                cursor = start
                while cursor <= end_all:
                    rows = fetch_klines(pair, ct, start_ts=cursor,
                                        limit=MAX_LIMIT, http_get=http_get)
                    if not rows:
                        break
                    total += store(con, pair, ct, rows)
                    last = rows[-1][0]
                    if last < cursor:          # defensive: never loop forever
                        break
                    cursor = last + INTERVAL_S
                    time.sleep(REQUEST_GAP_S)
                out[name] = total
            except Exception as e:  # noqa: BLE001 — one slot must not kill the rest
                log.warning(f"{TABLE} {name} backfill failed after {total} rows: {e}")
                out[name] = -1
    finally:
        con.close()
    return out


def latest(pair: str, contract_type: str) -> tuple[int, float] | None:
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        row = con.execute(
            f"SELECT timestamp, close FROM {TABLE} WHERE pair=? AND contract_type=? "
            "ORDER BY timestamp DESC LIMIT 1", (pair, contract_type)).fetchone()
        return (int(row[0]), float(row[1])) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Binance quarterly-futures 1h feed")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="walk forward from the last stored bar to now")
    ap.add_argument("--since", default=None,
                    help="backfill start, YYYY-MM-DD or epoch seconds")
    ap.add_argument("--contracts", action="store_true",
                    help="force one exchangeInfo pull now")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill:
        since = None
        if args.since:
            since = (int(args.since) if args.since.isdigit() else
                     int(datetime.strptime(args.since, "%Y-%m-%d")
                         .replace(tzinfo=timezone.utc).timestamp()))
        print("backfill:", backfill(since))
    if args.contracts:
        print("contracts:", fetch_contracts())
    if args.refresh:
        print("refresh:", refresh())
    for pair, ct in SERIES:
        row = latest(pair, ct)
        stamp = _iso(row[0]) if row else "-"
        print(f"  {pair}-{ct:<15s} latest {row} {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
