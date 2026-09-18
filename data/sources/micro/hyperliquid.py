"""Hyperliquid REST pollers (public `POST /info`, no key).

  metaAndAssetCtxs  every 60 s   -> hl_asset_ctx, every coin in the universe
  leaderboard       daily        -> hl_leaderboard, top 300 by accountValue
                                    (~38 MB JSON from stats-data, 120 s timeout)
  clearinghouseState every 300 s -> hl_accounts (one row per polled address,
                                    even with no positions) + hl_positions,
                                    for the latest leaderboard's top 200

Grid: both periodic pollers key their rows to the poll grid (`_bucket_ms`:
ts_ms floored to the interval, never finer than 1 s) and sleep to the next
grid boundary rather than a fixed interval after the request, so request
latency cannot drift a poll across a boundary and skip a bucket. The first
poll after start keys to the current bucket immediately; a restart inside
the same bucket dedupes on the primary key.

Pacing for the positions poll: `CONCURRENCY` workers each sleep `GAP_S`
after a request, so 200 addresses take ~100 s at <= 2 req/s (weight 2 per
call against a 1200/min budget — never approached).

Leaderboard: a reply without usable `leaderboardRows` (maintenance page,
renamed key, empty list) is an error — `parse_leaderboard` raises — so the
poller takes its failure path (hourly retry, previous tracked list kept)
instead of refetching 38 MB every check tick.

All HTTP goes through injected `http_post(url, body) -> object` /
`http_get(url) -> object` so tests never touch the network; the defaults
use `requests` and run in a thread executor from the async pollers.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import time
from datetime import datetime, timezone

log = logging.getLogger("micro.hl")

INFO_URL = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
CTX_INTERVAL_S = 60
POSITIONS_INTERVAL_S = 300
LEADERBOARD_TOP = 300
TRACK_TOP = 200
CONCURRENCY = 4
GAP_S = 2.0
LEADERBOARD_TIMEOUT_S = 120
LEADERBOARD_RETRY_S = 3600
BOUNDARY_MARGIN_S = 0.02            # wake just after the boundary, never before it


def default_http_post(url: str, body: dict) -> object:
    import requests
    r = requests.post(url, json=body, timeout=30, headers={"User-Agent": "p300-collector/1.0"})
    r.raise_for_status()
    return r.json()


def default_http_get(url: str) -> object:
    import requests
    r = requests.get(url, timeout=LEADERBOARD_TIMEOUT_S,
                     headers={"User-Agent": "p300-collector/1.0"})
    r.raise_for_status()
    return r.json()


def _f(x) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date().isoformat()


def _bucket_ms(now_ms: int, interval_s: float) -> int:
    """Floor to the poll grid; never finer than 1 s."""
    step = max(1000, int(interval_s * 1000))
    return now_ms - now_ms % step


def next_boundary_s(now_s: float, interval_s: float) -> float:
    """The first grid boundary strictly after `now_s` (grid never finer
    than 1 s). Pure; the drift test drives it."""
    step = max(1.0, float(interval_s))
    return (math.floor(now_s / step) + 1) * step


# ─── parsers ─────────────────────────────────────────────────────────────────

def parse_asset_ctxs(payload, ts_ms: int, received_ms: int) -> list[tuple]:
    """metaAndAssetCtxs -> hl_asset_ctx rows (index-aligned universe/ctxs)."""
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    universe = (payload[0] or {}).get("universe") or []
    ctxs = payload[1] or []
    rows = []
    for meta, ctx in zip(universe, ctxs):
        coin = meta.get("name")
        if not coin or not isinstance(ctx, dict):
            continue
        rows.append((int(ts_ms), coin, _f(ctx.get("openInterest")), _f(ctx.get("funding")),
                     _f(ctx.get("oraclePx")), _f(ctx.get("markPx")), _f(ctx.get("midPx")),
                     _f(ctx.get("premium")), _f(ctx.get("dayNtlVlm")), int(received_ms)))
    return rows


def parse_leaderboard(payload, snapshot_day: str, received_ms: int,
                      top: int = LEADERBOARD_TOP) -> list[tuple]:
    """Top `top` rows by numeric accountValue -> hl_leaderboard rows (rank 1 =
    largest). Raises ValueError when the payload carries no usable rows, so
    a maintenance page or a renamed key is a failure, never an empty
    success."""
    items = payload.get("leaderboardRows") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        shape = sorted(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"leaderboard payload has no leaderboardRows (got {shape})")
    scored = []
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("ethAddress")
        av = _f(it.get("accountValue"))
        if not addr or av is None:
            continue
        pnl = {}
        for entry in it.get("windowPerformances") or []:
            if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[1], dict):
                pnl[entry[0]] = _f(entry[1].get("pnl"))
        scored.append((av, addr, pnl))
    if not scored:
        raise ValueError(f"leaderboard: {len(items)} items, none with ethAddress+accountValue")
    scored.sort(key=lambda t: t[0], reverse=True)
    rows = []
    for rank, (av, addr, pnl) in enumerate(scored[:top], start=1):
        rows.append((snapshot_day, addr, rank, av, pnl.get("day"), pnl.get("week"),
                     pnl.get("month"), pnl.get("allTime"), int(received_ms)))
    return rows


def parse_clearinghouse(payload, ts_ms: int, address: str,
                        received_ms: int) -> tuple[tuple, list[tuple]]:
    """clearinghouseState -> (hl_accounts row, hl_positions rows)."""
    ms = (payload or {}).get("marginSummary") or {}
    positions = []
    for ap in (payload or {}).get("assetPositions") or []:
        p = (ap or {}).get("position") or {}
        coin = p.get("coin")
        if not coin:
            continue
        lev = p.get("leverage") or {}
        positions.append((int(ts_ms), address, coin, _f(p.get("szi")), _f(p.get("entryPx")),
                          _f(p.get("positionValue")), _f(p.get("liquidationPx")),
                          lev.get("type"), _f(lev.get("value")), _f(p.get("unrealizedPnl")),
                          _f(p.get("marginUsed")), int(received_ms)))
    account = (int(ts_ms), address, _f(ms.get("accountValue")), _f(ms.get("totalNtlPos")),
               _f(ms.get("totalMarginUsed")), len(positions), int(received_ms))
    return account, positions


# ─── one-shot fetches (sync, injectable) ──────────────────────────────────────

def fetch_asset_ctxs(http_post=default_http_post, now_ms: int | None = None,
                     ts_ms: int | None = None, interval_s: float = CTX_INTERVAL_S) -> list[tuple]:
    """hl_asset_ctx rows keyed to `ts_ms` (the poll's grid boundary) or, when
    not given, to `now_ms` floored to the `interval_s` grid. received_ms is
    `now_ms` (the raw clock)."""
    now_ms = _now_ms() if now_ms is None else now_ms
    payload = http_post(INFO_URL, {"type": "metaAndAssetCtxs"})
    key = _bucket_ms(now_ms, interval_s) if ts_ms is None else int(ts_ms)
    return parse_asset_ctxs(payload, key, now_ms)


def fetch_leaderboard(http_get=default_http_get, now_ms: int | None = None,
                      top: int = LEADERBOARD_TOP) -> list[tuple]:
    """Raises (ValueError from the parser, or the HTTP error) rather than
    returning an empty list."""
    now_ms = _now_ms() if now_ms is None else now_ms
    payload = http_get(LEADERBOARD_URL)
    return parse_leaderboard(payload, utc_day(now_ms), now_ms, top=top)


def fetch_account(address: str, ts_ms: int, http_post=default_http_post,
                  now_ms: int | None = None) -> tuple[tuple, list[tuple]]:
    now_ms = _now_ms() if now_ms is None else now_ms
    payload = http_post(INFO_URL, {"type": "clearinghouseState", "user": address})
    return parse_clearinghouse(payload, ts_ms, address, now_ms)


async def fetch_positions(addresses: list[str], ts_ms: int, *, http_post=default_http_post,
                          concurrency: int = CONCURRENCY, gap_s: float = GAP_S,
                          now_ms: int | None = None,
                          stop: asyncio.Event | None = None) -> tuple[list[tuple], list[tuple]]:
    """Paced clearinghouseState for every address. A failing address is
    logged and skipped; the others still produce rows. Returns early with
    what it has once `stop` is set (a full pass takes ~100 s)."""
    loop = asyncio.get_running_loop()
    pending = list(addresses)
    accounts: list[tuple] = []
    positions: list[tuple] = []

    def stopping() -> bool:
        return stop is not None and stop.is_set()

    async def worker():
        while pending and not stopping():
            addr = pending.pop(0)
            try:
                acc, pos = await loop.run_in_executor(
                    None, fetch_account, addr, ts_ms, http_post, now_ms)
                accounts.append(acc)
                positions.extend(pos)
            except Exception as e:  # noqa: BLE001
                log.warning(f"hl positions {addr[:10]}..: {e!r}")
            if gap_s and not stopping():
                if stop is None:
                    await asyncio.sleep(gap_s)
                else:
                    await _sleep_or_stop(stop, gap_s)

    await asyncio.gather(*(worker() for _ in range(max(1, concurrency))))
    return accounts, positions


# ─── tracked addresses ────────────────────────────────────────────────────────

class Tracker:
    """The addresses the positions poller follows: the latest leaderboard's
    top `track_top` by account value. Keeps the previous list when a
    leaderboard fetch fails."""

    def __init__(self, track_top: int = TRACK_TOP):
        self.track_top = track_top
        self.addresses: list[str] = []
        self.snapshot_day: str | None = None

    def update(self, leaderboard_rows: list[tuple]) -> None:
        if not leaderboard_rows:
            return
        ordered = sorted(leaderboard_rows, key=lambda r: r[2])           # by rank
        self.addresses = [r[1] for r in ordered[:self.track_top]]
        self.snapshot_day = leaderboard_rows[0][0]


# ─── async pollers ────────────────────────────────────────────────────────────

async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _sleep_to_boundary(stop: asyncio.Event, interval_s: float) -> int | None:
    """Sleep to the next grid boundary (+ a small margin so a pre-boundary
    wake cannot floor into the previous bucket). Returns the boundary in
    epoch ms, or None when `stop` was set first."""
    now = time.time()
    boundary = next_boundary_s(now, interval_s)
    if await _sleep_or_stop(stop, boundary - now + BOUNDARY_MARGIN_S):
        return None
    return int(round(boundary * 1000))


async def poll_ctx(writer, status, stop: asyncio.Event, *, http_post=default_http_post,
                   interval_s: float = CTX_INTERVAL_S) -> None:
    loop = asyncio.get_running_loop()
    bucket_ms = _bucket_ms(_now_ms(), interval_s)          # first poll: now, current bucket
    while not stop.is_set():
        status.state = "polling"
        try:
            rows = await loop.run_in_executor(
                None, functools.partial(fetch_asset_ctxs, http_post, ts_ms=bucket_ms))
            writer.put_many("hl_asset_ctx", rows)
            status.note_message(len(rows))
            status.state = "idle"
        except Exception as e:  # noqa: BLE001
            status.errors += 1
            status.last_error = repr(e)
            status.state = "error"
            log.warning(f"hl ctx poll failed: {e!r}")
        bucket_ms = await _sleep_to_boundary(stop, interval_s)
        if bucket_ms is None:
            break
    status.state = "stopped"


async def poll_leaderboard(tracker: Tracker, writer, status, stop: asyncio.Event, *,
                           http_get=default_http_get, check_s: float = 60.0) -> None:
    """Fetch once per UTC day (first run immediately); retry hourly on
    failure — a reply with no usable rows counts as a failure."""
    loop = asyncio.get_running_loop()
    next_try = 0.0
    while not stop.is_set():
        today = utc_day(_now_ms())
        if tracker.snapshot_day != today and time.monotonic() >= next_try:
            status.state = "polling"
            try:
                rows = await loop.run_in_executor(None, fetch_leaderboard, http_get)
                writer.put_many("hl_leaderboard", rows)
                tracker.update(rows)
                status.note_message(len(rows))
                status.state = "idle"
                log.info(f"hl leaderboard {today}: {len(rows)} rows, tracking "
                         f"{len(tracker.addresses)} addresses")
            except Exception as e:  # noqa: BLE001
                status.errors += 1
                status.last_error = repr(e)
                status.state = "error"
                next_try = time.monotonic() + LEADERBOARD_RETRY_S
                log.warning(f"hl leaderboard failed (keeping {len(tracker.addresses)} "
                            f"tracked, retry in {LEADERBOARD_RETRY_S}s): {e!r}")
        if await _sleep_or_stop(stop, check_s):
            break
    status.state = "stopped"


async def poll_positions(tracker: Tracker, writer, status, stop: asyncio.Event, *,
                         http_post=default_http_post, interval_s: float = POSITIONS_INTERVAL_S,
                         concurrency: int = CONCURRENCY, gap_s: float = GAP_S) -> None:
    bucket_ms: int | None = None
    while not stop.is_set():
        if not tracker.addresses:
            status.state = "waiting-leaderboard"
            if await _sleep_or_stop(stop, min(5.0, interval_s)):
                break
            continue
        if bucket_ms is None:
            bucket_ms = _bucket_ms(_now_ms(), interval_s)    # first pass: now, current bucket
        status.state = "polling"
        t0 = time.monotonic()
        try:
            accounts, positions = await fetch_positions(
                list(tracker.addresses), bucket_ms, http_post=http_post,
                concurrency=concurrency, gap_s=gap_s, stop=stop)
            writer.put_many("hl_accounts", accounts)
            writer.put_many("hl_positions", positions)
            status.note_message(len(accounts) + len(positions))
            status.state = "idle"
            log.info(f"hl positions: {len(accounts)} accounts, {len(positions)} positions "
                     f"in {time.monotonic() - t0:.0f}s")
        except Exception as e:  # noqa: BLE001
            status.errors += 1
            status.last_error = repr(e)
            status.state = "error"
            log.warning(f"hl positions poll failed: {e!r}")
        bucket_ms = await _sleep_to_boundary(stop, interval_s)
        if bucket_ms is None:
            break
    status.state = "stopped"
