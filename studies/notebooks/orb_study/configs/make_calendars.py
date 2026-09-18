"""Write the frozen NYSE (XNYS) and London Stock Exchange (XLON) calendars used by the study.

Generated once with exchange_calendars 4.13.2 (not a repo dependency). To regenerate:

    python -m pip install --target <tmp> exchange_calendars==4.13.2
    python configs/make_calendars.py <tmp>

The study reads only the JSON files. tests/test_calendars.py checks them against an
independently compiled NYSE holiday/early-close list, so a library error cannot pass
silently.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if len(sys.argv) > 1:
    sys.path.insert(0, sys.argv[1])

import exchange_calendars as xc  # noqa: E402

HERE = Path(__file__).resolve().parent
START, END = "2019-10-01", "2026-12-31"
LOCAL = {"XNYS": ("America/New_York", "09:30", "16:00"), "XLON": ("Europe/London", "08:00", "16:30")}


def write(code: str) -> Path:
    tz, open_hm, close_hm = LOCAL[code]
    cal = xc.get_calendar(code, start=START, end=END)
    sessions, early, late_open = [], [], []
    for session in cal.sessions:
        day = str(session.date())
        sessions.append(day)
        o = cal.session_open(session).tz_convert(tz)
        c = cal.session_close(session).tz_convert(tz)
        if f"{c:%H:%M}" != close_hm:
            early.append({"date": day, "close_local": f"{c:%H:%M}"})
        if f"{o:%H:%M}" != open_hm:
            late_open.append({"date": day, "open_local": f"{o:%H:%M}"})
    out = HERE / f"calendar_{code}.json"
    out.write_text(json.dumps({
        "code": code, "timezone": tz, "regular_open_local": open_hm, "regular_close_local": close_hm,
        "source": f"exchange_calendars {xc.__version__}",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range": [START, END], "sessions": sessions, "early_closes": early,
        "non_standard_opens": late_open}, indent=1), encoding="utf-8")
    print(f"{code}: {len(sessions)} sessions, {len(early)} early closes, {len(late_open)} late opens -> {out.name}")
    return out


if __name__ == "__main__":
    for code in LOCAL:
        write(code)
