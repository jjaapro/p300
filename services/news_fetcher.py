"""Crypto news headlines — CryptoPanic free-tier ingest.

Pulls BTC/ETH-tagged news from https://cryptopanic.com/api/v1/posts/ and
persists deduped headlines to trader.db.news_headlines. Used by the
AI_QUANT sleeve as part of its daily decision context — a simple,
queryable, replay-safe alternative to the LLM doing live web searches
for routine market headlines.

Cadence: refresh() rate-limits itself to once per hour, so binance_feed's
60-second loop calls it cheaply most of the time. CryptoPanic's free tier
is 200 requests/day; hourly refresh = 24/day, well under cap.

Token: free registration at https://cryptopanic.com/developers/api/.
Without CRYPTOPANIC_TOKEN set, refresh() logs and is a no-op — the rest
of the system continues to work, the LLM just sees an empty news section.

Schema (idempotent CREATE on first refresh):

    news_headlines(
        url_hash       TEXT PRIMARY KEY,    -- sha256(url) for dedupe
        source         TEXT NOT NULL,       -- "cryptopanic:coindesk.com"
        published_utc  INTEGER NOT NULL,    -- epoch seconds
        fetched_utc    INTEGER NOT NULL,
        title          TEXT NOT NULL,
        url            TEXT NOT NULL,
        asset_tag      TEXT,                -- "BTC", "ETH", or NULL
        importance     INTEGER NOT NULL DEFAULT 0  -- 0 normal, 1 hot
    )

Retention: refresh() deletes rows older than 30 UTC days on each successful
call so the table stays bounded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from services import db

log = logging.getLogger("p300.news_fetcher")

API_URL = "https://cryptopanic.com/api/v1/posts/"
RATE_LIMIT_SECONDS = 60 * 60  # once per hour
RETENTION_DAYS = 30
CURRENCIES = ("BTC", "ETH")  # tags we ask the API to filter on

# In-process throttle. Persists across calls but not across processes —
# acceptable since binance_feed runs continuously; on restart we may double-
# fetch once which is harmless (INSERT OR IGNORE deduplicates).
_last_refresh_ts: float = 0.0


def _ensure_schema(con: sqlite3.Connection) -> None:
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


def _fetch_raw(token: str, currencies: tuple[str, ...] = CURRENCIES) -> dict:
    """HTTP GET to CryptoPanic. Returns parsed JSON dict. Raises on HTTP/JSON error."""
    url = f"{API_URL}?auth_token={token}&currencies={','.join(currencies)}&public=true"
    req = Request(url, headers={"User-Agent": "p300/1.0"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _parse_iso8601_utc(s: str) -> int | None:
    """CryptoPanic returns published_at like '2026-05-08T10:30:00Z'. Returns
    epoch seconds, or None if unparseable."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None


def _derive_importance(votes: dict | None) -> int:
    """1 (hot) if community-flagged important or aggregate engagement is high.
    0 otherwise. Simple heuristic — refine when we have signal data."""
    if not isinstance(votes, dict):
        return 0
    if int(votes.get("important", 0) or 0) > 0:
        return 1
    pos = int(votes.get("positive", 0) or 0)
    neg = int(votes.get("negative", 0) or 0)
    if pos + neg >= 5:
        return 1
    return 0


def _derive_asset_tag(currencies: list | None) -> str | None:
    """Pick the first matching crypto asset from the post's currencies list."""
    if not isinstance(currencies, list):
        return None
    for c in currencies:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code", "")).upper()
        if code in CURRENCIES:
            return code
    return None


def _parse_response(payload: dict) -> list[dict]:
    """CryptoPanic JSON → list of normalized rows ready for insert.

    Skips items missing url or title; treats published_at as required so
    we don't store undated news. Column-aligned with the SQL schema."""
    out: list[dict] = []
    results = payload.get("results")
    if not isinstance(results, list):
        return out
    for r in results:
        if not isinstance(r, dict):
            continue
        url = (r.get("original_url") or r.get("url") or "").strip()
        title = (r.get("title") or "").strip()
        if not url or not title:
            continue
        published = _parse_iso8601_utc(r.get("published_at") or r.get("created_at"))
        if published is None:
            continue
        domain = ((r.get("source") or {}).get("domain")) or r.get("domain") or "unknown"
        out.append({
            "url_hash": _hash_url(url),
            "source": f"cryptopanic:{domain}",
            "published_utc": published,
            "title": title[:500],
            "url": url[:500],
            "asset_tag": _derive_asset_tag(r.get("currencies")),
            "importance": _derive_importance(r.get("votes")),
        })
    return out


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
    """Delete rows whose published_utc is older than retention_days. Returns
    count of deleted rows."""
    cutoff = int(time.time()) - retention_days * 86400
    cur = con.execute("DELETE FROM news_headlines WHERE published_utc < ?", (cutoff,))
    con.commit()
    return cur.rowcount


def refresh(
    *,
    force: bool = False,
    http_fetch: Callable[[str], dict] = _fetch_raw,
    token: str | None = None,
) -> int:
    """Fetch latest CryptoPanic headlines and upsert into trader.db.

    Returns count of newly-inserted rows. Rate-limited to one network call
    per hour unless force=True. Returns 0 (no-op) when CRYPTOPANIC_TOKEN
    is unset, when rate-limited, or when the upstream is unreachable —
    callers should treat 0 as "no new headlines this tick".

    Args:
        force: bypass the once-per-hour throttle.
        http_fetch: injectable HTTP function (token) → dict. Tests pass a
            stub; production uses _fetch_raw. Bypassing this argument when
            no token is set returns 0 without calling the function.
        token: explicit token override; defaults to env CRYPTOPANIC_TOKEN.
    """
    global _last_refresh_ts
    now = time.time()
    if not force and (now - _last_refresh_ts) < RATE_LIMIT_SECONDS:
        return 0
    tok = token if token is not None else os.environ.get("CRYPTOPANIC_TOKEN", "")
    if not tok:
        log.info("CRYPTOPANIC_TOKEN unset — news_fetcher skipping (set the "
                 "env var to enable; free tier at cryptopanic.com).")
        return 0
    try:
        payload = http_fetch(tok)
    except (URLError, OSError, json.JSONDecodeError, ValueError) as e:
        log.warning(f"news_fetcher: upstream error: {e}")
        return 0
    rows = _parse_response(payload if isinstance(payload, dict) else {})
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        _ensure_schema(con)
        inserted = _persist(con, rows, fetched_utc=int(now))
        _prune(con)
    finally:
        con.close()
    _last_refresh_ts = now
    if inserted:
        log.info(f"news_fetcher: +{inserted} new headlines "
                 f"(of {len(rows)} returned).")
    return inserted


def query(
    asset: str | None = None,
    hours: int = 24,
    min_importance: int = 0,
    limit: int = 100,
) -> list[dict]:
    """Read recent headlines, newest first.

    Args:
        asset: filter to a specific asset_tag (e.g. "BTC"). None returns all
            including untagged macro headlines.
        hours: only return headlines published within the last N hours.
        min_importance: 0 or 1; 1 returns only hot headlines.
        limit: max rows.
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch CryptoPanic headlines into trader.db.")
    p.add_argument("--once", action="store_true",
                   help="One-shot fetch (default). Always bypasses the rate-limit.")
    p.add_argument("--show", type=int, default=0, metavar="N",
                   help="After fetching, print the N most recent headlines.")
    p.add_argument("--asset", default=None, help="Filter --show output by asset (BTC/ETH).")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = refresh(force=True)
    print(f"new rows: {n}")
    if args.show > 0:
        for h in query(asset=args.asset, hours=72, limit=args.show):
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(h["published_utc"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            tag = h["asset_tag"] or "—"
            hot = "🔥 " if h["importance"] else "  "
            print(f"{hot}{ts}  [{tag}]  {h['title'][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
