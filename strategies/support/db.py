"""strategies.support.db — canonical paths to the SQLite databases the bot uses.

Replaces the previous 26 duplicate ``DASH_DB`` / ``TRADER_DB`` definitions
scattered across ``services/`` and the top-level scripts. Tests can
monkeypatch the constants here in one place rather than chasing every
importer; consumers access ``db.DASH_DB`` / ``db.TRADER_DB`` (module-
attribute lookup, not ``from strategies.support.db import DASH_DB``) so that
``monkeypatch.setattr("strategies.support.db.DASH_DB", ...)`` actually reaches them.

Two databases:
  TRADER_DB  read-only ingest from binance_feed (price klines, funding
             rates, long/short ratio, scheduled events, etc.)
  DASH_DB    bot's own state — variant registry, trades log, daily NAV,
             variant_events.

The constants are plain ``pathlib.Path`` objects. Callers keep using
``str(db.DASH_DB)`` with ``sqlite3.connect`` — no API change beyond the
import-style tweak.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

TRADER_DB: Path = _REPO / "data" / "trader.db"
DASH_DB: Path = _REPO / "data" / "dashboard.db"
