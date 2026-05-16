"""strategies.support.db — canonical paths into the consolidated DB layout.

Single source of truth for where the bot's persistent data lives.
After 2026-05-16, the layout is:

    data/
      databases/
        prod.db              ← PROD_DB (= TRADER_DB = DASH_DB)
        sim_dash.db          ← created by sim.py per run (not maintained)
      archive/
        nyfed_rates.xml
        fed_funds_target_upper.json
        polymarket_fed_2026.json
        pdo_tv_validate_trades.csv
      ai_quant_archive/      ← AI_QUANT decision markdown mirror
      ai_quant_preview/      ← preview docs from research tooling
      known_unfillable.json  ← hand-curated gap config (root-of-data)

History: P2.6 (2026-05-15) consolidated the previous two-DB split
(data/trader.db + data/dashboard.db) into a single ``data/prod.db``;
``TRADER_DB`` and ``DASH_DB`` are kept as aliases pointing at the same
file so existing callers and test monkeypatches work without touching
the read sites. The 2026-05-16 reorganisation tucked ``prod.db`` under
``data/databases/`` and introduced ``DATA_DIR`` so consumers that need
"the data root" (AI_QUANT archive mirror, AI_QUANT preview generator)
have a stable anchor independent of where ``PROD_DB`` actually lives.

Why module-attribute lookup (``db.PROD_DB`` etc., not
``from db import PROD_DB``): tests / sim mode monkeypatch the constants
here in one place. ``monkeypatch.setattr("strategies.support.db.PROD_DB",
...)`` reaches every consumer that reads ``db.PROD_DB`` at call time.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# Root of the data directory. Use this when you need a stable anchor
# that doesn't follow the DB path (AI_QUANT archive + preview dirs,
# for example). Tests monkeypatch this directly when they want
# subdirectory-based generated content to land in a tmp tree.
DATA_DIR: Path = _REPO / "data"

# The consolidated DB — holds every table the bot reads/writes:
# market data (btc_1m, eth_1m, cd_*, ca_long_short_ratio,
# scheduled_events, news_headlines, fomc_observer) AND bot state
# (variants, trades, trade_adjustments, ai_quant_decisions,
# variant_daily_returns, variant_events, config).
PROD_DB: Path = DATA_DIR / "databases" / "prod.db"

# Legacy aliases — point at PROD_DB so old read sites don't have to
# change. Sim mode and tests that monkeypatch one alias affect both.
TRADER_DB: Path = PROD_DB
DASH_DB: Path = PROD_DB
