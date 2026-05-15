"""strategies.support.db — canonical path to the consolidated SQLite DB.

Single source of truth for the bot's DB path. P2.6 consolidated the
previous two-DB split (data/trader.db + data/dashboard.db) into one
``data/prod.db``; ``TRADER_DB`` and ``DASH_DB`` are kept as aliases
pointing at the same file so existing callers and test monkeypatches
work without touching the read sites.

Migration: ``studies/simulation/migrate_to_prod_db.py``.

Why module-attribute lookup (``db.PROD_DB`` etc., not
``from db import PROD_DB``): tests / sim mode monkeypatch the constants
here in one place. ``monkeypatch.setattr("strategies.support.db.PROD_DB",
...)`` reaches every consumer that reads ``db.PROD_DB`` at call time.

Callers keep using ``str(db.DASH_DB)`` (or the new ``str(db.PROD_DB)``)
with ``sqlite3.connect`` — no API change beyond the path.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# The consolidated DB — holds every table the bot reads/writes:
# market data (btc_1m, eth_1m, cd_*, ca_long_short_ratio,
# scheduled_events, news_headlines, fomc_observer) AND bot state
# (variants, trades, trade_adjustments, ai_quant_decisions,
# variant_daily_returns, variant_events, config).
PROD_DB: Path = _REPO / "data" / "prod.db"

# Legacy aliases — point at PROD_DB so old read sites don't have to
# change. Sim mode and tests that monkeypatch one alias affect both.
TRADER_DB: Path = PROD_DB
DASH_DB: Path = PROD_DB
