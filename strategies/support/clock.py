"""Simulated-clock infrastructure for deterministic historical replay.

Live mode:        now_utc() returns datetime.now(timezone.utc) — identical to
                  before; existing services behave exactly as they did.
Simulated mode:   set_simulated_now(T) then now_utc() returns T. All services
                  that route their time reads through this module see T as
                  "now", so DB queries bounded by now_ts() / now_ts_ms() return
                  rows whose timestamp ≤ T — no look-ahead.

Services must use:
  - clock.now_utc()      — datetime (UTC)
  - clock.now_iso()       — ISO 8601 string (for trades.actual_entry_time etc.)
  - clock.now_ts()        — unix seconds (for cd_futures_ohlcv, cd_funding_rate)
  - clock.now_ts_ms()     — unix ms     (for btc_1m, eth_1m)
  - clock.is_simulated()  — feature flag (e.g., staleness checks)

Do NOT cache clock values across a backtest iteration that spans a
set_simulated_now() call. Each dispatcher should read the clock once at its
entry point and derive all downstream timestamps from that single read."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

_simulated_now: Optional[datetime] = None


def now_utc() -> datetime:
    """Current UTC time. Returns simulated value when a replay is active."""
    if _simulated_now is not None:
        return _simulated_now
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """ISO 8601 representation of the current UTC time. Used wherever a
    sleeve writes a timestamp column as text (trades.actual_entry_time,
    actual_exit_time, etc.). Equivalent to ``now_utc().isoformat()`` —
    exists as a one-call shortcut so callers don't repeat the chain."""
    return now_utc().isoformat()


def now_ts() -> int:
    """Unix seconds — for timestamp columns stored as seconds."""
    return int(now_utc().timestamp())


def now_ts_ms() -> int:
    """Unix ms — for open_time columns stored as ms (btc_1m, eth_1m)."""
    return int(now_utc().timestamp() * 1000)


def set_simulated_now(dt: Optional[datetime]) -> None:
    """Activate simulated time. Pass None to return to live mode."""
    global _simulated_now
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    _simulated_now = dt


def is_simulated() -> bool:
    """True when a replay clock is active."""
    return _simulated_now is not None
