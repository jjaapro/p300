"""strategies.support.clock — simulated-now infrastructure.

Verifies:
  - Default mode returns real UTC now (within tolerance)
  - set_simulated_now/set(None) round-trips
  - now_ts() and now_ts_ms() reflect the simulated clock
  - is_simulated() flips correctly
  - Timezone naïve datetimes get UTC attached (defensive cast)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from strategies.support import clock


def test_live_mode_returns_close_to_real_now():
    assert not clock.is_simulated()
    dt = clock.now_utc()
    real = datetime.now(timezone.utc)
    # Allow up to 2s slack.
    assert abs((dt - real).total_seconds()) < 2.0


def test_set_simulated_now_is_honored():
    target = datetime(2024, 3, 15, 12, 30, tzinfo=timezone.utc)
    clock.set_simulated_now(target)
    assert clock.is_simulated()
    assert clock.now_utc() == target
    assert clock.now_ts() == int(target.timestamp())
    assert clock.now_ts_ms() == int(target.timestamp() * 1000)


def test_set_simulated_now_none_returns_to_live():
    clock.set_simulated_now(datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert clock.is_simulated()
    clock.set_simulated_now(None)
    assert not clock.is_simulated()
    assert abs((clock.now_utc() - datetime.now(timezone.utc)).total_seconds()) < 2.0


def test_naive_datetime_gets_utc_attached():
    naive = datetime(2024, 3, 15, 12, 30)  # no tzinfo
    clock.set_simulated_now(naive)
    dt = clock.now_utc()
    assert dt.tzinfo is not None
    assert dt.tzinfo.utcoffset(dt).total_seconds() == 0.0


def test_now_ts_and_ms_are_coherent():
    target = datetime(2023, 6, 1, 8, 0, tzinfo=timezone.utc)
    clock.set_simulated_now(target)
    assert clock.now_ts_ms() == clock.now_ts() * 1000


def test_now_iso_matches_now_utc_isoformat():
    """now_iso() is a one-call shortcut for now_utc().isoformat()."""
    target = datetime(2024, 5, 4, 14, 30, 45, tzinfo=timezone.utc)
    clock.set_simulated_now(target)
    assert clock.now_iso() == target.isoformat()
    assert clock.now_iso() == clock.now_utc().isoformat()
    clock.set_simulated_now(None)


def test_now_iso_in_live_mode_returns_parseable_iso_string():
    """In live mode, now_iso() returns a parseable ISO 8601 string close to wall clock."""
    clock.set_simulated_now(None)
    parsed = datetime.fromisoformat(clock.now_iso())
    real = datetime.now(timezone.utc)
    assert parsed.tzinfo is not None
    assert abs((parsed - real).total_seconds()) < 2.0
