"""The Binance open-interest fetcher's stamp convention (BACKLOG item 30).

Binance's /futures/data/openInterestHist row stamped T is a point snapshot
taken AT T — which is the open interest at the END of hour T-1. The table's
convention, set in the CoinDesk era and the one squeeze_bull and short_squeeze
were researched and validated on, is that the row stamped H holds the open
interest at the end of hour H. Storing the snapshot under T therefore made
every value from 2026-06-10 on one bar stale relative to the price bar it is
joined to: SJ-4250 fired on the stale values (-2.48 %) and would not have at
bar closes (-1.79 %, short of the -2 % trigger). The row must land at T-3600.
"""
from __future__ import annotations

import sqlite3

from data.sources import binance

H = 3600
T = 1789768800  # 2026-09-18 22:00:00 UTC, a period boundary


def _payload(*stamps_and_oi):
    return [{"timestamp": t * 1000, "sumOpenInterest": str(oi),
             "sumOpenInterestValue": str(oi * 80_000)} for t, oi in stamps_and_oi]


def test_snapshot_at_T_is_stored_as_the_close_of_hour_T_minus_one(tmp_path, monkeypatch):
    db = tmp_path / "prod.db"
    monkeypatch.setattr(binance, "DB_PATH", db)
    monkeypatch.setattr(binance, "_get", lambda url, params: _payload((T, 108000.5)))

    assert binance.fetch_open_interest() == 1

    rows = sqlite3.connect(db).execute(
        "SELECT timestamp, oi_open, oi_close FROM cd_open_interest").fetchall()
    assert rows == [(T - H, 108000.5, 108000.5)], (
        f"snapshot at T landed at {rows[0][0] - T:+d}s from T; it is the close "
        f"of hour T-1 and belongs at T-3600")


def test_consecutive_snapshots_keep_their_order_and_spacing(tmp_path, monkeypatch):
    db = tmp_path / "prod.db"
    monkeypatch.setattr(binance, "DB_PATH", db)
    monkeypatch.setattr(binance, "_get",
                        lambda url, params: _payload((T, 100.0), (T + H, 101.0), (T + 2 * H, 102.0)))

    assert binance.fetch_open_interest() == 3

    rows = sqlite3.connect(db).execute(
        "SELECT timestamp, oi_close FROM cd_open_interest ORDER BY timestamp").fetchall()
    assert rows == [(T - H, 100.0), (T, 101.0), (T + H, 102.0)]


def test_refetch_is_idempotent_on_the_shifted_stamps(tmp_path, monkeypatch):
    """INSERT OR IGNORE keys on the stored stamp, so the second pull of the
    same window must add nothing — the guard that keeps the feed's per-minute
    re-pull from ever double-writing."""
    db = tmp_path / "prod.db"
    monkeypatch.setattr(binance, "DB_PATH", db)
    monkeypatch.setattr(binance, "_get", lambda url, params: _payload((T, 100.0), (T + H, 101.0)))
    assert binance.fetch_open_interest() == 2
    assert binance.fetch_open_interest() == 0
