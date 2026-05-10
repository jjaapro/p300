"""Deterministic simulated-clock loop primitive.

Both ``run.py --mode sim`` (operator-facing simulator) and
``backtest_runner.py`` (research-replay tool) drive the live bot
under a fake clock. The shared primitive lives here so the two
callers stay structurally identical; only their per-tick callbacks
differ:

  - run.py passes ``variant_engine.tick`` (all-variants tick, like live).
  - backtest_runner.py passes a closure that runs its variant-scoped
    liquidation check + close-due check + tick_replay_variant + a
    progress log.

This module owns nothing else — no NAV building, no progress logic,
no DB writes. It just advances the clock and yields control.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from threading import Event
from typing import Callable

from services import clock


def run_sim(start: datetime,
            end: datetime,
            step_seconds: int,
            tick_fn: Callable[[datetime], None],
            *,
            stop_event: Event | None = None,
            ) -> int:
    """Advance the simulated clock from ``start`` to ``end`` (inclusive)
    by ``step_seconds`` seconds per tick, invoking ``tick_fn(cur)`` at
    each step.

    Returns the number of ticks executed. Resets the simulated clock to
    None on exit (so the wall clock is restored cleanly).

    No wall-clock sleep — runs as fast as ``tick_fn`` allows.

    Args:
        start: first simulated UTC datetime (inclusive).
        end: final simulated UTC datetime (inclusive).
        step_seconds: simulated-clock advance per tick.
        tick_fn: callable taking the current simulated datetime; runs
            whatever the caller wants per tick (variant_engine.tick,
            backtest_runner's liquidation+close+dispatch sequence, etc.).
            Exceptions are NOT caught here — let the caller decide.
        stop_event: optional threading.Event; loop exits early when set
            (Ctrl-C / SIGTERM in run.py).

    Returns:
        Number of ticks that executed before the loop exited.
    """
    n = 0
    cur = start
    step = timedelta(seconds=step_seconds)
    try:
        while cur <= end:
            if stop_event is not None and stop_event.is_set():
                break
            clock.set_simulated_now(cur)
            tick_fn(cur)
            n += 1
            cur += step
    finally:
        # Restore wall clock no matter how we exited (cleanly, via
        # stop_event, or by exception bubbling through tick_fn).
        clock.set_simulated_now(None)
    return n
