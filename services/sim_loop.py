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

## Exception handling — caller's responsibility

``run_sim`` does NOT catch exceptions raised by ``tick_fn``. The two
production callers wrap their tick_fn differently and that
inconsistency is intentional:

- **run.py --mode sim**: wraps ``variant_engine.tick`` in
  try/except + log.exception so a single bad tick does not abort a
  long sim. Operator semantics — "keep going, surface errors in the
  log."
- **backtest_runner.py**: does NOT wrap. A tick exception aborts the
  whole run. Research semantics — "loud failures are better than
  silent drift; investigate before continuing."

If you write a third caller, pick the wrapping that fits your
intent. ``run_sim`` itself stays neutral so neither caller has to
unwind a built-in policy.

The simulated clock is reset to ``None`` on exit (try/finally) even
if ``tick_fn`` raises, so the bot doesn't end up stuck on a
simulated time.
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
