"""Regression tests for stops hidden by recovered closes/coarse replay ticks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest



def _decide(sleeve, cfg, variant_id="v"):
    """Drive whichever surface this sleeve exposes. ADX was migrated to
    decide(variant, **kwargs); THU_BEAR is a dormant orchestrator sleeve and
    still takes (variant, sleeve_cfg)."""
    variant = {"id": variant_id}
    if hasattr(sleeve, "decide"):
        params = cfg.get("params") or {}
        return sleeve.decide(
            variant,
            weight_pct=float(cfg.get("_effective_weight_pct",
                                     cfg.get("weight_pct", 0.0))),
            leverage=float(cfg.get("_effective_leverage", 1.0)),
            priority=float(cfg.get("priority", 100)),
            stop_loss_pct=float(params.get("stop_loss_pct", 10.0)))
    return sleeve.try_decide_for_variant(variant, cfg)

from strategies.support import clock, db, stop_path, trade_db
from strategies.trades import close_perp_trade

ENTRY = datetime(2024, 1, 4, tzinfo=timezone.utc)  # Thursday


def _at(minutes=0, seconds=0):
    return ENTRY + timedelta(minutes=minutes, seconds=seconds)


def _ms(minutes=0):
    return int(_at(minutes).timestamp() * 1000)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "stop-test.db"
    for name in ("DASH_DB", "TRADER_DB", "PROD_DB"):
        monkeypatch.setattr(db, name, path)
    monkeypatch.setattr(trade_db, "DB_PATH", path)
    monkeypatch.setattr(clock, "_simulated_now", _at(10))
    trade_db.init_db()
    with sqlite3.connect(path) as con:
        for table in ("btc_1m", "eth_1m"):
            con.execute(f"CREATE TABLE {table} (open_time INTEGER PRIMARY KEY, "
                        "open REAL, high REAL, low REAL, close REAL)")
    return path


def _seed(path, *, direction="LONG", strategy="ADX", entry=ENTRY, notes=None):
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO trades (id, series, asset, direction, strategy, "
            "allocation_pct, leverage, entry_time, actual_entry_time, status, "
            "execution_mode, strategy_variant, entry_price, size_usdt, qty, notes, "
            "current_qty, current_size_usdt, current_leverage, realized_pnl_usdt) "
            "VALUES ('stop-test','SJ','BTC',?,?,100,1,?,?,'open','paper','v',"
            "100,1000,10,?,10,1000,1,0)",
            (direction, strategy, entry.isoformat(), entry.isoformat(),
             json.dumps({"keep": "entry context"}) if notes is None else notes),
        )
    return _trade(path)


def _trade(path):
    with sqlite3.connect(path) as con:
        con.row_factory = sqlite3.Row
        return dict(con.execute("SELECT * FROM trades WHERE id='stop-test'").fetchone())


def _bar(path, minute, *, o=100, h=101, l=99, c=100):
    with sqlite3.connect(path) as con:
        con.execute("INSERT OR REPLACE INTO btc_1m VALUES (?,?,?,?,?)",
                    (_ms(minute), o, h, l, c))


def _levels(direction="LONG"):
    return lambda when: [("stop_loss", 90 if direction == "LONG" else 105)]


@pytest.mark.parametrize("direction,ohlc,fill", [
    ("LONG", (100, 113, 85, 112), 90),
    ("SHORT", (100, 108, 87, 88), 105),
    ("LONG", (88, 113, 85, 112), 88),
    ("SHORT", (108, 110, 87, 88), 108),
])
def test_wick_recovery_and_gap_open_fills(ledger, direction, ohlc, fill):
    trade = _seed(ledger, direction=direction)
    _bar(ledger, 0, **dict(zip(("o", "h", "l", "c"), ohlc)))
    hit = stop_path.check_stop_path(trade, "BTC", _levels(direction),
                                    now=_at(10), current_price=ohlc[-1])
    assert hit.price == pytest.approx(fill)
    assert hit.at == (ENTRY if ohlc[0] != 100 else _at(1))
    assert hit.reason == "stop_loss"
    # A crash between discovery and close must not checkpoint past the stop.
    assert "_stop_path_v1" not in json.loads(_trade(ledger)["notes"])


def test_tighter_stop_executes_first_when_both_touch(ledger):
    trade = _seed(ledger)
    _bar(ledger, 0, l=85)
    hit = stop_path.check_stop_path(
        trade, "BTC", lambda when: [("stop_loss", 90), ("ATR_trail", 95)],
        now=_at(1))
    assert hit.price == 95
    assert hit.reason == "ATR_trail"


def test_excludes_pre_entry_wick_and_forming_minute(ledger):
    trade = _seed(ledger, entry=_at(0, 30))
    _bar(ledger, 0, l=80)  # Earlier low inside the entry minute is unknowable.
    _bar(ledger, 1)
    _bar(ledger, 2, l=70)  # Not complete at 00:02:30.
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(2, 30)) is None
    hit = stop_path.check_stop_path(_trade(ledger), "BTC", _levels(), now=_at(3))
    assert hit.price == 90
    assert hit.at == _at(3)


def test_live_waits_for_final_refresh_even_when_feed_stalls(ledger, monkeypatch):
    trade = _seed(ledger)
    _bar(ledger, 0)
    _bar(ledger, 1)  # Newest stored row may still hold provisional OHLC.
    monkeypatch.setattr(clock, "_simulated_now", None)
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(20)) is None
    checkpoint = json.loads(_trade(ledger)["notes"])["_stop_path_v1"]
    assert checkpoint["through_ms"] == _ms(0)
    _bar(ledger, 1, l=85)  # Feed completes the old row and appends a successor.
    _bar(ledger, 2)
    hit = stop_path.check_stop_path(_trade(ledger), "BTC", _levels(), now=_at(21))
    assert hit.price == 90
    assert hit.at == _at(2)


def test_restart_reuses_checkpoint_and_preserves_notes(ledger):
    trade = _seed(ledger)
    _bar(ledger, 0)
    _bar(ledger, 1)
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(2)) is None
    saved = _trade(ledger)
    assert json.loads(saved["notes"])["keep"] == "entry context"
    assert json.loads(saved["notes"])["_stop_path_v1"]["through_ms"] == _ms(1)
    # Changes to an already finalized row do not cause a full-history rescan.
    _bar(ledger, 0, l=50)
    _bar(ledger, 2, l=89)
    hit = stop_path.check_stop_path(saved, "BTC", _levels(), now=_at(3))
    assert hit.at == _at(3)


def test_missing_minute_is_retried_after_backfill(ledger):
    trade = _seed(ledger)
    _bar(ledger, 0)
    _bar(ledger, 2)
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(3)) is None
    checkpoint = json.loads(_trade(ledger)["notes"])["_stop_path_v1"]
    assert checkpoint["gaps"] == [[_ms(1), _ms(1)]]
    _bar(ledger, 1, l=85)
    hit = stop_path.check_stop_path(_trade(ledger), "BTC", _levels(), now=_at(3))
    assert hit.price == 90
    assert hit.at == _at(2)


def test_invalid_ohlc_is_retried_not_treated_as_stop(ledger):
    trade = _seed(ledger)
    _bar(ledger, 0, o=100, h=80, l=70, c=100)
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(1)) is None
    _bar(ledger, 0, l=80)
    hit = stop_path.check_stop_path(_trade(ledger), "BTC", _levels(), now=_at(1))
    assert hit.at == _at(1)


def test_checkpoint_does_not_overwrite_concurrent_notes(ledger):
    trade = _seed(ledger)
    _bar(ledger, 0)
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE trades SET notes=?", ('{"concurrent":true}',))
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(1)) is None
    assert json.loads(_trade(ledger)["notes"]) == {"concurrent": True}


def test_quote_fallback_keeps_gap_loss_when_history_unavailable(ledger):
    trade = _seed(ledger)
    hit = stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(5),
                                    current_price=85)
    assert hit.price == 85
    assert hit.at == _at(5)


def test_future_entry_cannot_close_using_earlier_quote(ledger):
    trade = _seed(ledger, entry=_at(5))
    assert stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(4),
                                     current_price=85) is None


def test_coarse_and_minute_ticks_find_same_first_crossing(ledger):
    trade = _seed(ledger)
    for minute in range(5):
        _bar(ledger, minute, l=85 if minute == 2 else 99)
    coarse = stop_path.check_stop_path(trade, "BTC", _levels(), now=_at(5))
    fine = None
    for minute in range(1, 6):
        fine = stop_path.check_stop_path(_trade(ledger), "BTC", _levels(), now=_at(minute))
        if fine:
            break
    assert coarse == fine
    assert fine.at == _at(3)


def test_adx_trail_is_effective_only_after_daily_close(ledger, monkeypatch):
    from bots.adx.strategy import signal as adx
    from strategies.support import indicators
    trade = _seed(ledger)
    candles = [
        {"dt": "2024-01-03", "close": 100},
        {"dt": "2024-01-04", "close": 110},
    ]
    monkeypatch.setattr(indicators, "atr", lambda *args: [2, 2])
    monkeypatch.setattr(adx, "ATR_TRAIL_MULT", 4)
    levels_at = adx._stop_levels_at(candles, trade, 20)
    # Jan 4 trail = 92; Jan 4's close can raise it to 102 only on Jan 5.
    assert levels_at(_at(0)) == [("stop_loss", 80), ("ATR_trail", 92)]
    assert levels_at(_at(24 * 60)) == [("stop_loss", 80), ("ATR_trail", 102)]
    _bar(ledger, 0, o=100, h=103, l=95, c=100)
    hit = stop_path.check_stop_path(trade, "BTC", levels_at, now=_at(24 * 60))
    assert hit is None  # Using the eventual 102 trail would falsely stop here.


@pytest.mark.parametrize("strategy,direction,low,high,expected", [
    ("ADX", "LONG", 85, 113, 90),
    ("THU_BEAR", "SHORT", 87, 108, 105),
])
def test_sleeve_closes_recovered_wick_with_historical_accounting(
        ledger, monkeypatch, strategy, direction, low, high, expected):
    from bots.adx.strategy import signal as adx
    from strategies.sleeves.timing_anomalies.internal.thu_bear import signal as thu
    from strategies.support import funding, price_feed
    _seed(ledger, direction=direction, strategy=strategy)
    _bar(ledger, 0, l=low, h=high)
    funding_calls = []
    monkeypatch.setattr(funding, "accrued_pct", lambda *args:
                        funding_calls.append(args) or 0.0)
    # Missing/stale current data must not prevent walking available history.
    monkeypatch.setattr(price_feed, "_get_current_price", lambda asset: None)
    monkeypatch.setattr(adx, "_load_btc_daily_candles", lambda: [])
    monkeypatch.setattr(adx, "_current_signal", lambda candles: None)
    monkeypatch.setattr(adx, "ATR_TRAIL_MULT", 0)
    sleeve = adx if strategy == "ADX" else thu
    _decide(sleeve, {"weight_pct": 100, "_effective_leverage": 1,
                     "params": {"assets": ["BTC"], "version": "V3_enhanced",
                                "stop_loss_pct": 10 if strategy == "ADX" else 5}})
    row = _trade(ledger)
    assert row["status"] == "closed"
    assert row["exit_price"] == pytest.approx(expected)
    assert row["actual_exit_time"] == _at(1).isoformat()
    assert funding_calls[0][2] == _at(1)
    with sqlite3.connect(ledger) as con:
        event = con.execute("SELECT event_time FROM trade_adjustments "
                            "WHERE event_type='CLOSE'").fetchone()
    assert event[0] == _at(1).isoformat()


@pytest.mark.parametrize("exit_time", [_at(-1), _at(11)])
def test_close_rejects_exit_before_entry_or_after_clock(ledger, exit_time):
    _seed(ledger)
    with pytest.raises(ValueError, match="between entry and the current clock"):
        close_perp_trade("stop-test", 90, "stop_loss", "ADX",
                         apply_funding=False, exit_dt=exit_time)
    assert _trade(ledger)["status"] == "open"


@pytest.mark.parametrize("strategy,direction,low,high,stored", [
    ("ADX", "LONG", 95, 101, 10),
    ("THU_BEAR", "SHORT", 99, 103, 5),
])
def test_historical_path_uses_entry_stop_not_new_config(
        ledger, monkeypatch, strategy, direction, low, high, stored):
    from bots.adx.strategy import signal as adx
    from strategies.sleeves.timing_anomalies.internal.thu_bear import signal as thu
    from strategies.support import price_feed
    _seed(ledger, strategy=strategy, direction=direction,
          notes=json.dumps({"sl_semantic_price_thresh_pct": stored, "stop_loss_pct": stored}))
    _bar(ledger, 0, l=low, h=high)
    monkeypatch.setattr(clock, "_simulated_now", _at(12 * 60))
    monkeypatch.setattr(price_feed, "_get_current_price", lambda asset: 100)
    monkeypatch.setattr(adx, "_load_btc_daily_candles", lambda: [])
    monkeypatch.setattr(adx, "_current_signal", lambda candles: None)
    monkeypatch.setattr(adx, "ATR_TRAIL_MULT", 0)
    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    sleeve = adx if strategy == "ADX" else thu
    _decide(sleeve, {"weight_pct": 100, "_effective_leverage": 10,
                     "params": {"stop_loss_pct": 2, "assets": ["BTC"]}})
    assert _trade(ledger)["status"] == "open"


def test_legacy_stop_fallback_uses_immutable_entry_leverage(ledger, monkeypatch):
    trade = _seed(ledger, notes='{"stop_loss_pct":10}')
    trade.update(leverage=2, current_leverage=20)
    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    assert stop_path.entry_stop_pct(trade, 1) == 5


@pytest.mark.parametrize("kind", ["SCALE_UP", "SCALE_DOWN", "LEVERAGE_ADJUST"])
@pytest.mark.parametrize("explicit_time", [True, False])
def test_backdated_close_cannot_apply_later_resized_position(
        ledger, monkeypatch, kind, explicit_time):
    from strategies.trades import apply_scale, apply_leverage_adjust
    _seed(ledger)
    if kind == "LEVERAGE_ADJUST":
        apply_leverage_adjust("stop-test", new_leverage=2, price=120,
                              event_time=_at(5).isoformat())
    else:
        apply_scale("stop-test", new_qty=20 if kind == "SCALE_UP" else 5,
                    price=120, event_time=_at(5).isoformat())
    before = _trade(ledger)
    if not explicit_time:
        # Liquidation replay temporarily rewinds the global clock and calls
        # the normal wrapper without an explicit historical exit parameter.
        monkeypatch.setattr(clock, "_simulated_now", _at(1))
    with pytest.raises(ValueError, match="later trade adjustment"):
        close_perp_trade("stop-test", 90, "stop_loss", "ADX", apply_funding=False,
                         **({"exit_dt": _at(1)} if explicit_time else {}))
    assert _trade(ledger) == before


def test_scheduled_backstops_cannot_bypass_recovered_thursday_stop(ledger, monkeypatch):
    """The backstop must not book the due-time price over a stop the path
    already hit. Parametrized over three callers until 2026-09-13; the
    backtest_runner and orchestrator arms went with those modules, and
    botlib.close_due_trades is the one the fleet actually runs."""
    from strategies.support import funding
    _seed(ledger, direction="SHORT", strategy="THU_BEAR",
          notes='{"sl_semantic_price_thresh_pct":5}')
    due = _at(25 * 60)
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE trades SET exit_time=?", (due.isoformat(),))
    _bar(ledger, 0, h=108, l=87, c=88)
    _bar(ledger, 25 * 60 - 1, o=88, h=89, l=87, c=88)
    monkeypatch.setattr(clock, "_simulated_now", due)
    monkeypatch.setattr(funding, "accrued_pct", lambda *args: 0)
    import botlib
    assert botlib.close_due_trades("v", due) == ["stop-test"]
    row = _trade(ledger)
    assert row["exit_price"] == pytest.approx(105)
    assert row["actual_exit_time"] == _at(1).isoformat()
    assert row["pnl_usdt"] == pytest.approx(-51.5)


@pytest.mark.parametrize("strategy,direction,ohlc,expected", [
    ("ADX", "LONG", (100, 113, 85, 112), 90),
    ("THU_BEAR", "SHORT", (100, 108, 87, 88), 105),
])
def test_runner_end_window_close_cannot_bypass_stop(
        ledger, monkeypatch, strategy, direction, ohlc, expected):
    """Closing an open trade at the current clock must still resolve a stop
    the path already hit. Drove backtest_runner.mark_remaining_at_end until
    2026-09-13; now drives the same close pipeline through the sleeve helper
    the fleet uses."""
    from bots.adx.strategy import signal as adx
    from strategies.support import funding
    _seed(ledger, direction=direction, strategy=strategy)
    _bar(ledger, 0, **dict(zip(("o", "h", "l", "c"), ohlc)))
    _bar(ledger, 9, o=ohlc[3], h=ohlc[3]+1, l=ohlc[3]-1, c=ohlc[3])
    monkeypatch.setattr(funding, "accrued_pct", lambda *args: 0)
    monkeypatch.setattr(adx, "_load_btc_daily_candles", lambda: [])
    monkeypatch.setattr(adx, "ATR_TRAIL_MULT", 0)
    from strategies import trades as trades_mod
    if strategy == "ADX":
        adx._close_adx_paper("stop-test", float(ohlc[3]), "end_of_window")
    else:
        trades_mod.close_perp_trade("stop-test", float(ohlc[3]),
                                    "end_of_window", sleeve_name=strategy)
    row = _trade(ledger)
    assert row["exit_price"] == pytest.approx(expected)
    assert row["actual_exit_time"] == _at(1).isoformat()


def test_late_scheduled_close_uses_due_price_not_later_wick_or_quote(ledger, monkeypatch):
    """A close that runs late must price at the DUE time, not a later wick.
    Drove backtest_runner.close_due_for_variant until 2026-09-13."""
    from strategies.support import funding
    _seed(ledger, direction="SHORT", strategy="THU_BEAR")
    due = _at(25 * 60)
    with sqlite3.connect(ledger) as con:
        con.execute("UPDATE trades SET exit_time=?", (due.isoformat(),))
    _bar(ledger, 0)
    _bar(ledger, 25 * 60 - 1, o=95, h=96, l=94, c=95)
    _bar(ledger, 25 * 60 + 1, h=110)  # Wick after the intended exit is irrelevant.
    _bar(ledger, 25 * 60 + 9, o=80, h=81, l=79, c=80)
    monkeypatch.setattr(clock, "_simulated_now", _at(25 * 60 + 10))
    monkeypatch.setattr(funding, "accrued_pct", lambda *args: 0)
    import botlib
    assert botlib.close_due_trades("v", clock.now_utc()) == ["stop-test"]
    row = _trade(ledger)
    assert row["actual_exit_time"] == due.isoformat()
    assert row["exit_price"] == 95


def test_generic_close_stop_resolution_preserves_callers_execution_costs(ledger):
    _seed(ledger, direction="SHORT", strategy="THU_BEAR")
    _bar(ledger, 0, h=108, l=87, c=88)
    close_perp_trade("stop-test", 88, "scheduled_exit", "THU_BEAR",
                     cost_bp_rt=25, slippage_bp_rt=7, apply_funding=False)
    row = _trade(ledger)
    assert row["exit_price"] == 105
    assert row["pnl_usdt"] == pytest.approx(-53.2)


def test_close_holds_position_lock_through_computation_and_persistence(ledger, monkeypatch):
    from strategies import trades
    _seed(ledger)
    original = trades.compute_perp_close
    blocked = []

    def concurrent_resize(**kwargs):
        with sqlite3.connect(ledger, timeout=0) as con:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                con.execute("UPDATE trades SET current_qty=999 WHERE id='stop-test'")
        blocked.append(True)
        return original(**kwargs)

    monkeypatch.setattr(trades, "compute_perp_close", concurrent_resize)
    close_perp_trade("stop-test", 90, "stop_loss", "ADX",
                     apply_funding=False, exit_dt=_at(1))
    assert blocked == [True]
    row = _trade(ledger)
    assert row["status"] == "closed"
    assert row["pnl_usdt"] == pytest.approx(-101.5)


def test_live_scheduled_close_waits_for_exit_minute_finalization(ledger, monkeypatch):
    _seed(ledger, direction="SHORT", strategy="THU_BEAR")
    _bar(ledger, 0)
    _bar(ledger, 1)  # Latest row still contains its earlier provisional values.
    monkeypatch.setattr(clock, "now_utc", lambda: _at(2))
    monkeypatch.setattr(clock, "is_simulated", lambda: False)
    with pytest.raises(ValueError, match="Unfinalized BTC exit minute"):
        close_perp_trade("stop-test", 100, "scheduled_exit", "THU_BEAR",
                         apply_funding=False)
    assert _trade(ledger)["status"] == "open"
    _bar(ledger, 1, h=108)  # Finalized wick reveals the earlier stop.
    _bar(ledger, 2)
    close_perp_trade("stop-test", 100, "scheduled_exit", "THU_BEAR",
                     apply_funding=False)
    assert _trade(ledger)["exit_price"] == 105
