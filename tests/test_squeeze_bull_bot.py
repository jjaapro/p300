"""Tests for bots/squeeze_bull — the standalone Squeeze Bull bot.

Signal parity with the research is covered by tests/test_squeeze_bull_parity.py.
These cover what the BOT and the sleeve's live path add: stale-input policy,
fixed-R sizing, the once-per-hour entry gate, the already-open guard, and the
stop / target / time-stop sweep.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import botlib
from bots.squeeze_bull import config as botcfg
from bots.squeeze_bull import runner
from strategies.sleeves.squeeze_bull import signal as sleeve
from strategies.support import clock
from strategies.support import db as _db_mod
from strategies.support import price_feed, trade_db, variant_registry

CAPITAL = 10_000.0
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
ENTRY = 70_000.0


def _bars(now, n=800, *, flush_at_last=True, bull=True):
    """Synthetic hourly frame ending at the bar that closed before `now`.

    Flat price and flat open interest, except: bars older than 29 days sit
    30% lower so the causal 30-day return clears +10% when `bull`, and the
    final bar drops OI 3% and price 1% against the bar four hours earlier,
    which is the trigger.
    """
    end = int(now.replace(minute=0, second=0, microsecond=0).timestamp())
    out = []
    for k in range(n, 0, -1):
        px = ENTRY / 1.30 if (bull and k / 24.0 > 29) else ENTRY
        out.append({"ts": end - k * 3600, "open": px, "high": px, "low": px,
                    "close": px, "oi_close": 1_000_000.0})
    if flush_at_last:
        last = out[-1]
        last["oi_close"] = out[-5]["oi_close"] * 0.97      # -3% over 4 bars
        last["close"] = out[-5]["close"] * 0.99            # -1% over 4 bars
        last["high"] = last["low"] = last["open"] = last["close"]
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: ENTRY)
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    monkeypatch.setattr(botcfg, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(botcfg, "DIAG_PATH", tmp_path / "logs" / "diag.jsonl")
    sleeve._last_eval_hour.clear()
    clock.set_simulated_now(NOW)
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name=botcfg.SHORT_NAME,
        capital_usdt=CAPITAL, bot_name=botcfg.BOT_NAME)
    yield {"db": db_path, "variant": variant,
           "cfg": {"weight_pct": 100.0, "_effective_leverage": 1.0, "priority": 100}}
    clock.set_simulated_now(None)
    sleeve._last_eval_hour.clear()


def _trades(db_path):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY entry_time")]
    finally:
        con.close()


# ─── stale-input policy ──────────────────────────────────────────────────

def test_stale_mgmt_tables_short_circuit_the_tick(env, monkeypatch):
    """A stale price feed means exits cannot be priced, so nothing runs."""
    called = []
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"btc_1m": 999.0} if tables == botcfg.MGMT_TABLES else {})
    monkeypatch.setattr(sleeve, "try_decide_for_variant",
                        lambda *a, **k: called.append(1) or ([], {}))
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "stale_mgmt_inputs"
    assert out["hb_status"] == "degraded"
    assert not called, "decide must not run when the price feed is stale"


def test_stale_entry_tables_block_the_entry_but_not_the_sweep(env, monkeypatch):
    """cd_open_interest is a live-read table; a stale one must not trade."""
    from strategies.support.dispatch import Intent
    intent = Intent(asset="BTC", direction="LONG", allocation_pct=100.0,
                    leverage=1.0, conviction=100, priority=100,
                    reason={"_entry_price": ENTRY, "_stop_price": ENTRY * 0.98,
                            "_target_price": ENTRY * 1.03, "bar_ts": 1},
                    scheduled_exit_dt=NOW + timedelta(hours=48))
    monkeypatch.setattr(sleeve, "try_decide_for_variant",
                        lambda *a, **k: ([intent], {"status": "decided"}))
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"cd_open_interest": 5.0}
                        if tables == botcfg.ENTRY_TABLES else {})
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "entry_blocked_stale_inputs"
    assert out["hb_status"] == "degraded"
    assert not _trades(env["db"]), "no trade may open on stale positioning data"


# ─── sizing ──────────────────────────────────────────────────────────────

def test_fixed_r_sizing_is_half_notional_and_never_hits_the_cap(env):
    """1% risk over a 2% stop is a 0.5x notional, so the 3x cap is inert."""
    from strategies.support.dispatch import Intent
    intent = Intent(asset="BTC", direction="LONG", allocation_pct=0.0, leverage=1.0,
                    conviction=100, priority=100,
                    reason={"_entry_price": ENTRY, "_stop_price": ENTRY * 0.98},
                    scheduled_exit_dt=None)
    resized, info = runner.size_intent(intent, CAPITAL)
    assert info["stop_pct"] == pytest.approx(0.02)
    assert info["notional"] == pytest.approx(CAPITAL * 0.5)
    assert info["at_cap"] is False
    assert resized.allocation_pct == 100.0
    assert resized.leverage == pytest.approx(0.5)


# ─── entry gating ────────────────────────────────────────────────────────

def test_entry_evaluated_once_per_hour(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now, flush_at_last=False))
    first = sleeve.try_decide_for_variant(env["variant"], env["cfg"])
    second = sleeve.try_decide_for_variant(env["variant"], env["cfg"])
    assert first[1]["status"] == "no_flush"
    assert second[1]["status"] == "already_evaluated_this_hour"


def test_no_bars_does_not_burn_the_hour(env, monkeypatch):
    """A data outage must not consume the hour's single evaluation slot."""
    monkeypatch.setattr(sleeve, "_load_hourly", lambda now, lookback_days=45: [])
    assert sleeve.try_decide_for_variant(env["variant"], env["cfg"])[1]["status"] == "no_bars"
    assert sleeve.try_decide_for_variant(env["variant"], env["cfg"])[1]["status"] == "no_bars"


def test_flush_in_bull_regime_fires_and_opens_one_trade(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "decided", out
    rows = _trades(env["db"])
    assert len(rows) == 1
    tr = rows[0]
    assert tr["direction"] == "LONG" and tr["asset"] == "BTC"
    blob = json.loads(tr["notes"])
    assert blob["_stop_price"] == pytest.approx(tr["entry_price"] * 0.98)
    assert blob["_target_price"] == pytest.approx(tr["entry_price"] * 1.03)
    assert blob["regime"] == "bull_30d"
    assert blob["ret_30d_backonly"] > 0.10


def test_flat_regime_does_not_fire(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now, bull=False))
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "regime_not_bull"
    assert not _trades(env["db"])


def test_no_flush_does_not_fire(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now, flush_at_last=False))
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "no_flush"
    assert not _trades(env["db"])


def test_open_position_blocks_a_second_entry(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"], env["cfg"])
    assert len(_trades(env["db"])) == 1
    sleeve._last_eval_hour.clear()
    clock.set_simulated_now(NOW + timedelta(hours=1))
    out = runner.tick(env["variant"], env["cfg"])
    assert out["status"] == "position_open"
    assert len(_trades(env["db"])) == 1


# ─── exits ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("side,kind", [(0.979, "stop"), (1.031, "target")])
def test_sweep_closes_on_stop_and_target(env, monkeypatch, side, kind):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"], env["cfg"])
    assert len(_trades(env["db"])) == 1
    # price relative to the ACTUAL entry (the flush bar closes below ENTRY)
    price = float(_trades(env["db"])[0]["entry_price"]) * side
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: price)
    closed = sleeve._sweep_open_positions(env["variant"]["id"])
    assert closed == 1, kind
    tr = _trades(env["db"])[0]
    assert tr["status"] == "closed"
    assert tr["exit_price"] == pytest.approx(price)


def test_sweep_closes_on_time_stop(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"], env["cfg"])
    clock.set_simulated_now(NOW + timedelta(hours=49))
    closed = sleeve._sweep_open_positions(env["variant"]["id"])
    assert closed == 1
    assert _trades(env["db"])[0]["status"] == "closed"


def test_sweep_skips_when_price_is_unavailable(env, monkeypatch):
    """No price means no exit decision — never close at a guessed price."""
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"], env["cfg"])
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: None)
    assert sleeve._sweep_open_positions(env["variant"]["id"]) == 0
    assert _trades(env["db"])[0]["status"] == "open"


# ─── wiring ──────────────────────────────────────────────────────────────

def test_bot_is_wired_into_the_fleet_surfaces():
    import dashboard.botinfo as botinfo
    import dashboard.procscan as procscan
    import monitor
    assert procscan.UNIT_SCRIPTS["squeeze_bull"] == "bots/squeeze_bull/runner.py"
    assert "squeeze_bull" in monitor.BOT_EXPECTATIONS
    assert "squeeze_bull" in botinfo.BOTS
    meta = botinfo.BOTS["squeeze_bull"]
    assert (botinfo.CALIB_DIR / meta["calibration"]).is_file()
