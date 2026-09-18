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
        "bots.short_squeeze.strategy.signal.decide",
        lambda v, **kw: ([_mk_intent()], {"status": "decided"}))

    def boom(*a, **k):
        raise AssertionError("execute must not run on stale entry tables")
    monkeypatch.setattr(
        "bots.short_squeeze.strategy.signal.execute", boom)
    monkeypatch.setattr(
        botlib, "stale_tables",
        lambda tables=None: {"cd_open_interest": 9999.0}
        if "cd_open_interest" in (tables or []) else {})

    out = runner.tick(variant)
    assert out["status"] == "entry_blocked_stale_inputs"
    assert out["hb_status"] == "degraded"


# ─── Forced-fire integration: execute → price-sweep stop close ────────────────

def test_execute_then_sweep_stop_hit(tmp_db):
    from bots.short_squeeze.strategy import signal as ssq

    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)

    intent = _mk_intent(entry=100_000.0, stop=99_900.0)
    resized, info = runner.size_intent(intent, float(variant["capital_usdt"]))
    res = ssq.execute(variant, resized)
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
    from bots.short_squeeze.strategy import signal as ssq

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


# ─── no-stop paper variant (2026-09-12, sizing_style_2026_09 policy P1) ────

def _nostop_intent(entry=100_000.0, stop=99_900.0):
    import dataclasses
    base = _mk_intent(entry=entry, stop=stop)
    reason = {**base.reason, "variant_id": botcfg.VARIANTS[1]["id"],
              "exit_policy": "time_only",
              "_stop_price": None, "_target_price": None,
              "_reference_stop_price": stop,
              "_reference_target_price": base.reason["_target_price"]}
    return dataclasses.replace(base, reason=reason)


def test_variants_are_the_incumbent_plus_a_no_stop_twin():
    assert [v["id"] for v in botcfg.VARIANTS] == ["bot_short_squeeze_v1",
                                                  "bot_short_squeeze_nostop_v1"]
    assert [v["use_stop"] for v in botcfg.VARIANTS] == [True, False]
    assert botcfg.VARIANT_ID == "bot_short_squeeze_v1"
    assert botcfg.NOSTOP_NOTIONAL_X == 1.0


def test_no_stop_sizing_is_a_fixed_one_x_whatever_the_stop_width():
    """The stop variant sizes this 0.1% stop at the 3x cap; the no-stop
    variant has no stop to size from and takes 1x capital."""
    resized, info = runner.size_intent(_nostop_intent(), 10_000.0, use_stop=False)
    assert info["notional"] == pytest.approx(10_000.0)
    assert info["at_cap"] is False
    assert info["stop_pct"] == pytest.approx(0.001)
    assert resized.leverage == pytest.approx(1.0)
    assert resized.allocation_pct == 100.0


def test_no_stop_execute_then_sweep_ignores_stop_and_target_and_closes_on_time(tmp_db):
    from bots.short_squeeze.strategy import signal as ssq

    v = botcfg.VARIANTS[1]
    variant = botlib.ensure_bot_variant(
        v["id"], short_name="t", capital_usdt=10_000.0, bot_name=botcfg.BOT_NAME)
    resized, info = runner.size_intent(_nostop_intent(), 10_000.0, use_stop=False)
    res = ssq.execute(variant, resized)
    tid = res["trade_id"]
    assert res["status"] == "opened" and res["stop_price"] is None

    con = sqlite3.connect(str(tmp_db))
    size_usdt, notes = con.execute(
        "SELECT size_usdt, notes FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    blob = json.loads(notes)
    assert size_usdt == pytest.approx(10_000.0)
    assert blob["_stop_price"] is None and blob["_target_price"] is None
    assert blob["_reference_stop_price"] == 99_900.0

    # far through the reference stop, then far through the reference target:
    # nothing closes
    _seed_price(tmp_db, T0 + timedelta(minutes=4), 99_000.0)
    clock.set_simulated_now(T0 + timedelta(minutes=5))
    assert ssq._sweep_open_positions(variant["id"]) == 0
    _seed_price(tmp_db, T0 + timedelta(minutes=9), 101_000.0)
    clock.set_simulated_now(T0 + timedelta(minutes=10))
    assert ssq._sweep_open_positions(variant["id"]) == 0

    # the 6h time stop does
    _seed_price(tmp_db, T0 + timedelta(hours=6), 100_500.0)
    clock.set_simulated_now(T0 + timedelta(hours=6, minutes=1))
    assert ssq._sweep_open_positions(variant["id"]) == 1
    con = sqlite3.connect(str(tmp_db))
    status, exit_price = con.execute(
        "SELECT status, exit_price FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert status == "closed"
    assert exit_price == pytest.approx(100_500.0)


def test_decide_honours_use_stop_and_count_diag(tmp_db, monkeypatch):
    """The sleeve-side flags: `use_stop=False` blanks the stop and target in
    the intent (keeping the reference levels); `count_diag=False` skips the
    per-day gate counters so a two-variant bot counts each bar once."""
    from bots.short_squeeze.strategy import signal as ssq

    bar = {"ts": int((T0 - timedelta(minutes=15)).timestamp()),
           "open": 100_050.0, "high": 100_100.0, "low": 99_900.0, "close": 100_000.0,
           "perp_cvd": -5.0, "divergence": 9.0}
    diag = {"status": "FIRE", "bar": bar, "perp_cvd_pct": 0.05,
            "divergence_pct": 0.9, "close_in_range": 0.5, "prior_low": 99_950.0}
    counted = []
    monkeypatch.setattr(ssq, "_sweep_open_positions", lambda vid: 0)
    monkeypatch.setattr(ssq, "_get_open_short_squeeze_trades", lambda vid: [])
    monkeypatch.setattr(ssq, "_evaluate_trigger", lambda vid, now: (True, diag))
    monkeypatch.setattr(ssq, "_diag_count", lambda status, now: counted.append(status))
    variant = {"id": "v", "capital_usdt": 10_000.0}

    intents, status = ssq.decide(variant, weight_pct=100.0, use_stop=False,
                                 count_diag=False)
    assert status["status"] == "decided" and len(intents) == 1
    r = intents[0].reason
    assert r["exit_policy"] == "time_only"
    assert r["_stop_price"] is None and r["_target_price"] is None
    assert r["_reference_stop_price"] == pytest.approx(99_900.0 * 0.999)
    assert r["_reference_target_price"] == pytest.approx(
        100_000.0 + 3.0 * (100_000.0 - 99_900.0 * 0.999))
    assert counted == []

    intents, status = ssq.decide(variant, weight_pct=100.0)
    r = intents[0].reason
    assert r["exit_policy"] == "stop_target_time"
    assert r["_stop_price"] == pytest.approx(r["_reference_stop_price"])
    assert r["_target_price"] == pytest.approx(r["_reference_target_price"])
    assert counted == ["FIRE"]


def test_tick_all_runs_both_variants_and_counts_diag_once(tmp_db, monkeypatch):
    calls = []

    def fake_decide(v, **kw):
        calls.append((v["id"], kw.get("use_stop", True),
                      kw.get("count_diag", True)))
        return [], {"status": "no_sweep"}
    monkeypatch.setattr(
        "bots.short_squeeze.strategy.signal.decide", fake_decide)
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    rows = [botlib.ensure_bot_variant(v["id"], short_name="t", capital_usdt=10_000.0,
                                      bot_name=botcfg.BOT_NAME) for v in botcfg.VARIANTS]
    variants = [{"row": r, "use_stop": v["use_stop"]}
                for r, v in zip(rows, botcfg.VARIANTS)]
    out = runner.tick_all(variants)
    assert calls == [("bot_short_squeeze_v1", True, True),
                     ("bot_short_squeeze_nostop_v1", False, False)]
    assert out["status"] == "no_sweep" and out["hb_status"] == "ok"
    assert out["evaluated"] and not out["signal"]
    assert out["open_trades"] == 0


# ─── Scheduled-exit backstop (BACKLOG 4.4) and a raising decide ──────────────
# Until 2026-09-14 the backstop booked every trade at the trades.py defaults,
# 10 bp fee + 5 bp slippage + funding, where this sleeve books 10 bp with no
# slippage; and a decide() that raised skipped the backstop altogether.

def _backstop_env(monkeypatch):
    """Fresh tables, a 50,500 quote and funding stubbed at -0.10% for a long,
    so a close that books funding shows it."""
    from strategies.support import funding, price_feed
    monkeypatch.setattr(botlib, "stale_tables", lambda tables=None: {})
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: 50_500.0)
    monkeypatch.setattr(funding, "accrued_pct",
                        lambda asset, a, b, d: -0.10 if str(d).upper() == "LONG" else 0.10)


def _seed_due(variant, *, strategy="SHORT_SQUEEZE", tag="due", reason=None):
    """A $10,000 long from 50,000 (+$100 of price P&L at 50,500) whose exit
    time passed a minute before T0."""
    from strategies import trades
    return trades.open_paper_trade(
        variant=variant, sleeve_name=strategy, asset="BTC", direction="LONG",
        entry_price=50_000.0, allocation_pct=100.0, leverage=1.0,
        reason=reason or {"trigger": "short_squeeze_long"},
        scheduled_exit_dt=T0 - timedelta(minutes=1),
        entry_dt=T0 - timedelta(hours=6), signal_time_iso=f"{strategy}-{tag}")


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
def test_backstop_books_the_sleeves_cost_not_trades_defaults(tmp_db, monkeypatch, which):
    v = botcfg.VARIANTS[which]
    variant = botlib.ensure_bot_variant(
        v["id"], short_name="t", capital_usdt=10_000.0, bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    tid = _seed_due(variant)
    # The sleeve's own sweep must not get there first.
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_sweep"}))

    out = runner.tick(variant, use_stop=v["use_stop"])

    assert out.get("backstop_closed") == [tid], out
    assert out["hb_status"] == "ok"
    status, pnl, notes, fee = _closed_row(tmp_db, tid)
    assert status == "closed"
    # +100 price P&L - 10 bp of $10,000 - 0.10% funding. The trades.py
    # defaults book 100 - 15 - 10 = 75.00.
    assert pnl == pytest.approx(80.00)
    assert fee == pytest.approx(10.00)
    assert notes.endswith(
        "\nSHORT_SQUEEZE_EXIT: scheduled_exit; fees=10bp RT, slip=0bp RT, "
        "funding=-0.100%")


def test_decide_raising_still_runs_the_backstop(tmp_db, monkeypatch):
    """The error reaches the heartbeat, no entry is attempted, and the
    overdue trade still closes."""
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    tid = _seed_due(variant)

    def broken_decide(*a, **k):
        raise RuntimeError("percentile refresh failed")
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except RuntimeError as escaped:
        raise AssertionError("decide's error escaped the tick, so the backstop "
                             "never ran") from escaped

    assert out.get("backstop_closed") == [tid], out
    assert _closed_row(tmp_db, tid)[0] == "closed"
    assert out["status"] == "decide_error"
    assert out["hb_status"] == "error"
    assert out["hb_note"] == repr(RuntimeError("percentile refresh failed"))
    assert out["evaluated"] is False and "opened" not in out


def test_backstop_refusal_keeps_closed_ids_and_turns_the_heartbeat_error(tmp_db, monkeypatch):
    """A due trade of a strategy this bot has no closer for stays open, is
    named in the heartbeat note with status 'error', and does not escape the
    tick; the SHORT_SQUEEZE trade that did close is still reported."""
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    own = _seed_due(variant)
    foreign = _seed_due(variant, strategy="SQUEEZE_BULL", tag="foreign")
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide",
                        lambda *a, **k: ([], {"status": "no_sweep"}))

    try:
        out = runner.tick(variant)
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the refusal escaped the tick") from escaped

    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out
    assert out["hb_status"] == "error" and "SQUEEZE_BULL" in out["hb_note"]
    assert _closed_row(tmp_db, own)[0] == "closed"
    assert _closed_row(tmp_db, foreign)[0] == "open"


def test_decide_error_and_backstop_refusal_both_reach_the_heartbeat(tmp_db, monkeypatch):
    """On a tick where decide raises AND the backstop refuses a trade, the
    note keeps the decide error first and appends the refusal."""
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    own = _seed_due(variant)
    foreign = _seed_due(variant, strategy="SQUEEZE_BULL", tag="foreign")

    def broken_decide(*a, **k):
        raise RuntimeError("percentile refresh failed")
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide", broken_decide)

    try:
        out = runner.tick(variant)
    except (RuntimeError, botlib.BackstopRefused) as escaped:
        raise AssertionError("an error escaped the tick") from escaped

    assert out["status"] == "decide_error" and out["hb_status"] == "error"
    assert out["hb_note"].startswith(
        repr(RuntimeError("percentile refresh failed"))
        + "; backstop left 1 due trade(s) open in "), out["hb_note"]
    assert "SQUEEZE_BULL" in out["hb_note"]
    assert out.get("backstop_closed") == [own], out
    assert out.get("backstop_refused") == [foreign], out


def test_stale_mgmt_tables_skip_the_backstop(tmp_db, monkeypatch):
    """Kept on purpose (BACKLOG 18): the backstop would price off btc_1m, the
    table that just went stale, so a stale-mgmt tick leaves a due trade open."""
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    tid = _seed_due(variant)

    def boom(*a, **k):
        raise AssertionError("decide must not run on stale mgmt tables")
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide", boom)
    monkeypatch.setattr(botlib, "stale_tables",
                        lambda tables=None: {"btc_1m": 900.0}
                        if tables == botcfg.MGMT_TABLES else {})

    out = runner.tick(variant)

    assert out["status"] == "stale_mgmt_inputs" and out["hb_status"] == "degraded"
    assert "backstop_closed" not in out, out
    assert _closed_row(tmp_db, tid)[0] == "open"


def test_tick_all_still_sweeps_the_twin_when_the_first_variant_has_a_refused_trade(
        tmp_db, monkeypatch):
    """A due trade the backstop has no closer for stays open and turns the
    heartbeat 'error' — but it must not stop the no-stop twin's tick, whose
    sweep is the only thing closing ITS positions. Real decide on both
    variants; 15:07 is off the 15m boundary, so decide only sweeps."""
    rows = [botlib.ensure_bot_variant(v["id"], short_name="t", capital_usdt=10_000.0,
                                      bot_name=botcfg.BOT_NAME) for v in botcfg.VARIANTS]
    _backstop_env(monkeypatch)
    foreign = _seed_due(rows[0], strategy="SQUEEZE_BULL", tag="foreign")
    own = _seed_due(rows[1], tag="own", reason={
        "trigger": "short_squeeze_long", "exit_policy": "time_only",
        "_stop_price": None, "_target_price": None,
        "_time_stop_iso": (T0 - timedelta(minutes=1)).isoformat()})
    clock.set_simulated_now(T0 + timedelta(minutes=7))
    variants = [{"row": r, "use_stop": v["use_stop"]}
                for r, v in zip(rows, botcfg.VARIANTS)]

    try:
        out = runner.tick_all(variants)
    except botlib.BackstopRefused as escaped:
        raise AssertionError("the first variant's refusal escaped tick_all, so "
                             "the twin never ticked") from escaped

    first = out["per_variant"]["bot_short_squeeze_v1"]
    assert first["hb_status"] == "error" and first.get("backstop_refused") == [foreign]
    assert "SQUEEZE_BULL" in first["hb_note"]
    assert out["hb_status"] == "error"
    assert _closed_row(tmp_db, foreign)[0] == "open"
    status, _, notes, _ = _closed_row(tmp_db, own)
    assert status == "closed"
    assert "\nSHORT_SQUEEZE_EXIT: time_stop;" in notes


def test_tick_all_still_sweeps_the_twin_when_the_first_variants_entry_raises(
        tmp_db, monkeypatch):
    """While this process stands down as a duplicate instance, entries raise
    and exits must keep running (strategies/support/instance_guard.py). A
    fire on the stop variant raises out of its execute(); the no-stop twin
    must still tick and close its due time stop. Real decide on the twin; 15:07
    is off the 15m boundary, so it only sweeps."""
    from bots.short_squeeze.strategy import signal as ssq
    from strategies.support import instance_guard
    from strategies.support.instance_guard import DuplicateInstanceError
    rows = [botlib.ensure_bot_variant(v["id"], short_name="t", capital_usdt=10_000.0,
                                      bot_name=botcfg.BOT_NAME) for v in botcfg.VARIANTS]
    _backstop_env(monkeypatch)
    own = _seed_due(rows[1], tag="own", reason={
        "trigger": "short_squeeze_long", "exit_policy": "time_only",
        "_stop_price": None, "_target_price": None,
        "_time_stop_iso": (T0 - timedelta(minutes=1)).isoformat()})
    clock.set_simulated_now(T0 + timedelta(minutes=7))
    real_decide = ssq.decide

    def decide(variant, **kw):
        if kw.get("use_stop", True):
            return [_mk_intent()], {"status": "decided"}
        return real_decide(variant, **kw)
    monkeypatch.setattr(ssq, "decide", decide)
    # Set after seeding: a standing-down process refuses every open.
    monkeypatch.setattr(instance_guard, "_reason", "duplicate heartbeat")
    variants = [{"row": r, "use_stop": v["use_stop"]}
                for r, v in zip(rows, botcfg.VARIANTS)]

    try:
        out = runner.tick_all(variants)
    except DuplicateInstanceError as escaped:
        raise AssertionError("the first variant's entry error escaped tick_all, "
                             "so the twin never ticked") from escaped

    first = out["per_variant"]["bot_short_squeeze_v1"]
    # BACKLOG 23 (2026-09-19): the entry error is now caught inside the
    # variant's own tick, whose backstop still runs; before, it escaped to
    # tick_all as "tick_error" and only the twin was protected.
    assert first["status"] == "entry_error" and first["hb_status"] == "error"
    assert first["hb_note"].startswith("DuplicateInstanceError("), first
    assert out["hb_status"] == "error" and not out["opened"]
    status, _, notes, _ = _closed_row(tmp_db, own)
    assert status == "closed"
    assert "\nSHORT_SQUEEZE_EXIT: time_stop;" in notes


def test_entry_path_raising_still_runs_the_backstop(tmp_db, monkeypatch):
    """BACKLOG 23: until 2026-09-19 an error after decide() — sizing, the
    entry-table check, execute() — escaped the tick before the backstop at its
    end, so that tick's exits were skipped. The overdue trade must still close,
    nothing must open, and the error must reach the heartbeat."""
    variant = botlib.ensure_bot_variant(
        botcfg.VARIANT_ID, short_name="t", capital_usdt=10_000.0,
        bot_name=botcfg.BOT_NAME)
    _backstop_env(monkeypatch)
    tid = _seed_due(variant)
    monkeypatch.setattr("botlib.stale_tables", lambda tables=None: {})
    monkeypatch.setattr("bots.short_squeeze.strategy.signal.decide", lambda *a, **k: ([object()], {"status": "signal"}))

    def broken_sizing(*a, **k):
        raise RuntimeError("sizing failed")
    monkeypatch.setattr(runner, "size_intent", broken_sizing)

    try:
        out = runner.tick(variant)
    except RuntimeError as escaped:
        raise AssertionError("the entry path's error escaped the tick, so the "
                             "backstop never ran") from escaped

    assert out.get("backstop_closed") == [tid], out
    assert _closed_row(tmp_db, tid)[0] == "closed"
    assert out["status"] == "entry_error"
    assert out["hb_status"] == "error"
    assert out["hb_note"] == repr(RuntimeError("sizing failed"))
    assert "opened" not in out
