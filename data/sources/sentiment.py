"""Sentiment index — Crypto Fear & Greed (alternative.me).

Daily index, 0-100, classifies into Extreme Fear / Fear / Neutral / Greed /
Extreme Greed. Free public API at https://api.alternative.me/fng/, no auth.
History reaches back to 2018-02-01.

Empirical findings on F&G as a signal (2018-2026, 8y daily data):

  Standalone (buy F&G<25, sell F&G>55) is NEGATIVE: 9 signals over 8y,
    cumulative -22.8%. F&G is correlated with regime, not predictive.

  As an event-window FILTER, it has clear edge:
    F&G at FOMC -> T-10h..T+0.5h BTC return:
      Extreme Fear (0-25)   8/8   wins  (+2.40%)  <- best
      Fear (26-45)         13     events (+0.75%, 62%)
      Neutral (46-55)       9     events (+0.09%, 67%)
      Greed (56-75)        17     events (+1.27%, 82%)
      Extreme Greed (76+)   2/5   wins  (+1.18%)  <- worst

  This is the OPPOSITE of buy-and-hold contrarian intuition: at short
  event windows, fear bottoms get relief rallies, greed peaks dump.

The FOMC sleeve uses F&G as one of its skip/trade gates. Other sleeves
may consume it for sizing or veto.

Storage (2026-05-16): the daily series lives in the ``fear_greed_index``
table in ``prod.db``. Previously cached as ``data/fear_greed.json``;
the migration backfills the table from the JSON on first
:func:`refresh` if the table is empty.

Refresh cadence: once per day is sufficient (index updates daily 00:00 UTC).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
LEGACY_JSON_PATH = REPO / "data" / "fear_greed.json"
API_URL = "https://api.alternative.me/fng/?limit=0&format=json"

log = logging.getLogger("p300.sentiment")


# ─── Storage backend (prod.db.fear_greed_index) ─────────────────────────────

def _db_path() -> str:
    """Lookup PROD_DB at call time so tests / sim can monkeypatch
    ``strategies.support.db.PROD_DB`` and have us follow."""
    from strategies.support import db
    return str(db.PROD_DB)


def _ensure_schema(con: sqlite3.Connection) -> None:
    """Create the fear_greed_index table if it's missing. Cheap; we run
    it on every read+write call so this module never asserts a separate
    init_db() ran first (a few tests build minimal DBs without invoking
    init_db, and we want them to keep working)."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS fear_greed_index (
            date TEXT PRIMARY KEY,
            value INTEGER NOT NULL,
            classification TEXT
        )
    """)


def _upsert_rows(rows: list[tuple[str, int, str | None]]) -> int:
    """INSERT OR REPLACE per row. Returns count written."""
    if not rows:
        return 0
    con = sqlite3.connect(_db_path())
    try:
        _ensure_schema(con)
        con.executemany(
            "INSERT OR REPLACE INTO fear_greed_index "
            "(date, value, classification) VALUES (?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()
    return len(rows)


def _rowcount() -> int:
    con = sqlite3.connect(_db_path())
    try:
        _ensure_schema(con)
        n = con.execute("SELECT COUNT(*) FROM fear_greed_index").fetchone()[0]
    finally:
        con.close()
    return int(n)


def _backfill_from_legacy_json() -> int:
    """One-shot: if the DB table is empty and ``data/fear_greed.json``
    exists, populate the table from it. Returns rows written. Called
    from :func:`refresh` so a fresh install / restored DB / pre-migration
    backup picks up history without an extra step."""
    if not LEGACY_JSON_PATH.exists():
        return 0
    try:
        payload = json.loads(LEGACY_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"legacy fear_greed.json unreadable, skipping backfill: {e}")
        return 0
    rows: list[tuple[str, int, str | None]] = []
    for r in payload.get("data", []):
        try:
            ts = int(r["timestamp"])
            value = int(r["value"])
        except (KeyError, ValueError, TypeError):
            continue
        date_iso = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        classification = r.get("value_classification")
        rows.append((date_iso, value, classification))
    return _upsert_rows(rows)


# ─── Fetch + cache ───────────────────────────────────────────────────────────

def refresh() -> bool:
    """Download full F&G history (~3000 rows) and upsert into the
    ``fear_greed_index`` table. Cheap (<200KB on the wire). Idempotent
    via INSERT OR REPLACE. Returns True on success.

    First call also backfills from the legacy ``data/fear_greed.json``
    if the table is empty — covers the bootstrap case where someone
    deletes prod.db and restarts.

    No-op (returns False) in sim mode — sim must not hit the network.
    """
    from strategies.support import clock
    if clock.is_simulated():
        return False
    # One-shot legacy backfill if the table is empty.
    if _rowcount() == 0:
        n = _backfill_from_legacy_json()
        if n:
            log.info(f"backfilled {n} F&G rows from {LEGACY_JSON_PATH.name}")
    req = Request(API_URL, headers={"User-Agent": "p300/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
        parsed = json.loads(data)
        rows_raw = parsed.get("data")
        if not isinstance(rows_raw, list):
            log.warning("F&G response missing 'data' array")
            return False
        rows: list[tuple[str, int, str | None]] = []
        for r in rows_raw:
            try:
                ts = int(r["timestamp"])
                value = int(r["value"])
            except (KeyError, ValueError, TypeError):
                continue
            date_iso = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            classification = r.get("value_classification")
            rows.append((date_iso, value, classification))
        _upsert_rows(rows)
        return True
    except (URLError, OSError, json.JSONDecodeError) as e:
        log.warning(f"F&G refresh failed: {e}")
        return False


# ─── Read ────────────────────────────────────────────────────────────────────

def get_value(date_str: str) -> int | None:
    """F&G on `date_str` (YYYY-MM-DD). Returns None if not in storage."""
    con = sqlite3.connect(_db_path())
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT value FROM fear_greed_index WHERE date=?",
            (date_str,),
        ).fetchone()
    finally:
        con.close()
    return int(row[0]) if row else None


def get_latest() -> tuple[str, int] | None:
    """Most recent (date, value) tuple, or None if storage is empty."""
    con = sqlite3.connect(_db_path())
    try:
        _ensure_schema(con)
        row = con.execute(
            "SELECT date, value FROM fear_greed_index "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    return (row[0], int(row[1]))


def invalidate_cache() -> None:
    """Kept for backward-compatibility with the pre-DB caller surface;
    the DB-backed reads have no in-process cache to invalidate."""
    return None


# ─── Bucketing ───────────────────────────────────────────────────────────────

def bucket(value: int | None) -> str:
    """Map F&G value to one of the standard 5 buckets.

    Boundaries match alternative.me's own classification with one
    refinement: we treat value<=25 as Extreme Fear (vs their <=24) so the
    boundary aligns with the FOMC backtest's binning.
    """
    if value is None:
        return "unknown"
    if value <= 25:
        return "extreme_fear"
    if value <= 45:
        return "fear"
    if value <= 55:
        return "neutral"
    if value <= 75:
        return "greed"
    return "extreme_greed"
