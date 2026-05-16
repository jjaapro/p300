"""Fed Funds Target Rate trajectory + phase classification.

Source: NY Fed Reference Rates feed (Markets Data API), pulled as XML at
https://markets.newyorkfed.org/read?...&format=xml. The XML covers EFFR,
OBFR, TGCR, BGCR, SOFR per business day. We extract `targetRateTo`
(upper bound of the Fed Funds target band) into a date->rate map and
derive the per-meeting rate-change history.

Phase definitions (from empirical analysis 2020-2026):
  zirp_hold    — rate <= 0.25%, no change in trailing 90d
  hiking       — rate now > rate 90d ago by > 10bp
  peak_hold    — rate >= 5.0%, no change in trailing 90d
  cutting      — rate now < rate 90d ago by > 10bp
  mid_hold     — none of the above (post-cut pause, mid-range rate)

History per phase (52 FOMC events, T-10h -> T+0.5h BTC return):
  peak_hold    8/8 wins, +1.69% mean   <- best
  hiking       10/12 wins, +1.69% mean
  zirp_hold    11/14 wins, +1.17% mean
  cutting      7/10 wins, +1.26% mean
  mid_hold     2/8 wins, -0.70% mean   <- worst, FOMC sleeve skips

Refresh cadence: daily is plenty (rate changes only on FOMC days).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
# 2026-05-16 reorg: generated/cached external feeds live under
# ``data/archive/`` so the data-dir root stays readable.
XML_PATH = REPO / "data" / "archive" / "nyfed_rates.xml"
JSON_PATH = REPO / "data" / "archive" / "fed_funds_target_upper.json"

NYFED_URL = (
    "https://markets.newyorkfed.org/read"
    "?startDt=2019-01-01&endDt={end}"
    "&eventCodes=510,515,520,500,505&productCode=50"
    "&sort=postDt:-1,eventCode:1&format=xml"
)

log = logging.getLogger("p300.fed_funds")


# ─── Fetch + cache ───────────────────────────────────────────────────────────

def refresh_xml() -> bool:
    """Download fresh NY Fed rates XML. Returns True on success.

    The endpoint serves up to ~3.9MB (full daily history 2019->present).
    Idempotent — safe to call from binance_feed.refresh_all() each cycle.
    Server enforces a UA filter; default urllib UA gets 403.

    No-op in sim mode — sim runs against pre-populated cached data and
    must never hit the network. Returns False so the caller treats it
    as 'no fresh fetch this cycle'."""
    from strategies.support import clock
    if clock.is_simulated():
        return False
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = NYFED_URL.format(end=end)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 p300/1.0"})
    try:
        with urlopen(req, timeout=60) as resp:
            data = resp.read()
        XML_PATH.parent.mkdir(parents=True, exist_ok=True)
        XML_PATH.write_bytes(data)
        _parse_to_json()
        invalidate_cache()
        return True
    except (URLError, OSError) as e:
        log.warning(f"NY Fed XML refresh failed: {e}")
        return False


def _parse_to_json() -> None:
    """Parse XML -> data/fed_funds_target_upper.json. Idempotent."""
    text = XML_PATH.read_text(encoding="utf-8")
    blocks = re.findall(r"<rate>(.*?)</rate>", text, re.DOTALL)
    target_by_date: dict[str, float] = {}
    for blk in blocks:
        md = re.search(r"<effectiveDate>(.*?)</effectiveDate>", blk)
        mt = re.search(r"<targetRateTo>(.*?)</targetRateTo>", blk)
        if md and mt and mt.group(1).strip():
            target_by_date.setdefault(md.group(1), float(mt.group(1)))
    sorted_dates = sorted(target_by_date.keys())
    changes: list[tuple[str, float, float]] = []
    prev: float | None = None
    for d in sorted_dates:
        r = target_by_date[d]
        if prev is not None and abs(r - prev) > 1e-9:
            changes.append((d, r, round((r - prev) * 100, 2)))
        prev = r
    JSON_PATH.write_text(
        json.dumps({"changes": changes,
                    "as_of": sorted_dates[-1] if sorted_dates else ""},
                   indent=2),
        encoding="utf-8",
    )


# ─── Read ────────────────────────────────────────────────────────────────────

_changes_cache: list[tuple[str, float, float]] | None = None


def _changes() -> list[tuple[str, float, float]]:
    """Returns list of (effective_date, rate_after, change_bp), date-asc.
    Loaded lazily and cached per-process. Call refresh_xml() to update."""
    global _changes_cache
    if _changes_cache is None:
        if not JSON_PATH.exists():
            log.warning(f"{JSON_PATH} missing; call fed_funds_service.refresh_xml()")
            _changes_cache = []
        else:
            payload = json.loads(JSON_PATH.read_text(encoding="utf-8"))
            _changes_cache = [tuple(x) for x in payload.get("changes", [])]
    return _changes_cache


def invalidate_cache() -> None:
    """Force the next read to re-parse the JSON. Useful after a refresh."""
    global _changes_cache, _change_map_cache
    _changes_cache = None
    _change_map_cache = None


# Pre-2019-08 floor (data starts 2019-01 with the rate at 2.50%; first
# recorded change is the 2019-08-01 cut). For older dates we just hold
# at 2.50% — the FOMC sleeve only ever needs 2020-onward.
_PRE_HISTORY_RATE = 2.50


def get_target_rate(date_str: str) -> float:
    """Fed Funds target upper bound in effect on `date_str` (YYYY-MM-DD).
    Forward-fill from the most recent prior change."""
    last = _PRE_HISTORY_RATE
    for d, r, _bp in _changes():
        if d <= date_str:
            last = r
        else:
            break
    return last


def get_change_at(fomc_date: str) -> float:
    """Rate change in bp announced at this FOMC. NY Fed effective date is
    typically T+1, so we check d, d+1, d+2 and return the change if any."""
    cm = _change_map()
    for off in range(0, 3):
        cd = (datetime.fromisoformat(fomc_date).date()
              + timedelta(days=off)).isoformat()
        if cd in cm:
            return cm[cd][1]
    return 0.0


_change_map_cache: dict[str, tuple[float, float]] | None = None


def _change_map() -> dict[str, tuple[float, float]]:
    global _change_map_cache
    if _change_map_cache is None:
        _change_map_cache = {d: (r, bp) for d, r, bp in _changes()}
    return _change_map_cache


# ─── Phase classifier ────────────────────────────────────────────────────────

def classify_phase(date_str: str) -> str:
    """Categorize the rate environment on `date_str`.

    Returns one of: zirp_hold, hiking, peak_hold, cutting, mid_hold.
    See module docstring for the empirical edge associated with each.
    """
    today = datetime.fromisoformat(date_str).date()
    r_now = get_target_rate(today.isoformat())
    r_90d = get_target_rate((today - timedelta(days=90)).isoformat())
    if r_now > r_90d + 0.10:
        return "hiking"
    if r_now < r_90d - 0.10:
        return "cutting"
    if r_now <= 0.25 + 1e-9:
        return "zirp_hold"
    if r_now >= 5.00 - 1e-9:
        return "peak_hold"
    return "mid_hold"


# ─── DB integration (optional) ───────────────────────────────────────────────

def init_schema(db_path: Path) -> None:
    """Idempotent — creates a small lookup table mirroring the JSON, for
    SQL joins in research notebooks. The service itself reads from the
    JSON file; this table is a convenience artifact."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS fed_funds_changes (
                effective_date TEXT PRIMARY KEY,
                rate_after_pct REAL NOT NULL,
                change_bp REAL NOT NULL
            )
        """)
        con.commit()
        for d, r, bp in _changes():
            con.execute(
                "INSERT OR REPLACE INTO fed_funds_changes VALUES (?, ?, ?)",
                (d, r, bp),
            )
        con.commit()
    finally:
        con.close()
