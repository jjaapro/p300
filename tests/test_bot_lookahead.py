"""Look-ahead safety for the RUNNING bots.

Companion to tests/test_jplus_lookahead.py, which covers jplus.simulate, ADX,
the regime classifier and carry. Four of the six running bots had no
clock-invariance coverage at all until 2026-09-13 (BACKLOG step 6) — the eight
dormant sleeves archived that day had been subsidising the appearance of it.

THE CONTRACT HAS TWO HALVES, and neither alone is sufficient:

  boundary    a run must not SEE data after its own clock
  agreement   a date the run DID see must not be revised when the clock moves

The agreement half alone is the trap. If a loader ignores the clock entirely,
both runs return the whole table, every common-date comparison is trivially
equal, and the test passes green while the bot trades on tomorrow's candles.
Two of the four original arms in test_jplus_lookahead.py had exactly that
hole. `assert_no_data_after_clock` (imported from there) carries the first
half.

WHAT TWO-CLOCK TESTS CANNOT DO — learned the hard way here, and the reason
this file is shorter than it first looks. Three separate agreement arms were
written for squeeze_bull and every one of them was decorative: none could be
made to fail under an origin-anchored cooldown, COOLDOWN_HOURS x60, a
production lookback cut from 45 days to 7, or REGIME_SHIFT_DAYS set to the
value its own config calls "never". Two compounding reasons:

  1. BOTH runs get the same mutation, so it produces the same wrong answer
     twice. Clock-invariance is blind to anything that is not clock-DEPENDENT
     — which is most config and most arithmetic.
  2. Building a per-bar series by walking prefixes (`evaluate(bars[:k+1])`)
     enforces causality structurally: the data after the report bar is simply
     not in the list. That is what makes the series well-defined, and it is
     also what makes an intra-frame peek impossible to observe.

So the coverage that survives is the boundary half plus DIRECT causality
assertions — corrupt the present, prove the past does not move. Where a
two-clock arm could not be shown to catch anything it was deleted rather than
shipped, because a guard that cannot fail is worse than no guard: it reports
safety it has not checked. Every arm below has a named mutation it is proven
to catch, registered in tests/fixtures/mutation_drill.py.

All tests are read-only against prod.db and safe to run while the fleet is
writing it.
"""
from __future__ import annotations

# MUST be first: short_squeeze resolves SSQ_DIAG at import. It never leaked
# here — diag is on only when SSQ_DIAG is exactly "1", and the loader these
# arms call makes no diag writes — but a shell that exports SSQ_DIAG=1 would
# have this module appending to the RUNNING bot's JSONL. Added 2026-09-13.
from tests import _golden_guard  # noqa: F401

from datetime import datetime, timezone

import pytest

from strategies.support import clock
from tests.test_jplus_lookahead import assert_no_data_after_clock  # noqa: F401


# ─── squeeze_bull (S-107, OI flush) ─────────────────────────────────────────
#
# Two live paper variants run off this module: bot_squeeze_bull_v1 and the
# no-stop twin, in the same process.
#
# Its look-ahead surface is exactly two things, and each has an arm:
#   A  `_load_hourly` bounds the frame with `p.timestamp < end`, end = the
#      clock's hour floor (signal.py:73-82). A MAX(timestamp) anchor or a
#      dropped bound reads the forming hour, or the whole table.
#   C  `backward_only_ret_30d` + `REGIME_SHIFT_DAYS = 1` is the causal
#      guarantee behind the +0.246R backward-only gate this bot shipped on
#      (memory project_squeeze_bull_shipped). config.py:29 says "Never set
#      this to 0"; this is the only thing in the repo that would notice.
#
# `evaluate(bars)` itself reports on bars[-1] and so cannot look ahead.

SB_CLOCKS = (datetime(2024, 4, 15, tzinfo=timezone.utc),
             datetime(2024, 6, 14, tzinfo=timezone.utc))
SB_LOOKBACK = 200


@pytest.fixture(scope="module")
def sb_frames():
    """The loader's output at both clocks. Cheap — no decision walk, because
    the arms that needed one turned out to prove nothing (see module
    docstring)."""
    from bots.squeeze_bull.strategy import signal as sb
    out = {}
    for name, clk in zip(("early", "late"), SB_CLOCKS):
        clock.set_simulated_now(clk)
        try:
            out[name] = (clk, sb._load_hourly(clock.now_utc(),
                                              lookback_days=SB_LOOKBACK))
        finally:
            clock.set_simulated_now(None)
    return out


def test_squeeze_bull_loader_is_clock_bounded(sb_frames):
    """ARM A — the boundary half. The frame must stop before the clock's hour.

    `_load_hourly` uses `p.timestamp < end` (exclusive), so the FORMING hour
    is excluded too, not just the future — the bot's own stated guarantee at
    signal.py:69-71. Asserted strictly rather than as `<=`, because the
    weaker form would accept the forming bar.

    Proven to catch: the bound re-anchored to MAX(timestamp), and the bound
    made inclusive.
    """
    for which, (clk, bars) in sb_frames.items():
        assert len(bars) > 500, (
            f"{which}: {len(bars)} bars — too thin, the arm would be vacuous")
        end = int(clk.replace(minute=0, second=0, microsecond=0).timestamp())
        newest = max(b["ts"] for b in bars)
        assert newest < end, (
            f"squeeze_bull {which} frame: LOOK-AHEAD — newest bar "
            f"{datetime.fromtimestamp(newest, tz=timezone.utc)} is not strictly "
            f"before the clock hour "
            f"{datetime.fromtimestamp(end, tz=timezone.utc)}")


def test_squeeze_bull_regime_never_reads_its_own_day_or_later():
    """ARM C — the causal guarantee the bot shipped on, asserted DIRECTLY.

    SQUEEZE_BULL's gate is the BACKWARD-ONLY +0.246R construction, not the
    researched +0.202R that peeked. `REGIME_SHIFT_DAYS = 1` is what makes it
    backward-only; math.py:50-51 spells out the failure — "with a shift of 0
    an early-morning bar reads a close from later the same day".

    This was a two-clock arm first and it did NOT catch the shift being set
    to 0, for both reasons in the module docstring. So the assertion is
    direct: corrupt the bar's own day and every later day, and the value must
    not move. That is what backward-only means, and it fails loudly at 0.

    Proven to catch: REGIME_SHIFT_DAYS = 0.
    """
    from datetime import date, timedelta
    from bots.squeeze_bull.strategy import math as sb_math

    # Keys are `date` objects, matching signal._daily_closes (signal.py:95).
    d0 = date(2025, 3, 1)
    closes = {d0 + timedelta(days=i): 100.0 + i for i in range(45)}
    on_date = d0 + timedelta(days=40)

    base = sb_math.backward_only_ret_30d(dict(closes), on_date)
    assert base is not None, "ladder is too short — the arm would be vacuous"

    poisoned = dict(closes)
    for i in range(40, 45):                      # the bar's own day, and after
        poisoned[d0 + timedelta(days=i)] = 1e9
    after = sb_math.backward_only_ret_30d(poisoned, on_date)
    assert after == base, (
        f"SQUEEZE_BULL regime is NOT backward-only: corrupting {on_date} and "
        f"later moved ret_30d from {base} to {after}. REGIME_SHIFT_DAYS is the "
        f"causal guarantee behind the shipped +0.246R gate — see "
        f"bots/squeeze_bull/strategy/config.py:29.")

    # Non-vacuity: the day it IS entitled to read must actually matter, or
    # the assertion above would hold for a function that reads nothing.
    ref = dict(closes)
    ref[on_date - timedelta(days=1)] = 1e9
    assert sb_math.backward_only_ret_30d(ref, on_date) != base, (
        "ret_30d ignored the day it is supposed to read — the assertion "
        "above would be vacuous")


# ─── short_squeeze ──────────────────────────────────────────────────────────
#
# The dangerous shape here is the PERCENTILE POOL. `_refresh_percentile_
# distributions` builds 90 days of London/NY bars and every trigger ranks the
# current bar against it (signal.py:149-163). A pool built over the whole
# table would rank today's value against the future and the bot would still
# look like it was working.
#
# `_load_recent_15m_bars` bounds it at both ends (signal.py:114-132). This
# arm is what keeps it that way.

SS_CLOCK = datetime(2025, 6, 1, 14, 0, tzinfo=timezone.utc)   # inside London/NY


def test_short_squeeze_percentile_pool_is_clock_bounded():
    """The 90-day percentile pool must not contain a bar at or after the
    clock. This is the arm that matters most for this bot: every gate it has
    is a percentile rank against this pool, so a pool that reaches into the
    future silently re-scores every decision.

    Proven to catch: the pool's upper bound pushed past the clock.
    """
    from bots.short_squeeze.strategy import signal as ss
    from bots.short_squeeze.strategy import config as ss_cfg

    clock.set_simulated_now(SS_CLOCK)
    try:
        bars = ss._load_recent_15m_bars(clock.now_utc(), ss_cfg.WINDOW_DAYS)
    finally:
        clock.set_simulated_now(None)

    assert len(bars) > 1000, (
        f"pool has {len(bars)} bars — too thin to rank against, and the "
        f"assertion below would be vacuous")

    now_s = int(SS_CLOCK.timestamp())
    newest = max(b["ts"] for b in bars)
    assert newest <= now_s, (
        f"short_squeeze percentile pool: LOOK-AHEAD — newest pooled bar "
        f"{datetime.fromtimestamp(newest, tz=timezone.utc)} is after the clock "
        f"{SS_CLOCK}. Every gate in this bot is a rank against this pool.")

    # The pool must also be bounded BELOW by the window, or "90-day rolling"
    # is a fiction and the ranks drift as the table grows.
    oldest = min(b["ts"] for b in bars)
    assert oldest >= now_s - (ss_cfg.WINDOW_DAYS + 1) * 86400, (
        f"pool reaches back to "
        f"{datetime.fromtimestamp(oldest, tz=timezone.utc)}, beyond the "
        f"{ss_cfg.WINDOW_DAYS}d window — the distribution is not rolling")
