"""Tests for data.sources.coindesk.

HTTP is mocked via the `http_get` injection point on every fetcher. No
test makes a real network call. Coverage:

  • _ensure_schema is idempotent and creates all 3 tables
  • _paginate_backward stops at start_after, skips current bar, dedupes
  • Each per-endpoint mapper translates the API row shape correctly
  • fetch_oi / fetch_liquidations / fetch_dvol orchestrate paginate +
    floor + latest-ts lookup
  • refresh() throttle, per-feed failure isolation, multi-asset DVOL
  • Reader functions tolerate missing tables and apply windows correctly
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pytest

from data.sources import coindesk as cd


# ─── Fixture ────────────────────────────────────────────────────────────────

@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Empty trader.db; schema created on first refresh/fetch."""
    p = tmp_path / "trader.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", p)
    cd.reset_throttle()
    yield p


def _now_aligned_hours(n_hours_ago: int) -> int:
    """Return an hourly-aligned epoch n hours before the previous closed hour
    (so the row passes the `ts >= current_bar_ts` skip check)."""
    now = int(time.time())
    current_bar = (now // 3600) * 3600
    return current_bar - (n_hours_ago + 1) * 3600


def _now_aligned_days(n_days_ago: int) -> int:
    now = int(time.time())
    current_bar = (now // 86400) * 86400
    return current_bar - (n_days_ago + 1) * 86400


# ─── Schema ─────────────────────────────────────────────────────────────────

def test_ensure_schema_creates_all_three_tables(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    try:
        cd._ensure_schema(con)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        con.close()
    assert {"cd_open_interest", "cd_liquidations", "cd_dvol"}.issubset(names)


def test_ensure_schema_is_idempotent(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    try:
        cd._ensure_schema(con)
        cd._ensure_schema(con)  # second call must not raise
    finally:
        con.close()


def test_oi_table_columns_match_trader_schema(fixture_db):
    """Schema is intentionally 1:1 with trader/fetch_coindesk.py so
    cross-repo cd_open_interest reads work without translation."""
    con = sqlite3.connect(str(fixture_db))
    try:
        cd._ensure_schema(con)
        info = con.execute("PRAGMA table_info(cd_open_interest)").fetchall()
    finally:
        con.close()
    cols = {r[1] for r in info}
    assert {"timestamp", "oi_open", "oi_high", "oi_low", "oi_close",
            "oi_value_open", "oi_value_high", "oi_value_low",
            "oi_value_close"}.issubset(cols)


# ─── Mappers ────────────────────────────────────────────────────────────────

def test_map_oi_row_renames_settlement_to_oi_quote_to_oi_value():
    row = {
        "TIMESTAMP": 1746662400,
        "OPEN_SETTLEMENT": 100, "HIGH_SETTLEMENT": 110,
        "LOW_SETTLEMENT": 95,   "CLOSE_SETTLEMENT": 105,
        "OPEN_QUOTE": 8_000_000_000, "HIGH_QUOTE": 8_500_000_000,
        "LOW_QUOTE": 7_900_000_000,  "CLOSE_QUOTE": 8_300_000_000,
    }
    mapped = cd._map_oi_row(row)
    assert mapped[0] == 1746662400
    # Settlement → oi_*
    assert mapped[1:5] == (100, 110, 95, 105)
    # Quote (USD) → oi_value_*
    assert mapped[5:9] == (8_000_000_000, 8_500_000_000,
                            7_900_000_000, 8_300_000_000)


def test_map_liq_row_extracts_long_short_quote_count_vwap():
    row = {
        "TIMESTAMP": 1746662400,
        "LONG_QUANTITY": 5.0, "SHORT_QUANTITY": 1.5,
        "LONG_QUOTE_QUANTITY": 400_000, "SHORT_QUOTE_QUANTITY": 90_000,
        "TOTAL_LONG_LIQUIDATION_UPDATES": 12,
        "TOTAL_SHORT_LIQUIDATION_UPDATES": 3,
        "VWAP_LONG_PRICE": 80_000, "VWAP_SHORT_PRICE": 60_000,
    }
    mapped = cd._map_liq_row(row)
    assert mapped == (1746662400, 5.0, 1.5, 400_000, 90_000, 12, 3, 80_000, 60_000)


def test_map_dvol_row_factory_pins_asset():
    row = {"TIMESTAMP": 1746576000, "OPEN": 50, "HIGH": 55, "LOW": 49, "CLOSE": 52}
    btc_mapper = cd._map_dvol_row_factory("BTC")
    eth_mapper = cd._map_dvol_row_factory("ETH")
    assert btc_mapper(row)[0] == "BTC"
    assert eth_mapper(row)[0] == "ETH"


# ─── _paginate_backward ────────────────────────────────────────────────────

def _scripted_pages(pages: list[list[dict]]):
    """Build an http_get function that returns pages in order."""
    pages_iter = list(pages)

    def http_get(url: str) -> dict:
        if not pages_iter:
            return {"Data": []}
        return {"Data": pages_iter.pop(0)}
    return http_get


def test_paginate_inserts_only_rows_after_start_after(fixture_db, monkeypatch):
    """Rows with ts <= start_after_ts must be skipped."""
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    older_ts = _now_aligned_hours(48)
    newer_ts = _now_aligned_hours(2)
    pages = [[
        {"TIMESTAMP": older_ts, "OPEN_SETTLEMENT": 1, "HIGH_SETTLEMENT": 1,
         "LOW_SETTLEMENT": 1, "CLOSE_SETTLEMENT": 1,
         "OPEN_QUOTE": 1, "HIGH_QUOTE": 1, "LOW_QUOTE": 1, "CLOSE_QUOTE": 1},
        {"TIMESTAMP": newer_ts, "OPEN_SETTLEMENT": 2, "HIGH_SETTLEMENT": 2,
         "LOW_SETTLEMENT": 2, "CLOSE_SETTLEMENT": 2,
         "OPEN_QUOTE": 2, "HIGH_QUOTE": 2, "LOW_QUOTE": 2, "CLOSE_QUOTE": 2},
    ]]
    n = cd._paginate_backward(
        http_get=_scripted_pages(pages), con=con,
        endpoint="/x", params={"market": "binance", "instrument": "BTC"},
        table="cd_open_interest", mapper=cd._map_oi_row,
        start_after_ts=older_ts,   # <- excludes the older row
        bar_size_seconds=3600,
    )
    con.close()
    assert n == 1
    con = sqlite3.connect(str(fixture_db))
    timestamps = [r[0] for r in con.execute(
        "SELECT timestamp FROM cd_open_interest").fetchall()]
    con.close()
    assert timestamps == [newer_ts]


def test_paginate_skips_current_bar(fixture_db, monkeypatch):
    """A row with ts >= current_bar_ts is the still-forming bar; skip it."""
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    current_bar = (int(time.time()) // 3600) * 3600
    closed_bar = current_bar - 3600
    pages = [[
        # Current (in-progress) bar — must be skipped
        {"TIMESTAMP": current_bar, "OPEN_SETTLEMENT": 9, "HIGH_SETTLEMENT": 9,
         "LOW_SETTLEMENT": 9, "CLOSE_SETTLEMENT": 9,
         "OPEN_QUOTE": 9, "HIGH_QUOTE": 9, "LOW_QUOTE": 9, "CLOSE_QUOTE": 9},
        # Last closed bar — must be inserted
        {"TIMESTAMP": closed_bar, "OPEN_SETTLEMENT": 5, "HIGH_SETTLEMENT": 5,
         "LOW_SETTLEMENT": 5, "CLOSE_SETTLEMENT": 5,
         "OPEN_QUOTE": 5, "HIGH_QUOTE": 5, "LOW_QUOTE": 5, "CLOSE_QUOTE": 5},
    ]]
    n = cd._paginate_backward(
        http_get=_scripted_pages(pages), con=con,
        endpoint="/x", params={"market": "binance", "instrument": "BTC"},
        table="cd_open_interest", mapper=cd._map_oi_row,
        start_after_ts=closed_bar - 3600, bar_size_seconds=3600,
    )
    con.close()
    assert n == 1
    con = sqlite3.connect(str(fixture_db))
    timestamps = [r[0] for r in con.execute(
        "SELECT timestamp FROM cd_open_interest").fetchall()]
    con.close()
    assert timestamps == [closed_bar]


def test_paginate_dedupes_on_repeat_call(fixture_db, monkeypatch):
    """Same payload twice → second call inserts 0 (PRIMARY KEY conflict)."""
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    ts = _now_aligned_hours(2)
    page = [
        {"TIMESTAMP": ts, "OPEN_SETTLEMENT": 1, "HIGH_SETTLEMENT": 1,
         "LOW_SETTLEMENT": 1, "CLOSE_SETTLEMENT": 1,
         "OPEN_QUOTE": 1, "HIGH_QUOTE": 1, "LOW_QUOTE": 1, "CLOSE_QUOTE": 1},
    ]
    n1 = cd._paginate_backward(
        http_get=_scripted_pages([page, []]), con=con,
        endpoint="/x", params={"market": "x", "instrument": "y"},
        table="cd_open_interest", mapper=cd._map_oi_row,
        start_after_ts=0, bar_size_seconds=3600,
    )
    n2 = cd._paginate_backward(
        http_get=_scripted_pages([page, []]), con=con,
        endpoint="/x", params={"market": "x", "instrument": "y"},
        table="cd_open_interest", mapper=cd._map_oi_row,
        start_after_ts=0, bar_size_seconds=3600,
    )
    con.close()
    assert n1 == 1
    assert n2 == 0


def test_paginate_swallows_http_errors_returns_partial_count(fixture_db, monkeypatch):
    """HTTP error mid-pagination → log + return whatever we already inserted."""
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    calls = {"n": 0}

    def flaky_get(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"Data": [
                {"TIMESTAMP": _now_aligned_hours(2),
                 "OPEN_SETTLEMENT": 1, "HIGH_SETTLEMENT": 1,
                 "LOW_SETTLEMENT": 1, "CLOSE_SETTLEMENT": 1,
                 "OPEN_QUOTE": 1, "HIGH_QUOTE": 1, "LOW_QUOTE": 1, "CLOSE_QUOTE": 1},
            ]}
        from urllib.error import URLError
        raise URLError("simulated outage")

    n = cd._paginate_backward(
        http_get=flaky_get, con=con, endpoint="/x",
        params={"market": "x", "instrument": "y"},
        table="cd_open_interest", mapper=cd._map_oi_row,
        start_after_ts=0, bar_size_seconds=3600,
    )
    con.close()
    # First page inserted 1 row; second page errored → no exception bubbled
    assert n == 1


# ─── Per-endpoint fetchers ──────────────────────────────────────────────────

def test_fetch_oi_inserts_via_paginate(fixture_db, monkeypatch):
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    ts = _now_aligned_hours(3)
    pages = [[
        {"TIMESTAMP": ts,
         "OPEN_SETTLEMENT": 100, "HIGH_SETTLEMENT": 110,
         "LOW_SETTLEMENT": 95, "CLOSE_SETTLEMENT": 105,
         "OPEN_QUOTE": 8e9, "HIGH_QUOTE": 8.5e9,
         "LOW_QUOTE": 7.9e9, "CLOSE_QUOTE": 8.3e9},
    ]]
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    n = cd.fetch_oi(con, http_get=_scripted_pages(pages), lookback_hours=48)
    con.close()
    assert n == 1


def test_fetch_liquidations_uses_correct_endpoint(fixture_db, monkeypatch):
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    captured_urls = []

    def http_get(url):
        captured_urls.append(url)
        return {"Data": [{
            "TIMESTAMP": _now_aligned_hours(2),
            "LONG_QUANTITY": 5, "SHORT_QUANTITY": 1,
            "LONG_QUOTE_QUANTITY": 400_000, "SHORT_QUOTE_QUANTITY": 90_000,
            "TOTAL_LONG_LIQUIDATION_UPDATES": 12,
            "TOTAL_SHORT_LIQUIDATION_UPDATES": 3,
            "VWAP_LONG_PRICE": 80_000, "VWAP_SHORT_PRICE": 60_000,
        }]}

    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    cd.fetch_liquidations(con, http_get=http_get, lookback_hours=48)
    con.close()
    assert captured_urls
    assert "/futures/v1/historical/liquidation/hours" in captured_urls[0]
    assert "market=binance" in captured_urls[0]
    assert "BTC-USDT-VANILLA-PERPETUAL" in captured_urls[0]


def test_fetch_dvol_pins_asset_into_rows(fixture_db, monkeypatch):
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    ts = _now_aligned_days(1)
    pages = [[{"TIMESTAMP": ts, "OPEN": 50, "HIGH": 55, "LOW": 49, "CLOSE": 52}]]
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    n = cd.fetch_dvol(con, "BTC", http_get=_scripted_pages(pages), lookback_days=14)
    assert n == 1
    rows = con.execute("SELECT asset, timestamp, close FROM cd_dvol").fetchall()
    con.close()
    assert rows == [("BTC", ts, 52)]


def test_fetch_dvol_rejects_unsupported_asset(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    with pytest.raises(ValueError, match="DVOL: unsupported"):
        cd.fetch_dvol(con, "DOGE", http_get=lambda url: {"Data": []})
    con.close()


def test_fetch_dvol_separate_assets_isolated_by_composite_key(fixture_db, monkeypatch):
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    ts = _now_aligned_days(1)
    btc_page = [{"TIMESTAMP": ts, "OPEN": 50, "HIGH": 55, "LOW": 49, "CLOSE": 52}]
    eth_page = [{"TIMESTAMP": ts, "OPEN": 60, "HIGH": 65, "LOW": 59, "CLOSE": 62}]
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    cd.fetch_dvol(con, "BTC", http_get=_scripted_pages([btc_page]), lookback_days=14)
    cd.fetch_dvol(con, "ETH", http_get=_scripted_pages([eth_page]), lookback_days=14)
    rows = con.execute(
        "SELECT asset, close FROM cd_dvol ORDER BY asset"
    ).fetchall()
    con.close()
    assert rows == [("BTC", 52), ("ETH", 62)]


# ─── refresh() ──────────────────────────────────────────────────────────────

def test_refresh_invokes_liq_and_dvol_feeds(fixture_db, monkeypatch):
    # OI moved to data/sources/binance.py::fetch_open_interest(); refresh()
    # now covers only the AI_QUANT-gated liquidations + DVOL feeds.
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    captured = []

    def http_get(url):
        captured.append(url)
        return {"Data": []}

    out = cd.refresh(force=True, http_get=http_get)
    assert set(out.keys()) == {"liquidations", "dvol_btc", "dvol_eth"}
    # OI is no longer fetched by this module
    assert not any("open-interest/hours" in u for u in captured)
    assert any("liquidation/hours" in u for u in captured)
    assert sum(1 for u in captured if "BTCDVOL_USDC" in u) >= 1
    assert sum(1 for u in captured if "ETHDVOL_USDC" in u) >= 1


def test_refresh_throttle_blocks_within_hour(fixture_db, monkeypatch):
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)
    calls = {"n": 0}

    def http_get(url):
        calls["n"] += 1
        return {"Data": []}

    # First call goes through (force=True), second within window does not
    cd.refresh(force=True, http_get=http_get)
    n_after_first = calls["n"]
    cd.refresh(http_get=http_get)  # no force
    assert calls["n"] == n_after_first  # unchanged


def test_refresh_isolates_per_feed_failures(fixture_db, monkeypatch):
    """If liquidations raises a transport-level error mid-stream, the others
    still run (per-feed failure isolation)."""
    monkeypatch.setattr(cd, "PER_REQUEST_DELAY", 0)

    def http_get(url):
        if "liquidation" in url:
            raise RuntimeError("liquidation endpoint down")
        return {"Data": []}

    out = cd.refresh(force=True, http_get=http_get)
    assert out["liquidations"] == -1   # marked as error
    assert out["dvol_btc"] == 0        # other feeds still ran
    assert out["dvol_eth"] == 0


# ─── Reader functions ──────────────────────────────────────────────────────

def test_latest_oi_returns_window_oldest_first(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    now = int(time.time())
    rows = [
        (now - 5 * 3600, 100, 100, 100, 105, 8e9, 8e9, 8e9, 8.4e9),
        (now - 2 * 3600, 105, 110, 100, 108, 8.4e9, 8.6e9, 8e9, 8.5e9),
        (now - 200 * 3600, 50, 50, 50, 50, 4e9, 4e9, 4e9, 4e9),  # outside window
    ]
    con.executemany("INSERT INTO cd_open_interest VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    data = cd.latest_oi(hours_back=24)
    assert len(data) == 2
    assert data[0]["ts"] < data[1]["ts"]   # oldest-first
    assert data[-1]["oi_value_close"] == 8.5e9


def test_latest_liquidations_includes_long_short_split(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    now = int(time.time())
    con.execute("INSERT INTO cd_liquidations VALUES (?,?,?,?,?,?,?,?,?)",
                (now - 3 * 3600, 5.0, 1.5, 400_000, 90_000, 12, 3, 80_000, 60_000))
    con.commit()
    con.close()
    rows = cd.latest_liquidations(hours_back=24)
    assert len(rows) == 1
    r = rows[0]
    assert r["long_quote_quantity"] == 400_000
    assert r["short_quote_quantity"] == 90_000
    assert r["long_count"] == 12 and r["short_count"] == 3


def test_latest_dvol_filters_by_asset_and_window(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    cd._ensure_schema(con)
    now = int(time.time())
    con.executemany("INSERT INTO cd_dvol VALUES (?,?,?,?,?,?)", [
        ("BTC", now - 86400, 50, 55, 48, 52),
        ("BTC", now - 60 * 86400, 40, 41, 39, 40),  # outside 30d window
        ("ETH", now - 86400, 60, 62, 58, 61),
    ])
    con.commit()
    con.close()
    btc = cd.latest_dvol("BTC", days_back=30)
    eth = cd.latest_dvol("ETH", days_back=30)
    assert [r["close"] for r in btc] == [52]
    assert [r["close"] for r in eth] == [61]


def test_reader_functions_tolerate_missing_table(tmp_path, monkeypatch):
    """A pristine DB without cd_* tables → readers auto-create + return []."""
    p = tmp_path / "fresh.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("strategies.support.db.TRADER_DB", p)
    assert cd.latest_oi() == []
    assert cd.latest_liquidations() == []
    assert cd.latest_dvol("BTC") == []
