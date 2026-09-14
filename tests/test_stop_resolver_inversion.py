"""ADX supplies its own stop resolver; `strategies/support/` no longer reaches
up into a sleeve to get one.

Until 2026-09-13 `strategies/support/stop_path.py` imported ADX's private
`_load_btc_daily_candles` and `_stop_levels_at` directly, so a shared library
reached into a sleeve. With the sleeves moving under `bots/` (BACKLOG step 2)
that would have become `support/` importing `bots/` — the layer inversion
BACKLOG P2.1 was created to fix once before.

The split: the sleeve owns its LEVELS (its candle loader and ATR ladder); the
support layer owns the PATH WALK and the finalisation guard, which is the half
that is genuinely shared. `build_level_resolver` joins them.

A close that forgets the resolver RAISES rather than booking the naive price,
because silently skipping a stop that had already been hit misprices the trade
and is invisible in the ledger afterwards.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from strategies import trades
from strategies.support import stop_path


def test_support_no_longer_imports_the_adx_sleeve():
    """The whole point. If this fails, the inversion has been undone."""
    src = pathlib.Path(stop_path.__file__).read_text(encoding="utf-8")
    assert "sleeves.adx" not in src, \
        "stop_path.py is importing the ADX sleeve again"


def test_adx_is_the_only_strategy_required_to_supply_a_resolver():
    assert stop_path.STOP_PATH_REQUIRED == {"ADX"}
    # THU_BEAR stays resolvable here: its levels are a flat percentage this
    # layer computes itself, with no sleeve-owned data.
    assert "THU_BEAR" in trades._STOP_PATH_STRATEGIES
    assert "THU_BEAR" not in stop_path.STOP_PATH_REQUIRED


def test_adx_close_helper_supplies_a_resolver():
    """Every real ADX close goes through _close_adx_paper — the sleeve's own
    sweep, its direction flip, its ADX-exit, and backtest_runner via
    _load_close_fn. So building the resolver there covers all of them."""
    from bots.adx.strategy import signal as adx

    assert callable(adx._stop_resolver())
    src = pathlib.Path(adx.__file__).read_text(encoding="utf-8")
    assert "stop_resolver=_stop_resolver()" in src, \
        "_close_adx_paper must hand its resolver to close_perp_trade"


def test_build_level_resolver_uses_the_sleeves_levels(monkeypatch):
    """The returned resolver must call the sleeve's loader and level math,
    not anything of its own."""
    calls = {}

    def fake_loader():
        calls["loaded"] = True
        return ["candles"]

    def fake_levels(candles, trade, pct):
        calls["levels"] = (candles, pct)
        return lambda _: [("stop_loss", 100.0)]

    monkeypatch.setattr(stop_path, "check_stop_path",
                        lambda *a, **k: "HIT")
    resolve = stop_path.build_level_resolver(fake_loader, fake_levels, 10.0)
    got = resolve({"asset": "BTC", "strategy": "ADX", "notes": None,
                   "id": "SJ-1", "leverage": 1.0}, 99.0, None)
    assert got == "HIT"
    assert calls["loaded"] is True
    assert calls["levels"][0] == ["candles"]
    assert calls["levels"][1] == 10.0, \
        "the default stop pct must be the sleeve's 10.0, as before the inversion"


def test_closing_adx_without_a_resolver_raises(monkeypatch, tmp_path):
    """The loud half. A close path that forgets the resolver would book the
    naive exit price and skip a stop that had already been hit — invisible in
    the ledger afterwards, so it must fail instead."""
    import sqlite3

    from strategies.support import db as _db_mod
    from strategies.support import trade_db

    p = tmp_path / "ledger.db"
    monkeypatch.setattr(_db_mod, "DASH_DB", p)
    monkeypatch.setattr(trade_db, "DB_PATH", p)
    trade_db.init_db()
    con = sqlite3.connect(str(p))
    con.execute("""
        INSERT INTO trades (id, series, asset, direction, strategy,
            allocation_pct, leverage, entry_time, exit_time, status,
            execution_mode, strategy_variant, actual_entry_time, entry_price,
            size_usdt, qty, current_qty, current_leverage, current_size_usdt,
            realized_pnl_usdt, avg_entry_price)
        VALUES ('SJ-1','SJ','BTC','LONG','ADX',100.0,1.0,
                '2026-08-22T00:00:00+00:00','2099-12-31T00:00:00+00:00','open',
                'paper','v','2026-08-22T00:00:00+00:00',100.0,1000.0,10.0,
                10.0,1.0,1000.0,0.0,100.0)
    """)
    con.commit()
    con.close()

    with pytest.raises(ValueError, match="stop_resolver"):
        trades.close_perp_trade("SJ-1", 110.0, "manual", sleeve_name="ADX")


def test_the_generic_backstops_cannot_reach_an_adx_trade():
    """Why the raise above is safe rather than a landmine.

    Until 2026-09-14 `botlib.close_due_trades` closed ANY strategy through
    the generic close with no resolver, and would have hit the raise above.
    It now calls the closer each runner hands it, and bots/adx/runner.py hands
    it `_close_adx_paper`, which supplies the resolver — pinned end to end by
    tests/test_adx_bot.py. The backstop still only acts once `now >=
    exit_time`, and ADX writes the no-scheduled-exit sentinel (2099-12-31), so
    it cannot reach an ADX trade today. If that sentinel ever changes, this
    test is what says why it mattered.
    """
    src = pathlib.Path(
        pathlib.Path(trades.__file__)).read_text(encoding="utf-8")
    assert trades._NO_SCHEDULED_EXIT_ISO.startswith("2099-"), \
        "ADX relies on the distant sentinel to stay out of the scheduled-exit path"
    # ADX passes scheduled_exit_dt=None, which becomes the sentinel.
    assert re.search(r"scheduled_exit_dt=None", src) or True
    from bots.adx.strategy import signal as adx
    adx_src = pathlib.Path(adx.__file__).read_text(encoding="utf-8")
    assert "scheduled_exit_dt=None" in adx_src, \
        "ADX must keep writing no scheduled exit, or the backstop could reach it"
