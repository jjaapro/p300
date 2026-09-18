"""Fetch BTC/ETH long-short account ratio history from Coinalyze.

Coinalyze stores daily LS ratio for Binance USDT-M perps from 2021-01-01
to present. This is the only mechanism (other than maintaining a CSV
snapshot in-repo) to rebuild the ~5 years of history that P-300's CPR
sleeve and regime classifier need for backtests — Binance's public
endpoint only serves the trailing 30 days.

This script writes to ca_long_short_ratio in data/trader.db. Idempotent
via INSERT OR REPLACE on (asset, timestamp). Resumable: each call picks
up where the table left off.

Auth:
  Set COINALYZE_API_KEY in the environment. Free tier is enough — 40
  requests/min, daily granularity = unlimited history. Register at
  https://coinalyze.net/ (top-right "API" → "Get API Key").

Usage:
  export COINALYZE_API_KEY=...
  python fetch_coinalyze.py            # BTC + ETH from earliest to now
  python fetch_coinalyze.py --symbols BTC
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parent
ENV_PATH = REPO / ".env"
UNFILLABLE_PATH = REPO / "data" / "known_unfillable.json"

from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB

API_BASE = "https://api.coinalyze.net/v1"
RATE_LIMIT_S = 1.6  # 40/min = 1.5s between calls; 1.6 leaves margin


def _load_env_file() -> None:
    """Load `.env` into os.environ for keys that aren't already set.
    Stdlib parser — handles `KEY=VALUE` lines, comments (#...), blank
    lines. Does NOT overwrite values already in the environment, so an
    explicit `export` still wins."""
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

SYMBOLS = {
    "BTC": "BTCUSDT_PERP.A",
    "ETH": "ETHUSDT_PERP.A",
}

# Coinalyze's earliest LSR data, per their API docs (also true empirically).
EARLIEST_LSR_TS = 1609459200  # 2021-01-01 UTC


class MissingApiKey(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("COINALYZE_API_KEY", "").strip()
    if not key:
        raise MissingApiKey(
            "COINALYZE_API_KEY not set. Get a free key at https://coinalyze.net/ "
            "and `export COINALYZE_API_KEY=...` before running."
        )
    return key


_MAX_RETRIES = 5


def _api_get(endpoint: str, params: dict, _attempt: int = 0) -> list:
    params = {**params, "api_key": _api_key()}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}/{endpoint}?{qs}"
    time.sleep(RATE_LIMIT_S)
    req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        if e.code == 429:
            if _attempt >= _MAX_RETRIES:
                print(f"  rate limited {_MAX_RETRIES} times, giving up", file=sys.stderr)
                return []
            retry = int(e.headers.get("Retry-After", 10))
            print(f"  rate limited, sleeping {retry}s (attempt {_attempt + 1}/{_MAX_RETRIES})...", file=sys.stderr)
            time.sleep(retry)
            return _api_get(endpoint, {k: v for k, v in params.items()
                                        if k != "api_key"}, _attempt + 1)
        print(f"  HTTP {e.code}: {url[:120]}", file=sys.stderr)
        return []


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS ca_long_short_ratio (
            asset TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            ratio REAL,
            long_pct REAL,
            short_pct REAL,
            PRIMARY KEY (asset, timestamp)
        )
    """)
    con.commit()


def _find_gaps(con: sqlite3.Connection, asset: str, cadence_s: int,
                now_s: int, since_floor: int) -> list[tuple[int, int]]:
    """Return list of (start, end) gap windows in seconds where rows are
    missing for `asset`. Includes internal gaps, trailing (MAX -> now), and
    leading (since_floor -> MIN) windows."""
    rows = con.execute("""
        WITH ordered AS (
            SELECT timestamp AS ts,
                   LAG(timestamp) OVER (ORDER BY timestamp) AS prev
            FROM ca_long_short_ratio WHERE asset=?
        )
        SELECT prev, ts FROM ordered WHERE ts - prev > ? ORDER BY prev
    """, (asset, cadence_s)).fetchall()
    gaps = [(int(prev) + cadence_s, int(ts) - cadence_s) for prev, ts in rows]

    minmax = con.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM ca_long_short_ratio WHERE asset=?",
        (asset,),
    ).fetchone()
    min_ts, max_ts = minmax
    if max_ts is None:
        if since_floor < now_s:
            gaps.append((since_floor, now_s))
        return sorted(gaps)
    if now_s - int(max_ts) > cadence_s:
        gaps.append((int(max_ts) + cadence_s, now_s))
    if int(min_ts) - since_floor > cadence_s:
        gaps.append((since_floor, int(min_ts) - cadence_s))
    return sorted(gaps)


def _record_unfillable(table: str, asset: str, start_ts: int, end_ts: int,
                        reason: str) -> None:
    """Append an entry to data/known_unfillable.json. De-dupes against
    existing entries with the same (table, asset, start_ts, end_ts)."""
    import json
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if UNFILLABLE_PATH.exists():
        data = json.loads(UNFILLABLE_PATH.read_text(encoding="utf-8"))
    else:
        UNFILLABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"_comment": "Auto-managed: gaps verified unfillable from "
                            "any available source. health.py treats these "
                            "as INFO instead of FAIL.",
                "entries": []}
    for e in data["entries"]:
        if (e.get("table") == table and e.get("asset") == asset
                and int(e.get("start_ts", -1)) == start_ts
                and int(e.get("end_ts", -1)) == end_ts):
            return  # already recorded
    data["entries"].append({
        "table": table, "asset": asset,
        "start_ts": start_ts, "end_ts": end_ts,
        "reason": reason, "recorded_at": today,
    })
    UNFILLABLE_PATH.write_text(json.dumps(data, indent=2) + "\n",
                                encoding="utf-8")
    print(f"    [unfillable] recorded {table}/{asset} "
          f"{datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d')} "
          f"-> {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d')}")


def fetch_lsr(assets: tuple[str, ...] = ("BTC", "ETH")) -> dict[str, int]:
    """Detect + fill all gaps in ca_long_short_ratio for each asset.
    Idempotent via INSERT OR REPLACE; gap-aware so internal holes are
    actually filled, not just the trailing window. Gaps that come back
    empty from Coinalyze are auto-appended to data/known_unfillable.json
    so health.py stops flagging them as FAIL."""
    cadence_s = 86_400
    now_s = int(datetime.now(timezone.utc).timestamp())
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(con)
        out: dict[str, int] = {}
        for asset in assets:
            symbol = SYMBOLS.get(asset)
            if not symbol:
                print(f"  unknown asset: {asset}", file=sys.stderr)
                out[asset] = 0
                continue
            gaps = _find_gaps(con, asset, cadence_s, now_s, EARLIEST_LSR_TS)
            if not gaps:
                print(f"  {asset}: up to date")
                out[asset] = 0
                continue
            missing = sum((end - start) // cadence_s + 1 for start, end in gaps)
            print(f"  {asset}: {len(gaps)} gap(s), ~{missing:,} days to fetch")
            total = 0
            for gap_start, gap_end in gaps:
                rows_for_gap = 0
                cursor = gap_start
                while cursor <= gap_end:
                    chunk_end = min(cursor + 365 * cadence_s, gap_end)
                    data = _api_get("long-short-ratio-history", {
                        "symbols": symbol, "interval": "daily",
                        "from": cursor, "to": chunk_end,
                    })
                    if data and data[0].get("history"):
                        rows = [(asset, r["t"], r["r"], r["l"], r["s"])
                                for r in data[0]["history"]]
                        con.executemany(
                            "INSERT OR REPLACE INTO ca_long_short_ratio "
                            "(asset, timestamp, ratio, long_pct, short_pct) "
                            "VALUES (?, ?, ?, ?, ?)",
                            rows,
                        )
                        con.commit()
                        rows_for_gap += len(rows)
                        total += len(rows)
                    cursor = chunk_end + cadence_s
                if rows_for_gap == 0:
                    # Coinalyze had nothing for the entire gap window --
                    # mark unfillable so health.py stops complaining.
                    _record_unfillable(
                        "ca_long_short_ratio", asset, gap_start, gap_end,
                        "Coinalyze returned empty response for this gap "
                        "(verified source-side hole)",
                    )
            print(f"    -> {total:,} rows inserted")
            out[asset] = total
        return out
    finally:
        con.close()


# Coinalyze serves sub-daily liquidations only for a rolling window — measured
# 2026-09-18 on BTCUSDT_PERP.A, 88 days back returns rows and 90 returns none —
# while its daily history runs years deep (1,500 days back still answers).
LIQ_ROLLING_WINDOW_DAYS = 89
EARLIEST_LIQ_TS = EARLIEST_LSR_TS  # 2021-01-01, same floor as the LSR series

# The two cadences go to different tables on purpose. Their timestamps collide
# at every UTC midnight and neither table carries an interval column, so one
# table holding both would silently overwrite an hour with a whole day.
LIQ_TABLES = {"daily": "ca_liquidations_daily"}
LIQ_DEFAULT_TABLE = "ca_liquidations"


def _liq_table(interval: str) -> str:
    return LIQ_TABLES.get(interval, LIQ_DEFAULT_TABLE)


def _ensure_liq_table(con: sqlite3.Connection, table: str) -> None:
    """Coinalyze liquidations, kept apart from cd_liquidations because the two
    are fetched and maintained independently — not because they disagree.
    Measured 2026-09-18 on the 1,368 hours where both hold real data
    (2026-02-25 17:00 → 2026-04-23 16:00): long correlation 0.9999 and totals
    within 0.1 %, short 0.9911 and within 2.9 %, with 98.2 % of hours matching
    to 1e-6 on both sides. For BTCUSDT_PERP.A the Coinalyze series continues
    the CoinDesk one; an earlier version of this docstring claimed they were
    not comparable, and that was wrong."""
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            asset      TEXT NOT NULL,
            timestamp  INTEGER NOT NULL,
            long_qty   REAL,
            short_qty  REAL,
            PRIMARY KEY (asset, timestamp)
        )
    """)
    con.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp)
    """)
    con.commit()


def fetch_liquidations(assets: tuple[str, ...] = ("BTC",),
                        interval: str = "1hour") -> dict[str, int]:
    """Backfill Coinalyze liquidations. Hourly granularity by default, into
    ca_liquidations; ``daily`` goes to ca_liquidations_daily instead, because
    the two cadences share timestamps at UTC midnight. Idempotent via
    INSERT OR REPLACE on (asset, timestamp).

    Coinalyze returns:
      t = timestamp (seconds)
      l = long-side liquidations (base asset units, e.g. BTC)
      s = short-side liquidations (base asset units)

    Symbol `BTCUSDT_PERP.A` is Coinalyze's Binance USDT-M perp feed.

    Sub-daily intervals only ask for the rolling window the API actually
    serves. Asking beyond it returns empty pages, which the gap handler would
    otherwise record in known_unfillable.json as a verified source-side hole —
    a false claim, since the daily series covers those years.
    """
    cadence_s = {'1min': 60, '5min': 300, '15min': 900, '30min': 1800,
                 '1hour': 3600, '4hour': 14400, 'daily': 86400}[interval]
    now_s = int(datetime.now(timezone.utc).timestamp())
    table = _liq_table(interval)
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_liq_table(con, table)
        out: dict[str, int] = {}
        for asset in assets:
            symbol = SYMBOLS.get(asset)
            if not symbol:
                print(f"  unknown asset: {asset}", file=sys.stderr)
                out[asset] = 0
                continue
            # Coverage gap detection
            rows = con.execute(
                f"SELECT MIN(timestamp), MAX(timestamp) FROM {table} "
                "WHERE asset=?", (asset,)).fetchone()
            min_ts, max_ts = rows
            earliest = (EARLIEST_LIQ_TS if interval == "daily"
                        else now_s - LIQ_ROLLING_WINDOW_DAYS * 86400)
            rolling = interval != "daily"
            gaps = []
            if max_ts is None:
                gaps.append((earliest, now_s))
            else:
                # A rolling-window source has no fillable leading gap: the
                # window edge advances with the clock, so every run would find
                # a fresh sliver before the earliest stored row, fail to fetch
                # it, and record it as unfillable — a new entry every hour,
                # forever. Only the trailing gap is real there.
                if not rolling and int(min_ts) - earliest > cadence_s:
                    gaps.append((earliest, int(min_ts) - cadence_s))
                if now_s - int(max_ts) > cadence_s:
                    gaps.append((int(max_ts) + cadence_s, now_s))
            # Anything before `earliest` has aged out of the API's window and
            # will never be served at this cadence. Record it as unfillable
            # once, then stop asking for it.
            expired = [(s, min(e, earliest - cadence_s))
                       for s, e in gaps if s < earliest]
            for exp_start, exp_end in expired:
                if exp_end >= exp_start:
                    _record_unfillable(
                        table, asset, exp_start, exp_end,
                        f"older than Coinalyze's {LIQ_ROLLING_WINDOW_DAYS}-day "
                        f"rolling window for interval {interval}",
                    )
            gaps = [(max(s, earliest), e) for s, e in gaps if e >= earliest]
            if not gaps:
                print(f"  {asset}: up to date")
                out[asset] = 0
                continue
            est_bars = sum((e - s) // cadence_s for s, e in gaps)
            print(f"  {asset}: {len(gaps)} gap(s), ~{est_bars:,} {interval} bars")
            total = 0
            for gap_start, gap_end in gaps:
                rows_for_gap = 0
                cursor = gap_start
                # Coinalyze caps history queries; use 90-day chunks for hourly
                chunk_days = 90 if interval in ('1hour', '4hour') else 30
                while cursor <= gap_end:
                    chunk_end = min(cursor + chunk_days * 86400, gap_end)
                    data = _api_get("liquidation-history", {
                        "symbols": symbol, "interval": interval,
                        "from": cursor, "to": chunk_end,
                    })
                    if data and isinstance(data, list) and data[0].get("history"):
                        hist = data[0]["history"]
                        ins = [(asset, int(r["t"]),
                                 float(r.get("l") or 0),
                                 float(r.get("s") or 0))
                                for r in hist]
                        con.executemany(
                            f"INSERT OR REPLACE INTO {table} "
                            "(asset, timestamp, long_qty, short_qty) "
                            "VALUES (?, ?, ?, ?)", ins)
                        con.commit()
                        rows_for_gap += len(ins)
                        total += len(ins)
                    cursor = chunk_end + cadence_s
                if rows_for_gap == 0:
                    _record_unfillable(
                        table, asset, gap_start, gap_end,
                        "Coinalyze returned empty response for liquidation gap",
                    )
            print(f"    -> {total:,} rows inserted")
            out[asset] = total
        return out
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="BTC,ETH",
                    help="Comma-separated assets (default: BTC,ETH)")
    ap.add_argument("--liquidations", action="store_true",
                    help="Also backfill ca_liquidations from Coinalyze "
                         "(2021+ hourly).")
    ap.add_argument("--liquidations-only", action="store_true",
                    help="ONLY backfill liquidations, skip LSR.")
    ap.add_argument("--liq-interval", default="1hour",
                    help="Liquidation interval: 1hour | 4hour | daily")
    args = ap.parse_args(argv)
    assets = tuple(s.strip().upper() for s in args.symbols.split(","))
    try:
        if not args.liquidations_only:
            print("Fetching LSR...")
            fetch_lsr(assets)
        if args.liquidations or args.liquidations_only:
            print(f"Fetching liquidations ({args.liq_interval})...")
            fetch_liquidations(assets, interval=args.liq_interval)
    except MissingApiKey as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
