"""Crypto + macro news headlines — direct RSS aggregator.

Pulls headlines straight from a hand-picked list of reputable outlets
via their RSS feeds. No third-party aggregator, no auth tokens, no
vendor dependency. The feeds are static XML — there are no rate limits
because we're just downloading public files. If a source goes down,
the rest still work; we just log the failure for that source.

Editorial choice (defaults): three crypto-native outlets and three
TradFi business outlets. Crypto-native picks have a track record of
breaking real news (CoinDesk + FTX, The Block + Tether, Decrypt's
news-vs-opinion separation, Bitcoin Magazine's longevity). TradFi
picks (BBC Business, CNBC, The Guardian Business) cover macro context
the crypto outlets miss. Notably excluded: outlets with a sensationalist-
headline reputation (Cointelegraph) or low editorial bar (republisher
sites). Operators can edit `SOURCES` to add/drop outlets without any
schema or interface change.

Cadence: refresh() rate-limits itself to once per hour, so binance_feed's
60-second loop calls it cheaply most of the time. Each refresh fetches
all six feeds in sequence and dedupes by sha256(url) before insert.

Schema (unchanged from the previous CryptoPanic-backed implementation,
so existing query() consumers continue to work):

    news_headlines(
        url_hash       TEXT PRIMARY KEY,    -- sha256(url) for dedupe
        source         TEXT NOT NULL,       -- "rss:coindesk" etc.
        published_utc  INTEGER NOT NULL,    -- epoch seconds, UTC
        fetched_utc    INTEGER NOT NULL,
        title          TEXT NOT NULL,
        url            TEXT NOT NULL,
        asset_tag      TEXT,                -- "BTC", "ETH", or NULL (macro)
        importance     INTEGER NOT NULL DEFAULT 0  -- always 0 from RSS
    )

asset_tag is derived client-side via a word-boundary regex on the title:
"Bitcoin"/"BTC" → BTC, "Ethereum"/"ETH" → ETH, otherwise NULL (macro).
Headlines without a crypto tag are visible to the AI_QUANT context
bundle's macro-untagged section, so genuine macro coverage (CPI, FOMC,
geopolitical) reaches the LLM.

importance is always 0 — RSS has no "hot" flag and we're not running
sentiment analysis on titles. The LLM forms its own importance judgement.

Retention: refresh() deletes rows older than 30 UTC days on each
successful call so the table stays bounded.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import logging
import os
import re
import sqlite3
import time
from typing import Callable

import feedparser

from services import db

log = logging.getLogger("p300.news_fetcher")

RATE_LIMIT_SECONDS = 60 * 60       # once per hour across the whole aggregator
RETENTION_DAYS = 30
PER_FEED_CAP = 50                   # max entries we ingest per feed per refresh
HTTP_TIMEOUT_SECONDS = 15

# Hand-picked sources. Each entry is (name, url). Operators can edit this
# list directly to add or drop a feed; no other code or schema changes
# needed. The `name` becomes the per-row source field as "rss:<name>", so
# downstream queries can filter or group on it.
SOURCES: tuple[dict, ...] = (
    # ── Crypto-native ──
    {"name": "coindesk",
     "url":  "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "theblock",
     "url":  "https://www.theblock.co/rss.xml"},
    {"name": "decrypt",
     "url":  "https://decrypt.co/feed"},
    {"name": "bitcoin_magazine",
     "url":  "https://bitcoinmagazine.com/feed"},
    # ── TradFi macro (relevant to crypto via FOMC / risk-on flows) ──
    {"name": "bbc_business",
     "url":  "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "cnbc_top_news",
     "url":  "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
)

# Asset-tag derivation. Word-boundary regex avoids false matches like
# "ETH-" inside an unrelated ticker or "btc" buried in a hash.
_BTC_RE = re.compile(r"\b(bitcoin|btc)\b", re.IGNORECASE)
_ETH_RE = re.compile(r"\b(ethereum|ether|eth)\b", re.IGNORECASE)

# In-process throttle — same pattern as the previous implementation.
_last_refresh_ts: float = 0.0


# ─── Schema ─────────────────────────────────────────────────────────────────

def _ensure_schema(con: sqlite3.Connection) -> None:
    """Create news_headlines if missing. Idempotent. Schema unchanged from
    the previous CryptoPanic implementation so existing query() consumers
    keep working."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS news_headlines (
            url_hash       TEXT PRIMARY KEY,
            source         TEXT NOT NULL,
            published_utc  INTEGER NOT NULL,
            fetched_utc    INTEGER NOT NULL,
            title          TEXT NOT NULL,
            url            TEXT NOT NULL,
            asset_tag      TEXT,
            importance     INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_published "
                "ON news_headlines(published_utc DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_news_asset "
                "ON news_headlines(asset_tag, published_utc DESC)")
    con.commit()


# ─── Pure helpers ───────────────────────────────────────────────────────────

def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _derive_asset_tag(title: str) -> str | None:
    """Word-boundary keyword match on the headline. Returns "BTC", "ETH",
    or None for macro headlines. If both BTC and ETH appear, BTC wins —
    larger market, the LLM is more interested in BTC's reaction.

    Naive on purpose: false negatives (e.g. "BlackRock files spot ETF"
    without spelling out "Bitcoin") are tolerated because the LLM still
    sees the headline in the macro-untagged section."""
    if _BTC_RE.search(title):
        return "BTC"
    if _ETH_RE.search(title):
        return "ETH"
    return None


def _struct_time_to_utc_epoch(t) -> int | None:
    """feedparser's published_parsed is a struct_time in UTC by spec.
    `time.mktime` would interpret it as local time — wrong. Use
    `calendar.timegm` to read it as UTC."""
    if t is None:
        return None
    try:
        return calendar.timegm(t)
    except (TypeError, ValueError):
        return None


# ─── Fetch one feed ────────────────────────────────────────────────────────

def _fetch_one_feed(source: dict) -> list[dict]:
    """Pull one RSS feed and return normalized rows ready for insert.
    Skips entries missing url, title, or a parseable timestamp.

    feedparser.parse handles HTTP, gzip, etag/modified caching, and the
    RSS 0.9-2.0 / Atom variations transparently. We pass a User-Agent
    because some outlets reject the default."""
    parsed = feedparser.parse(
        source["url"],
        request_headers={"User-Agent": "p300/1.0 RSS aggregator"},
    )
    # feedparser sets `bozo` to 1 on parse failure but still returns a
    # (possibly empty) entries list. We log the bozo state once per
    # source for visibility but still ingest whatever we got.
    if getattr(parsed, "bozo", 0) and not parsed.entries:
        bozo_msg = getattr(parsed, "bozo_exception", "unknown")
        log.warning(f"news_fetcher: source {source['name']} parse error: {bozo_msg}")
        return []
    out: list[dict] = []
    for entry in parsed.entries[:PER_FEED_CAP]:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        if not title or not url:
            continue
        # Try published_parsed first, fall back to updated_parsed.
        ts = _struct_time_to_utc_epoch(entry.get("published_parsed"))
        if ts is None:
            ts = _struct_time_to_utc_epoch(entry.get("updated_parsed"))
        if ts is None:
            continue
        out.append({
            "url_hash": _hash_url(url),
            "source": f"rss:{source['name']}",
            "published_utc": ts,
            "title": title[:500],
            "url": url[:500],
            "asset_tag": _derive_asset_tag(title),
            "importance": 0,
        })
    return out


# ─── Persist + prune ───────────────────────────────────────────────────────

def _persist(con: sqlite3.Connection, rows: list[dict], fetched_utc: int) -> int:
    """INSERT OR IGNORE rows. Returns count of newly-inserted rows."""
    if not rows:
        return 0
    cur = con.cursor()
    inserted = 0
    for r in rows:
        cur.execute(
            "INSERT OR IGNORE INTO news_headlines "
            "(url_hash, source, published_utc, fetched_utc, title, url, asset_tag, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r["url_hash"], r["source"], r["published_utc"], fetched_utc,
             r["title"], r["url"], r["asset_tag"], r["importance"]),
        )
        inserted += cur.rowcount
    con.commit()
    return inserted


def _prune(con: sqlite3.Connection, retention_days: int = RETENTION_DAYS) -> int:
    cutoff = int(time.time()) - retention_days * 86400
    cur = con.execute("DELETE FROM news_headlines WHERE published_utc < ?", (cutoff,))
    con.commit()
    return cur.rowcount


# ─── Top-level refresh ─────────────────────────────────────────────────────

def refresh(
    *,
    force: bool = False,
    fetcher: Callable[[dict], list[dict]] = _fetch_one_feed,
    sources: tuple[dict, ...] | None = None,
) -> int:
    """Fetch every configured RSS source and upsert new headlines.

    Returns count of newly-inserted rows across all sources. Rate-limited
    to one fetch per hour unless force=True. Failures on individual
    sources are logged but don't stop the rest.

    Args:
        force: bypass the once-per-hour throttle (CLI/manual use).
        fetcher: injectable per-source fetch function — tests pass a
            stub returning canned rows.
        sources: override SOURCES (testing or A/B).
    """
    global _last_refresh_ts
    now = time.time()
    if not force and (now - _last_refresh_ts) < RATE_LIMIT_SECONDS:
        return 0
    src_list = sources if sources is not None else SOURCES
    all_rows: list[dict] = []
    successful_sources = 0
    for src in src_list:
        try:
            rows = fetcher(src)
        except Exception as e:  # noqa: BLE001 — one outlet's outage shouldn't sink the rest
            log.warning(f"news_fetcher: source {src['name']} failed: {e}")
            continue
        if rows:
            successful_sources += 1
            all_rows.extend(rows)
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        inserted = _persist(con, all_rows, fetched_utc=int(now))
        _prune(con)
    finally:
        con.close()
    _last_refresh_ts = now
    if inserted:
        log.info(f"news_fetcher: +{inserted} new headlines from "
                  f"{successful_sources}/{len(src_list)} feeds")
    return inserted


# ─── Read API (unchanged shape) ────────────────────────────────────────────

def query(
    asset: str | None = None,
    hours: int = 24,
    min_importance: int = 0,
    limit: int = 100,
    source: str | None = None,
) -> list[dict]:
    """Read recent headlines, newest first. Same shape as the previous
    implementation so the AI_QUANT context bundle and tools.query_news
    handler keep working unchanged.

    Args:
        asset: filter to "BTC" / "ETH" only. None returns all incl. macro.
        hours: only return headlines published within the last N hours.
        min_importance: 0 or 1. Always pass 0 — RSS rows are all
            importance=0; passing 1 would return nothing.
        limit: max rows.
        source: filter to one feed by its short name (e.g. "coindesk").
            The stored value is "rss:<name>", so we match on suffix
            equality. None returns all sources.
    """
    cutoff = int(time.time()) - hours * 3600
    sql = (
        "SELECT url_hash, source, published_utc, fetched_utc, title, url, "
        "asset_tag, importance "
        "FROM news_headlines WHERE published_utc >= ? AND importance >= ?"
    )
    args: list = [cutoff, int(min_importance)]
    if asset is not None:
        sql += " AND asset_tag = ?"
        args.append(asset.upper())
    if source is not None:
        sql += " AND source = ?"
        args.append(f"rss:{source}")
    sql += " ORDER BY published_utc DESC LIMIT ?"
    args.append(int(limit))
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    cols = ["url_hash", "source", "published_utc", "fetched_utc",
            "title", "url", "asset_tag", "importance"]
    return [dict(zip(cols, r)) for r in rows]


def reset_throttle() -> None:
    """Test helper: clear the in-process rate-limit so refresh() will fetch."""
    global _last_refresh_ts
    _last_refresh_ts = 0.0


# ─── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Fetch RSS news headlines into trader.db.")
    p.add_argument("--once", action="store_true",
                   help="(default) one-shot fetch; bypasses the rate-limit.")
    p.add_argument("--show", type=int, default=0, metavar="N",
                   help="After fetching, print the N most recent headlines.")
    p.add_argument("--asset", default=None,
                   help="Filter --show output by asset (BTC/ETH). Omit for all.")
    p.add_argument("--source", default=None,
                   help="Restrict to a single named source (e.g. 'coindesk') "
                        "for testing one feed in isolation.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    src_filter = (
        tuple(s for s in SOURCES if s["name"] == args.source)
        if args.source else None
    )
    if args.source and not src_filter:
        names = ", ".join(s["name"] for s in SOURCES)
        print(f"Unknown source {args.source!r}. Known: {names}")
        return 2
    n = refresh(force=True, sources=src_filter)
    print(f"new rows: {n}")
    if args.show > 0:
        for h in query(asset=args.asset, hours=72, limit=args.show,
                       source=args.source):
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(h["published_utc"],
                                          tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            tag = h["asset_tag"] or "macro"
            src = h["source"].replace("rss:", "")
            print(f"  {ts}  [{tag:<5}]  ({src:<18})  {h['title'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
