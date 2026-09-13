"""Pure window helpers for the R4 bot — no DB, no clock reads. (Named windows.py, not calendar.py: a script-relative module called calendar would shadow the stdlib module.)

Two uses:
  window_open_for(strategy, exit_dt)  — the runner derives "when did this
      window open" from the sleeve's scheduled exit and the fixed hold, so
      the late-entry guard never re-implements the calendar.
  next_windows(now, n)                — the dashboard's read-only listing of
      upcoming windows, built from the SAME predicates the sleeve uses
      (tests/test_r4_bot.py pins the two together).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from bots.r4.strategy.config import (
    R4_BTC_ENTRY_HOUR, R4_BTC_EXIT_HOUR, R4_ETH_ENTRY_HOUR, R4_ETH_EXIT_HOUR,
    R4_V2_ENTRY_HOUR, R4_V2_EXIT_HOUR,
    STRATEGY_R4_BTC, STRATEGY_R4_BTC_V2, STRATEGY_R4_ETH, STRATEGY_R4_ETH_V2,
)

HOLD_HOURS = {
    STRATEGY_R4_BTC: R4_BTC_EXIT_HOUR - R4_BTC_ENTRY_HOUR,          # 12
    STRATEGY_R4_ETH: 24 - R4_ETH_ENTRY_HOUR + R4_ETH_EXIT_HOUR,     # 24
    STRATEGY_R4_BTC_V2: R4_V2_EXIT_HOUR - R4_V2_ENTRY_HOUR,         # 10
    STRATEGY_R4_ETH_V2: R4_V2_EXIT_HOUR - R4_V2_ENTRY_HOUR,         # 10
}

ASSET = {
    STRATEGY_R4_BTC: "BTC", STRATEGY_R4_ETH: "ETH",
    STRATEGY_R4_BTC_V2: "BTC", STRATEGY_R4_ETH_V2: "ETH",
}


def window_open_for(strategy: str, exit_dt: datetime) -> datetime:
    """Window open time = scheduled exit minus the strategy's fixed hold."""
    return exit_dt - timedelta(hours=HOLD_HOURS[strategy])


def windows_on(day: datetime) -> list[dict]:
    """All R4 windows whose ENTRY falls on `day` (tz-aware UTC midnight).
    Mirrors r4/signal.py: Mon day≤14 (BTC V1); Tue with next-day day≤14
    (ETH V1, exits Wed); Wed+Fri day≤14 (both V2)."""
    out = []
    wd, dom = day.weekday(), day.day
    if wd == 0 and dom <= 14:
        out.append(_w(STRATEGY_R4_BTC, day, R4_BTC_ENTRY_HOUR))
    if wd == 1 and (day + timedelta(days=1)).day <= 14:
        out.append(_w(STRATEGY_R4_ETH, day, R4_ETH_ENTRY_HOUR))
    if wd in (2, 4) and dom <= 14:
        out.append(_w(STRATEGY_R4_BTC_V2, day, R4_V2_ENTRY_HOUR))
        out.append(_w(STRATEGY_R4_ETH_V2, day, R4_V2_ENTRY_HOUR))
    return out


def _w(strategy: str, day: datetime, entry_hour: int) -> dict:
    open_dt = day.replace(hour=entry_hour, minute=0, second=0, microsecond=0)
    return {"strategy": strategy, "asset": ASSET[strategy],
            "open_utc": open_dt,
            "close_utc": open_dt + timedelta(hours=HOLD_HOURS[strategy])}


def next_windows(now: datetime, n: int = 6, horizon_days: int = 45,
                 strategies=None) -> list[dict]:
    """The next `n` windows whose close is still in the future, oldest first.
    `strategies` (an iterable of strategy names) restricts the listing to
    those windows — the runner and the dashboard pass the ENABLED set so a
    disabled window is never announced as upcoming; None lists all four."""
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    keep = None if strategies is None else set(strategies)
    out = []
    for i in range(horizon_days):
        for w in windows_on(day0 + timedelta(days=i)):
            if w["close_utc"] > now and (keep is None or w["strategy"] in keep):
                out.append(w)
    out.sort(key=lambda w: (w["open_utc"], w["strategy"]))
    return out[:n]
