"""Import this FIRST in every golden module. Before anything else.

Two hazards, both of which have to be handled at import time rather than in a
fixture, because the thing being protected against happens at import:

1. **Diagnostics escaping to the live fleet.** The chento and short_squeeze
   sleeves resolve ``CHENTO_V3_DIAG`` / ``SSQ_DIAG`` and their paths at MODULE
   IMPORT. An autouse fixture runs at test setup — after the test module's
   imports — so it is too late. A golden module that imports a sleeve before
   these are set appends to the JSONL of a RUNNING bot, which the dashboard
   reads. tests/test_chento_bot.py does the same thing at module scope for the
   same reason.

2. **decide() running against the live ledger.** `decide()` is not a pure
   function for any sleeve: adx closes on stop-path, ADX-exit and direction
   flip; carry closes the whole book on the 30-day cumulative exit; both
   squeeze sleeves sweep stop/target/time-stop on every call. Pointed at
   prod.db it could close SJ-4242, SJ-4247 or SJ-4250 — real open positions in
   a running paper fleet. tests/conftest.py patches the clock and the
   SL-semantics env but NOT the DB paths, so nothing else stops this.

`assert_fixture_db()` is the autouse half, and it fails loudly rather than
skipping: a golden that silently degrades to "no assertions" is worse than one
that errors.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Import-time half. Must precede any `strategies.sleeves.*` import. -----
os.environ["CHENTO_V3_DIAG"] = "0"
os.environ["SSQ_DIAG"] = "0"

REPO = Path(__file__).resolve().parents[1]
LIVE_DB = (REPO / "data" / "databases" / "prod.db").resolve()


def assert_fixture_db() -> None:
    """Hard-fail if anything still points at the live ledger."""
    from strategies.support import db, trade_db

    for label, value in (("db.PROD_DB", db.PROD_DB),
                         ("db.DASH_DB", db.DASH_DB),
                         ("db.TRADER_DB", db.TRADER_DB),
                         ("trade_db.DB_PATH", trade_db.DB_PATH)):
        if Path(value).resolve() == LIVE_DB:
            raise AssertionError(
                f"{label} points at the LIVE prod.db. A golden runs decide(), "
                f"which closes trades — it would act on the running fleet's "
                f"open positions. Patch the DB paths before calling anything.")


def assert_diag_disabled() -> None:
    """The sleeves cache these at import; if a golden module forgot to import
    this module first, say so rather than writing to a live bot's log."""
    for var in ("CHENTO_V3_DIAG", "SSQ_DIAG"):
        if os.environ.get(var) != "0":
            raise AssertionError(
                f"{var} is {os.environ.get(var)!r}, not '0' — import "
                f"tests._golden_guard before the sleeve modules.")
