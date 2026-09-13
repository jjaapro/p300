"""Every sleeve the paper fleet runs must key its opens on the signal bar,
not on the fill instant.

This is the 2026-08-15..24 doubled-fleet failure reproduced per sleeve. In
that incident two chento processes executed the same three signals seconds
apart; because the idempotency key fell back to the fill timestamp, the two
executions minted two different keys, the partial UNIQUE index on
``trades.unique_key`` had nothing to collapse, and three signals were booked
as six rows at two different prices.

Each test below executes the SAME intent twice with the clock advanced
between the calls — exactly what a second process does — and asserts the
ledger holds one row. A sleeve that regresses to a wall-clock key fails here
rather than in production.

The granularity per sleeve is deliberately the one the sleeve's own
in-process guard already enforces, so this adds DB-level enforcement without
changing which trades are allowed:
  chento / short_squeeze / squeeze_bull   the trigger bar
  adx / carry                             the UTC day (once-per-day gate)
  r4                                      the UTC day, per window (sleeve name)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies.support import clock, trade_db
from strategies.support import db as _db_mod
from strategies.support.dispatch import Intent

T0 = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
VARIANT = {"id": "v_test", "capital_usdt": 10_000.0}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "ledger.db"
    monkeypatch.setattr(_db_mod, "DASH_DB", p)
    monkeypatch.setattr(trade_db, "DB_PATH", p)
    trade_db.init_db()
    clock.set_simulated_now(T0)
    yield p
    clock.set_simulated_now(None)


def _rows(ledger) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(ledger))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, strategy, unique_key FROM trades ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _twice(execute, intent):
    """Execute the same intent twice, 33 seconds apart — the observed gap
    between the doubled fleet's paired chento fills."""
    first = execute(intent)
    clock.set_simulated_now(T0 + timedelta(seconds=33))
    second = execute(intent)
    return first, second


def _assert_one_row(ledger, first, second):
    rows = _rows(ledger)
    assert len(rows) == 1, (
        f"same signal executed twice booked {len(rows)} rows: "
        f"{[dict(r) for r in rows]}"
    )
    assert first["trade_id"] == second["trade_id"]
    assert rows[0]["unique_key"] is not None


def _intent(reason: dict, **kw) -> Intent:
    args = dict(asset="BTC", direction="LONG", allocation_pct=10.0,
                leverage=1.0, conviction=100, priority=100.0,
                reason=reason, scheduled_exit_dt=None)
    args.update(kw)
    return Intent(**args)


def test_short_squeeze_keys_on_the_trigger_bar(ledger):
    """Regression: this read reason["bar_ts"] while the dict it is handed
    carries "bar_ts_utc". The name never matched, so every SHORT_SQUEEZE open
    silently fell back to the fill instant."""
    from bots.short_squeeze.strategy import signal as sleeve

    exit_dt = T0 + timedelta(hours=6)
    intent = _intent({
        "trigger": "short_squeeze_long",
        "sleeve": "SHORT_SQUEEZE",
        "bar_ts_utc": "2026-09-12T09:45:00+00:00",
        "_stop_price": 99.0, "_target_price": 103.0,
        "_reference_stop_price": 99.0,
        "_time_stop_iso": exit_dt.isoformat(),
        "_entry_price": 100.0,
    }, scheduled_exit_dt=exit_dt)

    first, second = _twice(
        lambda i: sleeve.execute(VARIANT, i), intent)
    _assert_one_row(ledger, first, second)
    assert _rows(ledger)[0]["unique_key"].endswith("2026-09-12T09:45:00+00:00")


def test_squeeze_bull_keys_on_the_trigger_bar(ledger):
    from bots.squeeze_bull.strategy import signal as sleeve

    exit_dt = T0 + timedelta(hours=48)
    intent = _intent({
        "trigger": "squeeze_bull_long",
        "sleeve": "SQUEEZE_BULL",
        "bar_ts": 1789146000,
        "regime": "bull_30d",
        "_stop_price": 98.0, "_target_price": 103.0,
        "_reference_stop_price": 98.0,
        "_time_stop_iso": exit_dt.isoformat(),
        "_entry_price": 100.0,
    }, scheduled_exit_dt=exit_dt)

    first, second = _twice(
        lambda i: sleeve.execute(VARIANT, i), intent)
    _assert_one_row(ledger, first, second)
    assert _rows(ledger)[0]["unique_key"].endswith("|1789146000")


def test_squeeze_bull_without_a_bar_never_keys_on_the_string_none(ledger):
    """A bar-less reason must fall back to the fill instant, NOT to the
    literal "None" — a "None" key would be identical on every future open
    and the UNIQUE index would block this variant's second trade forever."""
    from bots.squeeze_bull.strategy import signal as sleeve

    def _mk():
        return _intent({
            "sleeve": "SQUEEZE_BULL", "regime": "bull_30d",
            "_stop_price": 98.0, "_target_price": 103.0,
            "_entry_price": 100.0,
        })

    first = sleeve.execute(VARIANT, _mk())
    clock.set_simulated_now(T0 + timedelta(days=3))
    second = sleeve.execute(VARIANT, _mk())

    rows = _rows(ledger)
    assert len(rows) == 2, "an unrelated later signal must not be swallowed"
    assert first["trade_id"] != second["trade_id"]
    for r in rows:
        assert not r["unique_key"].endswith("|None")


def test_adx_keys_on_the_signal_day(ledger):
    from bots.adx.strategy import signal as sleeve

    intent = _intent({
        "trigger": "S-003_ADX_entry", "sleeve": "ADX",
        "regime": "unknown", "stop_loss_pct": 2.0,
        "_stop_price": 98.0, "_entry_price": 100.0,
        "_signal_day": "2026-09-12",
    })

    first, second = _twice(
        lambda i: sleeve.execute(VARIANT, i), intent)
    _assert_one_row(ledger, first, second)
    assert _rows(ledger)[0]["unique_key"] == "v_test|ADX|BTC|2026-09-12"


def test_carry_keys_on_the_signal_day(ledger):
    from bots.carry.strategy import signal as sleeve

    intent = _intent({
        "trigger": "S-078_carry_entry", "sleeve": "CARRY",
        "regime": "unknown",
        "_entry_price": 100.0,
        "_fr_7d_avg_pct": 0.0123,
        "_signal_day": "2026-09-12",
    })

    first, second = _twice(
        lambda i: sleeve.execute(VARIANT, i), intent)
    _assert_one_row(ledger, first, second)
    assert _rows(ledger)[0]["unique_key"] == "v_test|CARRY|BTC|2026-09-12"


def test_r4_keys_on_the_signal_day_per_window(ledger):
    """R4's windows overlap by design (up to three concurrent positions), so
    the key must separate them. The window is already in sleeve_name, which
    makes (variant | window | asset | day) the correct granularity."""
    from bots.r4.strategy import signal as sleeve

    exit_dt = T0 + timedelta(hours=12)

    def _r4_intent(strategy: str) -> Intent:
        return _intent({
            "sleeve": strategy, "mode": "neutral", "trigger": "calendar_open",
            "_entry_price": 100.0,
            "_strategy": strategy,
            "_exit_dt_iso": exit_dt.isoformat(),
            "_mode": "neutral",
            "_now_iso": T0.isoformat(),
            "_signal_day": "2026-09-12",
        }, asset="ETH", scheduled_exit_dt=exit_dt)

    first, second = _twice(
        lambda i: sleeve.execute(VARIANT, i), _r4_intent("JPLUS_R4_ETH"))
    _assert_one_row(ledger, first, second)

    # A different window on the same day is a different trade, not a dupe.
    clock.set_simulated_now(T0 + timedelta(minutes=5))
    sleeve.execute(VARIANT, _r4_intent("JPLUS_R4_ETH_V2"))
    keys = {r["unique_key"] for r in _rows(ledger)}
    assert keys == {"v_test|JPLUS_R4_ETH|ETH|2026-09-12",
                    "v_test|JPLUS_R4_ETH_V2|ETH|2026-09-12"}


def test_missing_signal_key_is_logged_not_silent(ledger, caplog):
    """The fallback stays legal — the dormant sleeves still use it — but it
    must announce itself. Its silence is why the short_squeeze key bug
    survived from deployment on 2026-07-21 to 2026-09-12."""
    import logging

    from strategies import trades

    with caplog.at_level(logging.WARNING):
        trades.open_paper_trade(
            variant=VARIANT, sleeve_name="TEST", asset="BTC",
            direction="LONG", entry_price=100.0, allocation_pct=10.0,
            reason={},
        )
    warned = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.WARNING]
    assert any("no signal key" in m for m in warned), warned
    assert any("v_test/TEST" in m for m in warned), warned
