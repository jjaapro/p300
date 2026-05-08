"""Tests for services.news_fetcher (RSS aggregator).

The aggregator is mocked via the `fetcher` injection point on refresh();
no test makes a real network call. We verify multi-source aggregation,
asset-tag derivation, dedupe, retention, query filters, throttle, and
graceful single-source-failure isolation.
"""
from __future__ import annotations

import calendar
import sqlite3
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import news_fetcher


# ─── Test helpers ───────────────────────────────────────────────────────────

def _entry(*, title: str, url: str, published: datetime | None = None):
    """Build a feedparser-like entry dict."""
    if published is None:
        published = datetime.now(timezone.utc)
    return {
        "title": title,
        "link": url,
        "published_parsed": published.utctimetuple(),
    }


def _canned_fetcher(name_to_entries: dict[str, list[dict]]):
    """Return a function that mimics _fetch_one_feed: given a source,
    look up its name in `name_to_entries` and return the canned rows."""
    def fetch(source):
        entries = name_to_entries.get(source["name"], [])
        out = []
        for e in entries:
            ts = calendar.timegm(e["published_parsed"])
            url = e["link"]
            title = e["title"]
            out.append({
                "url_hash": news_fetcher._hash_url(url),
                "source": f"rss:{source['name']}",
                "published_utc": ts,
                "title": title[:500],
                "url": url[:500],
                "asset_tag": news_fetcher._derive_asset_tag(title),
                "importance": 0,
            })
        return out
    return fetch


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Empty trader.db; news_fetcher creates the schema on first refresh.
    Resets the in-process throttle so refresh() will actually run."""
    p = tmp_path / "trader.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    news_fetcher.reset_throttle()
    yield p


# ─── Pure helpers: asset-tag regex ─────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Bitcoin breaks $100k after ETF inflows", "BTC"),
    ("BTC tumbles after Powell hawkish remarks", "BTC"),
    ("Ethereum staking yield update", "ETH"),
    ("ETH outflows from Coinbase pick up", "ETH"),
    ("Ether price recovers above $4k", "ETH"),
    ("Powell signals more hikes ahead", None),
    ("CPI prints +0.4% MoM", None),
    # Both BTC and ETH mentioned: BTC wins (larger market)
    ("BTC and ETH both fall on FOMC day", "BTC"),
    # Mixed-case shouldn't fool us
    ("bitcoin holds steady", "BTC"),
    ("BITCOIN ETF approved", "BTC"),
    # Word-boundary check: "btc" inside an unrelated token should NOT match.
    # The regex \b is on word characters; "abtcd" has no word boundary
    # between "a" and "btc", so this stays None.
    ("hash 7abtcd9 audit complete", None),
    # Empty
    ("", None),
])
def test_derive_asset_tag(title, expected):
    assert news_fetcher._derive_asset_tag(title) == expected


def test_struct_time_parsed_as_utc_not_local():
    """feedparser's published_parsed is UTC by spec; we must use
    calendar.timegm, not time.mktime (which interprets as local time)."""
    # 2026-05-08 12:00:00 UTC
    dt = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
    expected_epoch = int(dt.timestamp())
    actual = news_fetcher._struct_time_to_utc_epoch(dt.utctimetuple())
    assert actual == expected_epoch


def test_struct_time_handles_none_and_invalid():
    assert news_fetcher._struct_time_to_utc_epoch(None) is None
    assert news_fetcher._struct_time_to_utc_epoch("not a struct_time") is None


# ─── _fetch_one_feed: feedparser integration ──────────────────────────────

def test_fetch_one_feed_normalizes_entries(monkeypatch):
    """_fetch_one_feed returns dicts in the schema-row shape; we mock
    feedparser.parse so this test never touches the network."""
    fake_parsed = SimpleNamespace(
        bozo=0,
        entries=[
            {
                "title": "Bitcoin reclaims $80k",
                "link": "https://example.com/btc-80k",
                "published_parsed": datetime(2026, 5, 1, 12, 0,
                                              tzinfo=timezone.utc).utctimetuple(),
            },
            {
                "title": "Powell speech recap",
                "link": "https://example.com/powell",
                "published_parsed": datetime(2026, 5, 2, 14, 30,
                                              tzinfo=timezone.utc).utctimetuple(),
            },
        ],
    )
    monkeypatch.setattr(news_fetcher.feedparser, "parse",
                         lambda url, **kw: fake_parsed)
    rows = news_fetcher._fetch_one_feed({"name": "test", "url": "x"})
    assert len(rows) == 2
    btc = rows[0]
    assert btc["source"] == "rss:test"
    assert btc["asset_tag"] == "BTC"
    assert btc["importance"] == 0
    assert btc["url"] == "https://example.com/btc-80k"
    assert btc["url_hash"] == news_fetcher._hash_url(btc["url"])
    macro = rows[1]
    assert macro["asset_tag"] is None  # no BTC/ETH in title


def test_fetch_one_feed_drops_entries_missing_required_fields(monkeypatch):
    fake_parsed = SimpleNamespace(
        bozo=0,
        entries=[
            {"title": "", "link": "https://x/empty-title",
             "published_parsed": datetime.now(timezone.utc).utctimetuple()},
            {"title": "Has title, no link", "link": "",
             "published_parsed": datetime.now(timezone.utc).utctimetuple()},
            {"title": "No date", "link": "https://x/no-date"},  # no _parsed
            {"title": "Good", "link": "https://x/good",
             "published_parsed": datetime.now(timezone.utc).utctimetuple()},
        ],
    )
    monkeypatch.setattr(news_fetcher.feedparser, "parse",
                         lambda url, **kw: fake_parsed)
    rows = news_fetcher._fetch_one_feed({"name": "t", "url": "x"})
    assert [r["title"] for r in rows] == ["Good"]


def test_fetch_one_feed_falls_back_to_updated_parsed(monkeypatch):
    """Some feeds use updated_parsed instead of published_parsed."""
    fake_parsed = SimpleNamespace(
        bozo=0,
        entries=[{
            "title": "Atom-style entry",
            "link": "https://x/atom",
            "updated_parsed": datetime(2026, 5, 1, tzinfo=timezone.utc).utctimetuple(),
            # No published_parsed
        }],
    )
    monkeypatch.setattr(news_fetcher.feedparser, "parse",
                         lambda url, **kw: fake_parsed)
    rows = news_fetcher._fetch_one_feed({"name": "t", "url": "x"})
    assert len(rows) == 1
    assert rows[0]["published_utc"] == int(
        datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())


def test_fetch_one_feed_caps_per_feed_entries(monkeypatch):
    """Don't ingest more than PER_FEED_CAP entries from one feed (DoS guard)."""
    big = [
        {"title": f"Item {i}", "link": f"https://x/{i}",
         "published_parsed": datetime(2026, 5, 1, tzinfo=timezone.utc).utctimetuple()}
        for i in range(news_fetcher.PER_FEED_CAP + 25)
    ]
    fake_parsed = SimpleNamespace(bozo=0, entries=big)
    monkeypatch.setattr(news_fetcher.feedparser, "parse",
                         lambda url, **kw: fake_parsed)
    rows = news_fetcher._fetch_one_feed({"name": "t", "url": "x"})
    assert len(rows) == news_fetcher.PER_FEED_CAP


def test_fetch_one_feed_returns_empty_when_parse_fails(monkeypatch):
    """bozo=1 with no entries → empty list (logged, not raised)."""
    fake_parsed = SimpleNamespace(
        bozo=1, bozo_exception=Exception("malformed XML"), entries=[],
    )
    monkeypatch.setattr(news_fetcher.feedparser, "parse",
                         lambda url, **kw: fake_parsed)
    rows = news_fetcher._fetch_one_feed({"name": "t", "url": "x"})
    assert rows == []


# ─── refresh + persist ──────────────────────────────────────────────────────

def test_refresh_aggregates_across_multiple_sources(fixture_db):
    """Three sources × 2 entries each = 6 rows inserted, schema autocreated."""
    now = datetime.now(timezone.utc)
    canned = {
        "src_a": [_entry(title="Bitcoin pumps", url="https://a/1", published=now)],
        "src_b": [_entry(title="ETH update",   url="https://b/1", published=now)],
        "src_c": [_entry(title="Powell talks", url="https://c/1", published=now),
                  _entry(title="CPI release",  url="https://c/2", published=now)],
    }
    sources = tuple({"name": k, "url": "x"} for k in canned)
    n = news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                                sources=sources)
    assert n == 4
    rows = news_fetcher.query(hours=24, limit=100)
    assert len(rows) == 4
    # Each carries the right per-source tag
    sources_seen = {r["source"] for r in rows}
    assert sources_seen == {"rss:src_a", "rss:src_b", "rss:src_c"}


def test_refresh_dedupes_across_sources_by_url(fixture_db):
    """If two feeds republish the same URL, dedup keeps one."""
    now = datetime.now(timezone.utc)
    canned = {
        "src_a": [_entry(title="Bitcoin moves", url="https://wire/abc", published=now)],
        "src_b": [_entry(title="Bitcoin moves (republish)",
                          url="https://wire/abc", published=now)],
    }
    sources = tuple({"name": k, "url": "x"} for k in canned)
    n = news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                                sources=sources)
    assert n == 1


def test_refresh_dedupes_on_repeat_call_same_session(fixture_db):
    """Same payload twice → second insert adds 0 rows."""
    now = datetime.now(timezone.utc)
    canned = {"src_a": [_entry(title="X", url="https://x/1", published=now)]}
    sources = tuple({"name": k, "url": "x"} for k in canned)
    first = news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                                    sources=sources)
    news_fetcher.reset_throttle()
    second = news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                                     sources=sources)
    assert first == 1
    assert second == 0


def test_refresh_isolates_individual_source_failures(fixture_db):
    """If one source raises, the others still ingest."""
    def flaky_fetcher(source):
        if source["name"] == "broken":
            raise RuntimeError("simulated outage")
        if source["name"] == "good":
            return [{
                "url_hash": news_fetcher._hash_url("https://good/1"),
                "source": "rss:good", "published_utc": int(time.time()),
                "title": "BTC news", "url": "https://good/1",
                "asset_tag": "BTC", "importance": 0,
            }]
        return []
    sources = ({"name": "broken", "url": "x"}, {"name": "good", "url": "x"})
    n = news_fetcher.refresh(force=True, fetcher=flaky_fetcher, sources=sources)
    assert n == 1
    rows = news_fetcher.query(hours=24, limit=100)
    assert len(rows) == 1
    assert rows[0]["source"] == "rss:good"


def test_refresh_throttle_blocks_within_hour(fixture_db):
    canned = {"a": [_entry(title="x", url="https://x/1")]}
    sources = ({"name": "a", "url": "x"},)
    calls = {"n": 0}

    def counting_fetch(s):
        calls["n"] += 1
        return _canned_fetcher(canned)(s)

    news_fetcher.refresh(fetcher=counting_fetch, sources=sources)  # force=False
    news_fetcher.refresh(fetcher=counting_fetch, sources=sources)  # within window
    assert calls["n"] == 1


def test_refresh_persists_correct_row_shape(fixture_db):
    """Sanity: the row reaching SQL has the columns the schema requires."""
    now = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    canned = {"a": [_entry(title="Bitcoin update", url="https://a/1", published=now)]}
    sources = ({"name": "a", "url": "x"},)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    con = sqlite3.connect(str(fixture_db))
    try:
        row = con.execute(
            "SELECT url_hash, source, published_utc, fetched_utc, title, url, "
            "asset_tag, importance FROM news_headlines"
        ).fetchone()
    finally:
        con.close()
    url_hash, source, pub, fetched, title, url, tag, imp = row
    assert source == "rss:a"
    assert title == "Bitcoin update"
    assert tag == "BTC"
    assert imp == 0
    assert pub == int(now.timestamp())
    assert fetched > 0


# ─── Retention ──────────────────────────────────────────────────────────────

def test_refresh_prunes_rows_older_than_retention(fixture_db):
    old_ts = int(time.time()) - (news_fetcher.RETENTION_DAYS + 5) * 86400
    con = sqlite3.connect(str(fixture_db))
    try:
        news_fetcher._ensure_schema(con)
        con.execute(
            "INSERT INTO news_headlines VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("oldhash", "rss:legacy", old_ts, old_ts,
             "Ancient news", "https://old.com/x", "BTC", 0),
        )
        con.commit()
    finally:
        con.close()
    canned = {"a": [_entry(title="Fresh", url="https://a/fresh")]}
    sources = ({"name": "a", "url": "x"},)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    rows = news_fetcher.query(hours=24 * 365, limit=100)
    titles = {r["title"] for r in rows}
    assert "Ancient news" not in titles
    assert "Fresh" in titles


# ─── Query API (unchanged from before) ─────────────────────────────────────

def test_query_filters_by_asset(fixture_db):
    now = datetime.now(timezone.utc)
    canned = {
        "a": [
            _entry(title="Bitcoin moves",  url="https://x/btc", published=now),
            _entry(title="Bitcoin update", url="https://x/btc2", published=now),
            _entry(title="Ethereum news",  url="https://x/eth", published=now),
            _entry(title="CPI prints",     url="https://x/macro", published=now),
        ],
    }
    sources = ({"name": "a", "url": "x"},)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    btc = news_fetcher.query(asset="BTC", hours=24, limit=100)
    eth = news_fetcher.query(asset="ETH", hours=24, limit=100)
    all_news = news_fetcher.query(hours=24, limit=100)
    assert len(btc) == 2
    assert len(eth) == 1
    assert len(all_news) == 4
    # The CPI headline shows up only in the all-news query (asset_tag=None)
    assert any(h["title"] == "CPI prints" and h["asset_tag"] is None
                for h in all_news)


def test_query_returns_newest_first_and_respects_limit(fixture_db):
    base = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    canned = {
        "a": [
            _entry(title="A", url="https://x/a",
                    published=base.replace(hour=8)),
            _entry(title="B", url="https://x/b",
                    published=base.replace(hour=12)),
            _entry(title="C", url="https://x/c",
                    published=base.replace(hour=16)),
        ],
    }
    sources = ({"name": "a", "url": "x"},)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    rows = news_fetcher.query(hours=24 * 365, limit=2)
    assert len(rows) == 2
    assert rows[0]["title"] == "C"
    assert rows[1]["title"] == "B"


def test_query_filters_by_source(fixture_db):
    """Regression: --source coindesk on the CLI used to limit the fetch
    but not the read-back, leaving the operator looking at the entire
    cache. query() must accept and apply a source filter."""
    now = datetime.now(timezone.utc)
    canned = {
        "coindesk": [_entry(title="CoinDesk one", url="https://cd/1", published=now),
                      _entry(title="CoinDesk two", url="https://cd/2", published=now)],
        "decrypt":   [_entry(title="Decrypt one",  url="https://dc/1", published=now)],
        "bbc_business": [_entry(title="BBC one", url="https://bbc/1", published=now)],
    }
    sources = tuple({"name": k, "url": "x"} for k in canned)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    cd_only = news_fetcher.query(hours=24, limit=100, source="coindesk")
    assert len(cd_only) == 2
    assert all(r["source"] == "rss:coindesk" for r in cd_only)
    bbc_only = news_fetcher.query(hours=24, limit=100, source="bbc_business")
    assert len(bbc_only) == 1
    # Unknown source name → empty list (not all rows)
    assert news_fetcher.query(hours=24, source="nonesuch") == []
    # No source filter still returns everything
    assert len(news_fetcher.query(hours=24, limit=100)) == 4


def test_query_combines_asset_and_source_filters(fixture_db):
    """Both filters AND together: asset=BTC + source=coindesk should
    return only CoinDesk's BTC headlines, none of CoinDesk's macro
    headlines, and none of other sources' BTC headlines."""
    now = datetime.now(timezone.utc)
    canned = {
        "coindesk": [
            _entry(title="Bitcoin reclaims 90k", url="https://cd/1", published=now),
            _entry(title="CPI release recap",   url="https://cd/2", published=now),
        ],
        "decrypt": [
            _entry(title="Bitcoin ETF flows surge", url="https://dc/1", published=now),
        ],
    }
    sources = tuple({"name": k, "url": "x"} for k in canned)
    news_fetcher.refresh(force=True, fetcher=_canned_fetcher(canned),
                            sources=sources)
    btc_cd = news_fetcher.query(asset="BTC", source="coindesk", hours=24)
    assert len(btc_cd) == 1
    assert btc_cd[0]["title"] == "Bitcoin reclaims 90k"


def test_query_window_excludes_older_rows(fixture_db):
    now_ts = int(time.time())
    con = sqlite3.connect(str(fixture_db))
    try:
        news_fetcher._ensure_schema(con)
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h1", "rss:x", now_ts - 48 * 3600, now_ts, "old",
                     "u1", "BTC", 0))
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h2", "rss:x", now_ts - 600, now_ts, "new",
                     "u2", "BTC", 0))
        con.commit()
    finally:
        con.close()
    rows = news_fetcher.query(hours=24, limit=100)
    titles = {r["title"] for r in rows}
    assert titles == {"new"}


def test_query_empty_when_table_does_not_exist_yet(tmp_path, monkeypatch):
    """Pristine DB (no schema yet): query() must auto-create and return []."""
    p = tmp_path / "fresh.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    assert news_fetcher.query(hours=24) == []


# ─── SOURCES default list integrity ────────────────────────────────────────

def test_default_sources_have_required_shape():
    """Each entry must have non-empty `name` (used as 'rss:<name>' source
    column) and a parseable URL string."""
    assert len(news_fetcher.SOURCES) >= 3
    seen_names: set[str] = set()
    for s in news_fetcher.SOURCES:
        assert isinstance(s, dict)
        assert s["name"] and isinstance(s["name"], str)
        assert s["url"].startswith(("http://", "https://"))
        assert s["name"] not in seen_names, f"duplicate source name {s['name']}"
        seen_names.add(s["name"])


def test_default_sources_include_both_crypto_and_macro():
    """The editorial choice is 3 crypto + 3 macro. Anchor that here so a
    well-meaning operator who deletes the macro feeds notices in tests."""
    names = {s["name"] for s in news_fetcher.SOURCES}
    crypto = {"coindesk", "theblock", "decrypt", "bitcoin_magazine"}
    macro = {"bbc_business", "cnbc_top_news"}
    assert crypto.intersection(names), \
        f"need at least one crypto-native source; got {names}"
    assert macro.intersection(names), \
        f"need at least one TradFi macro source; got {names}"
