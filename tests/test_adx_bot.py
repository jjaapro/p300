"""Tests for bots/adx — the standalone ADX (S-003 T2) bot."""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from bots.adx import runner
from strategies.support.dispatch import Intent


def _mk_intent(entry=100_000.0, stop=92_000.0, direction="LONG"):
    return Intent(asset="BTC", direction=direction, allocation_pct=15.0,
                  leverage=5.0, conviction=100, priority=100.0,
                  reason={"_entry_price": entry, "_stop_price": stop},
                  scheduled_exit_dt=None)


def test_fixed_r_sizing_over_effective_stop():
    # 8% stop -> notional = 10_000 * 2% / 8% = 2_500 (cap far away)
    resized, info = runner.size_intent(_mk_intent(), capital=10_000.0)
    assert info["stop_pct"] == pytest.approx(0.08)
    assert info["notional"] == pytest.approx(2_500.0)
    assert not info["at_cap"]
    assert resized.allocation_pct == 100.0
    assert resized.leverage == pytest.approx(0.25)


def test_tick_stale_mgmt_skips(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr(
        "bots.adx.strategy.signal.decide", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"cd_spot_binance": 99999.0}
        if "cd_spot_binance" in (tables or []) else {})
    out = runner.tick({"id": "x", "capital_usdt": 10_000.0})
    assert out["status"] == "stale_mgmt_inputs"
    assert out["hb_status"] == "degraded"


def test_atr_trail_level_ratchets():
    """Hand-computed trail on synthetic candles: seeds at the anchor bar's
    close - 4*ATR and ratchets up with later closes (LONG)."""
    from bots.adx.strategy import signal as adx_sig
    from strategies.support.indicators import atr as atr_fn

    candles = []
    price = 100.0
    for i in range(40):
        candles.append({"dt": f"2026-01-{i + 1:02d}" if i < 31
                        else f"2026-02-{i - 30:02d}",
                        "ts": 0, "open": price, "high": price + 2,
                        "low": price - 2, "close": price + 1})
        price += 1.0

    entry_iso = "2026-02-05T10:00:00+00:00"      # anchor = last dt < 02-05
    level = adx_sig._atr_trail_level(candles, entry_iso, "LONG")
    a = atr_fn(candles, adx_sig.ATR_TRAIL_PERIOD)
    anchor = max(i for i, c in enumerate(candles) if c["dt"] < "2026-02-05")
    expected = max(candles[j]["close"] - adx_sig.ATR_TRAIL_MULT * a[j]
                   for j in range(anchor, len(candles))
                   if not math.isnan(a[j]))
    assert level == pytest.approx(expected)
    # rising closes -> the newest bar dominates the ratchet
    assert level == pytest.approx(
        candles[-1]["close"] - adx_sig.ATR_TRAIL_MULT * a[-1])


# ─── Scheduled-exit backstop (BACKLOG 4.4) and a raising decide ──────────────
# ADX trades carry the 2099 no-exit placeholder, so the backstop cannot reach
# one today. Until 2026-09-14 it would have raised if it did: it called the
# generic close with no stop resolver. These force a real exit time.

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Tmp prod.db with a full-OHLC btc_1m (the stop path walks open/high/
    low/close), flat at 50,500 and nowhere near the 10% stop; a simulated
    clock, so the resolver does not demand a finalised live exit minute; no
    daily candles and no ATR trail, so the fixed stop is the only level; a
    50,500 quote; funding stubbed at -0.10% for a long."""
    from bots.adx import config as botcfg
    from bots.adx.strategy import signal as adx
    from strategies.support import clock, funding, price_feed, trade_db, variant_registry
    from strategies.support import db as _db_mod
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("CREATE TABLE btc_1m (open_time INTEGER PRIMARY KEY, "
                    "open REAL, high REAL, low REAL, close REAL)")
        for k in range(1, 6):
            con.execute("INSERT INTO btc_1m VALUES (?,?,?,?,?)",
                        (int((NOW - timedelta(minutes=k)).timestamp() * 1000),
                         50_500.0, 50_500.0, 50_500.0, 50_500.0))
        con.commit()
    finally:
        con.close()
    clock.set_simulated_now(NOW)
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: 50_500.0)
    monkeypatch.setattr(funding, "accrued_pct",
                        lambda asset, a, b, d: -0.10 if str(d).upper() == "LONG" else 0.10)
    monkeypatch.setattr(adx, "_load_btc_daily_candles", lambda: [])
    monkeypatch.setattr(adx, "ATR_TRAIL_MULT", 0)
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    from strategies import trades
    tid = trades.open_paper_trade(
        variant=variant, sleeve_name="ADX", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"stop_loss_pct": 10}, scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso="2026-09-01")
    yield db_path, variant, tid
    clock.set_simulated_now(None)


def _closed_row(db_path, tid):
    con = sqlite3.connect(str(db_path))
    try:
        status, pnl, notes = con.execute(
            "SELECT status, pnl_usdt, notes FROM trades WHERE id=?", (tid,)).fetchone()
        fee = con.execute(
            "SELECT fee_usdt FROM trade_adjustments WHERE trade_id=? "
            "AND event_type='CLOSE'", (tid,)).fetchone()
    finally:
        con.close()
    return status, pnl, notes, (fee[0] if fee else None)


def test_backstop_closes_an_overdue_adx_trade_through_the_sleeve_close(ledger, monkeypatch):
    db_path, variant, tid = ledger
    # The sleeve's own sweep must not get there first.
    monkeypatch.setattr("bots.adx.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_action"}))

    out = runner.tick(variant)

    assert out.get("backstop_closed") == [tid], out
    assert out["hb_status"] == "ok"
    status, pnl, notes, fee = _closed_row(db_path, tid)
    assert status == "closed"
    # +100 price P&L - (10 bp fee + 1 bp slippage) of $10,000 - 0.10% funding.
    assert pnl == pytest.approx(79.00)
    assert fee == pytest.approx(11.00)
    assert notes.endswith(
        "\nADX_EXIT: scheduled_exit; fees=10bp RT, slip=1bp RT, funding=-0.100%")


def test_backstop_resolves_an_earlier_adx_stop(ledger, monkeypatch):
    """The closer carries ADX's stop resolver, not just its costs: a 10% stop
    crossed three hours before the due time is booked at the stop, on the
    minute after the crossing bar, labelled stop_loss — not at the tick's
    50,500 quote."""
    db_path, variant, tid = ledger
    hit = NOW - timedelta(hours=3)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("INSERT INTO btc_1m VALUES (?,?,?,?,?)",
                    (int(hit.timestamp() * 1000), 46_000.0, 46_000.0, 44_000.0, 45_500.0))
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr("bots.adx.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_action"}))

    out = runner.tick(variant)

    assert out.get("backstop_closed") == [tid], out
    con = sqlite3.connect(str(db_path))
    try:
        exit_price, exit_at = con.execute(
            "SELECT exit_price, actual_exit_time FROM trades WHERE id=?", (tid,)).fetchone()
    finally:
        con.close()
    status, pnl, notes, fee = _closed_row(db_path, tid)
    assert status == "closed"
    assert exit_price == pytest.approx(45_000.0)
    assert exit_at == (hit + timedelta(minutes=1)).isoformat()
    # -5,000 x 0.2 BTC - (10 bp fee + 1 bp slippage) of $10,000 - 0.10% funding
    assert pnl == pytest.approx(-1021.00)
    assert fee == pytest.approx(11.00)
    assert notes.endswith(
        "\nADX_EXIT: stop_loss; fees=10bp RT, slip=1bp RT, funding=-0.100%")


def test_decide_raising_still_runs_the_backstop(ledger, monkeypatch):
    """The error reaches the heartbeat, no entry is attempted, and the
    overdue trade still closes."""
    db_path, variant, tid = ledger

    def broken_decide(*a, **k):
        raise RuntimeError("daily candles unavailable")
    monkeypatch.setattr("bots.adx.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except RuntimeError as escaped:
        raise AssertionError("decide's error escaped the tick, so the backstop "
                             "never ran") from escaped

    assert out.get("backstop_closed") == [tid], out
    assert _closed_row(db_path, tid)[0] == "closed"
    assert out["status"] == "decide_error"
    assert out["hb_status"] == "error"
    assert out["hb_note"] == repr(RuntimeError("daily candles unavailable"))
    assert out["evaluated"] is False and "opened" not in out


def test_backstop_refusal_keeps_closed_ids_and_turns_the_heartbeat_error(ledger, monkeypatch):
    """A due trade of a strategy ADX has no closer for stays open, is named in
    the heartbeat note with status 'error', and does not escape the tick; the
    ADX trade that did close is still reported."""
    from strategies import trades
    db_path, variant, own = ledger
    foreign = trades.open_paper_trade(
        variant=variant, sleeve_name="SQUEEZE_BULL", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"t": "foreign"}, scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso="foreign")
    monkeypatch.setattr("bots.adx.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_action"}))

    try:
        out = runner.tick(variant)
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the refusal escaped the tick") from escaped

    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out
    assert out["hb_status"] == "error" and "SQUEEZE_BULL" in out["hb_note"]
    assert _closed_row(db_path, own)[0] == "closed"
    assert _closed_row(db_path, foreign)[0] == "open"


def test_decide_error_and_backstop_refusal_both_reach_the_heartbeat(ledger, monkeypatch):
    """On a tick where decide raises AND the backstop refuses a trade, the
    note keeps the decide error first and appends the refusal."""
    from strategies import trades
    db_path, variant, own = ledger
    foreign = trades.open_paper_trade(
        variant=variant, sleeve_name="SQUEEZE_BULL", asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason={"t": "foreign"}, scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso="foreign")

    def broken_decide(*a, **k):
        raise RuntimeError("daily candles unavailable")
    monkeypatch.setattr("bots.adx.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except (RuntimeError, botlib.BackstopRefused) as escaped:
        raise AssertionError("an error escaped the tick") from escaped

    assert out["status"] == "decide_error" and out["hb_status"] == "error"
    assert out["hb_note"].startswith(
        repr(RuntimeError("daily candles unavailable"))
        + "; backstop left 1 due trade(s) open in "), out["hb_note"]
    assert "SQUEEZE_BULL" in out["hb_note"]
    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out


def test_stale_mgmt_tables_skip_the_backstop(ledger, monkeypatch):
    """Kept on purpose (BACKLOG 18): the close prices off btc_1m and resolves
    stops from the daily candles, so a stale-mgmt tick leaves a due trade
    open."""
    from bots.adx import config as botcfg
    db_path, variant, tid = ledger

    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr("bots.adx.strategy.signal.decide", boom)
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"cd_spot_binance": 99_999.0}
                        if tables == botcfg.MGMT_TABLES else {})

    out = runner.tick(variant)

    assert out["status"] == "stale_mgmt_inputs" and out["hb_status"] == "degraded"
    assert "backstop_closed" not in out, out
    assert _closed_row(db_path, tid)[0] == "open"
