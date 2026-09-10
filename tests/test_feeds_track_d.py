"""Tests for the Track D feeds (2026-09-06): Yahoo macro daily, Deribit public
feed, and the PAXG kline table wiring. Network is never touched — every
fetcher takes an injected http_get."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from strategies.support import clock
from strategies.support import db as _db_mod
from data.sources import deribit, macro_yahoo


@pytest.fixture
def tmp_prod(tmp_path, monkeypatch):
    path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    deribit._done.clear()
    yield path
    deribit._done.clear()
    clock.set_simulated_now(None)


# ─── macro_yahoo ──────────────────────────────────────────────────────────────

def _yahoo_payload(ts, closes):
    def shift(c, d):
        return None if c is None else c + d
    return {"chart": {"result": [{
        "timestamp": ts,
        "indicators": {"quote": [{
            "open": [shift(c, -1) for c in closes], "high": [shift(c, 2) for c in closes],
            "low": [shift(c, -2) for c in closes], "close": closes,
            "volume": [100] * len(closes)}]}}]}}


def test_yahoo_parse_uses_utc_dates_and_skips_null_closes():
    ts = [1788615000, 1788701400, 1788787800]          # 13:30 UTC sessions
    payload = _yahoo_payload(ts, [100.0, None, 102.0])
    rows = macro_yahoo.fetch_yahoo("^GSPC", http_get=lambda url: payload)
    assert [r[0] for r in rows] == ["2026-09-05", "2026-09-07"]
    assert rows[0][4] == 100.0 and rows[0][5] == 100


def test_yahoo_deep_uses_period_bounds_not_range_max():
    seen = []
    macro_yahoo.fetch_yahoo("^GSPC", http_get=lambda url: (seen.append(url), _yahoo_payload([], []))[1],
                            deep=True)
    assert "period1=946684800" in seen[0] and "range=max" not in seen[0]


def test_macro_refresh_upserts_per_symbol_and_isolates_failures(tmp_prod, monkeypatch):
    monkeypatch.setattr(macro_yahoo, "REQUEST_GAP_S", 0)
    def fake(url):
        if "VIX" in url:
            raise RuntimeError("yahoo down")
        return _yahoo_payload([1788615000], [50.0])
    out = macro_yahoo.refresh(http_get=fake)
    assert out["VIX"] == -1 and out["SPX"] == 1
    con = sqlite3.connect(str(tmp_prod))
    n = con.execute("SELECT COUNT(*) FROM macro_daily").fetchone()[0]
    pk = [r[1] for r in con.execute("PRAGMA table_info(macro_daily)") if r[5]]
    con.close()
    assert n == len(macro_yahoo.SYMBOLS) - 1
    assert pk == ["symbol", "date"]
    assert macro_yahoo.latest("SPX") == ("2026-09-05", 50.0)


def test_macro_seed_from_copies_only_our_symbols(tmp_prod, tmp_path):
    src = tmp_path / "trader.db"
    con = sqlite3.connect(str(src))
    con.execute(macro_yahoo.DDL)
    con.executemany("INSERT INTO macro_daily VALUES (?,?,?,?,?,?,?)", [
        ("SPX", "2020-01-02", 1, 2, 0, 1.5, 9), ("COCOA", "2020-01-02", 1, 2, 0, 1.5, 9)])
    con.commit(); con.close()
    assert macro_yahoo.seed_from(src) == 1
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT symbol FROM macro_daily").fetchall() == [("SPX",)]
    con.close()


# ─── deribit ──────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 6, 8, 6, tzinfo=timezone.utc)


def test_parse_instrument_and_liquid_filter():
    p = deribit.parse_instrument("BTC-26DEC25-85000-P")
    assert p == {"asset": "BTC", "strike": 85000.0, "option_type": "PUT",
                 "expiry_ts": int(datetime(2025, 12, 26, 8, tzinfo=timezone.utc).timestamp())}
    assert deribit.parse_instrument("BTC_USDC-11APR26-79000-P") is None
    now_ts = int(NOW.timestamp())
    near = now_ts + 30 * 86400
    assert deribit.is_liquid(near, 70_000, 70_000, now_ts)
    assert not deribit.is_liquid(near, 70_000 * 1.5, 70_000, now_ts)        # ln 1.5 > 0.30
    assert not deribit.is_liquid(now_ts + 120 * 86400, 70_000, 70_000, now_ts)
    assert not deribit.is_liquid(now_ts - 1, 70_000, 70_000, now_ts)


def _fake_api(calls: list):
    def http_get(url):
        calls.append(url)
        if "get_volatility_index_data" in url:
            return {"result": {"data": [[1788652800000, 38.6, 39.1, 38.6, 38.9]]}}
        if "get_instruments" in url:
            return {"result": [
                {"instrument_name": "BTC-2OCT26-70000-C", "expiration_timestamp": 1790668800000,
                 "creation_timestamp": 1788000000000},
                {"instrument_name": "BTC_USDC-2OCT26-70000-C", "expiration_timestamp": 1790668800000,
                 "creation_timestamp": 1788000000000}]}
        if "get_book_summary_by_currency" in url:
            return {"result": [
                {"instrument_name": "BTC-2OCT26-70000-C", "mark_price": 0.05, "mark_iv": 40.0,
                 "bid_price": 0.049, "ask_price": 0.051, "underlying_price": 70_000.0,
                 "open_interest": 100.0, "volume": 5.0, "volume_usd": 17_500.0},
                {"instrument_name": "BTC-2OCT26-120000-C", "mark_price": 0.001, "mark_iv": 60.0,
                 "bid_price": None, "ask_price": 0.002, "underlying_price": 70_000.0,
                 "open_interest": 1.0, "volume": 0.0, "volume_usd": 0.0}]}
        raise AssertionError(url)
    return http_get


def test_deribit_refresh_throttles_and_snapshots_liquid_subset(tmp_prod, monkeypatch):
    monkeypatch.setattr(deribit, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(deribit, "ASSETS", ("BTC",))
    calls: list = []
    out = deribit.refresh(now=NOW, http_get=_fake_api(calls))
    assert out == {"dvol_BTC": 1, "instruments_BTC": 1, "snapshot_BTC": 1}
    con = sqlite3.connect(str(tmp_prod))
    dv = con.execute("SELECT asset, timestamp, close, source FROM deribit_dvol_daily").fetchall()
    assert dv == [("BTC", 1788652800, 38.9, "deribit")]
    inst = con.execute("SELECT instrument, option_type, strike, expiry_ts FROM deribit_options_instruments").fetchall()
    assert inst == [("BTC-2OCT26-70000-C", "CALL", 70000.0, 1790668800)]
    snap = con.execute("SELECT instrument, timestamp, mark_price, mark_price_usd, mark_iv "
                       "FROM deribit_options_daily").fetchall()
    assert snap == [("BTC-2OCT26-70000-C", int(NOW.replace(minute=0).timestamp()), 0.05, 3500.0, 40.0)]
    for t, cols in (("deribit_dvol_daily", ["asset", "timestamp"]),
                    ("deribit_options_daily", ["instrument", "timestamp"]),
                    ("deribit_options_instruments", ["instrument"])):
        assert [r[1] for r in con.execute(f"PRAGMA table_info({t})") if r[5]] == cols
    con.close()
    # second call in the same day/hour bucket does nothing
    assert deribit.refresh(now=NOW, http_get=_fake_api(calls)) == {}
    # a non-snapshot hour re-runs nothing either; a new day re-runs the dailies only
    later = NOW.replace(hour=13)
    assert deribit.refresh(now=later, http_get=_fake_api(calls)) == {}
    # A day later the dailies re-run, AND the snapshot catches up: the newest
    # row is ~27h old, past SNAPSHOT_MAX_AGE_S, so the 00:05/08:05 window was
    # missed and waiting for the next one would breach the 18h contract.
    nxt = NOW.replace(day=7, hour=3)
    assert set(deribit.refresh(now=nxt, http_get=_fake_api(calls))) == {
        "dvol_BTC", "instruments_BTC", "snapshot_BTC"}


def test_deribit_snapshot_catches_up_after_downtime(tmp_prod, monkeypatch):
    """A missed 00:05/08:05 window is not recoverable by waiting, so a snapshot
    older than SNAPSHOT_MAX_AGE_S makes one due at any hour. Two overnight
    outages in 2026-09 left a STALE TABLE alert that running the feed could not
    clear."""
    monkeypatch.setattr(deribit, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(deribit, "ASSETS", ("BTC",))
    calls: list = []
    deribit.refresh(now=NOW, http_get=_fake_api(calls))          # seeds a snapshot
    deribit._done.clear()

    # 15h later, off-window: within the legitimate 08:05 -> 00:05 gap, not due.
    assert "snapshot_BTC" not in deribit.refresh(
        now=NOW + timedelta(hours=15), http_get=_fake_api(calls))

    # 17h later, off-window: the window was missed, so catch up now.
    out = deribit.refresh(now=NOW + timedelta(hours=17), http_get=_fake_api(calls))
    assert out.get("snapshot_BTC") == 1

    # ...and only once per hour, so a repeat tick does not hammer the API.
    assert "snapshot_BTC" not in deribit.refresh(
        now=NOW + timedelta(hours=17, minutes=3), http_get=_fake_api(calls))


def test_catch_up_is_scoped_per_asset(tmp_prod, monkeypatch):
    """The table has no asset column, so the age query must filter by
    instrument prefix. Without it the first asset's catch-up write makes the
    table look fresh and the second asset is silently skipped for another 16h
    -- exactly what happened on the 2026-09-10 catch-up (BTC wrote 496 rows,
    ETH did not run)."""
    monkeypatch.setattr(deribit, "REQUEST_GAP_S", 0)
    con = sqlite3.connect(str(tmp_prod))
    con.execute(f"CREATE TABLE {deribit.DAILY_TABLE} "
                "(instrument TEXT, timestamp INTEGER, PRIMARY KEY (instrument, timestamp))")
    # BTC is fresh, ETH is a day stale.
    con.execute(f"INSERT INTO {deribit.DAILY_TABLE} VALUES ('BTC-2OCT26-70000-C', ?)",
                (int(NOW.timestamp()),))
    con.execute(f"INSERT INTO {deribit.DAILY_TABLE} VALUES ('ETH-2OCT26-4000-C', ?)",
                (int((NOW - timedelta(hours=25)).timestamp()),))
    con.commit(); con.close()

    assert deribit._snapshot_age_s(NOW, "BTC") == pytest.approx(0, abs=2)
    assert deribit._snapshot_age_s(NOW, "ETH") == pytest.approx(25 * 3600, abs=2)
    # unscoped would see only the fresh BTC row and hide the stale ETH one
    assert deribit._snapshot_age_s(NOW) == pytest.approx(0, abs=2)


def test_snapshot_age_is_none_when_unreadable(tmp_prod):
    """No table and no rows both mean "cannot tell", which must never be read
    as due — otherwise a fresh DB would snapshot on every single tick."""
    assert deribit._snapshot_age_s(NOW) is None
    con = sqlite3.connect(str(tmp_prod))
    con.execute(f"CREATE TABLE {deribit.DAILY_TABLE} (instrument TEXT, timestamp INTEGER)")
    con.commit(); con.close()
    assert deribit._snapshot_age_s(NOW) is None


def test_deribit_refresh_noops_under_simulated_clock(tmp_prod):
    clock.set_simulated_now(NOW)
    assert deribit.refresh(http_get=lambda url: (_ for _ in ()).throw(AssertionError(url))) == {}


def test_deribit_seed_from_maps_snapshot_tables(tmp_prod, tmp_path):
    src = tmp_path / "trader.db"
    con = sqlite3.connect(str(src))
    con.execute("CREATE TABLE cd_dvol (asset TEXT, timestamp INTEGER, open REAL, high REAL, low REAL, close REAL, PRIMARY KEY (asset, timestamp))")
    con.execute("INSERT INTO cd_dvol VALUES ('BTC', 1679443200, 50, 51, 49, 50.5)")
    con.execute("CREATE TABLE cd_options_instruments (instrument TEXT PRIMARY KEY, asset TEXT, market TEXT, option_type TEXT, strike REAL, expiry_ts INTEGER, first_trade_ts INTEGER, last_trade_ts INTEGER)")
    con.executemany("INSERT INTO cd_options_instruments VALUES (?,?,?,?,?,?,?,?)", [
        ("BTC-26DEC25-85000-P", "BTC", "deribit", "PUT", 85000, 1766736000, 1735000000, 1766000000),
        ("BTC_USDC-11APR26-79000-P", "BTC", "deribit", "PUT", 79000, 1775894400, 0, 0)])
    con.execute("CREATE TABLE cd_options_oi (instrument TEXT, timestamp INTEGER, close_mark_price REAL, PRIMARY KEY (instrument, timestamp))")
    con.execute("INSERT INTO cd_options_oi VALUES ('BTC-26DEC25-85000-P', 1735171200, 14136.15)")
    con.execute("CREATE TABLE cd_options_ohlcv (instrument TEXT, timestamp INTEGER, volume REAL, notional_volume REAL, PRIMARY KEY (instrument, timestamp))")
    con.execute("INSERT INTO cd_options_ohlcv VALUES ('BTC-26DEC25-85000-P', 1735171200, 3.0, 42000.0)")
    con.commit(); con.close()
    out = deribit.seed_from(src)
    assert out == {"dvol": 1, "instruments": 1, "daily": 1}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT source FROM deribit_dvol_daily").fetchone() == ("coindesk_seed",)
    assert con.execute("SELECT instrument FROM deribit_options_instruments").fetchall() == [("BTC-26DEC25-85000-P",)]
    row = con.execute("SELECT mark_price, mark_price_usd, volume, volume_usd, source FROM deribit_options_daily").fetchone()
    assert row == (None, 14136.15, 3.0, 42000.0, "coindesk_seed")
    con.close()


# ─── PAXG wiring ──────────────────────────────────────────────────────────────

def test_paxg_table_ddl_matches_spot_shape_and_is_registered():
    import bootstrap, botlib
    from data import check_gaps
    from data.sources import binance
    assert "paxg_spot_1h" in bootstrap.SCHEMAS
    assert "timestamp INTEGER PRIMARY KEY" in bootstrap.SCHEMAS["paxg_spot_1h"]
    assert binance.PAXG_TABLE == "paxg_spot_1h"
    for t in ("paxg_spot_1h", "macro_daily", "deribit_dvol_daily",
              "deribit_options_daily", "deribit_options_instruments"):
        assert t in botlib.FRESHNESS_CONTRACTS
    assert {s.table for s in check_gaps.SPECS} >= {"paxg_spot_1h", "deribit_dvol_daily"}


def test_ensure_kline_table_creates_pk(tmp_path):
    from data.sources import binance
    con = sqlite3.connect(str(tmp_path / "x.db"))
    binance._ensure_kline_1h_table(con, "paxg_spot_1h")
    assert [r[1] for r in con.execute("PRAGMA table_info(paxg_spot_1h)") if r[5]] == ["timestamp"]
    con.close()
