"""Daily macro series from Yahoo Finance (chart v8 JSON, no key, stdlib only).

Feeds `macro_daily(symbol, date, open, high, low, close, volume)` with
PRIMARY KEY (symbol, date) — the same shape and symbol names as the
predecessor repo's `fetch_macro.py` / `macro_daily` so its 2000→2026-04
snapshot can be seeded with `--seed-from`.

Symbols (kept small: what the anchor-allocator study and regime context
need; add more only with a consumer):
  SPX  ^GSPC      DXY  DX-Y.NYB   VIX  ^VIX   TNX  ^TNX (10y yield, %)
  GOLD GC=F       IEF  IEF        TLT  TLT

Cadence: once per UTC day via `binance.refresh_all()` →
`_refresh_daily_external("macro_yahoo", ...)`; `refresh()` itself pulls the
trailing month so a missed day self-heals. Weekday cadence, so the
freshness contract is loose (5 days) and the table has no check_gaps spec.

CLI:
  python data/sources/macro_yahoo.py --refresh
  python data/sources/macro_yahoo.py --backfill                 # range=max
  python data/sources/macro_yahoo.py --seed-from C:/Source/Repos/trader/data/trader.db
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
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

log = logging.getLogger("macro_yahoo")

TABLE = "macro_daily"
SYMBOLS: dict[str, str] = {
    "SPX": "^GSPC", "DXY": "DX-Y.NYB", "VIX": "^VIX", "TNX": "^TNX",
    "GOLD": "GC=F", "IEF": "IEF", "TLT": "TLT",
}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?{window}&interval=1d"
REQUEST_GAP_S = 0.5
DEEP_PERIOD1 = 946684800          # 2000-01-01 -- range=max would return MONTHLY bars

DDL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        symbol TEXT NOT NULL,
        date   TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY (symbol, date)
    )
"""


def _http_get(url: str) -> dict:
    """Three attempts -- Yahoo is intermittently slow."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (p300 feed)"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError("yahoo fetch failed")


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(DDL)
    con.commit()


def fetch_yahoo(yahoo_symbol: str, range_: str = "1mo",
                http_get: Callable[[str], dict] = _http_get,
                deep: bool = False) -> list[tuple]:
    """Return [(date_iso, open, high, low, close, volume)] for one symbol.
    Session timestamps are converted to UTC dates (a US session opening at
    13:30 UTC lands on its own calendar date). `deep=True` uses explicit
    period1/period2 bounds instead of `range=` because Yahoo silently
    returns monthly bars for range=max."""
    if deep:
        window = f"period1={DEEP_PERIOD1}&period2={int(time.time())}"
    else:
        window = f"range={range_}"
    payload = http_get(CHART_URL.format(sym=quote(yahoo_symbol), window=window))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    r0 = result[0]
    ts = r0.get("timestamp") or []
    q = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        close = (q.get("close") or [None])[i] if i < len(q.get("close") or []) else None
        if close is None:
            continue
        d = datetime.fromtimestamp(int(t), tz=timezone.utc).date().isoformat()
        rows.append((d, q["open"][i], q["high"][i], q["low"][i], close,
                     (q.get("volume") or [None] * len(ts))[i]))
    return rows


def store(con: sqlite3.Connection, symbol: str, rows: list[tuple]) -> int:
    con.executemany(
        f"INSERT OR REPLACE INTO {TABLE} (symbol, date, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(symbol, *r) for r in rows])
    con.commit()
    return len(rows)


def refresh(*, range_: str = "1mo", deep: bool = False,
            http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """Pull the trailing window for every symbol; per-symbol failures are
    isolated and reported as -1. Returns {symbol: rows_upserted}."""
    out: dict[str, int] = {}
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        for sym, ysym in SYMBOLS.items():
            try:
                out[sym] = store(con, sym, fetch_yahoo(ysym, range_, http_get, deep=deep))
            except Exception as e:  # noqa: BLE001 — one symbol must not kill the rest
                log.warning(f"{TABLE} {sym} ({ysym}) failed: {e}")
                out[sym] = -1
            time.sleep(REQUEST_GAP_S)
    finally:
        con.close()
    return out


def backfill(http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """One-time deep pull (2000->now, daily). CLI only -- never called by refresh()."""
    return refresh(deep=True, http_get=http_get)


def seed_from(source_db: Path) -> int:
    """Copy the predecessor snapshot's macro_daily rows for our symbols
    (INSERT OR IGNORE — live rows win)."""
    src = sqlite3.connect(str(source_db))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        rows = src.execute(
            f"SELECT symbol, date, open, high, low, close, volume FROM {TABLE} "
            f"WHERE symbol IN ({','.join('?' * len(SYMBOLS))})",
            tuple(SYMBOLS)).fetchall()
        con.executemany(
            f"INSERT OR IGNORE INTO {TABLE} (symbol, date, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.commit()
        return len(rows)
    finally:
        src.close()
        con.close()


def latest(symbol: str) -> tuple[str, float] | None:
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        row = con.execute(
            f"SELECT date, close FROM {TABLE} WHERE symbol=? ORDER BY date DESC LIMIT 1",
            (symbol,)).fetchone()
        return (row[0], float(row[1])) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Yahoo macro daily feed")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--seed-from", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.seed_from:
        print(f"seeded {seed_from(args.seed_from):,} rows from {args.seed_from}")
    if args.backfill:
        print("backfill:", backfill())
    if args.refresh:
        print("refresh:", refresh())
    for sym in SYMBOLS:
        print(f"  {sym:5s} latest {latest(sym)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
