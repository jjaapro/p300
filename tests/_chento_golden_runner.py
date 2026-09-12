"""Produce one chento golden document in a FRESH process, then print it.

`CHENTO_V3_ASSET` is read at IMPORT (strategies/sleeves/chento_triple_v3/
config.py), and it drives both the table names and `FILTER_NO_TILT`, so the
two assets are behaviourally different, not merely table-different. A single
pytest process cannot hold both goldens: a `monkeypatch.setenv` plus
`importlib.reload` half-works and leaves stale module globals and a stale
feature-cache frame behind, which then makes the BTC golden fail intermittently
depending on test order.

So the ETH golden runs here, in its own interpreter.

    python tests/_chento_golden_runner.py --asset ETH --anchor <iso>
        --fixture <path> [--execute]

Prints the golden document as JSON on stdout. Any other output is a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True, choices=["BTC", "ETH"])
    ap.add_argument("--anchor", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--price", type=float, default=None)
    a = ap.parse_args(argv)

    # Before ANY sleeve import.
    os.environ["CHENTO_V3_ASSET"] = a.asset
    os.environ["CHENTO_V3_DIAG"] = "0"
    os.environ["SSQ_DIAG"] = "0"

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))

    from strategies.support import db as dbm
    from strategies.support import clock, price_feed, trade_db, variant_registry

    tmp = Path(tempfile.mkdtemp()) / "prod.db"
    shutil.copy(a.fixture, tmp)
    dbm.PROD_DB = dbm.DASH_DB = dbm.TRADER_DB = tmp
    trade_db.DB_PATH = tmp
    trade_db.init_db()
    variant_registry.init_schema()

    from tests._golden_guard import assert_fixture_db
    assert_fixture_db()

    if a.price is not None:
        price_feed.get_current_price = lambda _a=None: a.price
        price_feed._get_current_price = lambda _a=None: a.price

    from strategies.sleeves import chento_triple_v3 as sleeve
    from strategies.sleeves.chento_triple_v3 import config as ch_cfg
    if ch_cfg.ASSET != a.asset:
        print(f"asset is {ch_cfg.ASSET}, expected {a.asset}", file=sys.stderr)
        return 2

    from tests._golden_normalize import golden_document

    clock.set_simulated_now(datetime.fromisoformat(a.anchor))
    variant = {"id": f"bot_chento_{a.asset.lower()}_test",
               "capital_usdt": 10_000.0}
    intents, status = sleeve.decide(variant, weight_pct=100.0, leverage=1.0,
                                    priority=100.0)
    if a.execute and intents:
        sleeve.execute(variant, intents[0])

    doc = golden_document(intents=intents, status=status, db_path=tmp)
    # The asset is IN the document, so a mis-set env fails loudly instead of
    # quietly testing BTC twice.
    doc["asset_under_test"] = ch_cfg.ASSET
    doc["filter_no_tilt"] = bool(ch_cfg.FILTER_NO_TILT)
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
