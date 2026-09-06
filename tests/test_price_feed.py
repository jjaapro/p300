"""price_feed.get_current_price must never return the forming 1m bar
(review 2026-09-06, finding 6). The feed upserts the in-progress candle
in place, so between minute boundaries the newest row is provisional."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies.support import clock, price_feed
from strategies.support import db as _db_mod

T = datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc)   # a minute boundary


@pytest.fixture
def bars(tmp_path, monkeypatch):
    p = tmp_path / "prod.db"
    monkeypatch.setattr(_db_mod, "TRADER_DB", p)
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE btc_1m (open_time INTEGER PRIMARY KEY, close REAL)")
    # 09:59 closed at 100; 10:00 is the forming bar carrying a wild print
    con.execute("INSERT INTO btc_1m VALUES (?, 100.0)",
                (int((T - timedelta(minutes=1)).timestamp() * 1000),))
    con.execute("INSERT INTO btc_1m VALUES (?, 999.0)",
                (int(T.timestamp() * 1000),))
    con.commit()
    con.close()
    yield p
    clock.set_simulated_now(None)


def test_mid_minute_clock_skips_forming_bar(bars):
    clock.set_simulated_now(T + timedelta(seconds=30))
    assert price_feed.get_current_price("BTC") == 100.0


def test_clock_at_bar_open_returns_previous_bar(bars):
    clock.set_simulated_now(T)                          # 10:00 bar just opened
    assert price_feed.get_current_price("BTC") == 100.0


def test_clock_at_bar_close_returns_that_bar(bars):
    clock.set_simulated_now(T + timedelta(minutes=1))   # 10:00 bar closed at 10:01
    assert price_feed.get_current_price("BTC") == 999.0
