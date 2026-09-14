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
from bots.squeeze_bull.strategy import signal as sleeve
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
    monkeypatch.setattr(sleeve, "decide",
                        lambda *a, **k: called.append(1) or ([], {}))
    out = runner.tick(env["variant"])
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
    monkeypatch.setattr(sleeve, "decide",
                        lambda *a, **k: ([intent], {"status": "decided"}))
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"cd_open_interest": 5.0}
                        if tables == botcfg.ENTRY_TABLES else {})
    out = runner.tick(env["variant"])
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
    first = sleeve.decide(env["variant"])
    second = sleeve.decide(env["variant"])
    assert first[1]["status"] == "no_flush"
    assert second[1]["status"] == "already_evaluated_this_hour"


def test_no_bars_does_not_burn_the_hour(env, monkeypatch):
    """A data outage must not consume the hour's single evaluation slot."""
    monkeypatch.setattr(sleeve, "_load_hourly", lambda now, lookback_days=45: [])
    assert sleeve.decide(env["variant"])[1]["status"] == "no_bars"
    assert sleeve.decide(env["variant"])[1]["status"] == "no_bars"


def test_flush_in_bull_regime_fires_and_opens_one_trade(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    out = runner.tick(env["variant"])
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
    out = runner.tick(env["variant"])
    assert out["status"] == "regime_not_bull"
    assert not _trades(env["db"])


def test_no_flush_does_not_fire(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now, flush_at_last=False))
    out = runner.tick(env["variant"])
    assert out["status"] == "no_flush"
    assert not _trades(env["db"])


def test_open_position_blocks_a_second_entry(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"])
    assert len(_trades(env["db"])) == 1
    sleeve._last_eval_hour.clear()
    clock.set_simulated_now(NOW + timedelta(hours=1))
    out = runner.tick(env["variant"])
    assert out["status"] == "position_open"
    assert len(_trades(env["db"])) == 1


# ─── exits ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("side,kind", [(0.979, "stop"), (1.031, "target")])
def test_sweep_closes_on_stop_and_target(env, monkeypatch, side, kind):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"])
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
    runner.tick(env["variant"])
    clock.set_simulated_now(NOW + timedelta(hours=49))
    closed = sleeve._sweep_open_positions(env["variant"]["id"])
    assert closed == 1
    assert _trades(env["db"])[0]["status"] == "closed"


def test_sweep_skips_when_price_is_unavailable(env, monkeypatch):
    """No price means no exit decision — never close at a guessed price."""
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"])
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
    assert meta["variant_ids"] == [v["id"] for v in botcfg.VARIANTS]


# ─── no-stop paper variant (2026-09-12, sizing_style_2026_09 policy P1b) ──

def _nostop_variant():
    v = botcfg.VARIANTS[1]
    assert v["use_stop"] is False
    return botlib.ensure_bot_variant(v["id"], short_name=v["short_name"],
                                     capital_usdt=CAPITAL, bot_name=botcfg.BOT_NAME)


def _rows_for(db_path, variant_id):
    return [r for r in _trades(db_path) if r["strategy_variant"] == variant_id]


def test_variants_are_the_incumbent_plus_a_no_stop_twin():
    assert [v["id"] for v in botcfg.VARIANTS] == ["bot_squeeze_bull_v1",
                                                  "bot_squeeze_bull_nostop_v1"]
    assert [v["use_stop"] for v in botcfg.VARIANTS] == [True, False]
    assert botcfg.VARIANT_ID == "bot_squeeze_bull_v1"
    # sized as if the 2% stop existed: 1% / 2% = 0.5x, same as the stop variant
    assert botcfg.NOSTOP_NOTIONAL_X == pytest.approx(0.5)


def test_no_stop_sizing_is_the_same_half_notional():
    from strategies.support.dispatch import Intent
    intent = Intent(asset="BTC", direction="LONG", allocation_pct=0.0, leverage=1.0,
                    conviction=100, priority=100,
                    reason={"_entry_price": ENTRY, "_stop_price": None,
                            "_reference_stop_price": ENTRY * 0.98},
                    scheduled_exit_dt=None)
    resized, info = runner.size_intent(intent, CAPITAL, use_stop=False)
    assert info["notional"] == pytest.approx(CAPITAL * 0.5)
    assert info["stop_pct"] == pytest.approx(0.02)
    assert info["at_cap"] is False
    assert resized.allocation_pct == 100.0
    assert resized.leverage == pytest.approx(0.5)


def test_no_stop_variant_opens_without_a_stop_and_survives_the_stop_price(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    v = _nostop_variant()
    out = runner.tick(v, use_stop=False)
    assert out["status"] == "decided", out
    rows = _rows_for(env["db"], v["id"])
    assert len(rows) == 1
    tr = rows[0]
    blob = json.loads(tr["notes"])
    assert blob["exit_policy"] == "target_time"
    assert blob["_stop_price"] is None
    assert blob["_reference_stop_price"] == pytest.approx(tr["entry_price"] * 0.98)
    assert blob["_target_price"] == pytest.approx(tr["entry_price"] * 1.03)
    assert tr["size_usdt"] == pytest.approx(CAPITAL * 0.5)
    # a print far through the reference stop closes nothing ...
    monkeypatch.setattr(price_feed, "get_current_price",
                        lambda a: float(tr["entry_price"]) * 0.95)
    assert sleeve._sweep_open_positions(v["id"]) == 0
    assert _rows_for(env["db"], v["id"])[0]["status"] == "open"
    # ... the target still does
    monkeypatch.setattr(price_feed, "get_current_price",
                        lambda a: float(tr["entry_price"]) * 1.031)
    assert sleeve._sweep_open_positions(v["id"]) == 1
    assert _rows_for(env["db"], v["id"])[0]["status"] == "closed"


def test_no_stop_variant_time_stop_still_closes_a_loser(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    v = _nostop_variant()
    runner.tick(v, use_stop=False)
    tr = _rows_for(env["db"], v["id"])[0]
    monkeypatch.setattr(price_feed, "get_current_price",
                        lambda a: float(tr["entry_price"]) * 0.95)
    clock.set_simulated_now(NOW + timedelta(hours=49))
    assert sleeve._sweep_open_positions(v["id"]) == 1
    closed = _rows_for(env["db"], v["id"])[0]
    assert closed["status"] == "closed"
    assert closed["exit_price"] == pytest.approx(float(tr["entry_price"]) * 0.95)


def test_tick_all_runs_both_variants_on_the_same_bar_and_diags_once(env, monkeypatch):
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    v2 = _nostop_variant()
    variants = [{"row": env["variant"], "use_stop": True},
                {"row": v2, "use_stop": False}]
    out = runner.tick_all(variants)
    assert out["status"] == "decided"
    assert out["signal"] and out["evaluated"] and out["hb_status"] == "ok"
    assert out["open_trades"] == 2
    assert set(out["per_variant"]) == {env["variant"]["id"], v2["id"]}
    rows = _trades(env["db"])
    assert len(rows) == 2
    blobs = {r["strategy_variant"]: json.loads(r["notes"]) for r in rows}
    assert len({b["bar_ts"] for b in blobs.values()}) == 1, "same bar for both"
    assert blobs[env["variant"]["id"]]["_stop_price"] is not None
    assert blobs[v2["id"]]["_stop_price"] is None
    assert all(r["size_usdt"] == pytest.approx(CAPITAL * 0.5) for r in rows)
    lines = botcfg.DIAG_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, "the evaluation is recorded once per tick, not per variant"


# ─── time stop: 48h from ENTRY (BACKLOG 12a) ──────────────────────────────

def _time_stop_iso(db_path):
    notes = _trades(db_path)[0]["notes"] or ""
    blob, _ = json.JSONDecoder().raw_decode(notes.split("\n")[0])
    return datetime.fromisoformat(blob["_time_stop_iso"])


def test_time_stop_is_48h_after_entry_not_after_the_trigger_bar_open(env, monkeypatch):
    """Until 2026-09-13 the live schedule was `bar_ts + TIF_HOURS`, where
    bar_ts is the trigger bar's OPEN. Entry happens at that bar's CLOSE, an
    hour later, so every live hold was 47h — while the research walker
    (math.replay_bracket) and the re-cut replay (recut_lib) both hold 48h from
    entry. SJ-4250 held exactly 47.0h.

    That mattered beyond an hour of price: the pre-registered rule "disable a
    variant whose live record diverges from its own replay by > 0.05 R" was
    comparing two different exit times on every time-stop trade.

    Here the trigger bar opens 11:00 and entry is its 12:00 close (= NOW).
    """
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"])
    assert len(_trades(env["db"])) == 1, "the synthetic flush must fire"
    assert _time_stop_iso(env["db"]) == NOW + timedelta(hours=48), (
        f"time stop {_time_stop_iso(env['db'])} is not 48h after entry "
        f"{NOW} — measured from the trigger bar's open again?")


def test_time_stop_boundary_one_minute_early_stays_open(env, monkeypatch):
    """The behavioural half. The pre-existing test jumps straight to +49h, so
    it passed on the 47h bug too. At entry+47h59m the trade must still be open;
    at entry+48h it must close. Price is flat, so nothing but the time stop can
    close it."""
    monkeypatch.setattr(sleeve, "_load_hourly",
                        lambda now, lookback_days=45: _bars(now))
    runner.tick(env["variant"])
    vid = env["variant"]["id"]

    clock.set_simulated_now(NOW + timedelta(hours=47, minutes=59))
    assert sleeve._sweep_open_positions(vid) == 0
    assert _trades(env["db"])[0]["status"] == "open", (
        "closed before 48h from entry — the 47h time-stop bug is back")

    clock.set_simulated_now(NOW + timedelta(hours=48))
    assert sleeve._sweep_open_positions(vid) == 1
    assert _trades(env["db"])[0]["status"] == "closed"


# ─── scheduled-exit backstop (BACKLOG 4.4) and a raising decide ──────────
# Until 2026-09-14 the backstop booked every trade at the trades.py defaults,
# 10 bp fee + 5 bp slippage + funding, where this sleeve books PAPER_COST_BP_RT
# 7 bp with no slippage (COST_BP_RT 18 is research-only); and a decide() that
# raised skipped the backstop altogether.

def _backstop_env(monkeypatch):
    """A 50,500 quote and funding stubbed at -0.10% for a long, so a close
    that books funding shows it."""
    from strategies.support import funding
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: 50_500.0)
    monkeypatch.setattr(funding, "accrued_pct",
                        lambda asset, a, b, d: -0.10 if str(d).upper() == "LONG" else 0.10)


def _seed_due(variant, *, strategy=sleeve.SLEEVE_NAME, tag="due", reason=None):
    """A $10,000 long from 50,000 (+$100 of price P&L at 50,500) whose exit
    time passed a minute before NOW."""
    from strategies import trades
    return trades.open_paper_trade(
        variant=variant, sleeve_name=strategy, asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason=reason or {"trigger": "squeeze_bull_long"},
        scheduled_exit_dt=NOW - timedelta(minutes=1),
        entry_dt=NOW - timedelta(hours=6), signal_time_iso=f"{strategy}-{tag}")


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


@pytest.mark.parametrize("which", [0, 1], ids=["stop", "nostop"])
def test_backstop_books_the_sleeves_cost_not_trades_defaults(env, monkeypatch, which):
    v = botcfg.VARIANTS[which]
    variant = botlib.ensure_bot_variant(v["id"], short_name=v["short_name"],
                                        capital_usdt=CAPITAL, bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    tid = _seed_due(variant)
    # The sleeve's own sweep must not get there first.
    monkeypatch.setattr(sleeve, "decide", lambda *a, **k: ([], {"status": "no_flush"}))

    out = runner.tick(variant, use_stop=v["use_stop"])

    assert out.get("backstop_closed") == [tid], out
    assert out["hb_status"] == "ok"
    status, pnl, notes, fee = _closed_row(env["db"], tid)
    assert status == "closed"
    # +100 price P&L - 7 bp of $10,000 - 0.10% funding. The trades.py
    # defaults book 100 - 15 - 10 = 75.00; the research-only 18 bp, 72.00.
    assert pnl == pytest.approx(83.00)
    assert fee == pytest.approx(7.00)
    assert notes.endswith(
        "\nSQUEEZE_BULL_EXIT: scheduled_exit; fees=7bp RT, slip=0bp RT, "
        "funding=-0.100%")


def test_decide_raising_still_runs_the_backstop(env, monkeypatch):
    """The error reaches the heartbeat, no entry is attempted, and the
    overdue trade still closes."""
    _backstop_env(monkeypatch)
    tid = _seed_due(env["variant"])

    def broken_decide(*a, **k):
        raise RuntimeError("hourly load failed")
    monkeypatch.setattr(sleeve, "decide", broken_decide)

    try:
        out = runner.tick(env["variant"])
    except RuntimeError as escaped:
        raise AssertionError("decide's error escaped the tick, so the backstop "
                             "never ran") from escaped

    assert out.get("backstop_closed") == [tid], out
    assert _closed_row(env["db"], tid)[0] == "closed"
    assert out["status"] == "decide_error"
    assert out["hb_status"] == "error"
    assert out["hb_note"] == repr(RuntimeError("hourly load failed"))
    assert out["evaluated"] is False and "opened" not in out


def test_backstop_refusal_keeps_closed_ids_and_turns_the_heartbeat_error(env, monkeypatch):
    """A due trade of a strategy this bot has no closer for stays open, is
    named in the heartbeat note with status 'error', and does not escape the
    tick; the SQUEEZE_BULL trade that did close is still reported."""
    _backstop_env(monkeypatch)
    own = _seed_due(env["variant"])
    foreign = _seed_due(env["variant"], strategy="SHORT_SQUEEZE", tag="foreign")
    monkeypatch.setattr(sleeve, "decide", lambda *a, **k: ([], {"status": "no_flush"}))

    try:
        out = runner.tick(env["variant"])
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the refusal escaped the tick") from escaped

    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out
    assert out["hb_status"] == "error" and "SHORT_SQUEEZE" in out["hb_note"]
    assert _closed_row(env["db"], own)[0] == "closed"
    assert _closed_row(env["db"], foreign)[0] == "open"


def test_decide_error_and_backstop_refusal_both_reach_the_heartbeat(env, monkeypatch):
    """On a tick where decide raises AND the backstop refuses a trade, the
    note keeps the decide error first and appends the refusal."""
    _backstop_env(monkeypatch)
    own = _seed_due(env["variant"])
    foreign = _seed_due(env["variant"], strategy="SHORT_SQUEEZE", tag="foreign")

    def broken_decide(*a, **k):
        raise RuntimeError("hourly load failed")
    monkeypatch.setattr(sleeve, "decide", broken_decide)

    try:
        out = runner.tick(env["variant"])
    except (RuntimeError, botlib.BackstopRefused) as escaped:
        raise AssertionError("an error escaped the tick") from escaped

    assert out["status"] == "decide_error" and out["hb_status"] == "error"
    assert out["hb_note"].startswith(
        repr(RuntimeError("hourly load failed"))
        + "; backstop left 1 due trade(s) open in "), out["hb_note"]
    assert "SHORT_SQUEEZE" in out["hb_note"]
    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out


def test_stale_mgmt_tables_skip_the_backstop(env, monkeypatch):
    """Kept on purpose (BACKLOG 18): the backstop would price off btc_1m, the
    table that just went stale, so a stale-mgmt tick leaves a due trade open."""
    _backstop_env(monkeypatch)
    tid = _seed_due(env["variant"])

    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr(sleeve, "decide", boom)
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"btc_1m": 999.0}
                        if tables == botcfg.MGMT_TABLES else {})

    out = runner.tick(env["variant"])

    assert out["status"] == "stale_mgmt_inputs" and out["hb_status"] == "degraded"
    assert "backstop_closed" not in out, out
    assert _closed_row(env["db"], tid)[0] == "open"


def test_tick_all_still_sweeps_the_twin_when_the_first_variant_has_a_refused_trade(
        env, monkeypatch):
    """A due trade the backstop has no closer for stays open and turns the
    heartbeat 'error' — but it must not stop the no-stop twin's tick, whose
    sweep is the only thing closing ITS positions. Real decide on both
    variants, with no hourly bars so it only sweeps."""
    monkeypatch.setattr(sleeve, "_load_hourly", lambda now, lookback_days=45: [])
    _backstop_env(monkeypatch)
    v2 = _nostop_variant()
    foreign = _seed_due(env["variant"], strategy="SHORT_SQUEEZE", tag="foreign")
    own = _seed_due(v2, tag="own", reason={
        "trigger": "squeeze_bull_long", "exit_policy": "target_time",
        "_stop_price": None, "_target_price": None,
        "_time_stop_iso": (NOW - timedelta(minutes=1)).isoformat()})
    variants = [{"row": env["variant"], "use_stop": True},
                {"row": v2, "use_stop": False}]

    try:
        out = runner.tick_all(variants)
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the first variant's refusal escaped tick_all, so "
                             "the twin never ticked") from escaped

    first = out["per_variant"][env["variant"]["id"]]
    assert first["hb_status"] == "error" and first.get("backstop_refused") == [foreign]
    assert "SHORT_SQUEEZE" in first["hb_note"]
    assert out["hb_status"] == "error"
    assert _closed_row(env["db"], foreign)[0] == "open"
    status, _, notes, _ = _closed_row(env["db"], own)
    assert status == "closed"
    assert "\nSQUEEZE_BULL_EXIT: time_stop;" in notes


def test_tick_all_still_sweeps_the_twin_when_the_first_variants_entry_raises(
        env, monkeypatch):
    """While this process stands down as a duplicate instance, entries raise
    and exits must keep running (strategies/support/instance_guard.py). A
    fire on the stop variant raises out of its execute(); the no-stop twin
    must still tick and close its due time stop. Real decide on the twin,
    with no hourly bars so it only sweeps."""
    from strategies.support import instance_guard
    from strategies.support.dispatch import Intent
    from strategies.support.instance_guard import DuplicateInstanceError
    monkeypatch.setattr(sleeve, "_load_hourly", lambda now, lookback_days=45: [])
    _backstop_env(monkeypatch)
    v2 = _nostop_variant()
    own = _seed_due(v2, tag="own", reason={
        "trigger": "squeeze_bull_long", "exit_policy": "target_time",
        "_stop_price": None, "_target_price": None,
        "_time_stop_iso": (NOW - timedelta(minutes=1)).isoformat()})
    intent = Intent(asset="BTC", direction="LONG", allocation_pct=100.0,
                    leverage=1.0, conviction=100, priority=100,
                    reason={"_entry_price": ENTRY, "_stop_price": ENTRY * 0.98,
                            "_target_price": ENTRY * 1.03, "bar_ts": 1},
                    scheduled_exit_dt=NOW + timedelta(hours=48))
    real_decide = sleeve.decide

    def decide(variant, **kw):
        if kw.get("use_stop", True):
            return [intent], {"status": "decided"}
        return real_decide(variant, **kw)
    monkeypatch.setattr(sleeve, "decide", decide)
    # Set after seeding: a standing-down process refuses every open.
    monkeypatch.setattr(instance_guard, "_reason", "duplicate heartbeat")
    variants = [{"row": env["variant"], "use_stop": True},
                {"row": v2, "use_stop": False}]

    try:
        out = runner.tick_all(variants)
    except DuplicateInstanceError as escaped:
        raise AssertionError("the first variant's entry error escaped tick_all, "
                             "so the twin never ticked") from escaped

    first = out["per_variant"][env["variant"]["id"]]
    assert first["status"] == "tick_error" and first["hb_status"] == "error"
    assert first["hb_note"].startswith("DuplicateInstanceError("), first
    assert out["hb_status"] == "error" and not out["opened"]
    status, _, notes, _ = _closed_row(env["db"], own)
    assert status == "closed"
    assert "\nSQUEEZE_BULL_EXIT: time_stop;" in notes
