"""Pure helpers for S-105 SHORT_SQUEEZE.

Stateless functions for percentile rank, session detection, and the daily
asia macro context. No I/O, no DB access — testable in isolation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import numpy as np

from .config import ASIA_OI_PCT_MIN, SESSIONS


def session_of_hour(hour: int) -> str | None:
    """Return 'asia' / 'london' / 'ny' for a UTC hour-of-day, else None
    (which marks the 21-24 UTC late-NY tail we don't trade on)."""
    for name, (lo, hi) in SESSIONS.items():
        if lo <= hour < hi:
            return name
    return None


def percentile_rank(value: float, distribution: np.ndarray) -> float:
    """Fraction of `distribution` that is <= `value`. Returns 0.0 if
    `distribution` is empty (caller should check before passing).

    O(n) — for our 5000-bar rolling window this is sub-millisecond.
    """
    if len(distribution) == 0:
        return 0.0
    return float((distribution <= value).mean())


def rolling_percentile(series: np.ndarray, window: int) -> np.ndarray:
    """Trailing-window percentile rank of each value within the prior
    `window` values (excludes the current value).

    Returns an array of the same length as `series`; first `window`
    entries are NaN. Used for full backtest precomputation; the live path
    just calls `percentile_rank` against the trailing window directly.
    """
    out = np.full(len(series), np.nan)
    for i in range(window, len(series)):
        out[i] = (series[i - window: i] <= series[i]).mean()
    return out


def close_in_range(open_p: float, high: float, low: float, close: float) -> float:
    """How far up the bar's range did the close print? 0 = at the low,
    1 = at the high. Used as a reversal-strength signal."""
    rng = max(high - low, 1e-9)
    return (close - low) / rng


def asia_session_summary(asia_bars: list[dict]) -> dict | None:
    """Reduce the Asia session's hourly bars into a macro summary.

    Each bar is ``{'open','high','low','close','oi_close','funding'}``.
    Returns ``None`` if there aren't enough bars to summarize.

    Returns ``{'close_lt_open', 'oi_pct', 'fund_mean', 'is_short_macro',
    'is_long_macro'}``.
    """
    if not asia_bars or len(asia_bars) < 2:
        return None
    open_p = asia_bars[0]["open"]
    close_p = asia_bars[-1]["close"]
    oi_open = asia_bars[0]["oi_close"]
    oi_close = asia_bars[-1]["oi_close"]
    fund_vals = [b["funding"] for b in asia_bars if b.get("funding") is not None]
    fund_mean = float(np.mean(fund_vals)) if fund_vals else 0.0
    oi_pct = (oi_close - oi_open) / max(oi_open, 1e-9)
    close_lt_open = close_p < open_p
    close_gt_open = close_p > open_p
    return {
        "close_lt_open": close_lt_open,
        "close_gt_open": close_gt_open,
        "oi_pct": oi_pct,
        "fund_mean": fund_mean,
        "is_short_macro": close_lt_open and (oi_pct > ASIA_OI_PCT_MIN) and (fund_mean < 0),
        "is_long_macro":  close_gt_open and (oi_pct > ASIA_OI_PCT_MIN) and (fund_mean > 0),
    }


def is_15m_boundary(now_utc: datetime) -> bool:
    """True at the start of a fresh 15m bar (minute mod 15 == 0).

    Live ticks fire every minute; we only want to do real work once per
    15m bar close. Caller should also check that the second is < some
    threshold to avoid double-firing within the same minute.
    """
    return now_utc.minute % 15 == 0


def utc_date_of(ts: datetime) -> str:
    """ISO-date string in UTC. Caller uses this as a cache key."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).date().isoformat()
