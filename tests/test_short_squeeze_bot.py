"""Tests for bots/short_squeeze — the standalone Short Squeeze bot.

Covers: the notional cap binding on tight swept-low stops (the design that
replaces the README's 20-100× leverage suggestion), stale-entry refusal,
a forced-fire integration pass (execute → sleeve price-sweep closes on
stop), and the new SSQ_DIAG per-day counter JSONL."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

os.environ["SSQ_DIAG"] = "0"          # tests must never write live diag
os.environ["CHENTO_V3_DIAG"] = "0"

import botlib  # noqa: E402
from bots.short_squeeze import config as botcfg  # noqa: E402
from bots.short_squeeze import runner  # noqa: E402
from strategies.support import clock  # noqa: E402
from strategies.support import db as _db_mod  # noqa: E402
from strategies.support import trade_db, variant_registry  # noqa: E402
from strategies.support.dispatch import Intent  # noqa: E402

T0 = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)   # NY session boundary


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "prod.db"
    monkeypatch.setattr(_db_mod, "PROD_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "DASH_DB", db_path.resolve())
    monkeypatch.setattr(_db_mod, "TRADER_DB", db_path.resolve())
    monkeypatch.setattr(trade_db, "DB_PATH", db_path.resolve())
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()
    clock.set_simulated_now(T0)
    yield db_path
    clock.set_simulated_now(None)


def _mk_intent(entry=100_000.0, stop=99_900.0, tp_r=3.0):
    risk = entry - stop
    reason = {
        "trigger": "short_squeeze_long",
        "variant_id": botcfg.VARIANT_ID,
        "sleeve": "SHORT_SQUEEZE",
        "bar_ts_utc": (T0 - timedelta(minutes=15)).isoformat(),
        "_entry_price": entry,
        "_stop_price": stop,
        "_target_price": entry + tp_r * risk,
        "_time_stop_iso": (T0 + timedelta(hours=6)).isoformat(),
    }
    return Intent(asset="BTC", direction="LONG", allocation_pct=5.0,
                  leverage=1.0, conviction=100, priority=100.0,
                  reason=reason, scheduled_exit_dt=T0 + timedelta(hours=6))


def _seed_price(db_path, at: datetime, close):
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS btc_1m (open_time INTEGER, close REAL)")
    con.execute("INSERT INTO btc_1m VALUES (?, ?)",
                (int(at.timestamp() * 1000), close))
    con.commit()
    con.close()


# ─── Sizing: the cap IS the design ────────────────────────────────────────────

def test_tight_swept_low_stop_binds_cap():
    intent = _mk_intent(entry=100_000.0, stop=99_900.0)    # 0.1% stop
    resized, info = runner.size_intent(intent, capital=10_000.0)
    # uncapped: 10_000 × 1% / 0.1% = 100_000 (10× capital) → capped at 3×
    assert info["at_cap"]
    assert info["notional"] == pytest.approx(30_000.0)
    assert resized.leverage == pytest.approx(3.0)


def test_wide_stop_uncapped():
    intent = _mk_intent(entry=100_000.0, stop=99_000.0)    # 1% stop
    resized, info = runner.size_intent(intent, capital=10_000.0)
    assert not info["at_cap"]
    assert info["notional"] == pytest.approx(10_000.0)     # 1%/1% = 1× capital


# ─── Stale-entry refusal ──────────────────────────────────────────────────────

def test_tick_stale_entry_drops_intent(tmp_db, monkeypatch):
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    monkeypatch.setattr(
        "strategies.sleeves.short_squeeze.signal.try_decide_for_variant",
        lambda v, cfg: ([_mk_intent()], {"status": "decided"}))

    def boom(*a, **k):
        raise AssertionError("execute must not run on stale entry tables")
    monkeypatch.setattr(
        "strategies.sleeves.short_squeeze.signal.execute_for_variant", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"cd_open_interest": 9999.0}
        if "cd_open_interest" in (tables or []) else {})

    out = runner.tick(variant, {})
    assert out["status"] == "entry_blocked_stale_inputs"
    assert out["hb_status"] == "degraded"


# ─── Forced-fire integration: execute → price-sweep stop close ────────────────

def test_execute_then_sweep_stop_hit(tmp_db):
    from strategies.sleeves.short_squeeze import signal as ssq

    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    intent = _mk_intent(entry=100_000.0, stop=99_900.0)
    resized, info = runner.size_intent(intent, float(variant["capital_usdt"]))
    res = ssq.execute_for_variant(variant, {}, resized)
    tid = res["trade_id"]
    assert res["status"] == "opened"

    con = sqlite3.connect(str(tmp_db))
    size_usdt, notes = con.execute(
        "SELECT size_usdt, notes FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert size_usdt == pytest.approx(info["notional"], rel=1e-6)
    assert json.loads(notes)["_stop_price"] == 99_900.0

    # price prints below the stop → sweep closes at current price
    _seed_price(tmp_db, T0 + timedelta(minutes=4), 99_850.0)
    clock.set_simulated_now(T0 + timedelta(minutes=5))
    n = ssq._sweep_open_positions(variant["id"])
    assert n == 1

    con = sqlite3.connect(str(tmp_db))
    status, exit_price = con.execute(
        "SELECT status, exit_price FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert status == "closed"
    assert exit_price == pytest.approx(99_850.0)


# ─── SSQ_DIAG counters ────────────────────────────────────────────────────────

def test_diag_counters_flush_on_day_rollover(tmp_path, monkeypatch):
    from strategies.sleeves.short_squeeze import signal as ssq

    diag_path = tmp_path / "diag.jsonl"
    monkeypatch.setattr(ssq, "_DIAG_ENABLED", True)
    monkeypatch.setattr(ssq, "_DIAG_PATH", str(diag_path))
    monkeypatch.setattr(ssq, "_diag_state", {"date": None, "counters": {}})

    d1 = datetime(2026, 7, 21, 15, 0, tzinfo=timezone.utc)
    ssq._diag_count("no_sweep", d1)
    ssq._diag_count("no_sweep", d1)
    ssq._diag_count("macro_not_short", d1)
    assert not diag_path.exists()                     # same day: buffered

    d2 = d1 + timedelta(days=1)
    ssq._diag_count("cooldown", d2)                   # rollover → flush d1
    lines = diag_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["utc_date"] == "2026-07-21"
    assert rec["counters"] == {"no_sweep": 2, "macro_not_short": 1}
    assert ssq._diag_state["counters"] == {"cooldown": 1}
