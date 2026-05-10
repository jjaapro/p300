"""Derivatives data ingest from CoinDesk Data API.

Three endpoints, all free and public (no auth required):

  • Hourly Open Interest (Binance BTC-USDT perpetual)
  • Hourly Liquidations (Binance BTC-USDT perpetual)
  • Daily DVOL — Deribit implied-volatility index for BTC and ETH

Schemas are 1:1 compatible with the upstream `trader` repo's
`fetch_coindesk.py` so a future merge / cross-replay between the two
repos doesn't churn. The `cd_` prefix in our table names already meant
"CoinDesk" — this module fills in the OI / liquidations / DVOL slots
that p300 inherited as schemas but never populated.

Cadence: refresh() rate-limits itself to once per hour; binance_feed's
60-second loop calls it cheaply most of the time. Each fetch paginates
backward from now until it reaches the latest in-DB timestamp or a
configurable safety floor (default 48h for OI / liquidations, 14d for
DVOL). Use `backfill()` for the one-time deeper history pull.

Reader functions (`latest_oi`, `latest_liquidations`, `latest_dvol`)
are exposed so services.ai_quant.context can read recent rows without
re-implementing SQL.

CoinDesk Data API has a generous free quota that resets daily; at our
hourly cadence with three endpoints we use ~72 requests/day, leaving
plenty of headroom. No auth header required.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services import db

log = logging.getLogger("p300.coindesk_fetcher")

BASE_URL = "https://data-api.coindesk.com"
FUTURES_MARKET = "binance"
FUTURES_INSTRUMENT = "BTC-USDT-VANILLA-PERPETUAL"
DVOL_INSTRUMENTS = {"BTC": "BTCDVOL_USDC", "ETH": "ETHDVOL_USDC"}

RATE_LIMIT_SECONDS = 60 * 60       # whole-orchestrator throttle (refresh)
PER_REQUEST_DELAY = 0.30           # small delay between paginated calls
HTTP_TIMEOUT_SECONDS = 30
PAGE_LIMIT = 2000                   # CoinDesk's max page size

# Default safety floors for incremental refresh (so a stale-DB resync
# doesn't accidentally walk back years on a single tick). Backfill mode
# uses much wider lookbacks via the CLI.
DEFAULT_OI_LOOKBACK_HOURS = 48
DEFAULT_LIQ_LOOKBACK_HOURS = 48
DEFAULT_DVOL_LOOKBACK_DAYS = 14

# In-process throttle — same pattern as news_fetcher.
_last_refresh_ts: float = 0.0


# ─── Schema ────────────────────────────────────────────────────────────────

def _ensure_schema(con: sqlite3.Connection) -> None:
    """Idempotent CREATE for the three tables this module owns. Schemas
    match trader/fetch_coindesk.py exactly so a cross-repo replay reads
    cleanly from either DB."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS cd_open_interest (
            timestamp INTEGER PRIMARY KEY,
            oi_open       REAL, oi_high       REAL, oi_low       REAL, oi_close       REAL,
            oi_value_open REAL, oi_value_high REAL, oi_value_low REAL, oi_value_close REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS cd_liquidations (
            timestamp INTEGER PRIMARY KEY,
            long_quantity        REAL,
            short_quantity       REAL,
            long_quote_quantity  REAL,
            short_quote_quantity REAL,
            long_count           INTEGER,
            short_count          INTEGER,
            vwap_long_price      REAL,
            vwap_short_price     REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS cd_dvol (
            asset TEXT, timestamp INTEGER,
            open  REAL, high REAL, low REAL, close REAL,
            PRIMARY KEY (asset, timestamp)
        )
    """)
    con.commit()


# ─── HTTP layer (injectable) ───────────────────────────────────────────────

def _http_get(url: str) -> dict:
    """Default HTTP fetch: GET → JSON dict. Raises on transport errors so
    callers can decide whether to swallow or propagate. Tests inject
    their own callable to avoid the network."""
    req = Request(url, headers={"User-Agent": "p300/1.0 coindesk fetcher"})
    with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read())


# ─── Pagination + persist ──────────────────────────────────────────────────

def _get_latest_ts(con: sqlite3.Connection, table: str,
                    where_clause: str = "", where_args: tuple = ()) -> int:
    """Latest timestamp in `table`, or 0 if empty. Optional WHERE clause for
    composite-key tables (e.g. cd_dvol filtered by asset)."""
    sql = f"SELECT MAX(timestamp) FROM {table}"
    if where_clause:
        sql += f" WHERE {where_clause}"
    try:
        row = con.execute(sql, where_args).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and row[0] else 0


def _paginate_backward(
    *,
    http_get: Callable[[str], dict],
    con: sqlite3.Connection,
    endpoint: str,
    params: dict,
    table: str,
    mapper: Callable[[dict], tuple],
    start_after_ts: int,
    bar_size_seconds: int,
    max_pages: int = 30,
) -> int:
    """Walk backward from `now` until the page's oldest row is at or before
    `start_after_ts`. INSERT OR IGNORE each mapped row into `table`.
    Returns the count of newly-inserted rows.

    `bar_size_seconds` is used to skip the still-forming current bar
    (3600 for hourly endpoints, 86400 for daily). Without that guard the
    in-progress bar could be re-fetched on every refresh, polluting the
    table with intermediate values.
    """
    to_ts = int(time.time())
    current_bar_ts = (to_ts // bar_size_seconds) * bar_size_seconds
    inserted_total = 0
    for _ in range(max_pages):
        param_str = "&".join(f"{k}={v}" for k, v in params.items())
        url = (f"{BASE_URL}{endpoint}?{param_str}"
                f"&limit={PAGE_LIMIT}&to_ts={to_ts}")
        try:
            data = http_get(url)
        except (URLError, HTTPError, OSError, json.JSONDecodeError) as e:
            log.warning(f"coindesk_fetcher {endpoint} fetch error: {e}")
            break
        rows = data.get("Data") or []
        if not rows:
            break
        new_rows: list[tuple] = []
        for r in rows:
            ts = int(r.get("TIMESTAMP") or 0)
            if ts <= start_after_ts or ts >= current_bar_ts:
                continue
            try:
                new_rows.append(mapper(r))
            except (KeyError, TypeError, ValueError):
                continue
        # Insert one-at-a-time to count actual new rows accurately
        cur = con.cursor()
        for row in new_rows:
            placeholders = ",".join(["?"] * len(row))
            cur.execute(
                f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})", row,
            )
            inserted_total += cur.rowcount
        con.commit()
        oldest_ts = min(int(r.get("TIMESTAMP") or to_ts) for r in rows)
        if oldest_ts <= start_after_ts + bar_size_seconds:
            break
        if len(rows) < PAGE_LIMIT:
            break
        to_ts = oldest_ts - 1
        time.sleep(PER_REQUEST_DELAY)
    return inserted_total


# ─── Per-endpoint mappers ──────────────────────────────────────────────────

def _map_oi_row(r: dict) -> tuple:
    return (
        int(r["TIMESTAMP"]),
        r.get("OPEN_SETTLEMENT", 0), r.get("HIGH_SETTLEMENT", 0),
        r.get("LOW_SETTLEMENT", 0),  r.get("CLOSE_SETTLEMENT", 0),
        r.get("OPEN_QUOTE", 0),  r.get("HIGH_QUOTE", 0),
        r.get("LOW_QUOTE", 0),   r.get("CLOSE_QUOTE", 0),
    )


def _map_liq_row(r: dict) -> tuple:
    return (
        int(r["TIMESTAMP"]),
        r.get("LONG_QUANTITY", 0),         r.get("SHORT_QUANTITY", 0),
        r.get("LONG_QUOTE_QUANTITY", 0),   r.get("SHORT_QUOTE_QUANTITY", 0),
        int(r.get("TOTAL_LONG_LIQUIDATION_UPDATES") or 0),
        int(r.get("TOTAL_SHORT_LIQUIDATION_UPDATES") or 0),
        r.get("VWAP_LONG_PRICE", 0),       r.get("VWAP_SHORT_PRICE", 0),
    )


def _map_dvol_row_factory(asset: str) -> Callable[[dict], tuple]:
    def _map(r: dict) -> tuple:
        return (asset, int(r["TIMESTAMP"]),
                r["OPEN"], r["HIGH"], r["LOW"], r["CLOSE"])
    return _map


# ─── Per-endpoint fetchers ─────────────────────────────────────────────────

def fetch_oi(
    con: sqlite3.Connection,
    *,
    http_get: Callable[[str], dict] = _http_get,
    lookback_hours: int = DEFAULT_OI_LOOKBACK_HOURS,
) -> int:
    floor_ts = int(time.time()) - lookback_hours * 3600
    start_after = max(_get_latest_ts(con, "cd_open_interest"), floor_ts - 1)
    return _paginate_backward(
        http_get=http_get, con=con,
        endpoint="/futures/v1/historical/open-interest/hours",
        params={"market": FUTURES_MARKET, "instrument": FUTURES_INSTRUMENT},
        table="cd_open_interest", mapper=_map_oi_row,
        start_after_ts=start_after, bar_size_seconds=3600,
    )


def fetch_liquidations(
    con: sqlite3.Connection,
    *,
    http_get: Callable[[str], dict] = _http_get,
    lookback_hours: int = DEFAULT_LIQ_LOOKBACK_HOURS,
) -> int:
    floor_ts = int(time.time()) - lookback_hours * 3600
    start_after = max(_get_latest_ts(con, "cd_liquidations"), floor_ts - 1)
    return _paginate_backward(
        http_get=http_get, con=con,
        endpoint="/futures/v1/historical/liquidation/hours",
        params={"market": FUTURES_MARKET, "instrument": FUTURES_INSTRUMENT},
        table="cd_liquidations", mapper=_map_liq_row,
        start_after_ts=start_after, bar_size_seconds=3600,
    )


def fetch_dvol(
    con: sqlite3.Connection,
    asset: str = "BTC",
    *,
    http_get: Callable[[str], dict] = _http_get,
    lookback_days: int = DEFAULT_DVOL_LOOKBACK_DAYS,
) -> int:
    if asset not in DVOL_INSTRUMENTS:
        raise ValueError(f"DVOL: unsupported asset {asset!r}")
    instrument = DVOL_INSTRUMENTS[asset]
    floor_ts = int(time.time()) - lookback_days * 86400
    latest = _get_latest_ts(con, "cd_dvol",
                              "asset = ?", (asset,))
    start_after = max(latest, floor_ts - 1)
    return _paginate_backward(
        http_get=http_get, con=con,
        endpoint="/index/v1/historical/days",
        params={"market": "deribit", "instrument": instrument},
        table="cd_dvol", mapper=_map_dvol_row_factory(asset),
        start_after_ts=start_after, bar_size_seconds=86400,
    )


# ─── Top-level refresh ─────────────────────────────────────────────────────

def refresh(
    *,
    force: bool = False,
    http_get: Callable[[str], dict] = _http_get,
) -> dict[str, int]:
    """Refresh all three CoinDesk feeds. Throttled to once per hour
    (use force=True or the CLI's --once to bypass). Returns a dict of
    {feed_name: rows_inserted}; -1 indicates a fetcher error.

    Per-feed failures are isolated — one outage doesn't sink the rest.
    No-op (returns {}) in sim mode — sim must not hit the network."""
    from services import clock
    if clock.is_simulated():
        return {}
    global _last_refresh_ts
    now = time.time()
    if not force and (now - _last_refresh_ts) < RATE_LIMIT_SECONDS:
        return {}
    out: dict[str, int] = {}
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        for name, fn in (
            ("open_interest", lambda: fetch_oi(con, http_get=http_get)),
            ("liquidations", lambda: fetch_liquidations(con, http_get=http_get)),
            ("dvol_btc", lambda: fetch_dvol(con, "BTC", http_get=http_get)),
            ("dvol_eth", lambda: fetch_dvol(con, "ETH", http_get=http_get)),
        ):
            try:
                out[name] = fn()
            except Exception as e:  # noqa: BLE001
                log.warning(f"coindesk_fetcher {name} failed: {e}")
                out[name] = -1
    finally:
        con.close()
    _last_refresh_ts = now
    if any(v > 0 for v in out.values()):
        summary = ", ".join(f"{k}={v}" for k, v in out.items() if v > 0)
        log.info(f"coindesk_fetcher: new rows — {summary}")
    return out


def backfill(
    *,
    http_get: Callable[[str], dict] = _http_get,
    oi_days: int = 365,
    liq_days: int = 365,
    dvol_days: int = 365 * 3,
) -> dict[str, int]:
    """One-time deeper history pull. Pass via the CLI; not called from
    refresh() so we don't accidentally re-walk years of history every
    hour."""
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        return {
            "open_interest": fetch_oi(con, http_get=http_get,
                                        lookback_hours=oi_days * 24),
            "liquidations": fetch_liquidations(con, http_get=http_get,
                                                  lookback_hours=liq_days * 24),
            "dvol_btc": fetch_dvol(con, "BTC", http_get=http_get,
                                     lookback_days=dvol_days),
            "dvol_eth": fetch_dvol(con, "ETH", http_get=http_get,
                                     lookback_days=dvol_days),
        }
    finally:
        con.close()


def reset_throttle() -> None:
    """Test helper: clear the in-process rate-limit so refresh() will fetch."""
    global _last_refresh_ts
    _last_refresh_ts = 0.0


# ─── Reader functions (consumed by ai_quant context bundle) ────────────────

def latest_oi(hours_back: int = 168) -> list[dict]:
    """Hourly OI rows from the last `hours_back` hours, oldest-first.
    Empty list if the table doesn't exist or has no rows in the window."""
    cutoff = int(time.time()) - hours_back * 3600
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT timestamp, oi_close, oi_value_close "
            "FROM cd_open_interest WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    return [{"ts": r[0], "oi_close": r[1], "oi_value_close": r[2]} for r in rows]


def latest_liquidations(hours_back: int = 168) -> list[dict]:
    """Hourly liquidation rows from the last `hours_back` hours, oldest-first."""
    cutoff = int(time.time()) - hours_back * 3600
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT timestamp, long_quote_quantity, short_quote_quantity, "
            "       long_count, short_count, vwap_long_price, vwap_short_price "
            "FROM cd_liquidations WHERE timestamp >= ? ORDER BY timestamp",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()
    return [
        {"ts": r[0],
         "long_quote_quantity": r[1] or 0.0,
         "short_quote_quantity": r[2] or 0.0,
         "long_count": int(r[3] or 0),
         "short_count": int(r[4] or 0),
         "vwap_long_price": r[5],
         "vwap_short_price": r[6]}
        for r in rows
    ]


def latest_dvol(asset: str, days_back: int = 30) -> list[dict]:
    """Daily DVOL rows for `asset` over the last `days_back` days."""
    cutoff = int(time.time()) - days_back * 86400
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        rows = con.execute(
            "SELECT timestamp, close FROM cd_dvol "
            "WHERE asset = ? AND timestamp >= ? ORDER BY timestamp",
            (asset.upper(), cutoff),
        ).fetchall()
    finally:
        con.close()
    return [{"ts": r[0], "close": r[1]} for r in rows]


# ─── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Fetch CoinDesk derivatives data into trader.db.")
    p.add_argument("--once", action="store_true",
                   help="Run refresh() once, bypassing the rate-limit. Default mode.")
    p.add_argument("--backfill", action="store_true",
                   help="Run a deeper history pull (default: 1y OI/liq, 3y DVOL).")
    p.add_argument("--oi-days", type=int, default=365,
                   help="Backfill window for OI (--backfill only).")
    p.add_argument("--liq-days", type=int, default=365,
                   help="Backfill window for liquidations (--backfill only).")
    p.add_argument("--dvol-days", type=int, default=365 * 3,
                   help="Backfill window for DVOL (--backfill only).")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.backfill:
        result = backfill(oi_days=args.oi_days,
                            liq_days=args.liq_days,
                            dvol_days=args.dvol_days)
    else:
        result = refresh(force=True)
    for k, v in result.items():
        marker = "OK " if v >= 0 else "ERR"
        print(f"  [{marker}] {k:<20} {v:>6} new rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
