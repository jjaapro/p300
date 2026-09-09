"""Tests for the Coinbase hourly spot feed (Track D4, 2026-09-08).

Network is never touched — every fetcher takes an injected `http_get`.
Same shape as tests/test_feeds_track_d.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from strategies.support import clock
from strategies.support import db as _db_mod
from data.sources import coinbase


NOW = datetime(2026, 9, 8, 12, 30, tzinfo=timezone.utc)
H = 3600


@pytest.fixture
def tmp_prod(tmp_path, monkeypatch):
    path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(coinbase, "REQUEST_GAP_S", 0)
    coinbase._done.clear()
    yield path
    coinbase._done.clear()
    clock.set_simulated_now(None)


def _candle(ts, low, high, opn, close, vol):
    """Coinbase wire order: [time, low, high, open, close, volume]."""
    return [ts, low, high, opn, close, vol]


def _payload(base_ts=1788739200, n=3):
    """Newest-first, as the API returns it."""
    return [_candle(base_ts + i * H, 100.0 + i, 110.0 + i, 105.0 + i, 106.0 + i, 1.5 + i)
            for i in range(n - 1, -1, -1)]


# ─── parsing ──────────────────────────────────────────────────────────────────

def test_parse_reorders_ohlc_and_sorts_ascending():
    rows = coinbase.fetch_candles("BTC", 0, 1, http_get=lambda url: _payload())
    assert [r[0] for r in rows] == [1788739200, 1788742800, 1788746400]
    # (timestamp, open, high, low, close, volume) from [t, low, high, open, close, vol]
    assert rows[0] == (1788739200, 105.0, 110.0, 100.0, 106.0, 1.5)
    assert rows[2] == (1788746400, 107.0, 112.0, 102.0, 108.0, 3.5)


def test_parse_drops_off_hour_and_malformed_rows():
    payload = _payload(n=1) + [_candle(1788739200 + 90, 1, 2, 1, 2, 3)] + [[1788750000, 1, 2]]
    rows = coinbase.fetch_candles("BTC", 0, 1, http_get=lambda url: payload)
    assert [r[0] for r in rows] == [1788739200]


def test_request_url_carries_granularity_iso_bounds_and_product():
    seen: list[str] = []
    coinbase.fetch_candles("ETH", 1788739200, 1788746400,
                           http_get=lambda url: (seen.append(url), [])[1])
    assert "products/ETH-USD/candles" in seen[0]
    assert "granularity=3600" in seen[0]
    assert "start=2026-09-07T00%3A00%3A00Z" in seen[0]
    assert "end=2026-09-07T02%3A00%3A00Z" in seen[0]


def test_error_body_raises_instead_of_writing_garbage():
    with pytest.raises(RuntimeError):
        coinbase.fetch_candles("BTC", 0, 1,
                               http_get=lambda url: {"message": "rate limit exceeded"})


# ─── refresh ──────────────────────────────────────────────────────────────────

def test_refresh_upserts_both_assets_and_declares_pk(tmp_prod):
    out = coinbase.refresh(now=NOW, http_get=lambda url: _payload())
    assert out == {"BTC": 3, "ETH": 3}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM coinbase_spot_1h").fetchone()[0] == 6
    assert con.execute(
        "SELECT asset, timestamp, open, high, low, close, volume FROM coinbase_spot_1h "
        "WHERE asset='BTC' ORDER BY timestamp").fetchone() == (
            "BTC", 1788739200, 105.0, 110.0, 100.0, 106.0, 1.5)
    pk = [r[1] for r in con.execute("PRAGMA table_info(coinbase_spot_1h)") if r[5]]
    idx = [r for r in con.execute("PRAGMA index_list(coinbase_spot_1h)") if r[3] == "pk"]
    con.close()
    assert pk == ["asset", "timestamp"]
    assert len(idx) == 1
    assert coinbase.latest("BTC") == (1788746400, 108.0)


def test_refresh_throttles_to_one_pull_per_asset_per_hour(tmp_prod):
    calls: list[str] = []

    def fake(url):
        calls.append(url)
        return _payload()

    assert coinbase.refresh(now=NOW, http_get=fake) == {"BTC": 3, "ETH": 3}
    assert len(calls) == 2
    assert coinbase.refresh(now=NOW.replace(minute=59), http_get=fake) == {}
    assert len(calls) == 2
    nxt = NOW.replace(hour=13)
    assert coinbase.refresh(now=nxt, http_get=fake) == {"BTC": 3, "ETH": 3}
    assert len(calls) == 4


def test_refresh_isolates_a_failing_asset(tmp_prod):
    def fake(url):
        if "ETH-USD" in url:
            raise RuntimeError("coinbase 503")
        return _payload()

    out = coinbase.refresh(now=NOW, http_get=fake)
    assert out == {"BTC": 3, "ETH": -1}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT DISTINCT asset FROM coinbase_spot_1h").fetchall() == [("BTC",)]
    con.close()
    # the failed asset is not marked done, so the next tick retries it
    assert "ETH:2026-09-08:12" not in coinbase._done


def test_refresh_noops_under_simulated_clock(tmp_prod):
    clock.set_simulated_now(NOW)
    assert coinbase.refresh(
        http_get=lambda url: (_ for _ in ()).throw(AssertionError(url))) == {}


def test_refresh_replaces_the_previously_partial_bar(tmp_prod):
    coinbase.refresh(now=NOW, http_get=lambda url: [_candle(1788739200, 1, 2, 1, 2, 0.1)])
    coinbase._done.clear()
    coinbase.refresh(now=NOW.replace(hour=13),
                     http_get=lambda url: [_candle(1788739200, 1, 9, 1, 8, 42.0)])
    con = sqlite3.connect(str(tmp_prod))
    row = con.execute("SELECT close, volume FROM coinbase_spot_1h WHERE asset='BTC'").fetchall()
    con.close()
    assert row == [(8.0, 42.0)]


# ─── backfill (CLI only) ──────────────────────────────────────────────────────

def test_backfill_pages_forward_and_is_not_called_by_refresh(tmp_prod):
    import inspect
    assert "backfill" not in inspect.getsource(coinbase.refresh)
    assert "backfill" not in inspect.getsource(coinbase.refresh_asset)

    starts: list[str] = []

    def fake(url):
        starts.append(url.split("start=")[1].split("&")[0])
        return []

    now = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)
    since = int(now.timestamp()) - 400 * H          # needs two 300-candle pages
    out = coinbase.backfill(since, now=now, http_get=fake)
    assert out == {"BTC": 0, "ETH": 0}
    assert len(starts) == 4                          # 2 pages x 2 assets
    assert starts[0] == "2026-08-22T08%3A00%3A00Z"
    assert starts[1] == "2026-09-03T20%3A00%3A00Z"   # exactly 300h later, no overlap


def test_backfill_default_start_resumes_after_last_stored_bar(tmp_prod):
    con = sqlite3.connect(str(tmp_prod))
    coinbase.ensure_schema(con)
    con.execute("INSERT INTO coinbase_spot_1h VALUES ('BTC', 1788739200, 1, 2, 0, 1.5, 9)")
    con.commit()
    con.close()
    starts: list[str] = []
    now = datetime.fromtimestamp(1788739200 + 5 * H, tz=timezone.utc)
    coinbase.backfill(now=now, http_get=lambda url: (
        starts.append(url.split("start=")[1].split("&")[0]), [])[1])
    assert starts[0] == "2026-09-07T01%3A00%3A00Z"                # last bar + 1h
    assert starts[1] == coinbase._iso(coinbase.DEFAULT_BACKFILL_START).replace(":", "%3A")


def test_backfill_isolates_a_failing_asset(tmp_prod):
    def fake(url):
        if "BTC-USD" in url:
            raise RuntimeError("coinbase down")
        return _payload()

    now = datetime.fromtimestamp(1788746400, tz=timezone.utc)
    out = coinbase.backfill(1788739200, now=now, http_get=fake)
    assert out["BTC"] == -1 and out["ETH"] == 3


# ─── seed ─────────────────────────────────────────────────────────────────────

def _seed_db(path):
    con = sqlite3.connect(str(path))
    for t in ("coinbase_btc_hourly", "coinbase_eth_hourly"):
        con.execute(f"CREATE TABLE {t} (timestamp INTEGER PRIMARY KEY, open REAL, "
                    "high REAL, low REAL, close REAL, volume REAL)")
        con.executemany(f"INSERT INTO {t} VALUES (?,?,?,?,?,?)", [
            (1577836800, 1.0, 2.0, 0.5, 1.5, 9.0),
            (1577840400, 1.5, 2.5, 1.0, 2.0, 8.0)])
    con.commit()
    con.close()


def test_seed_from_loads_both_assets(tmp_prod, tmp_path):
    src = tmp_path / "trader.db"
    _seed_db(src)
    assert coinbase.seed_from(src) == {"BTC": 2, "ETH": 2}
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT COUNT(*) FROM coinbase_spot_1h").fetchone()[0] == 4
    assert con.execute("SELECT open, high, low, close, volume FROM coinbase_spot_1h "
                       "WHERE asset='ETH' AND timestamp=1577836800").fetchone() == (
                           1.0, 2.0, 0.5, 1.5, 9.0)
    con.close()


def test_seed_never_overwrites_a_live_row(tmp_prod, tmp_path):
    src = tmp_path / "trader.db"
    _seed_db(src)
    con = sqlite3.connect(str(tmp_prod))
    coinbase.ensure_schema(con)
    con.execute("INSERT INTO coinbase_spot_1h VALUES ('BTC', 1577836800, 7165.72, "
                "7165.72, 7136.05, 7150.35, 250.84)")
    con.commit()
    con.close()
    coinbase.seed_from(src)
    con = sqlite3.connect(str(tmp_prod))
    assert con.execute("SELECT close FROM coinbase_spot_1h WHERE asset='BTC' "
                       "AND timestamp=1577836800").fetchone() == (7150.35,)
    con.close()


def test_seed_isolates_a_missing_source_table(tmp_prod, tmp_path):
    src = tmp_path / "partial.db"
    con = sqlite3.connect(str(src))
    con.execute("CREATE TABLE coinbase_btc_hourly (timestamp INTEGER PRIMARY KEY, "
                "open REAL, high REAL, low REAL, close REAL, volume REAL)")
    con.execute("INSERT INTO coinbase_btc_hourly VALUES (1577836800, 1, 2, 0, 1.5, 9)")
    con.commit()
    con.close()
    assert coinbase.seed_from(src) == {"BTC": 1, "ETH": -1}


# ─── wiring ───────────────────────────────────────────────────────────────────

def test_table_is_registered_everywhere():
    import bootstrap, botlib
    from data import check_gaps
    from data.sources import binance
    assert botlib.FRESHNESS_CONTRACTS["coinbase_spot_1h"] == ("timestamp", 1.0, 2 * 3600 + 900)
    spec = next(s for s in check_gaps.SPECS if s.table == "coinbase_spot_1h")
    assert (spec.time_col, spec.asset_col, spec.cadence_seconds) == ("timestamp", "asset", 3600)
    assert "coinbase_spot_1h" in bootstrap.SCHEMAS
    assert "data.sources.coinbase" in binance.refresh_all.__code__.co_consts


def test_bootstrap_and_module_ddl_agree():
    """Two copies of the DDL exist (bootstrap for fresh DBs, the module for
    feed.py which never runs bootstrap) — they must not drift."""
    import bootstrap

    def norm(s):
        return " ".join(s.split())
    assert norm(bootstrap.SCHEMAS["coinbase_spot_1h"]) == norm(coinbase.DDL)
