"""Deribit public-API feed (no key, stdlib only): DVOL index history, option
instruments, and twice-daily option book snapshots for the liquid subset.
Track D1 (2026-09-06) — unblocks the VRP study and supersedes the gated
CoinDesk `cd_dvol`.

Tables (explicit PKs, classified in botlib.py in the same commit):
  deribit_dvol_daily          (asset, timestamp)      DVOL OHLC per UTC day
  deribit_options_instruments (instrument)            static per instrument
  deribit_options_daily       (instrument, timestamp) snapshot rows

Snapshot rows come from /public/get_book_summary_by_currency — one call per
asset returns every live option with mark price (in base currency), mark IV,
bid/ask, open interest, volume and the underlying price. Only the liquid
subset is kept: expiry <= 90 days and |ln(K/S)| <= 0.30 (a few hundred rows
per asset per snapshot). Snapshots are taken at 00:05 and 08:05 UTC (Deribit
settles/expires at 08:00 UTC).

History: Deribit's public endpoints serve DVOL only from ~2023 and NO history
for expired instruments, so the predecessor repo's CoinDesk snapshot
(2023-03 -> 2026-04) is copied in once with `--seed-from` (source =
'coindesk_seed'); live rows then append. Seeded option rows carry the USD
mark (`mark_price_usd`) only; live rows carry both the base-currency mark and
its USD equivalent.

Cadence: `refresh()` is called every feed cycle and throttles itself —
DVOL + instruments once per UTC day, snapshots once per (day, hour) bucket.

CLI:
  python data/sources/deribit.py --refresh
  python data/sources/deribit.py --snapshot        # force one snapshot now
  python data/sources/deribit.py --backfill-dvol   # whatever Deribit still serves
  python data/sources/deribit.py --seed-from C:/Source/Repos/trader/data/trader.db
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import clock, db as _db  # noqa: E402

log = logging.getLogger("deribit")

API = "https://www.deribit.com/api/v2/public/"
ASSETS = ("BTC", "ETH")
SNAPSHOT_HOURS_UTC = (0, 8)
SNAPSHOT_MIN_MINUTE = 5           # let the 08:00 settlement print first
# Longest legitimate gap between snapshots (08:05 -> 00:05). If the newest
# row is older than this the scheduled window was MISSED -- the machine was
# off, or the feed restarted outside 00:05-00:59 / 08:05-08:59 -- and waiting
# for the next window would leave the table stale for up to another 16h.
# Twice in 2026-09 (a restart, then an overnight crash) that produced a
# STALE TABLE alert that no amount of running the feed could clear.
SNAPSHOT_MAX_AGE_S = 16 * 3600
LIQUID_MAX_DAYS = 90
LIQUID_MAX_LOG_MONEYNESS = 0.30
REQUEST_GAP_S = 0.5
DVOL_TABLE = "deribit_dvol_daily"
INST_TABLE = "deribit_options_instruments"
DAILY_TABLE = "deribit_options_daily"

DDL = [
    f"""CREATE TABLE IF NOT EXISTS {DVOL_TABLE} (
        asset TEXT NOT NULL, timestamp INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        source TEXT NOT NULL DEFAULT 'deribit',
        PRIMARY KEY (asset, timestamp))""",
    f"""CREATE TABLE IF NOT EXISTS {INST_TABLE} (
        instrument TEXT PRIMARY KEY, asset TEXT NOT NULL,
        option_type TEXT NOT NULL, strike REAL NOT NULL, expiry_ts INTEGER NOT NULL,
        creation_ts INTEGER, first_seen_ts INTEGER, last_seen_ts INTEGER)""",
    f"""CREATE TABLE IF NOT EXISTS {DAILY_TABLE} (
        instrument TEXT NOT NULL, timestamp INTEGER NOT NULL,
        mark_price REAL, mark_price_usd REAL, mark_iv REAL,
        bid_price REAL, ask_price REAL, underlying_price REAL,
        open_interest REAL, volume REAL, volume_usd REAL,
        source TEXT NOT NULL DEFAULT 'deribit',
        PRIMARY KEY (instrument, timestamp))""",
]

_NAME_RE = re.compile(r"^(?P<asset>[A-Z]+)-(?P<day>\d{1,2})(?P<mon>[A-Z]{3})(?P<yy>\d{2})-(?P<strike>\d+(?:\.\d+)?)-(?P<cp>[CP])$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}

# Per-process throttle state: what has already been fetched today / this bucket.
_done: set[str] = set()


def _http_get(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "p300-feed/1.0"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _call(method: str, params: dict, http_get: Callable[[str], dict]) -> object:
    payload = http_get(API + method + "?" + urlencode(params))
    if "result" not in payload:
        raise RuntimeError(f"deribit {method}: {payload.get('error')}")
    return payload["result"]


def ensure_schema(con: sqlite3.Connection) -> None:
    for ddl in DDL:
        con.execute(ddl)
    con.commit()


def parse_instrument(name: str) -> dict | None:
    """'BTC-26DEC25-85000-P' -> {asset, expiry_ts (08:00 UTC), strike, option_type}.
    Returns None for names that are not plain base-settled options
    (e.g. 'BTC_USDC-…' linear options)."""
    m = _NAME_RE.match(name)
    if not m:
        return None
    exp = datetime(2000 + int(m["yy"]), _MONTHS[m["mon"]], int(m["day"]),
                   8, 0, tzinfo=timezone.utc)
    return {"asset": m["asset"], "expiry_ts": int(exp.timestamp()),
            "strike": float(m["strike"]),
            "option_type": "CALL" if m["cp"] == "C" else "PUT"}


def is_liquid(expiry_ts: int, strike: float, underlying: float, now_ts: int) -> bool:
    if underlying is None or underlying <= 0 or strike <= 0:
        return False
    if expiry_ts <= now_ts or expiry_ts - now_ts > LIQUID_MAX_DAYS * 86400:
        return False
    return abs(math.log(strike / underlying)) <= LIQUID_MAX_LOG_MONEYNESS


# ─── DVOL ─────────────────────────────────────────────────────────────────────

def fetch_dvol(asset: str, days: int = 14, *, now: datetime | None = None,
               http_get: Callable[[str], dict] = _http_get) -> int:
    """Upsert the trailing `days` of daily DVOL bars (timestamp = UTC day, s)."""
    now = now or clock.now_utc()
    end_ms = int(now.timestamp() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    res = _call("get_volatility_index_data",
                {"currency": asset, "resolution": "1D",
                 "start_timestamp": start_ms, "end_timestamp": end_ms}, http_get)
    rows = [(asset, int(t) // 1000, o, h, l, c) for t, o, h, l, c in (res.get("data") or [])]
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        con.executemany(
            f"INSERT OR REPLACE INTO {DVOL_TABLE} (asset, timestamp, open, high, low, close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'deribit')", rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


# ─── Instruments ──────────────────────────────────────────────────────────────

def fetch_instruments(asset: str, *, now: datetime | None = None,
                      http_get: Callable[[str], dict] = _http_get) -> int:
    now = now or clock.now_utc()
    now_ts = int(now.timestamp())
    res = _call("get_instruments", {"currency": asset, "kind": "option",
                                    "expired": "false"}, http_get)
    rows = []
    for r in res:
        p = parse_instrument(r["instrument_name"])
        if p is None:
            continue
        rows.append((r["instrument_name"], asset, p["option_type"], p["strike"],
                     int(r["expiration_timestamp"]) // 1000,
                     int(r.get("creation_timestamp") or 0) // 1000 or None,
                     now_ts, now_ts))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        con.executemany(
            f"INSERT INTO {INST_TABLE} (instrument, asset, option_type, strike, expiry_ts, "
            "creation_ts, first_seen_ts, last_seen_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(instrument) DO UPDATE SET last_seen_ts = excluded.last_seen_ts, "
            "creation_ts = COALESCE(deribit_options_instruments.creation_ts, excluded.creation_ts)",
            rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


# ─── Snapshot ─────────────────────────────────────────────────────────────────

def snapshot_options(asset: str, *, now: datetime | None = None,
                     http_get: Callable[[str], dict] = _http_get) -> int:
    """One book-summary call → liquid-subset rows stamped at the hour."""
    now = now or clock.now_utc()
    ts = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    now_ts = int(now.timestamp())
    res = _call("get_book_summary_by_currency", {"currency": asset, "kind": "option"}, http_get)
    rows = []
    for r in res:
        p = parse_instrument(r["instrument_name"])
        if p is None:
            continue
        underlying = r.get("underlying_price")
        if not is_liquid(p["expiry_ts"], p["strike"], underlying, now_ts):
            continue
        mark = r.get("mark_price")
        rows.append((r["instrument_name"], ts, mark,
                     (mark * underlying) if (mark is not None and underlying) else None,
                     r.get("mark_iv"), r.get("bid_price"), r.get("ask_price"), underlying,
                     r.get("open_interest"), r.get("volume"), r.get("volume_usd")))
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        ensure_schema(con)
        con.executemany(
            f"INSERT OR REPLACE INTO {DAILY_TABLE} (instrument, timestamp, mark_price, "
            "mark_price_usd, mark_iv, bid_price, ask_price, underlying_price, open_interest, "
            "volume, volume_usd, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deribit')",
            rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


# ─── Orchestration ────────────────────────────────────────────────────────────

def _snapshot_age_s(now: datetime, asset: str | None = None) -> float | None:
    """Age in seconds of the newest option-book snapshot row, or None when the
    table is unreadable or empty (a fresh DB, or a test with no schema). None
    means "cannot tell", and the caller must not treat that as due.

    `asset` scopes the query to that asset's instruments. It MUST be passed
    from the per-asset loop: the table has no asset column, and without the
    scope the first asset's catch-up write makes the table look fresh and the
    second asset is skipped for another 16h (observed on the 2026-09-10
    catch-up, where BTC wrote 496 rows and ETH silently did not run)."""
    try:
        con = sqlite3.connect(f"file:{_db.PROD_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        if asset:
            row = con.execute(
                f"SELECT MAX(timestamp) FROM {DAILY_TABLE} WHERE instrument LIKE ?",
                (f"{asset}-%",)).fetchone()
        else:
            row = con.execute(f"SELECT MAX(timestamp) FROM {DAILY_TABLE}").fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()
    if not row or row[0] is None:
        return None
    return now.timestamp() - float(row[0])


def refresh(*, now: datetime | None = None, force: bool = False,
            http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """Throttled entry point for the feed loop. Returns {feed: rows or -1};
    keys are absent when nothing was due."""
    now = now or clock.now_utc()
    if clock.is_simulated() and not force:
        return {}
    day = now.date().isoformat()
    out: dict[str, int] = {}
    for asset in ASSETS:
        key = f"daily:{asset}:{day}"
        if force or key not in _done:
            try:
                out[f"dvol_{asset}"] = fetch_dvol(asset, now=now, http_get=http_get)
                time.sleep(REQUEST_GAP_S)
                out[f"instruments_{asset}"] = fetch_instruments(asset, now=now, http_get=http_get)
                _done.add(key)
            except Exception as e:  # noqa: BLE001 — one asset must not kill the other
                log.warning(f"deribit daily {asset} failed: {e}")
                out[f"dvol_{asset}"] = -1
            time.sleep(REQUEST_GAP_S)
        bucket = f"snap:{asset}:{day}:{now.hour}"
        due = now.hour in SNAPSHOT_HOURS_UTC and now.minute >= SNAPSHOT_MIN_MINUTE
        if not due:
            # Catch-up after downtime: a missed window is not recoverable by
            # waiting, so take one snapshot now. Bucketed by hour so a failing
            # catch-up retries hourly rather than on every 60s tick.
            age = _snapshot_age_s(now, asset)
            if age is not None and age > SNAPSHOT_MAX_AGE_S:
                due = True
                bucket = f"snap:{asset}:catchup:{int(now.timestamp()) // 3600}"
                log.info(f"deribit {asset} snapshot catch-up: newest row is "
                         f"{age / 3600:.1f}h old (> {SNAPSHOT_MAX_AGE_S / 3600:.0f}h)")
        if force or (due and bucket not in _done):
            try:
                out[f"snapshot_{asset}"] = snapshot_options(asset, now=now, http_get=http_get)
                _done.add(bucket)
            except Exception as e:  # noqa: BLE001
                log.warning(f"deribit snapshot {asset} failed: {e}")
                out[f"snapshot_{asset}"] = -1
            time.sleep(REQUEST_GAP_S)
    return out


def backfill_dvol(days: int = 365 * 4,
                  http_get: Callable[[str], dict] = _http_get) -> dict[str, int]:
    """CLI only: pull whatever daily DVOL history the public endpoint still
    serves (paginated 1000 points per call)."""
    out = {}
    for asset in ASSETS:
        total = 0
        end = clock.now_utc()
        while days > 0:
            chunk = min(days, 900)
            n = fetch_dvol(asset, days=chunk, now=end, http_get=http_get)
            total += n
            if n == 0:
                break
            end = end - timedelta(days=chunk)
            days -= chunk
            time.sleep(REQUEST_GAP_S)
        out[asset] = total
        days = 365 * 4
    return out


def seed_from(source_db: Path) -> dict[str, int]:
    """Copy the predecessor CoinDesk snapshot once (INSERT OR IGNORE — live
    rows win): cd_dvol → DVOL, cd_options_instruments → instruments,
    cd_options_oi ⋈ cd_options_ohlcv → daily rows (USD mark only)."""
    src = sqlite3.connect(str(source_db))
    con = sqlite3.connect(str(_db.PROD_DB))
    out: dict[str, int] = {}
    try:
        ensure_schema(con)
        rows = src.execute("SELECT asset, timestamp, open, high, low, close FROM cd_dvol").fetchall()
        con.executemany(
            f"INSERT OR IGNORE INTO {DVOL_TABLE} (asset, timestamp, open, high, low, close, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'coindesk_seed')", rows)
        out["dvol"] = len(rows)
        inst = []
        for name, asset, otype, strike, expiry_ts, first_ts, last_ts in src.execute(
                "SELECT instrument, asset, option_type, strike, expiry_ts, first_trade_ts, "
                "last_trade_ts FROM cd_options_instruments WHERE market='deribit'"):
            if parse_instrument(name) is None:
                continue
            inst.append((name, asset, str(otype).upper(), strike, expiry_ts,
                         first_ts or None, first_ts or None, last_ts or None))
        con.executemany(
            f"INSERT OR IGNORE INTO {INST_TABLE} (instrument, asset, option_type, strike, "
            "expiry_ts, creation_ts, first_seen_ts, last_seen_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            inst)
        out["instruments"] = len(inst)
        cur = src.execute(
            "SELECT o.instrument, o.timestamp, o.close_mark_price, v.volume, v.notional_volume "
            "FROM cd_options_oi o LEFT JOIN cd_options_ohlcv v "
            "ON v.instrument = o.instrument AND v.timestamp = o.timestamp")
        n = 0
        while True:
            batch = cur.fetchmany(20000)
            if not batch:
                break
            con.executemany(
                f"INSERT OR IGNORE INTO {DAILY_TABLE} (instrument, timestamp, mark_price, "
                "mark_price_usd, mark_iv, bid_price, ask_price, underlying_price, open_interest, "
                "volume, volume_usd, source) VALUES (?, ?, NULL, ?, NULL, NULL, NULL, NULL, NULL, "
                "?, ?, 'coindesk_seed')",
                [(i, t, m, vol, notional) for i, t, m, vol, notional in batch
                 if parse_instrument(i) is not None])
            n += len(batch)
        con.commit()
        out["daily"] = n
    finally:
        src.close()
        con.close()
    return out


def latest_dvol(asset: str) -> tuple[int, float] | None:
    con = sqlite3.connect(str(_db.PROD_DB))
    try:
        row = con.execute(
            f"SELECT timestamp, close FROM {DVOL_TABLE} WHERE asset=? "
            "ORDER BY timestamp DESC LIMIT 1", (asset,)).fetchone()
        return (int(row[0]), float(row[1])) if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Deribit public feed")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--snapshot", action="store_true", help="force one snapshot now")
    ap.add_argument("--backfill-dvol", action="store_true")
    ap.add_argument("--seed-from", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.seed_from:
        print("seeded:", seed_from(args.seed_from))
    if args.backfill_dvol:
        print("dvol backfill:", backfill_dvol())
    if args.snapshot:
        print("snapshot:", refresh(force=True))
    elif args.refresh:
        print("refresh:", refresh())
    for a in ASSETS:
        print(f"  {a} latest DVOL {latest_dvol(a)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
