"""Build the scheduled_events table from hardcoded FOMC/CPI dates +
calendar-rule events (NFP, OPEX). Pure stdlib — no external data, no auth.

Stored event types in scheduled_events (date, event_type, description):
  - FOMC           Fed FOMC decision dates (hardcoded from press releases)
  - CPI            US CPI release dates (hardcoded from BLS schedule)
  - NFP            US Non-Farm Payrolls (computed: first Friday of month)
  - OPEX_MONTHLY   Monthly options expiry (computed: last Friday of month)
  - OPEX_QUARTERLY Quarterly options expiry (last Friday of Mar/Jun/Sep/Dec)

Maintenance:
  Annually, when the Fed and BLS publish next year's release schedule,
  append new rows to FOMC_DECISIONS and CPI_DATES below and re-run.

Read by [services/thu_bear_service.py] (event-day filter) and the regime
classifier (no-trade-on-FOMC rule). Failing closed when this table is
empty disables those filters — health.py will pass but THU_BEAR will
not fire.

Usage:
  python fetch_events.py        # rebuild scheduled_events end-to-end
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "trader.db"


# ─── Hardcoded events ───────────────────────────────────────────────────────

# FOMC decision dates (second day of two-day meetings, or the day itself for
# single-day emergency meetings). Source: https://www.federalreserve.gov/
FOMC_DECISIONS = [
    # 2020
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-03-18",
    "2020-04-29", "2020-06-10", "2020-07-29", "2020-09-16",
    "2020-11-05", "2020-12-16",
    # 2021
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    # 2022
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    # 2023
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026 (scheduled)
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# CPI release dates — BLS, ~10th-15th of month for prior month's data.
# Source: https://www.bls.gov/schedule/news_release/cpi.htm
CPI_DATES = [
    # 2020
    "2020-01-14", "2020-02-13", "2020-03-11", "2020-04-10",
    "2020-05-12", "2020-06-10", "2020-07-14", "2020-08-12",
    "2020-09-11", "2020-10-13", "2020-11-12", "2020-12-10",
    # 2021
    "2021-01-13", "2021-02-10", "2021-03-10", "2021-04-13",
    "2021-05-12", "2021-06-10", "2021-07-13", "2021-08-11",
    "2021-09-14", "2021-10-13", "2021-11-10", "2021-12-10",
    # 2022
    "2022-01-12", "2022-02-10", "2022-03-10", "2022-04-12",
    "2022-05-11", "2022-06-10", "2022-07-13", "2022-08-10",
    "2022-09-13", "2022-10-13", "2022-11-10", "2022-12-13",
    # 2023
    "2023-01-12", "2023-02-14", "2023-03-14", "2023-04-12",
    "2023-05-10", "2023-06-13", "2023-07-12", "2023-08-10",
    "2023-09-13", "2023-10-12", "2023-11-14", "2023-12-12",
    # 2024
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
    "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    # 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-11", "2025-10-14", "2025-11-13", "2025-12-10",
    # 2026 (estimated mid-month — replace with BLS-published dates as released)
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-14",
    "2026-05-13", "2026-06-10",
]

EVENT_YEAR_START = 2019
EVENT_YEAR_END = 2027


# ─── Computed events (calendar rules) ──────────────────────────────────────

def _first_friday(year: int, month: int) -> datetime:
    d = datetime(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)  # Mon=0, Fri=4


def _last_friday(year: int, month: int) -> datetime:
    nxt = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def _nfp_dates(start_year: int, end_year: int) -> list[str]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(_first_friday(y, m).strftime("%Y-%m-%d"))
    return out


def _monthly_expiry(start_year: int, end_year: int) -> list[str]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(_last_friday(y, m).strftime("%Y-%m-%d"))
    return out


def _quarterly_expiry(start_year: int, end_year: int) -> list[str]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in (3, 6, 9, 12):
            out.append(_last_friday(y, m).strftime("%Y-%m-%d"))
    return out


# ─── DB write ──────────────────────────────────────────────────────────────

def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_events (
            date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT,
            PRIMARY KEY (date, event_type)
        )
    """)
    con.commit()


def _insert(con: sqlite3.Connection, dates: list[str],
             event_type: str, description: str) -> int:
    con.executemany(
        "INSERT OR REPLACE INTO scheduled_events (date, event_type, description) "
        "VALUES (?, ?, ?)",
        [(d, event_type, description) for d in dates],
    )
    con.commit()
    return len(dates)


def rebuild() -> dict[str, int]:
    """Wipe + repopulate scheduled_events. Returns row counts per event type."""
    con = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_table(con)
        con.execute("DELETE FROM scheduled_events")
        con.commit()
        counts = {
            "FOMC": _insert(con, FOMC_DECISIONS, "FOMC",
                             "Fed FOMC decision announcement"),
            "CPI":  _insert(con, CPI_DATES, "CPI",
                             "US CPI release (BLS)"),
            "NFP":  _insert(con, _nfp_dates(EVENT_YEAR_START, EVENT_YEAR_END),
                             "NFP", "US Non-Farm Payrolls release (first Friday)"),
            "OPEX_MONTHLY":   _insert(con, _monthly_expiry(EVENT_YEAR_START, EVENT_YEAR_END),
                                       "OPEX_MONTHLY",
                                       "Monthly options expiry (Deribit/CME, last Fri)"),
            "OPEX_QUARTERLY": _insert(con, _quarterly_expiry(EVENT_YEAR_START, EVENT_YEAR_END),
                                       "OPEX_QUARTERLY",
                                       "Quarterly options expiry (Mar/Jun/Sep/Dec)"),
        }
        return counts
    finally:
        con.close()


def main() -> int:
    counts = rebuild()
    print("scheduled_events rebuilt:")
    for k, n in counts.items():
        print(f"  {k:<16} {n:>4} rows")
    print(f"  {'TOTAL':<16} {sum(counts.values()):>4} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
