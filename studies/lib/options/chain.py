"""Option-chain access on p300's Deribit tables (read-only).

Tables: `deribit_options_instruments` (instrument, asset, option_type CALL/PUT,
strike, expiry_ts) and `deribit_options_daily` (instrument, timestamp,
mark_price_usd, ...). Seeded rows (source='coindesk_seed') carry the USD mark
the trader repo's cd_options_oi.close_mark_price held, one row per UTC day at
00:00; live rows (source='deribit') are 00:00/08:00 snapshots. All pickers
use "the last mark within the UTC day", so both shapes work.

The selection logic mirrors trader research/probe_vrp_straddle.py exactly
(entry-day AND terminal-day marks required for every leg, nearest strike to
the target, expiry-day window = [expiry day 00:00, +2 days)).
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from strategies.support import db  # noqa: E402

INST_TABLE = "deribit_options_instruments"
DAILY_TABLE = "deribit_options_daily"
DAY = 86400


def connect_ro() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)


def load_spot_daily(asset: str = "BTC", since: str = "2023-01-01") -> dict[str, float]:
    """{date_iso: last close of the UTC day} from the hourly perp table (BTC)
    or eth_1m (ETH) — the trader used cd_futures_ohlcv as the BTC index proxy."""
    con = connect_ro()
    try:
        since_s = int(datetime.fromisoformat(since + "T00:00:00+00:00").timestamp())
        if asset == "BTC":
            rows = con.execute(
                "SELECT timestamp, close FROM cd_futures_ohlcv WHERE timestamp >= ? "
                "ORDER BY timestamp", (since_s,)).fetchall()
            out = {}
            for ts, close in rows:
                if close and close > 0:
                    out[datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()] = float(close)
            return out
        rows = con.execute(
            "SELECT open_time, close FROM eth_1m WHERE open_time >= ? ORDER BY open_time",
            (since_s * 1000,)).fetchall()
        out = {}
        for ts_ms, close in rows:
            if close and close > 0:
                out[datetime.fromtimestamp(ts_ms // 1000, tz=timezone.utc).date().isoformat()] = float(close)
        return out
    finally:
        con.close()


def load_spot_at_hour(asset: str = "BTC", hour: int = 8, since: str = "2023-01-01") -> dict[str, float]:
    """{date_iso: price at HH:00 UTC} = close of the hourly cd_futures_ohlcv bar
    that opens at HH−1. Deribit settles at 08:00 UTC, so hour=8 is the
    settlement proxy (BTC only)."""
    if asset != "BTC":
        raise NotImplementedError("hourly settlement proxy exists for BTC only (cd_futures_ohlcv)")
    con = connect_ro()
    try:
        since_s = int(datetime.fromisoformat(since + "T00:00:00+00:00").timestamp())
        out: dict[str, float] = {}
        for ts, close in con.execute(
                "SELECT timestamp, close FROM cd_futures_ohlcv WHERE timestamp >= ? "
                "AND (timestamp % 86400) = ? ORDER BY timestamp", (since_s, (hour - 1) * 3600)):
            if close and close > 0:
                out[datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()] = float(close)
        return out
    finally:
        con.close()


def find_expiries(con: sqlite3.Connection, asset: str = "BTC",
                  min_strikes: int = 5) -> list[tuple[int, str, int, int]]:
    """Expiries with >= min_strikes calls and puts that have daily data.
    Returns [(expiry_ts, expiry_date_iso, n_calls, n_puts)] ascending."""
    rows = con.execute(f"""
        SELECT i.expiry_ts,
               SUM(CASE WHEN i.option_type='CALL' THEN 1 ELSE 0 END) AS nc,
               SUM(CASE WHEN i.option_type='PUT'  THEN 1 ELSE 0 END) AS np
        FROM {INST_TABLE} i
        WHERE i.asset = ?
          AND EXISTS (SELECT 1 FROM {DAILY_TABLE} o WHERE o.instrument = i.instrument)
        GROUP BY i.expiry_ts HAVING nc >= ? AND np >= ?
        ORDER BY i.expiry_ts""", (asset, min_strikes, min_strikes)).fetchall()
    return [(int(ts), datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(),
             int(nc), int(np)) for ts, nc, np in rows]


def _mark_in(con: sqlite3.Connection, instrument: str, start_ts: int, end_ts: int,
             inclusive_end: bool = False) -> float | None:
    op = "<=" if inclusive_end else "<"
    row = con.execute(
        f"SELECT mark_price_usd FROM {DAILY_TABLE} WHERE instrument=? AND timestamp >= ? "
        f"AND timestamp {op} ? AND mark_price_usd IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
        (instrument, start_ts, end_ts)).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _entry_window(entry_date_iso: str) -> tuple[int, int]:
    t0 = int(datetime.fromisoformat(entry_date_iso + "T00:00:00+00:00").timestamp())
    return t0, t0 + DAY


def _expiry_window(expiry_ts: int) -> tuple[int, int]:
    start = (expiry_ts // DAY) * DAY
    return start, start + 2 * DAY


def get_terminal_value(con: sqlite3.Connection, instrument: str, expiry_ts: int) -> float | None:
    """USD mark on the expiry day (= settlement value; the trader repo found the
    separate `settlement` field was unrelated)."""
    s, e = _expiry_window(expiry_ts)
    return _mark_in(con, instrument, s, e, inclusive_end=True)


def _strikes_for(con: sqlite3.Connection, asset: str, expiry_ts: int):
    return con.execute(
        f"SELECT instrument, option_type, strike FROM {INST_TABLE} "
        "WHERE asset=? AND expiry_ts=?", (asset, expiry_ts)).fetchall()


def find_atm_pair(con: sqlite3.Connection, asset: str, expiry_ts: int,
                  entry_date_iso: str, spot: float):
    """Nearest-to-spot strike whose CALL and PUT both have entry-day and
    terminal-day marks. Returns (strike, call_inst, put_inst, call_mark, put_mark)."""
    by_strike: dict[float, dict] = defaultdict(dict)
    for inst, otype, strike in _strikes_for(con, asset, expiry_ts):
        by_strike[float(strike)][otype] = inst
    e0, e1 = _entry_window(entry_date_iso)
    x0, x1 = _expiry_window(expiry_ts)
    for strike in sorted(by_strike, key=lambda k: abs(k - spot)):
        pair = by_strike[strike]
        if "CALL" not in pair or "PUT" not in pair:
            continue
        cm = _mark_in(con, pair["CALL"], e0, e1)
        pm = _mark_in(con, pair["PUT"], e0, e1)
        if not (cm and pm):
            continue
        if _mark_in(con, pair["CALL"], x0, x1, True) is None or _mark_in(con, pair["PUT"], x0, x1, True) is None:
            continue
        return strike, pair["CALL"], pair["PUT"], cm, pm
    return None


def find_strangle_pair(con: sqlite3.Connection, asset: str, expiry_ts: int,
                       entry_date_iso: str, spot: float,
                       call_offset: float, put_offset: float,
                       require_terminal: bool = True):
    """CALL nearest spot×(1+call_offset), PUT nearest spot×(1−put_offset),
    picked independently; each leg needs an entry-day mark and (trader
    convention, require_terminal=True) an expiry-day mark.
    Returns (strike_call, strike_put, call_inst, put_inst, call_mark, put_mark)."""
    rows = _strikes_for(con, asset, expiry_ts)
    calls = sorted(((float(s), i) for i, t, s in rows if t == "CALL"),
                   key=lambda x: abs(x[0] - spot * (1 + call_offset)))
    puts = sorted(((float(s), i) for i, t, s in rows if t == "PUT"),
                  key=lambda x: abs(x[0] - spot * (1 - put_offset)))
    e0, e1 = _entry_window(entry_date_iso)
    x0, x1 = _expiry_window(expiry_ts)

    def first_valid(cands):
        for strike, inst in cands:
            entry = _mark_in(con, inst, e0, e1)
            if entry and (not require_terminal or _mark_in(con, inst, x0, x1, True) is not None):
                return strike, inst, entry
        return None

    c = first_valid(calls)
    p = first_valid(puts)
    if c is None or p is None:
        return None
    return c[0], p[0], c[1], p[1], c[2], p[2]


def load_marks(con: sqlite3.Connection, instrument: str) -> dict[str, float]:
    """{date_iso: last USD mark of that UTC day} for one instrument."""
    out: dict[str, float] = {}
    for ts, mark in con.execute(
            f"SELECT timestamp, mark_price_usd FROM {DAILY_TABLE} WHERE instrument=? "
            "AND mark_price_usd IS NOT NULL AND mark_price_usd > 0 ORDER BY timestamp",
            (instrument,)):
        out[datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()] = float(mark)
    return out


def spot_near(spot: dict[str, float], date_iso: str) -> tuple[str, float] | None:
    """Spot on the date or the nearest of ±1, ±2 days (trader convention)."""
    if date_iso in spot:
        return date_iso, spot[date_iso]
    d = datetime.fromisoformat(date_iso)
    for off in (1, -1, 2, -2):
        alt = (d + timedelta(days=off)).date().isoformat()
        if alt in spot:
            return alt, spot[alt]
    return None
