"""Shared fixture plumbing for the market-data goldens.

Builds a throwaway prod.db that contains the carved market tables plus the
ledger schema, points every DB constant at it, freezes the clock, and clears
the sleeve's module state.

The carved tables are read-only inputs; the trades/variants tables are created
empty so each golden starts from a known ledger. `squeeze_bull` opens its
connection with a read-only URI, so the file has to EXIST before decide() runs
— an unused tmp_path name is not enough.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from . import _golden_guard

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST = FIXTURES / "MANIFEST.json"


def fixture_db(name: str) -> Path:
    """The carved fixture, verified against its committed hash.

    Missing prod.db / missing fixture -> skip (the repo convention). A HASH
    MISMATCH is a failure, never a skip and never a re-baseline: it means the
    historical rows moved under the golden.
    """
    src = FIXTURES / f"{name}.db"
    if not src.exists():
        pytest.skip(f"fixture {name}.db not built — run "
                    f"tests/fixtures/build_sleeve_fixtures.py")
    if not MANIFEST.exists():
        pytest.skip("fixtures/MANIFEST.json missing")
    pinned = (json.loads(MANIFEST.read_text(encoding="utf-8"))
              .get(name, {}).get("sha256"))
    if pinned:
        from tests.fixtures.build_sleeve_fixtures import sha256
        got = sha256(src)
        assert got == pinned, (
            f"fixture {name}.db hash moved: pinned {pinned[:16]}... got "
            f"{got[:16]}.... The historical rows changed under this golden — "
            f"investigate before re-pinning, do NOT re-baseline.")
    return src


def make_env(name: str, tmp_path, monkeypatch, *, sleeve_keys=()) -> Path:
    """Copy the fixture, add the ledger schema, redirect everything at it."""
    from strategies.support import db as _db_mod
    from strategies.support import trade_db, variant_registry
    import botlib
    from . import _sleeve_surface as surface

    src = fixture_db(name)
    dest = (tmp_path / "prod.db").resolve()
    shutil.copy(src, dest)          # a static fixture file, not a live WAL DB

    for attr in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, attr, dest)
    monkeypatch.setattr(trade_db, "DB_PATH", dest)
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()

    _golden_guard.assert_fixture_db()
    _golden_guard.assert_diag_disabled()
    for key in sleeve_keys:
        surface.reset_module_state(key)
    return dest


def seed_open_trade(db_path: Path, *, trade_id: str, variant: str,
                    strategy: str, asset: str, direction: str,
                    entry_price: float, entry_time: str, notes: dict,
                    size_usdt: float = 5000.0, leverage: float = 1.0,
                    exit_time: str = "2099-12-31T00:00:00+00:00") -> None:
    """An open position, so the sweep half of decide() has something to act
    on. Without one, a golden only ever records the entry path — and every
    sleeve's decide() closes trades before it returns."""
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
            INSERT INTO trades
            (id, series, asset, direction, strategy, allocation_pct, leverage,
             entry_time, exit_time, status, execution_mode, strategy_variant,
             actual_entry_time, entry_price, size_usdt, qty, notes,
             current_qty, current_leverage, current_size_usdt,
             realized_pnl_usdt, avg_entry_price)
            VALUES (?, 'SJ', ?, ?, ?, 100.0, ?, ?, ?, 'open', 'paper', ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?)
        """, (trade_id, asset, direction, strategy, leverage, entry_time,
              exit_time, variant, entry_time, entry_price, size_usdt,
              size_usdt / entry_price, json.dumps(notes),
              size_usdt / entry_price, leverage, size_usdt, entry_price))
        con.commit()
    finally:
        con.close()
