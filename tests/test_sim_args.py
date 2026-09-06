"""sim.py argument parsing: a date-only --end is the whole calendar day
(review 2026-09-06, finding 8 — it used to stop at that day's midnight
tick, dropping final-day entries and scheduled exits)."""
from __future__ import annotations

from datetime import datetime, timezone

from studies.simulation import sim


def test_date_only_end_covers_whole_day():
    end = sim._parse_iso_utc("2024-12-31", end_of_day=True)
    assert end == datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_date_only_start_is_midnight():
    assert sim._parse_iso_utc("2024-12-31") == datetime(
        2024, 12, 31, tzinfo=timezone.utc)


def test_explicit_timestamp_is_used_as_given():
    ts = datetime(2024, 12, 31, 6, 30, tzinfo=timezone.utc)
    assert sim._parse_iso_utc("2024-12-31T06:30:00Z", end_of_day=True) == ts
    assert sim._parse_iso_utc("2024-12-31T06:30:00Z") == ts
