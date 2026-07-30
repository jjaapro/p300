"""Tests for bots/chento_v3 — the standalone Chento Triple v3 bot.

Covers: fixed-R sizing math, the stale-input refusal policy (mgmt vs entry
tables), and a forced-fire integration pass — execute through the sleeve
into a tmp ledger, then the sleeve's own bar-walking sweep closes the
position on a stop-hit bar. The runner never goes through the orchestrator.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

# Must land before any chento sleeve import: the sleeve reads the diag env
# at import time, and the runner module setdefaults it to "1" (live-on).
# Tests must never append to the live diag JSONL.
os.environ["CHENTO_V3_DIAG"] = "0"

import botlib  # noqa: E402
from bots.chento_v3 import config as botcfg  # noqa: E402
from bots.chento_v3 import runner  # noqa: E402
from strategies.support import clock  # noqa: E402
from strategies.support import db as _db_mod  # noqa: E402
from strategies.support import trade_db, variant_registry  # noqa: E402
from strategies.support.dispatch import Intent  # noqa: E402

T0 = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)   # a 15m boundary


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


def _mk_intent(entry=100_000.0, stop=97_000.0, target=118_000.0,
               direction="LONG"):
    """Intent with the exact reason/_state shape decide() builds
    (signal.py:734-763)."""
    reason = {
        "trigger": "chento_triple_v3",
        "variant_id": botcfg.VARIANT_ID,
        "sleeve": "CHENTO_TRIPLE_V3",
        "bar_ts": T0.isoformat(),
        "_entry_price": entry,
        "_stop_price": stop,
        "_target_price": target,
        "_atr_at_entry": abs(entry - stop) / 5.0,
        "_risk": abs(entry - stop),
        "_inside_va": False,
        "_ladder_size_frac": 1.0,
        "_time_stop_iso": (T0 + timedelta(hours=72)).isoformat(),
        "_filter_diag": {},
        "_state": {
            "entry_price": entry,
            "risk": abs(entry - stop),
            "stop_price": stop,
            "target_price": target,
            "ladder_added": False,
            "ladder_entry": None,
            "ladder_size_frac": 1.0,
            "atr_at_entry": abs(entry - stop) / 5.0,
            "entry_bar_ts": T0.isoformat(),
            "last_walked_ts": T0.isoformat(),
        },
    }
    return Intent(asset="BTC", direction=direction, allocation_pct=10.0,
                  leverage=5.0, conviction=100, priority=100.0,
                  reason=reason,
                  scheduled_exit_dt=T0 + timedelta(hours=72))


def _seed_15m_bar(db_path, ts: datetime, high, low, close):
    con = sqlite3.connect(str(db_path))
    con.execute("""CREATE TABLE IF NOT EXISTS cd_futures_15m (
        timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL,
        close REAL, volume REAL, volume_buy REAL, volume_sell REAL)""")
    con.execute(
        "INSERT OR REPLACE INTO cd_futures_15m "
        "(timestamp, open, high, low, close, volume, volume_buy, volume_sell) "
        "VALUES (?, ?, ?, ?, ?, 1, 1, 1)",
        (int(ts.timestamp()), close, high, low, close))
    con.commit()
    con.close()


def _seed_price(db_path, at: datetime, close):
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS btc_1m (open_time INTEGER, close REAL)")
    con.execute("INSERT INTO btc_1m VALUES (?, ?)",
                (int(at.timestamp() * 1000), close))
    con.commit()
    con.close()


# ─── Sizing ───────────────────────────────────────────────────────────────────

def test_size_intent_fixed_r():
    intent = _mk_intent(entry=100_000.0, stop=97_000.0)   # stop_pct = 3%
    resized, info = runner.size_intent(intent, capital=10_000.0)
    # 2% risk over a 3% stop -> notional = 10_000 * 0.02 / 0.03
    assert info["notional"] == pytest.approx(6_666.67, rel=1e-3)
    assert not info["at_cap"]
    assert resized.allocation_pct == 100.0
    assert resized.leverage == pytest.approx(0.6667, rel=1e-3)
    # ledger arithmetic lands on the target notional
    assert 10_000.0 * (resized.allocation_pct / 100) * resized.leverage \
        == pytest.approx(info["notional"])


def test_size_intent_notional_cap():
    intent = _mk_intent(entry=100_000.0, stop=99_500.0)   # stop_pct = 0.5%
    resized, info = runner.size_intent(intent, capital=10_000.0)
    # uncapped would be 40k -> capped at 3x capital
    assert info["notional"] == pytest.approx(30_000.0)
    assert info["at_cap"]
    assert resized.leverage == pytest.approx(3.0)


# ─── Stale-input policy ───────────────────────────────────────────────────────

def test_tick_stale_mgmt_skips_everything(tmp_db, monkeypatch):
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr(
        "strategies.sleeves.chento_triple_v3.try_decide_for_variant", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"cd_futures_15m": 7200.0}
        if "cd_futures_15m" in (tables or []) else {})

    out = runner.tick(variant, {})
    assert out["status"] == "stale_mgmt_inputs"
    assert out["hb_status"] == "degraded"


def test_tick_stale_entry_drops_intent_keeps_sweep(tmp_db, monkeypatch):
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    swept = {"n": 0}

    def fake_decide(v, cfg):
        swept["n"] += 1                      # stands in for the sweep running
        return [_mk_intent()], {"status": "decided"}

    def boom(*a, **k):
        raise AssertionError("execute must not run on stale entry tables")

    monkeypatch.setattr(
        "strategies.sleeves.chento_triple_v3.try_decide_for_variant",
        fake_decide)
    monkeypatch.setattr(
        "strategies.sleeves.chento_triple_v3.execute_for_variant", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"okx_perp_1h": None}
        if "okx_perp_1h" in (tables or []) else {})

    out = runner.tick(variant, {})
    assert swept["n"] == 1                   # sweep/decide DID run
    assert out["status"] == "entry_blocked_stale_inputs"
    assert out["hb_status"] == "degraded"
    assert "opened" not in out


# ─── P0 boundary fix (2026-07-22): live entry-eval path ──────────────────────
# The live path anchors on wall-clock 15m boundaries and evaluates the
# JUST-CLOSED bar with final values; replay keeps the old selection. These
# tests drive the live branch by patching clock.is_simulated -> False while
# the simulated clock still controls now_utc.

def _seed_15m_range(db_path, start: datetime, n_bars: int, price=100_000.0):
    con = sqlite3.connect(str(db_path))
    con.execute("""CREATE TABLE IF NOT EXISTS cd_futures_15m (
        timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL,
        close REAL, volume REAL, quote_volume REAL,
        volume_buy REAL, volume_sell REAL,
        quote_volume_buy REAL, quote_volume_sell REAL)""")
    for i in range(n_bars):
        ts = int((start + timedelta(minutes=15 * i)).timestamp())
        con.execute(
            "INSERT OR REPLACE INTO cd_futures_15m VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (ts, price, price + 50, price - 50, price, 10, 10 * price,
             5, 5, 5 * price, 5 * price))
    con.execute("""CREATE TABLE IF NOT EXISTS ca_long_short_ratio (
        timestamp INTEGER, asset TEXT, ratio REAL, long_pct REAL,
        short_pct REAL)""")
    con.execute("CREATE TABLE IF NOT EXISTS okx_perp_1h "
                "(timestamp INTEGER PRIMARY KEY, close REAL)")
    con.commit()
    con.close()


@pytest.fixture
def live_clock(tmp_db, monkeypatch):
    """Live-mode time control: now_utc follows the simulated value but
    is_simulated() reports False, so the sleeve takes the LIVE branch."""
    from strategies.sleeves.chento_triple_v3 import signal as ch_sig
    monkeypatch.setattr(clock, "is_simulated", lambda: False)
    monkeypatch.setattr(ch_sig, "_cache_date", None)
    monkeypatch.setattr(ch_sig, "_cache_built_at", None)
    monkeypatch.setattr(ch_sig, "_cached_features", {})
    monkeypatch.setattr(ch_sig, "_last_eval_bar_ts", {})
    monkeypatch.setattr(ch_sig, "_last_trigger_ts", {})
    return ch_sig


def test_live_boundary_evaluates_just_closed_bar(tmp_db, live_clock):
    ch_sig = live_clock
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    # 40 closed bars ending T0-15m, plus the forming bar at T0
    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15 * 40), 41)

    clock.set_simulated_now(T0 + timedelta(seconds=33))
    intents, status = ch_sig.try_decide_for_variant(variant, {})
    target = ch_sig._last_eval_bar_ts.get(botcfg.VARIANT_ID)
    assert status["status"] not in ("not_at_15m_boundary", "bar_not_ready"), status
    assert target is not None and target.to_pydatetime() == T0 - timedelta(minutes=15)

    # same boundary again -> deduped, no second evaluation
    _, status2 = ch_sig.try_decide_for_variant(variant, {})
    assert status2["status"] == "already_evaluated"


def test_live_midbar_tick_skips(tmp_db, live_clock):
    ch_sig = live_clock
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15 * 40), 41)

    clock.set_simulated_now(T0 + timedelta(minutes=7, seconds=33))
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] == "not_at_15m_boundary"
    assert ch_sig._last_eval_bar_ts.get(botcfg.VARIANT_ID) is None


def test_live_stale_cache_rebuilt_after_bar_close(tmp_db, live_clock):
    """A frame built while a bar was forming must be rebuilt before that bar
    is evaluated (partial-values protection)."""
    ch_sig = live_clock
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15 * 40), 41)

    clock.set_simulated_now(T0 + timedelta(seconds=33))
    ch_sig.try_decide_for_variant(variant, {})       # builds cache at T0+33s
    built_first = ch_sig._cache_built_at

    # next boundary: target = T0 bar, which was FORMING at the first build
    _seed_15m_range(tmp_db, T0, 1)                    # bar T0 now final
    clock.set_simulated_now(T0 + timedelta(minutes=15, seconds=40))
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] not in ("not_at_15m_boundary", "bar_not_ready")
    assert ch_sig._cache_built_at > built_first       # rebuilt for final row
    assert ch_sig._last_eval_bar_ts[botcfg.VARIANT_ID].to_pydatetime() == T0


def test_live_bar_not_ready_then_recovers(tmp_db, live_clock):
    """Feed lag: closed bar not written yet -> bar_not_ready; once the row
    appears (still inside the grace window) the eval happens."""
    ch_sig = live_clock
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    # bars end 30 min before T0 -> the just-closed T0-15m bar is MISSING
    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15 * 40), 39)

    clock.set_simulated_now(T0 + timedelta(seconds=33))
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] == "bar_not_ready"

    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15), 1)   # feed catches up
    clock.set_simulated_now(T0 + timedelta(minutes=1, seconds=33))
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] not in ("not_at_15m_boundary", "bar_not_ready")
    assert ch_sig._last_eval_bar_ts[botcfg.VARIANT_ID].to_pydatetime() \
        == T0 - timedelta(minutes=15)


def test_just_closed_15m_ts_never_returns_forming_bar():
    from strategies.sleeves.chento_triple_v3 import signal as ch_sig
    # live mid-bar: 10:08:33 -> last FULLY closed bar opened 09:45
    assert ch_sig._just_closed_15m_ts(
        T0 + timedelta(minutes=8, seconds=33)).to_pydatetime() \
        == T0 - timedelta(minutes=15)
    # boundary-exact (replay convention): 10:00:00 -> 09:45 (unchanged)
    assert ch_sig._just_closed_15m_ts(T0).to_pydatetime() \
        == T0 - timedelta(minutes=15)
    # just after a boundary: 10:00:33 -> 09:45 (old code returned 10:00)
    assert ch_sig._just_closed_15m_ts(
        T0 + timedelta(seconds=33)).to_pydatetime() == T0 - timedelta(minutes=15)


def test_replay_path_selection_unchanged(tmp_db, monkeypatch):
    """Simulated clock keeps the historical semantics: bar open == now."""
    from strategies.sleeves.chento_triple_v3 import signal as ch_sig
    monkeypatch.setattr(ch_sig, "_cache_date", None)
    monkeypatch.setattr(ch_sig, "_cache_built_at", None)
    monkeypatch.setattr(ch_sig, "_cached_features", {})
    monkeypatch.setattr(ch_sig, "_last_trigger_ts", {})
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _seed_15m_range(tmp_db, T0 - timedelta(minutes=15 * 40), 41)

    clock.set_simulated_now(T0)                       # is_simulated() True
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] not in ("not_at_15m_boundary", "bar_not_ready",
                                     "already_evaluated")

    clock.set_simulated_now(T0 + timedelta(minutes=5))
    _, status = ch_sig.try_decide_for_variant(variant, {})
    assert status["status"] == "not_at_15m_boundary"


def test_diag_flush_accumulates_within_day(tmp_path, monkeypatch):
    """Same-day cache rebuilds must NOT fragment the diag JSONL (regression:
    the P0 15m rebuild flushed ~96 fragment lines/day until 2026-07-30)."""
    from strategies.sleeves.chento_triple_v3 import signal as ch_sig
    diag_path = tmp_path / "diag.jsonl"
    monkeypatch.setattr(ch_sig, "_DIAG_ENABLED", True)
    monkeypatch.setattr(ch_sig, "_DIAG_PATH", diag_path)
    monkeypatch.setattr(ch_sig, "_diag_current_day", None)
    monkeypatch.setattr(ch_sig, "_diag_counters", {})
    monkeypatch.setattr(ch_sig, "_diag_near_misses", [])

    ch_sig._diag_flush("2026-07-30")          # day starts
    ch_sig._diag_inc("bars_at_boundary")
    ch_sig._diag_flush("2026-07-30")          # same-day rebuild -> no write
    ch_sig._diag_inc("bars_at_boundary")
    assert not diag_path.exists()
    assert ch_sig._diag_counters == {"bars_at_boundary": 2}   # accumulated

    ch_sig._diag_flush("2026-07-31")          # rollover -> one line
    lines = diag_path.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["utc_date"] == "2026-07-30"
    assert rec["counters"] == {"bars_at_boundary": 2}


# ─── Forced-fire integration: execute → sleeve sweep closes on stop ──────────

def test_execute_then_sweep_stop_hit(tmp_db):
    from strategies.sleeves.chento_triple_v3 import signal as ch_sig

    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    intent = _mk_intent(entry=100_000.0, stop=97_000.0)
    resized, info = runner.size_intent(intent, float(variant["capital_usdt"]))
    res = ch_sig.execute_for_variant(variant, {}, resized)
    tid = res["trade_id"]
    assert res["status"] == "opened"

    con = sqlite3.connect(str(tmp_db))
    row = con.execute(
        "SELECT status, size_usdt, notes FROM trades WHERE id=?",
        (tid,)).fetchone()
    con.close()
    assert row[0] == "open"
    assert row[1] == pytest.approx(info["notional"], rel=1e-6)
    assert json.loads(row[2])["_state"]["stop_price"] == 97_000.0

    # next 15m bar pierces the stop; the one after exists so the walker has
    # a complete window up to the simulated now
    _seed_15m_bar(tmp_db, T0 + timedelta(minutes=15),
                  high=100_500.0, low=96_500.0, close=96_800.0)
    _seed_15m_bar(tmp_db, T0 + timedelta(minutes=30),
                  high=97_200.0, low=96_600.0, close=97_000.0)
    _seed_price(tmp_db, T0 + timedelta(minutes=34), 96_900.0)
    clock.set_simulated_now(T0 + timedelta(minutes=35))

    n = ch_sig._sweep_open_positions(variant, {})
    assert n >= 1

    con = sqlite3.connect(str(tmp_db))
    status, exit_price, notes = con.execute(
        "SELECT status, exit_price, notes FROM trades WHERE id=?",
        (tid,)).fetchone()
    con.close()
    assert status == "closed"
    assert exit_price == pytest.approx(97_000.0)      # closed AT the stop
    assert botlib.count_open_trades(variant["id"]) == 0
