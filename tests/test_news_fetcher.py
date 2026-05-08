"""Tests for services.news_fetcher.

The CryptoPanic API is mocked via the `http_fetch` injection point on
refresh(); no test makes a real network call. We verify parsing,
deduplication, importance/asset derivation, retention, and the query API.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from services import news_fetcher


def _sample_payload(extra: list[dict] | None = None) -> dict:
    """Realistic-shape CryptoPanic /api/v1/posts response with two BTC and one
    ETH item. Times are recent so retention won't drop them."""
    now = int(time.time())
    base = [
        {
            "kind": "news",
            "title": "BTC breaks 90k after ETF inflows",
            "published_at": _iso(now - 600),
            "original_url": "https://example.com/btc-90k",
            "source": {"domain": "example.com"},
            "currencies": [{"code": "BTC", "title": "Bitcoin"}],
            "votes": {"important": 2, "positive": 6, "negative": 0},
        },
        {
            "kind": "news",
            "title": "Quiet day for BTC, range-bound",
            "published_at": _iso(now - 1200),
            "url": "https://cryptopanic.com/news/abc/",
            "domain": "newsource.com",
            "currencies": [{"code": "BTC"}],
            "votes": {"positive": 1, "negative": 0},
        },
        {
            "kind": "media",
            "title": "ETH staking yield update",
            "published_at": _iso(now - 1800),
            "original_url": "https://example.com/eth-staking",
            "source": {"domain": "example.com"},
            "currencies": [{"code": "ETH"}],
            "votes": {"positive": 0, "negative": 0},
        },
    ]
    if extra:
        base.extend(extra)
    return {"count": len(base), "results": base}


def _iso(epoch_s: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Empty trader.db; news_fetcher creates the schema on first refresh.
    Also resets the in-process throttle so refresh() actually runs."""
    p = tmp_path / "trader.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    monkeypatch.setenv("CRYPTOPANIC_TOKEN", "test-token-not-real")
    news_fetcher.reset_throttle()
    yield p


# ─── parse helpers ──────────────────────────────────────────────────────────

def test_parse_response_keeps_well_formed_items():
    rows = news_fetcher._parse_response(_sample_payload())
    assert len(rows) == 3
    titles = {r["title"] for r in rows}
    assert "BTC breaks 90k after ETF inflows" in titles
    assert "ETH staking yield update" in titles


def test_parse_response_drops_items_missing_url_or_title():
    payload = {
        "results": [
            {"title": "Has title, no url", "published_at": _iso(int(time.time()))},
            {"title": "", "url": "https://x.com/empty",
             "published_at": _iso(int(time.time()))},
            {"title": "Good", "url": "https://x.com/good",
             "published_at": _iso(int(time.time()))},
        ],
    }
    rows = news_fetcher._parse_response(payload)
    assert [r["title"] for r in rows] == ["Good"]


def test_parse_response_drops_items_with_unparseable_published_at():
    payload = {"results": [
        {"title": "Bad date", "url": "https://x.com/baddate",
         "published_at": "not-a-date"},
        {"title": "Good", "url": "https://x.com/good",
         "published_at": _iso(int(time.time()))},
    ]}
    rows = news_fetcher._parse_response(payload)
    assert [r["title"] for r in rows] == ["Good"]


def test_parse_response_returns_empty_for_malformed_top_level():
    assert news_fetcher._parse_response({}) == []
    assert news_fetcher._parse_response({"results": "not a list"}) == []


def test_derive_importance_flags_community_important_and_high_engagement():
    assert news_fetcher._derive_importance({"important": 1, "positive": 0, "negative": 0}) == 1
    assert news_fetcher._derive_importance({"positive": 3, "negative": 3}) == 1  # 6 >= 5
    assert news_fetcher._derive_importance({"positive": 1, "negative": 1}) == 0
    assert news_fetcher._derive_importance(None) == 0
    assert news_fetcher._derive_importance({}) == 0


def test_derive_asset_tag_picks_first_universe_match():
    assert news_fetcher._derive_asset_tag([{"code": "BTC"}, {"code": "ETH"}]) == "BTC"
    assert news_fetcher._derive_asset_tag([{"code": "DOGE"}, {"code": "ETH"}]) == "ETH"
    assert news_fetcher._derive_asset_tag([{"code": "DOGE"}]) is None
    assert news_fetcher._derive_asset_tag(None) is None
    assert news_fetcher._derive_asset_tag([]) is None


# ─── refresh + persist ──────────────────────────────────────────────────────

def test_refresh_inserts_new_rows_and_creates_schema(fixture_db):
    payload = _sample_payload()
    n = news_fetcher.refresh(force=True, http_fetch=lambda tok: payload)
    assert n == 3
    con = sqlite3.connect(str(fixture_db))
    try:
        rows = con.execute(
            "SELECT title, asset_tag, importance FROM news_headlines "
            "ORDER BY published_utc DESC"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 3
    # First row is the BTC ETF headline (importance=1 from votes.important=2)
    assert rows[0][0].startswith("BTC breaks")
    assert rows[0][1] == "BTC"
    assert rows[0][2] == 1
    # ETH staking item is non-hot
    eth_row = next(r for r in rows if r[1] == "ETH")
    assert eth_row[2] == 0


def test_refresh_dedupes_on_repeat_call(fixture_db):
    payload = _sample_payload()
    first = news_fetcher.refresh(force=True, http_fetch=lambda tok: payload)
    news_fetcher.reset_throttle()
    second = news_fetcher.refresh(force=True, http_fetch=lambda tok: payload)
    assert first == 3
    assert second == 0  # dedupe by url_hash


def test_refresh_partial_overlap_inserts_only_new(fixture_db):
    """When the second response shares one item with the first, we insert
    only the new ones."""
    first_payload = _sample_payload()
    n1 = news_fetcher.refresh(force=True, http_fetch=lambda tok: first_payload)
    assert n1 == 3
    news_fetcher.reset_throttle()
    new_item = {
        "kind": "news", "title": "New ETH news",
        "published_at": _iso(int(time.time()) - 100),
        "original_url": "https://example.com/eth-new",
        "source": {"domain": "example.com"},
        "currencies": [{"code": "ETH"}], "votes": {"important": 1},
    }
    second_payload = {"count": 4, "results": first_payload["results"] + [new_item]}
    n2 = news_fetcher.refresh(force=True, http_fetch=lambda tok: second_payload)
    assert n2 == 1


def test_refresh_throttle_prevents_double_fetch_within_an_hour(fixture_db):
    payload = _sample_payload()
    calls = {"n": 0}

    def counting_fetch(tok: str) -> dict:
        calls["n"] += 1
        return payload

    n1 = news_fetcher.refresh(http_fetch=counting_fetch)  # force=False default
    n2 = news_fetcher.refresh(http_fetch=counting_fetch)  # within window
    assert calls["n"] == 1
    assert n1 == 3
    assert n2 == 0


def test_refresh_no_token_is_silent_noop(fixture_db, monkeypatch):
    monkeypatch.delenv("CRYPTOPANIC_TOKEN", raising=False)
    news_fetcher.reset_throttle()

    def should_not_be_called(tok: str) -> dict:
        raise AssertionError("http_fetch should not be invoked when token is missing")

    n = news_fetcher.refresh(force=True, http_fetch=should_not_be_called)
    assert n == 0


def test_refresh_swallows_upstream_errors(fixture_db):
    from urllib.error import URLError

    def boom(tok: str) -> dict:
        raise URLError("upstream down")

    n = news_fetcher.refresh(force=True, http_fetch=boom)
    assert n == 0


# ─── retention ──────────────────────────────────────────────────────────────

def test_refresh_prunes_rows_older_than_retention(fixture_db):
    """A row older than RETENTION_DAYS should be deleted on the next refresh."""
    # Manually insert an ancient row
    old_ts = int(time.time()) - (news_fetcher.RETENTION_DAYS + 5) * 86400
    con = sqlite3.connect(str(fixture_db))
    try:
        news_fetcher._ensure_schema(con)
        con.execute(
            "INSERT INTO news_headlines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("oldhash", "cryptopanic:old.com", old_ts, old_ts,
             "Ancient news", "https://old.com/x", "BTC", 0),
        )
        con.commit()
    finally:
        con.close()
    # Now refresh with current items — _prune should drop the ancient one
    n = news_fetcher.refresh(force=True, http_fetch=lambda tok: _sample_payload())
    assert n == 3
    headlines = news_fetcher.query(hours=24 * 365, limit=100)
    titles = {h["title"] for h in headlines}
    assert "Ancient news" not in titles
    assert len(headlines) == 3


# ─── query ──────────────────────────────────────────────────────────────────

def test_query_filters_by_asset(fixture_db):
    news_fetcher.refresh(force=True, http_fetch=lambda tok: _sample_payload())
    btc = news_fetcher.query(asset="BTC", hours=24, limit=100)
    eth = news_fetcher.query(asset="ETH", hours=24, limit=100)
    all_news = news_fetcher.query(hours=24, limit=100)
    assert len(btc) == 2
    assert len(eth) == 1
    assert len(all_news) == 3
    assert all(h["asset_tag"] == "BTC" for h in btc)


def test_query_filters_by_min_importance(fixture_db):
    news_fetcher.refresh(force=True, http_fetch=lambda tok: _sample_payload())
    hot_only = news_fetcher.query(min_importance=1, hours=24, limit=100)
    assert all(h["importance"] >= 1 for h in hot_only)
    # In the fixture, only the BTC-ETF headline is hot (importance=1)
    assert len(hot_only) == 1
    assert "ETF" in hot_only[0]["title"]


def test_query_returns_newest_first_and_respects_limit(fixture_db):
    news_fetcher.refresh(force=True, http_fetch=lambda tok: _sample_payload())
    rows = news_fetcher.query(hours=24, limit=2)
    assert len(rows) == 2
    assert rows[0]["published_utc"] >= rows[1]["published_utc"]


def test_query_window_excludes_older_rows(fixture_db):
    """Headlines published more than `hours` ago should not appear."""
    # Seed a row 48h old + a fresh one
    con = sqlite3.connect(str(fixture_db))
    try:
        news_fetcher._ensure_schema(con)
        old_ts = int(time.time()) - 48 * 3600
        new_ts = int(time.time()) - 600
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h1", "src", old_ts, old_ts, "old", "u1", "BTC", 0))
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h2", "src", new_ts, new_ts, "new", "u2", "BTC", 0))
        con.commit()
    finally:
        con.close()
    rows = news_fetcher.query(hours=24, limit=100)
    titles = {r["title"] for r in rows}
    assert titles == {"new"}


def test_query_empty_when_table_does_not_exist_yet(fixture_db, monkeypatch, tmp_path):
    """Pristine DB (no schema yet): query() must auto-create and return []."""
    p = tmp_path / "fresh.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    assert news_fetcher.query(hours=24) == []
