"""Accrued-funding helper for perp positions held on BTCUSDT / ETHUSDT.

Reads from the `cd_funding_rate` table (BTC) and `cd_funding_rate_eth` table
(ETH), both populated by `binance_feed.py`. The tables store HOURLY OHLC
of the funding rate (24 rows per day), but Binance perp funding only SETTLES
at 00:00, 08:00, 16:00 UTC (3 settlements/day). To compute accrual we sum
`fr_close` only at those 8h boundaries — Unix timestamps where ts % 28800 = 0.

A prior version of this function summed all hourly rows, which inflated the
funding cost by 8x. The bug was caught 2026-05-04 against a real MEXC trade
that recorded -1.43 USDC funding over 11.3 days; the broken function would
have reported ~$25 of funding on the same notional.

Convention (Binance perpetual): funding pays LONGS when rate < 0 and SHORTS
when rate > 0. The P&L to a LONG per settlement is `-rate * notional`; to a
SHORT it is `+rate * notional`.

`accrued_funding_pct(...)` returns the cumulative funding P&L as a percent of
notional over the [start_dt, end_dt] window for the given asset+direction,
i.e. the amount to ADD to trade P&L (negative = cost, positive = income).

If data for the asset/window is unavailable (e.g. ETH table missing), returns
0.0 and logs a warning — conservative in the sense that callers then see no
funding drag, but this is flagged clearly so paper-vs-backtest comparison
isn't silently biased.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger("dashboard.funding_util")

TRADER_DB = Path(__file__).resolve().parent.parent / "data" / "trader.db"

_TABLE_FOR_ASSET = {
    "BTC": "cd_funding_rate",
    "ETH": "cd_funding_rate_eth",
}

# Binance perp funding settlement period in seconds (00:00, 08:00, 16:00 UTC).
# Settlement timestamps are exact multiples of this value.
SETTLEMENT_PERIOD_SECONDS = 8 * 3600


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def accrued_funding_pct(asset: str, start_dt: datetime, end_dt: datetime,
                        direction: str) -> float:
    """Sum funding rate P&L over [start_dt, end_dt] for a perp position.

    Returns a percentage of notional (e.g. -0.15 = -0.15% = -15bp cost).
    Direction: 'LONG' or 'SHORT'. LONG loses when rate > 0; SHORT gains.
    Inclusive endpoints: any settlement whose timestamp is between start
    and end (UTC) is counted.
    """
    asset = asset.upper()
    direction = direction.upper()
    table = _TABLE_FOR_ASSET.get(asset)
    if table is None:
        return 0.0
    if end_dt <= start_dt:
        return 0.0
    start_s = int(start_dt.timestamp())
    end_s = int(end_dt.timestamp())
    try:
        con = sqlite3.connect(str(TRADER_DB))
        if not _table_exists(con, table):
            con.close()
            log.warning(
                f"[funding] {table} not present — funding treated as 0 for {asset} "
                f"{direction}. Re-run binance_feed.py to populate."
            )
            return 0.0
        # Only sample at 8h settlement boundaries — the table stores hourly
        # OHLC but funding actually pays out 3x/day. Without this filter we
        # would count each settlement 8 times.
        rows = con.execute(
            f"SELECT fr_close FROM {table} WHERE timestamp >= ? AND timestamp <= ? "
            f"AND fr_close IS NOT NULL AND timestamp % ? = 0",
            (start_s, end_s, SETTLEMENT_PERIOD_SECONDS),
        ).fetchall()
        con.close()
    except sqlite3.Error as e:
        log.warning(f"[funding] DB error for {asset}: {e} — treating funding as 0")
        return 0.0
    if not rows:
        return 0.0
    total_rate = sum(r[0] for r in rows)
    sign = -1.0 if direction == "LONG" else 1.0
    return sign * total_rate * 100.0
