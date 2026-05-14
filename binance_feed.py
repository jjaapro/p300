"""Keep data/trader.db up-to-date with Binance market data the live services need.

Handles 7 feeds via Binance public REST (no API key needed):
  btc_1m               — BTCUSDT spot 1-min klines (for PDO, CPR)
  eth_1m               — ETHUSDT spot 1-min klines (for PDO, CPR)
  cd_futures_ohlcv     — BTCUSDT perp 1-hour klines (for ADX, carry, regime, price_feed)
  cd_spot_binance      — BTCUSDT spot 1-hour klines (for carry)
  cd_funding_rate      — BTCUSDT perp funding rate history (read via strategies.support.funding)
  cd_funding_rate_eth  — ETHUSDT perp funding rate history (read via strategies.support.funding)
  ca_long_short_ratio  — BTCUSDT + ETHUSDT global long/short account ratio (for regime CB + CPR signal).
                         Binance serves only ~30d of history per call; rolling refresh keeps the table
                         current as long as binance_feed runs at least monthly. Older history is
                         backfilled by fetch_coinalyze.py during bootstrap.

Does NOT maintain:
  scheduled_events     — static calendar (CPI/NFP/OPEX), built by fetch_events.py

Runs in three modes:
  python binance_feed.py --once              — fetch latest bars and exit
  python binance_feed.py                     — loop forever, refresh every 60s
  python binance_feed.py --backfill-klines   — one-shot historical kline backfill (slow)
  python binance_feed.py --backfill-funding  — one-shot historical funding backfill

Uses stdlib urllib (no requests/ccxt dependency)."""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DB_PATH = Path(__file__).resolve().parent / "data" / "trader.db"
SPOT_API = "https://api.binance.com/api/v3"
FAPI = "https://fapi.binance.com/fapi/v1"
# /futures/data/* endpoints are NOT under /fapi/v1/ — they live at the base.
FAPI_DATA = "https://fapi.binance.com/futures/data"

log = logging.getLogger("binance_feed")


# ─── HTTP ────────────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None, timeout: float = 20.0) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "p300-bot/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ─── Kline upserts ────────────────────────────────────────────────────────────

def _latest_open_time(con: sqlite3.Connection, table: str, ts_col: str) -> int:
    row = con.execute(f"SELECT MAX({ts_col}) FROM {table}").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def fetch_spot_klines_1m(symbol: str, table: str) -> int:
    """Fetch spot 1-minute klines and upsert into (btc_1m | eth_1m).
    open_time is in ms (matches the original schema). Returns rows inserted.

    Re-fetches the latest stored bar on every call so a partial bar that was
    locked in mid-minute (Binance's klines endpoint returns the still-forming
    bar with whatever trades have aggregated so far) gets overwritten with the
    completed bar on the next refresh. INSERT OR REPLACE handles the update."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        last_ms = _latest_open_time(con, table, "open_time")
        # Start AT (not after) last_ms so a partial latest bar is re-fetched.
        start_ms = last_ms if last_ms else None
        params: dict = {"symbol": symbol, "interval": "1m", "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{SPOT_API}/klines", params)
        inserted = 0
        for r in rows:
            ot = int(r[0])
            con.execute(
                f"INSERT OR REPLACE INTO {table} "
                f"(open_time, open, high, low, close, volume, num_trades) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ot, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 float(r[5]), int(r[8])),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def fetch_klines_1h(api_base: str, symbol: str, table: str) -> int:
    """Fetch latest 1h klines for `symbol` from `api_base` and upsert into `table`.
    Used for cd_futures_ohlcv (FAPI/BTCUSDT) and cd_spot_binance (SPOT/BTCUSDT).
    timestamp is stored in seconds (matches the original CoinDesk schema).

    Re-fetches the latest stored bar so a partial bar that was locked in
    mid-hour gets overwritten with the completed bar on the next refresh.
    Without this, every restart of binance_feed leaves a partially-formed
    1H bar in the table forever (the bar's close = the price ~1m into the
    hour instead of at the hour boundary)."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        last_s = _latest_open_time(con, table, "timestamp")
        # Start AT (not after) last_s so a partial latest bar is re-fetched.
        start_ms = last_s * 1000 if last_s else None
        params: dict = {"symbol": symbol, "interval": "1h", "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{api_base}/klines", params)
        inserted = 0
        for r in rows:
            ts_s = int(r[0]) // 1000
            # Binance kline indexes 5/7/9/10 are base-volume, quote-volume,
            # taker-buy-base-volume, taker-buy-quote-volume. Sell-side =
            # total minus taker-buy. trades_buy/_sell aren't in the klines
            # endpoint (would need aggTrades) — kept NULL.
            vol, qvol = float(r[5]), float(r[7])
            buy_base, buy_quote = float(r[9]), float(r[10])
            con.execute(
                f"INSERT OR REPLACE INTO {table} "
                "(timestamp, open, high, low, close, volume, quote_volume, "
                " volume_buy, quote_volume_buy, volume_sell, quote_volume_sell, "
                " total_trades, trades_buy, trades_sell) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (ts_s, float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                 vol, qvol, buy_base, buy_quote,
                 vol - buy_base, qvol - buy_quote, int(r[8])),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def fetch_futures_klines_1h() -> int:
    """Convenience wrapper: BTCUSDT perp 1h → cd_futures_ohlcv."""
    return fetch_klines_1h(FAPI, "BTCUSDT", "cd_futures_ohlcv")


def fetch_spot_klines_1h() -> int:
    """Convenience wrapper: BTCUSDT spot 1h → cd_spot_binance."""
    return fetch_klines_1h(SPOT_API, "BTCUSDT", "cd_spot_binance")


def _ensure_funding_table(con: sqlite3.Connection, table: str) -> None:
    """Create a funding table with the cd_funding_rate schema if missing."""
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        f"  timestamp INTEGER PRIMARY KEY,"
        f"  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )


def fetch_funding_rate(symbol: str = "BTCUSDT",
                       table: str = "cd_funding_rate") -> int:
    """Fetch perp funding history for `symbol`, upsert into `table`.
    Binance /fapi/v1/fundingRate publishes one row per 8h settlement; the
    `timestamp` column is in seconds. fr_close stores the settlement rate."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_funding_table(con, table)
        last_s = _latest_open_time(con, table, "timestamp")
        # Funding settles every 8h. The endpoint can return the row for an
        # in-progress settlement window; re-fetching the latest row keeps it
        # current. INSERT OR REPLACE updates in place.
        start_ms = last_s * 1000 if last_s else None
        params: dict = {"symbol": symbol, "limit": 1000}
        if start_ms:
            params["startTime"] = start_ms
        rows = _get(f"{FAPI}/fundingRate", params)
        inserted = 0
        for r in rows:
            ts_s = int(r["fundingTime"]) // 1000
            rate = float(r["fundingRate"])
            con.execute(
                f"INSERT OR REPLACE INTO {table} "
                f"(timestamp, fr_open, fr_high, fr_low, fr_close) "
                f"VALUES (?, ?, ?, ?, ?)",
                (ts_s, rate, rate, rate, rate),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def fetch_long_short_ratio(symbol: str = "BTCUSDT", asset: str = "BTC",
                            period: str = "1d") -> int:
    """Fetch globalLongShortAccountRatio for `symbol`, upsert into
    ca_long_short_ratio with the given `asset` tag.

    NOTE: Binance's /futures/data/globalLongShortAccountRatio only serves
    the LAST ~30 DAYS of history regardless of startTime. So we just fetch
    the most recent 500 rows (= last 500 days available, capped at ~30 by
    Binance) and INSERT OR REPLACE; rows older than 30 days remain whatever
    the previous run put there. For a longer warmup history use
    fetch_coinalyze.py (free tier, requires API key).

    Period values supported by the endpoint: 5m, 15m, 30m, 1h, 2h, 4h,
    6h, 12h, 1d. We use 1d to match the existing schema's daily cadence.
    """
    con = sqlite3.connect(str(DB_PATH))
    try:
        params = {"symbol": symbol, "period": period, "limit": 500}
        rows = _get(f"{FAPI_DATA}/globalLongShortAccountRatio", params)
        inserted = 0
        for r in rows:
            ts_s = int(r["timestamp"]) // 1000
            long_pct = float(r["longAccount"]) * 100  # Binance returns fractions
            short_pct = float(r["shortAccount"]) * 100
            ratio = float(r["longShortRatio"])
            con.execute(
                "INSERT OR REPLACE INTO ca_long_short_ratio "
                "(asset, timestamp, ratio, long_pct, short_pct) "
                "VALUES (?, ?, ?, ?, ?)",
                (asset, ts_s, ratio, long_pct, short_pct),
            )
            inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def _find_gaps(con: sqlite3.Connection, table: str, ts_col: str,
                cadence: int, now_ts: int,
                since_floor: int | None = None) -> list[tuple[int, int]]:
    """Return list of (start, end) gap windows where rows are missing.
    All units (cadence, now_ts, since_floor, returned tuples) are in the
    table's ts_col unit (seconds for hourly/funding/LSR; ms for *_1m).

    A gap (a, b) is INCLUSIVE — every multiple of `cadence` from a to b
    should exist but doesn't.

    Includes:
      - internal gaps between two existing rows
      - trailing gap from MAX(ts) + cadence to now_ts (if data is stale)
      - leading gap from since_floor to MIN(ts) - cadence (if requested)
      - if table is empty and since_floor is given, one gap (since_floor, now_ts)
    """
    rows = con.execute(f"""
        WITH ordered AS (
            SELECT {ts_col} AS ts,
                   LAG({ts_col}) OVER (ORDER BY {ts_col}) AS prev
            FROM {table}
        )
        SELECT prev, ts FROM ordered
        WHERE ts - prev > ?
        ORDER BY prev
    """, (cadence,)).fetchall()
    gaps = [(int(prev) + cadence, int(ts) - cadence) for prev, ts in rows]

    minmax = con.execute(
        f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {table}"
    ).fetchone()
    min_ts, max_ts = minmax

    if max_ts is None:
        # Empty table — one giant gap if since_floor is specified.
        if since_floor is not None and since_floor < now_ts:
            gaps.append((since_floor, now_ts))
        return sorted(gaps)

    max_ts_i = int(max_ts)
    min_ts_i = int(min_ts)
    if now_ts - max_ts_i > cadence:
        gaps.append((max_ts_i + cadence, now_ts))
    if since_floor is not None and min_ts_i - since_floor > cadence:
        gaps.append((since_floor, min_ts_i - cadence))
    return sorted(gaps)


def _missing_count(gaps: list[tuple[int, int]], cadence: int) -> int:
    return sum((end - start) // cadence + 1 for start, end in gaps)


def backfill_funding_rate(symbol: str, table: str,
                          since: str | None = "2020-01-01") -> int:
    """Detect + fill all gaps in `table` (cd_funding_rate / cd_funding_rate_eth).
    `since` is an optional leading floor; with `since=None`, only existing
    gaps and the trailing (MAX -> now) window are filled.

    Funding settles every 8h; we use that as cadence. Idempotent via
    INSERT OR REPLACE on timestamp."""
    import time as _time
    cadence_s = 28800  # 8h
    now_s = int(datetime.now(timezone.utc).timestamp())
    floor_s = (int(datetime.fromisoformat(f"{since}T00:00:00+00:00").timestamp())
               if since else None)
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_funding_table(con, table)
        gaps = _find_gaps(con, table, "timestamp", cadence_s, now_s, floor_s)
        if not gaps:
            return 0
        log.info(f"[backfill {table}] {len(gaps)} gap(s), "
                 f"~{_missing_count(gaps, cadence_s):,} rows to fill")
        total = 0
        for i, (gap_start, gap_end) in enumerate(gaps, start=1):
            cursor = gap_start
            while cursor <= gap_end:
                params = {"symbol": symbol,
                          "startTime": cursor * 1000,
                          "endTime": gap_end * 1000,
                          "limit": 1000}
                try:
                    rows = _get(f"{FAPI}/fundingRate", params)
                except Exception as e:
                    log.warning(f"[backfill {table}] gap {i} fetch failed: {e}")
                    break
                if not rows:
                    break
                for r in rows:
                    ts_s = int(r["fundingTime"]) // 1000
                    rate = float(r["fundingRate"])
                    con.execute(
                        f"INSERT OR REPLACE INTO {table} "
                        f"(timestamp, fr_open, fr_high, fr_low, fr_close) "
                        f"VALUES (?, ?, ?, ?, ?)",
                        (ts_s, rate, rate, rate, rate),
                    )
                con.commit()
                total += len(rows)
                next_cursor = (int(rows[-1]["fundingTime"]) // 1000) + 1
                if next_cursor <= cursor:
                    break  # no forward progress
                cursor = next_cursor
                if len(rows) < 1000:
                    break
                _time.sleep(0.2)
            if i % 50 == 0 or i == len(gaps):
                log.info(f"[backfill {table}] {i}/{len(gaps)} gaps done, "
                         f"+{total:,} rows")
    finally:
        con.close()
    return total


def backfill_klines_1m(symbol: str, table: str,
                        since: str | None = "2020-01-01") -> int:
    """Detect + fill all gaps in `table` (btc_1m or eth_1m). `since` is an
    optional leading floor; with `since=None`, only existing internal gaps
    and the trailing (MAX -> now) window are filled.

    First run on a sparse table (e.g. 93% missing) can take ~20 minutes
    per symbol (3,000+ paginated calls @ 0.2s pacing). Subsequent runs
    only fill new gaps and are near-instant."""
    import time as _time
    cadence_ms = 60_000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    floor_ms = (int(datetime.fromisoformat(f"{since}T00:00:00+00:00").timestamp() * 1000)
                if since else None)
    con = sqlite3.connect(str(DB_PATH))
    try:
        gaps = _find_gaps(con, table, "open_time", cadence_ms, now_ms, floor_ms)
        if not gaps:
            return 0
        log.info(f"[backfill {table}] {len(gaps)} gap(s), "
                 f"~{_missing_count(gaps, cadence_ms):,} rows to fill")
        total = 0
        for i, (gap_start, gap_end) in enumerate(gaps, start=1):
            cursor = gap_start
            while cursor <= gap_end:
                params = {"symbol": symbol, "interval": "1m",
                          "startTime": cursor, "endTime": gap_end, "limit": 1000}
                try:
                    rows = _get(f"{SPOT_API}/klines", params)
                except Exception as e:
                    log.warning(f"[backfill {table}] gap {i} fetch failed: {e}")
                    break
                if not rows:
                    break
                con.executemany(
                    f"INSERT OR REPLACE INTO {table} "
                    f"(open_time, open, high, low, close, volume, num_trades) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(int(r[0]), float(r[1]), float(r[2]), float(r[3]),
                      float(r[4]), float(r[5]), int(r[8])) for r in rows],
                )
                con.commit()
                total += len(rows)
                next_cursor = int(rows[-1][0]) + cadence_ms
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
                if len(rows) < 1000:
                    break
                _time.sleep(0.2)
            if i % 50 == 0 or i == len(gaps):
                log.info(f"[backfill {table}] {i}/{len(gaps)} gaps done, "
                         f"+{total:,} rows")
    finally:
        con.close()
    return total


def backfill_klines_1h(api_base: str, symbol: str, table: str,
                        since: str | None = "2020-01-01") -> int:
    """Detect + fill all gaps in `table` (cd_futures_ohlcv or cd_spot_binance).
    `since` is an optional leading floor."""
    import time as _time
    cadence_s = 3600
    now_s = int(datetime.now(timezone.utc).timestamp())
    floor_s = (int(datetime.fromisoformat(f"{since}T00:00:00+00:00").timestamp())
               if since else None)
    con = sqlite3.connect(str(DB_PATH))
    try:
        gaps = _find_gaps(con, table, "timestamp", cadence_s, now_s, floor_s)
        if not gaps:
            return 0
        log.info(f"[backfill {table}] {len(gaps)} gap(s), "
                 f"~{_missing_count(gaps, cadence_s):,} rows to fill")
        total = 0
        for i, (gap_start, gap_end) in enumerate(gaps, start=1):
            cursor = gap_start
            while cursor <= gap_end:
                params = {"symbol": symbol, "interval": "1h",
                          "startTime": cursor * 1000,
                          "endTime": gap_end * 1000,
                          "limit": 1000}
                try:
                    rows = _get(f"{api_base}/klines", params)
                except Exception as e:
                    log.warning(f"[backfill {table}] gap {i} fetch failed: {e}")
                    break
                if not rows:
                    break
                con.executemany(
                    f"INSERT OR REPLACE INTO {table} "
                    "(timestamp, open, high, low, close, volume, quote_volume, "
                    " volume_buy, quote_volume_buy, volume_sell, quote_volume_sell, "
                    " total_trades, trades_buy, trades_sell) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    [(int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]),
                      float(r[4]), float(r[5]), float(r[7]),
                      float(r[9]), float(r[10]),
                      float(r[5]) - float(r[9]), float(r[7]) - float(r[10]),
                      int(r[8]))
                     for r in rows],
                )
                con.commit()
                total += len(rows)
                next_cursor = (int(rows[-1][0]) // 1000) + cadence_s
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
                if len(rows) < 1000:
                    break
                _time.sleep(0.2)
            if i % 50 == 0 or i == len(gaps):
                log.info(f"[backfill {table}] {i}/{len(gaps)} gaps done, "
                         f"+{total:,} rows")
    finally:
        con.close()
    return total


def _find_null_taker_ranges(con: sqlite3.Connection, table: str,
                              cadence_s: int) -> list[tuple[int, int]]:
    """Contiguous (inclusive) timestamp ranges where volume_buy IS NULL.
    Two NULL rows separated by more than one bar's cadence become separate
    ranges so we don't re-fetch large stretches of already-populated bars."""
    rows = con.execute(
        f"SELECT timestamp FROM {table} "
        f"WHERE volume_buy IS NULL ORDER BY timestamp"
    ).fetchall()
    if not rows:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = int(rows[0][0])
    for (ts,) in rows[1:]:
        ts = int(ts)
        if ts - prev <= cadence_s:
            prev = ts
            continue
        ranges.append((start, prev))
        start = prev = ts
    ranges.append((start, prev))
    return ranges


def repair_null_taker_volumes(api_base: str, symbol: str, table: str) -> int:
    """Repair rows in `table` where volume_buy IS NULL by re-fetching Binance
    klines for those ranges and overwriting the bar in place.

    Uses UPDATE … WHERE volume_buy IS NULL — only touches NULL-taker rows so
    older rows ingested from a different upstream stay untouched. Refreshes
    OHLC + volume + taker columns together so the buy+sell=volume invariant
    holds (some NULL-taker rows were partial-bar writes from the running
    bot whose volume field is also stale). Idempotent: subsequent calls find
    no NULLs and return 0."""
    import time as _time
    cadence_s = 3600
    con = sqlite3.connect(str(DB_PATH))
    try:
        ranges = _find_null_taker_ranges(con, table, cadence_s)
        if not ranges:
            return 0
        expected = sum((e - s) // cadence_s + 1 for s, e in ranges)
        log.info(f"[repair {table}] {len(ranges)} NULL taker range(s), "
                 f"~{expected:,} rows to fill")
        total = 0
        for i, (rng_start, rng_end) in enumerate(ranges, start=1):
            cursor = rng_start
            while cursor <= rng_end:
                params = {"symbol": symbol, "interval": "1h",
                          "startTime": cursor * 1000,
                          "endTime": rng_end * 1000,
                          "limit": 1000}
                try:
                    rows = _get(f"{api_base}/klines", params)
                except Exception as e:
                    log.warning(f"[repair {table}] range {i} fetch failed: {e}")
                    break
                if not rows:
                    break
                con.executemany(
                    f"UPDATE {table} "
                    "SET open = ?, high = ?, low = ?, close = ?, "
                    "    volume = ?, quote_volume = ?, "
                    "    volume_buy = ?, quote_volume_buy = ?, "
                    "    volume_sell = ?, quote_volume_sell = ?, "
                    "    total_trades = ? "
                    "WHERE timestamp = ? AND volume_buy IS NULL",
                    [(float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                      float(r[5]), float(r[7]),
                      float(r[9]), float(r[10]),
                      float(r[5]) - float(r[9]), float(r[7]) - float(r[10]),
                      int(r[8]),
                      int(r[0]) // 1000)
                     for r in rows],
                )
                con.commit()
                total += con.total_changes  # approximate; useful for log only
                next_cursor = (int(rows[-1][0]) // 1000) + cadence_s
                if next_cursor <= cursor:
                    break
                cursor = next_cursor
                if len(rows) < 1000:
                    break
                _time.sleep(0.2)
            if i % 50 == 0 or i == len(ranges):
                log.info(f"[repair {table}] {i}/{len(ranges)} ranges done")
        # total_changes is cumulative across the connection; report the
        # NULL count delta as the more meaningful number.
        remaining = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE volume_buy IS NULL"
        ).fetchone()[0]
        filled = expected - remaining
        log.info(f"[repair {table}] filled {filled:,} of {expected:,} NULL rows")
        return filled
    finally:
        con.close()


def backfill_all_klines(since: str | None = "2020-01-01") -> dict[str, int]:
    """Run gap-aware backfill for all four kline tables. With `since` set,
    extends history back to that floor; with `since=None`, only fills
    existing gaps + trailing window."""
    out: dict[str, int] = {}
    for sym, tbl in [("BTCUSDT", "btc_1m"), ("ETHUSDT", "eth_1m")]:
        out[tbl] = backfill_klines_1m(sym, tbl, since=since)
    out["cd_futures_ohlcv"] = backfill_klines_1h(FAPI, "BTCUSDT",
                                                  "cd_futures_ohlcv", since=since)
    out["cd_spot_binance"] = backfill_klines_1h(SPOT_API, "BTCUSDT",
                                                 "cd_spot_binance", since=since)
    return out


def fix_all_gaps() -> dict[str, int]:
    """Detect + fill gaps in all kline + funding tables. Same machinery as
    backfill but with no leading floor — only heals existing data, doesn't
    extend history. Wired into binance_feed.py startup so the DB self-heals
    every time the bot is restarted.

    Also repairs rows where the taker-buy/sell columns are NULL (legacy data
    from before the kline fetcher learned to populate those fields). The
    NULL-repair is idempotent — once-and-done after the first startup."""
    out = backfill_all_klines(since=None)
    out["repair_cd_futures_ohlcv"] = repair_null_taker_volumes(
        FAPI, "BTCUSDT", "cd_futures_ohlcv")
    out["repair_cd_spot_binance"] = repair_null_taker_volumes(
        SPOT_API, "BTCUSDT", "cd_spot_binance")
    for sym, tbl in [("BTCUSDT", "cd_funding_rate"),
                     ("ETHUSDT", "cd_funding_rate_eth")]:
        out[tbl] = backfill_funding_rate(sym, tbl, since=None)
    return out


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def refresh_all() -> dict[str, int]:
    results: dict[str, int] = {}
    for sym, table in [("BTCUSDT", "btc_1m"), ("ETHUSDT", "eth_1m")]:
        try:
            n = fetch_spot_klines_1m(sym, table)
            results[table] = n
        except Exception as e:
            log.warning(f"{table} fetch failed: {e}")
            results[table] = -1
    try:
        results["cd_futures_ohlcv"] = fetch_futures_klines_1h()
    except Exception as e:
        log.warning(f"cd_futures_ohlcv fetch failed: {e}")
        results["cd_futures_ohlcv"] = -1
    try:
        results["cd_spot_binance"] = fetch_spot_klines_1h()
    except Exception as e:
        log.warning(f"cd_spot_binance fetch failed: {e}")
        results["cd_spot_binance"] = -1
    for sym, table in [("BTCUSDT", "cd_funding_rate"),
                        ("ETHUSDT", "cd_funding_rate_eth")]:
        try:
            results[table] = fetch_funding_rate(sym, table)
        except Exception as e:
            log.warning(f"{table} fetch failed: {e}")
            results[table] = -1
    # ca_long_short_ratio — Binance only serves last ~30d but that's enough
    # to keep the table fresh as long as binance_feed runs at least monthly.
    for sym, asset in [("BTCUSDT", "BTC"), ("ETHUSDT", "ETH")]:
        try:
            results[f"ls_{asset}"] = fetch_long_short_ratio(sym, asset)
        except Exception as e:
            log.warning(f"long-short ratio {asset} fetch failed: {e}")
            results[f"ls_{asset}"] = -1

    # Daily-cadence external feeds (FOMC sleeve inputs). These rate-limit
    # themselves to once per UTC day so the per-minute refresh_all() doesn't
    # hammer the upstreams.
    try:
        results["fear_greed"] = 1 if _refresh_daily_external(
            "fear_greed",
            lambda: __import__("services.sentiment_index_service",
                                fromlist=["refresh"]).refresh()) else 0
    except Exception as e:
        log.warning(f"fear_greed refresh failed: {e}")
        results["fear_greed"] = -1
    try:
        results["fed_funds"] = 1 if _refresh_daily_external(
            "fed_funds",
            lambda: __import__("services.fed_funds_service",
                                fromlist=["refresh_xml"]).refresh_xml()) else 0
    except Exception as e:
        log.warning(f"fed_funds refresh failed: {e}")
        results["fed_funds"] = -1
    try:
        results["polymarket_fed"] = 1 if _refresh_daily_external(
            "polymarket_fed",
            lambda: __import__("services.polymarket_service",
                                fromlist=["refresh"]).refresh()) else 0
    except Exception as e:
        log.warning(f"polymarket_fed refresh failed: {e}")
        results["polymarket_fed"] = -1
    # AI_QUANT news headlines — hourly cadence, throttled inside the fetcher
    # itself (not the daily-external helper). Silent no-op if CRYPTOPANIC_TOKEN
    # is unset, so this is safe to leave wired even on installs that don't use
    # the AI_QUANT sleeve.
    try:
        results["news_headlines"] = __import__(
            "services.news_fetcher", fromlist=["refresh"]).refresh()
    except Exception as e:
        log.warning(f"news_fetcher refresh failed: {e}")
        results["news_headlines"] = -1
    # AI_QUANT derivatives data — CoinDesk Data API: OI, liquidations, DVOL.
    # Throttled to once per hour inside the fetcher. Free public endpoints,
    # no auth, so safe to leave wired even on installs that don't use AI_QUANT.
    try:
        cd = __import__("services.coindesk_fetcher",
                          fromlist=["refresh"]).refresh()
        for k, v in cd.items():
            results[f"cd_{k}"] = v
    except Exception as e:
        log.warning(f"coindesk_fetcher refresh failed: {e}")
        results["cd_open_interest"] = -1
    return results


# Tracks the last UTC date on which each daily-cadence external feed
# successfully refreshed. Keys are the feed names used in refresh_all().
_last_external_refresh_day: dict[str, str] = {}


def _refresh_daily_external(name: str, fn) -> bool:
    """Run `fn()` at most once per UTC day. Returns True on a successful
    refresh, False if either skipped (cached) or failed.

    `fn` should return truthy on success. Failures don't update the
    cached date so we'll retry next tick."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _last_external_refresh_day.get(name) == today:
        return False  # already refreshed today
    ok = bool(fn())
    if ok:
        _last_external_refresh_day[name] = today
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Binance feed updater for trader.db")
    ap.add_argument("--once", action="store_true",
                    help="Fetch once and exit (default: loop every 60s)")
    ap.add_argument("--interval", type=int, default=60,
                    help="Loop interval seconds (default 60)")
    ap.add_argument("--backfill-funding", action="store_true",
                    help="One-shot: backfill cd_funding_rate (BTC) + "
                         "cd_funding_rate_eth (ETH) from --since to present.")
    ap.add_argument("--backfill-klines", action="store_true",
                    help="One-shot: backfill btc_1m, eth_1m, cd_futures_ohlcv, "
                         "and cd_spot_binance from --since to present. Slow — "
                         "expect ~30-60 minutes for 5 years.")
    ap.add_argument("--since", default="2020-01-01",
                    help="Backfill start date (UTC, YYYY-MM-DD). Default 2020-01-01.")
    ap.add_argument("--skip-gap-fix", action="store_true",
                    help="Skip the startup gap-detection pass. Default is to "
                         "run it once at every startup so the DB self-heals.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if not DB_PATH.exists():
        log.error(f"{DB_PATH} not found -- run `python bootstrap.py` first")
        return 1

    # Explicit backfill modes do their own gap-aware fill, no need for the
    # auto pass on top.
    if args.backfill_funding:
        for sym, tbl in [("BTCUSDT", "cd_funding_rate"),
                         ("ETHUSDT", "cd_funding_rate_eth")]:
            n = backfill_funding_rate(sym, tbl, since=args.since)
            log.info(f"backfilled {tbl}: {n} rows since {args.since}")
        return 0

    if args.backfill_klines:
        backfill_all_klines(since=args.since)
        return 0

    # Auto gap-fix at startup. First run on a sparse DB can take ~20 min;
    # subsequent runs are near-instant (gaps should be <1k rows).
    if not args.skip_gap_fix:
        log.info("=== startup gap fix ===")
        res = fix_all_gaps()
        nonzero = {k: v for k, v in res.items() if v}
        if nonzero:
            summary = ", ".join(f"{k}:+{v:,}" for k, v in nonzero.items())
            log.info(f"gap fix complete -- {summary}")
        else:
            log.info("gap fix complete -- no gaps")

    while True:
        r = refresh_all()
        summary = ", ".join(f"{k}:{v}" for k, v in r.items())
        log.info(f"feed tick -- {summary}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
