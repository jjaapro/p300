"""The frozen calendars against an independently compiled list, and the DST behaviour of anchors."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orb_calendars as cal  # noqa: E402

# Compiled by hand from NYSE's published holiday schedules, independent of exchange_calendars.
NYSE_HOLIDAYS = {
    2020: ["01-01", "01-20", "02-17", "04-10", "05-25", "07-03", "09-07", "11-26", "12-25"],
    2021: ["01-01", "01-18", "02-15", "04-02", "05-31", "07-05", "09-06", "11-25", "12-24"],
    2022: ["01-17", "02-21", "04-15", "05-30", "06-20", "07-04", "09-05", "11-24", "12-26"],
    2023: ["01-02", "01-16", "02-20", "04-07", "05-29", "06-19", "07-04", "09-04", "11-23", "12-25"],
    2024: ["01-01", "01-15", "02-19", "03-29", "05-27", "06-19", "07-04", "09-02", "11-28", "12-25"],
    2025: ["01-01", "01-09", "01-20", "02-17", "04-18", "05-26", "06-19", "07-04", "09-01", "11-27", "12-25"],
    2026: ["01-01", "01-19", "02-16", "04-03", "05-25", "06-19", "07-03", "09-07", "11-26", "12-25"],
}
NYSE_EARLY = ["2020-11-27", "2020-12-24", "2021-11-26", "2022-11-25", "2023-07-03", "2023-11-24",
              "2024-07-03", "2024-11-29", "2024-12-24", "2025-07-03", "2025-11-28", "2025-12-24",
              "2026-11-27", "2026-12-24"]


def test_nyse_weekday_holidays_match_the_independent_list():
    ny = cal.sessions("NY", "2020-01-01", "2026-12-31")
    weekdays = {d.date().isoformat() for d in pd.bdate_range("2020-01-01", "2026-12-31")}
    holidays = sorted(weekdays - set(ny["date"]))
    expected = sorted(f"{y}-{md}" for y, mds in NYSE_HOLIDAYS.items() for md in mds)
    assert holidays == expected


def test_nyse_early_closes_match_the_independent_list():
    ny = cal.sessions("NY", "2020-01-01", "2026-12-31")
    assert sorted(ny.loc[ny["early_close"], "date"]) == NYSE_EARLY


def test_ny_anchor_follows_us_dst_and_london_follows_uk_dst():
    ny = cal.sessions("NY", "2024-03-01", "2024-04-05").set_index("date")
    ldn = cal.sessions("LDN", "2024-03-01", "2024-04-05").set_index("date")
    utc_hm = lambda t: pd.Timestamp(t, unit="ms", tz="UTC").strftime("%H:%M")  # noqa: E731
    assert utc_hm(ny.loc["2024-03-08", "t0_ms"]) == "14:30"         # EST
    assert utc_hm(ny.loc["2024-03-11", "t0_ms"]) == "13:30"         # EDT from 2024-03-10
    assert utc_hm(ldn.loc["2024-03-28", "t0_ms"]) == "08:00"        # GMT (Good Friday 03-29 closed)
    assert utc_hm(ldn.loc["2024-04-02", "t0_ms"]) == "07:00"        # BST from 2024-03-31
    assert ny.loc["2024-03-11", "ny_minus_ldn_hours"] == -4.0      # mismatch weeks
    assert ny.loc["2024-04-02", "ny_minus_ldn_hours"] == -5.0


def test_lse_one_off_closures_are_not_sessions():
    ldn = set(cal.sessions("LDN", "2020-01-01", "2026-09-13")["date"])
    for day in ("2020-05-08", "2022-06-02", "2022-06-03", "2022-09-19", "2023-05-08"):
        assert day not in ldn


def test_utc_anchor_has_every_calendar_day_and_placebo_shift_is_exact():
    utc = cal.sessions("UTC", "2024-02-27", "2024-03-02")
    assert list(utc["date"]) == ["2024-02-27", "2024-02-28", "2024-02-29", "2024-03-01", "2024-03-02"]
    base = cal.sessions("NY", "2024-06-03", "2024-06-07")
    shifted = cal.sessions("NY", "2024-06-03", "2024-06-07", shift_min=-120)
    assert ((base["t0_ms"] - shifted["t0_ms"]) == 120 * 60_000).all()
