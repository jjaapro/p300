"""Shared library for the R4 calendar-window study.

Loads BTC + ETH hourly bars from trader.db and computes
parameterized window returns. Each script in tools/r4_study/ imports
from here so the data path / return semantics stay consistent.

Window definition:
  - Enter at (date_iso, entry_hour) using that bar's OPEN price.
  - Hold for `hold_hours` hours, possibly crossing midnight.
  - Exit at (date_iso + offset_days, exit_hour) using that bar's OPEN.
  - Filter by weekday set (Mon=0..Sun=6) and by a day-of-month predicate.

This matches the convention in strategies/sleeves/r4/math.py where the live R4 strategies
use the open of the entry hour as the fill and the open of the exit
hour as the close-out (NOT the close of either bar).

Returns are GROSS — fees applied externally.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "trader.db"

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

WEEK_FILTERS = {
    "all":   lambda d: True,
    "wk1":   lambda d: d <= 7,
    "wk2":   lambda d: 8 <= d <= 14,
    "wk1-2": lambda d: d <= 14,
    "wk3":   lambda d: 15 <= d <= 21,
    "wk4+":  lambda d: d >= 22,
    "wk2-3": lambda d: 8 <= d <= 21,
    "wk1-3": lambda d: d <= 21,
}

# Structural events used as era boundaries.
BINANCE_FUT_LAUNCH = "2019-09-08"
BTC_SPOT_ETF       = "2024-01-11"
ETH_SPOT_ETF       = "2024-07-23"


def load_btc_hourly() -> dict[tuple[str, int], tuple[float, float]]:
    """Return {(date_iso, hour): (open, close)} from cd_spot_binance."""
    out: dict[tuple[str, int], tuple[float, float]] = {}
    con = sqlite3.connect(str(DB))
    try:
        for ts, o, c in con.execute(
            "SELECT timestamp, open, close FROM cd_spot_binance"
        ):
            if o is None or c is None or float(o) <= 0:
                continue
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            out[(dt.date().isoformat(), dt.hour)] = (float(o), float(c))
    finally:
        con.close()
    return out


def load_eth_hourly() -> dict[tuple[str, int], tuple[float, float]]:
    """Return {(date_iso, hour): (open, close)} aggregated from eth_1m."""
    con = sqlite3.connect(str(DB))
    try:
        rows = con.execute(
            "SELECT open_time, open, close FROM eth_1m ORDER BY open_time"
        ).fetchall()
    finally:
        con.close()
    buckets: dict[tuple[str, int], list[tuple[int, float, float]]] = defaultdict(list)
    for ot_ms, o, c in rows:
        if o is None or c is None or float(o) <= 0:
            continue
        dt = datetime.fromtimestamp(int(ot_ms) // 1000, tz=timezone.utc)
        buckets[(dt.date().isoformat(), dt.hour)].append((int(ot_ms), float(o), float(c)))
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for k, lst in buckets.items():
        lst.sort(key=lambda x: x[0])
        out[k] = (lst[0][1], lst[-1][2])
    return out


def window_returns(by_hour: dict, weekdays: set[int], week_fn,
                    entry_hour: int, hold_hours: int) -> dict[str, float]:
    """Compute decimal returns for the parameterized window.

    Returns dict keyed by ENTRY date_iso. Skips windows where either bar
    is missing (e.g. exchange downtime), keeping the sample honest.
    """
    out: dict[str, float] = {}
    exit_offset = (entry_hour + hold_hours) // 24
    exit_h = (entry_hour + hold_hours) % 24
    for (d, h), (o, _c) in by_hour.items():
        if h != entry_hour:
            continue
        try:
            dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt.weekday() not in weekdays:
            continue
        if not week_fn(dt.day):
            continue
        ed = (dt + timedelta(days=exit_offset)).date().isoformat()
        if (ed, exit_h) not in by_hour or o <= 0:
            continue
        out[d] = (by_hour[(ed, exit_h)][0] - o) / o
    return out


def filter_window(rd: dict[str, float], start_iso: str,
                   end_iso: str) -> dict[str, float]:
    """Slice a returns dict to a date window, inclusive on both ends."""
    return {d: r for d, r in rd.items() if start_iso <= d <= end_iso}


def stats(rd: dict[str, float]) -> dict | None:
    """Summary stats: n, mean%, std%, win%, cum%, t-stat. None if empty."""
    rs = list(rd.values())
    n = len(rs)
    if n == 0:
        return None
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / max(1, n - 1)
    std = math.sqrt(var)
    wr = 100 * sum(1 for r in rs if r > 0) / n
    cum = sum(rs)
    t = mean / (std / math.sqrt(n)) if std > 0 else 0.0
    return {"n": n, "mean": mean * 100, "std": std * 100,
            "wr": wr, "cum": cum * 100, "t": t}


def fmt_config(weekdays: set[int], week_filter: str,
                entry_hour: int, hold_hours: int) -> str:
    days = "+".join(WEEKDAY_NAMES[d] for d in sorted(weekdays))
    exit_h = (entry_hour + hold_hours) % 24
    next_day = "+1d " if (entry_hour + hold_hours) >= 24 else ""
    return (f"{days:<10s} {week_filter:<6s} "
            f"{entry_hour:02d}->{next_day}{exit_h:02d} ({hold_hours:>2d}h)")
