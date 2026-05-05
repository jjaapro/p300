"""services.funding — single source of truth for perp funding-rate access.

Replaces three separate per-caller implementations that previously lived in:
  services/funding_util.py:accrued_funding_pct           (point lookup over a window)
  services/carry_service.py:_load_recent_daily_funding   (daily sum)
  services/cpr_service.py:_load_funding_daily            (daily mean)

All three had their own SQL against cd_funding_rate / cd_funding_rate_eth.
Two of the three had the 8x-inflation bug found 2026-05-04 (fixed in commit
2ca7cdc); the third was structurally fine but only because it used AVG, not
SUM. The duplication made the bug hard to spot. This module centralizes the
access so the bug class is structurally hard to reintroduce — anyone reading
funding data in this codebase imports from here, not raw SQL.

Convention (Binance perpetual):
  funding pays LONGS  when rate < 0  (longs receive when rate negative)
  funding pays SHORTS when rate > 0  (shorts receive when rate positive)

Stored data:
  cd_funding_rate / cd_funding_rate_eth: timestamp INT (unix seconds), fr_close REAL
  Cadence is mixed:
    - Pre-2026-04-25: hourly OHLC (24 rows/day, CryptoDataDownload bulk import)
    - Post-2026-04-25:  8h-only  (3 rows/day, live binance_feed.fetch_funding_rate)
  All public functions filter `WHERE timestamp % 28800 = 0` so each settlement
  is counted exactly once regardless of stored cadence.

Units (stored fr_close is a DECIMAL fraction, e.g. 0.0001 = 0.01% per 8h):
  accrued_pct       returns PERCENT of notional, signed for direction.
  daily_sums_pct    returns PERCENT, unsigned (raw funding rate direction).
  daily_means_rate  returns DECIMAL fraction (the raw stored unit).
The function name documents the unit; callers convert as needed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from services import db

log = logging.getLogger("dashboard.funding")

# Module-level constant — overridable in tests via monkeypatch.
# Binance funding settles 3x/day at 00:00, 08:00, 16:00 UTC. As Unix seconds
# these are exact multiples of SETTLEMENT_PERIOD_SECONDS.
SETTLEMENT_PERIOD_SECONDS = 8 * 3600

_TABLE_FOR_ASSET = {
    "BTC": "cd_funding_rate",
    "ETH": "cd_funding_rate_eth",
}


# ─── Internal helpers ────────────────────────────────────────────────────────

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _table_for(asset: str) -> str | None:
    return _TABLE_FOR_ASSET.get(asset.upper())


def _connect_or_warn(asset: str, table: str) -> sqlite3.Connection | None:
    """Open a DB connection and verify the table exists. Returns None and
    logs a warning if the table is missing (defensive — callers see no
    funding rather than crashing)."""
    con = sqlite3.connect(str(db.TRADER_DB))
    if not _table_exists(con, table):
        con.close()
        log.warning(
            f"[funding] {table} not present — funding treated as 0 for "
            f"{asset}. Run binance_feed.py --backfill-funding to populate."
        )
        return None
    return con


# ─── Public API ──────────────────────────────────────────────────────────────

def accrued_pct(asset: str, start_dt: datetime, end_dt: datetime,
                direction: str) -> float:
    """Cumulative funding P&L over (start_dt, end_dt] as PERCENT of notional,
    signed for direction.

    Use to add funding cost/income to per-trade P&L on close::

        funding_pct = funding.accrued_pct(asset, entry_dt, exit_dt, direction)
        funding_usdt = size_usdt * funding_pct / 100.0
        pnl_usdt = price_pnl - fees + funding_usdt

    Sign convention:
        LONG  + rate>0 -> negative (LONG  pays  funding)
        LONG  + rate<0 -> positive (LONG  earns funding)
        SHORT + rate>0 -> positive (SHORT earns funding)
        SHORT + rate<0 -> negative (SHORT pays  funding)

    Returns 0.0 if asset is unknown, end <= start, or data table missing.
    """
    table = _table_for(asset)
    if table is None or end_dt <= start_dt:
        return 0.0
    start_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    try:
        con = _connect_or_warn(asset, table)
        if con is None:
            return 0.0
        rows = con.execute(
            f"SELECT fr_close FROM {table} "
            f"WHERE timestamp > ? AND timestamp <= ? "
            f"  AND fr_close IS NOT NULL "
            f"  AND timestamp % ? = 0",
            (start_s, end_s, SETTLEMENT_PERIOD_SECONDS),
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        log.warning(f"[funding] DB error for {asset}: {e} — treating as 0")
        return 0.0
    if not rows:
        return 0.0
    total_rate = sum(r[0] for r in rows)
    sign = -1.0 if direction.upper() == "LONG" else 1.0
    return sign * total_rate * 100.0


def daily_sums_pct(asset: str, since_ts: int, until_ts: int,
                   *, complete_only: bool = True) -> dict[str, float]:
    """``{date_iso: sum-of-3-settlements * 100}`` over ``[since_ts, until_ts]``.

    Returned values are PERCENT of notional, **unsigned** (raw funding-rate
    direction; positive = longs paying shorts that day). A "complete" day has
    all 3 settlements (00:00 / 08:00 / 16:00 UTC); incomplete days are dropped
    unless ``complete_only=False``.

    Used by carry_service for daily basis evaluation (which combines this
    with spot/perp price closes).
    """
    table = _table_for(asset)
    if table is None or until_ts < since_ts:
        return {}
    try:
        con = _connect_or_warn(asset, table)
        if con is None:
            return {}
        rows = con.execute(
            f"SELECT date(timestamp,'unixepoch'), SUM(fr_close), COUNT(*) "
            f"FROM {table} "
            f"WHERE timestamp >= ? AND timestamp <= ? "
            f"  AND fr_close IS NOT NULL "
            f"  AND timestamp % ? = 0 "
            f"GROUP BY 1 ORDER BY 1",
            (since_ts, until_ts, SETTLEMENT_PERIOD_SECONDS),
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        log.warning(f"[funding] DB error for {asset} daily_sums: {e}")
        return {}
    if complete_only:
        return {d: s * 100 for d, s, n in rows if n >= 3}
    return {d: s * 100 for d, s, n in rows}


def daily_means_rate(asset: str, until_ts: int) -> dict[str, float]:
    """``{date_iso: AVG(fr_close)}`` for all dates up to ``until_ts``.

    Returns the per-day mean funding rate as a DECIMAL fraction (e.g.
    ``0.0001`` = ``0.01%`` per 8h). The mean of 3 settlement rates that day
    — useful as a feature for ML/percentile work where the absolute unit
    is irrelevant; just keeps proportionality.

    Used by cpr_service.
    """
    table = _table_for(asset)
    if table is None:
        return {}
    try:
        con = _connect_or_warn(asset, table)
        if con is None:
            return {}
        rows = con.execute(
            f"SELECT date(timestamp,'unixepoch'), AVG(fr_close) FROM {table} "
            f"WHERE timestamp <= ? "
            f"  AND fr_close IS NOT NULL "
            f"  AND timestamp % ? = 0 "
            f"GROUP BY 1 ORDER BY 1",
            (until_ts, SETTLEMENT_PERIOD_SECONDS),
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        log.warning(f"[funding] DB error for {asset} daily_means: {e}")
        return {}
    return {r[0]: r[1] for r in rows}
