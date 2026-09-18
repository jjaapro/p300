"""Session anchors for the ORB study, from the frozen calendars in configs/.

An anchor is a local clock time on an eligible date, converted to UTC with the IANA
time-zone database, so DST moves the UTC anchor exactly as the local exchange moves:

    NY   09:30 America/New_York on full-length NYSE sessions
    LDN  08:00 Europe/London    on full-length LSE sessions
    UTC  00:00 UTC              on every calendar day

Early-close sessions are returned with early_close=True; P0 excludes them and the
study reports them separately. A placebo anchor is the NY anchor shifted by whole
minutes on the same NY dates.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CONFIGS = Path(__file__).resolve().parent / "configs"

ANCHORS = {
    "NY": {"calendar": "XNYS", "tz": "America/New_York", "local": time(9, 30)},
    "LDN": {"calendar": "XLON", "tz": "Europe/London", "local": time(8, 0)},
    "UTC": {"calendar": None, "tz": "UTC", "local": time(0, 0)},
}


@lru_cache(maxsize=None)
def load_calendar(code: str) -> dict:
    return json.loads((CONFIGS / f"calendar_{code}.json").read_text(encoding="utf-8"))


def _utc_ms(day: date, local: time, tz: str) -> int:
    return int(datetime.combine(day, local, ZoneInfo(tz)).astimezone(timezone.utc).timestamp() * 1000)


def sessions(anchor: str, start: str, end: str, shift_min: int = 0) -> pd.DataFrame:
    """Eligible sessions with start <= date <= end (inclusive, ISO dates).

    Columns: date, t0_ms (UTC anchor after any shift), early_close, weekday (0=Mon),
    ny_minus_ldn_hours (clock gap between New York and London that day; -5 normally,
    -4 in the weeks when the two DST calendars disagree).
    """
    spec = ANCHORS[anchor]
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    if spec["calendar"] is None:
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        early = set()
    else:
        cal = load_calendar(spec["calendar"])
        first, last = cal["range"]
        if start < first or end > last:
            raise ValueError(f"{anchor} calendar covers {first}..{last}, asked {start}..{end}")
        days = [date.fromisoformat(s) for s in cal["sessions"] if start <= s <= end]
        early = {e["date"] for e in cal["early_closes"]}
    rows = []
    for day in days:
        noon = datetime.combine(day, time(12, 0), timezone.utc)
        gap = (ZoneInfo("America/New_York").utcoffset(noon.replace(tzinfo=None))
               - ZoneInfo("Europe/London").utcoffset(noon.replace(tzinfo=None)))
        rows.append({
            "date": day.isoformat(),
            "t0_ms": _utc_ms(day, spec["local"], spec["tz"]) + shift_min * 60_000,
            "early_close": day.isoformat() in early,
            "weekday": day.weekday(),
            "ny_minus_ldn_hours": gap.total_seconds() / 3600,
        })
    return pd.DataFrame(rows)
