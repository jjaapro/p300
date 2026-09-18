"""Binance USD-M streams: all-market liquidations and diff depth.

Liquidations (`!forceOrder@arr`): Binance pushes only the LARGEST liquidation
per symbol per 1000 ms, so the table is a lower bound on liquidation flow —
record what arrives. Payload:
  {"e":"forceOrder","E":..,"o":{"s":"BTCUSDT","S":"SELL","o":"LIMIT","f":"IOC",
   "q":"0.014","p":"9910","ap":"9910","X":"FILLED","l":"0.014","z":"0.014","T":..}}
`S` is the ORDER side: SELL means a LONG position was liquidated.
price = `ap` when > 0 else `p`; qty = `z` (filled) when > 0 else `q`; ts = `T`.

Depth (`<sym>@depth@100ms`, combined stream): `DepthHandler` keeps one
`book.LocalBook` per symbol, applies every event, and whenever a book falls
out of sync the `resync_loop` task fetches a REST snapshot (in a thread so
the event loop keeps reading). `sampler` writes one depth_1s row per symbol
per wall-clock second.

Socket state: `on_connect` marks every book out of sync (a fresh socket is a
fresh update-id chain) and enables snapshots; `on_disconnect` marks them out
of sync again and disables snapshots — a frozen book is not a live one, so
depth_1s rows carry in_sync=0 until the next resync, and no REST call is
wasted while there is no socket to continue the chain.

REST budget (the IP is shared with feed.py): snapshots for a symbol are at
least SNAPSHOT_MIN_GAP_S apart and the gap doubles on every consecutive
failure — transport error, malformed body or a snapshot the stream does not
bracket — up to SNAPSHOT_BACKOFF_MAX_S, resetting on success. A 429 or 418
(`RateLimited`) stops every symbol's snapshots for max(Retry-After,
RATE_LIMIT_HOLD_MIN_S); 418 is an IP ban, so the hold honours the header in
full.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from data.sources.micro import book as _book

log = logging.getLogger("micro.binance")

FORCE_ORDER_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
DEPTH_URL_BASE = "wss://fstream.binance.com/stream?streams="
SNAPSHOT_URL = "https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit=1000"
DEPTH_SYMBOLS = ("BTCUSDT", "ETHUSDT")
MAX_AGE_S = 23 * 3600              # Binance closes sockets at 24 h; leave early
SNAPSHOT_MIN_GAP_S = 2.0
SNAPSHOT_BACKOFF_MAX_S = 120.0     # per-symbol gap after repeated failures
RATE_LIMIT_HOLD_MIN_S = 60.0       # IP-wide hold after a 429/418, at least
SNAPSHOT_TIMEOUT_S = 15
VENUE = "binance"


class RateLimited(Exception):
    """HTTP 429 (rate limit) or 418 (IP ban) from the REST snapshot.
    `retry_after_s` is the venue's Retry-After header, 0 when absent."""

    def __init__(self, status_code: int, retry_after_s: float = 0.0):
        super().__init__(f"HTTP {status_code}, Retry-After {retry_after_s:g}s")
        self.status_code = int(status_code)
        self.retry_after_s = float(retry_after_s)


def retry_after_seconds(value) -> float:
    """Seconds from a Retry-After header value; 0 when absent or unparsable."""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def depth_url(symbols=DEPTH_SYMBOLS) -> str:
    return DEPTH_URL_BASE + "/".join(f"{s.lower()}@depth@100ms" for s in symbols)


def default_http_get(url: str) -> dict:
    import requests
    r = requests.get(url, timeout=SNAPSHOT_TIMEOUT_S, headers={"User-Agent": "p300-collector/1.0"})
    if r.status_code in (429, 418):
        raise RateLimited(r.status_code, retry_after_seconds(r.headers.get("Retry-After")))
    r.raise_for_status()
    return r.json()


def unwrap_combined(obj):
    """Combined-stream frames are {"stream": ..., "data": {...}}."""
    if isinstance(obj, dict) and "stream" in obj and "data" in obj:
        return obj["data"]
    return obj


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# ─── liquidations ─────────────────────────────────────────────────────────────

def force_order_row(ev: dict, received_ms: int) -> tuple | None:
    """One liquidations row from a forceOrder event, None when malformed."""
    o = ev.get("o") if isinstance(ev, dict) else None
    if not o or ev.get("e") != "forceOrder":
        return None
    order_side = str(o.get("S", "")).lower()
    if order_side not in ("buy", "sell"):
        return None
    pos_side = "long" if order_side == "sell" else "short"
    price = _f(o.get("ap"))
    if price <= 0:
        price = _f(o.get("p"))
    qty = _f(o.get("z"))
    if qty <= 0:
        qty = _f(o.get("q"))
    if price <= 0 or qty <= 0:
        return None
    extra = json.dumps({k: o.get(k) for k in ("o", "f", "X", "l", "z", "ap")})
    return (VENUE, o.get("s"), int(o.get("T") or ev.get("E") or 0), pos_side, order_side,
            price, qty, price * qty, extra, int(received_ms))


def parse_force_order(text: str, received_ms: int | None = None) -> list[tuple]:
    received_ms = int(time.time() * 1000) if received_ms is None else received_ms
    try:
        obj = unwrap_combined(json.loads(text))
    except ValueError:
        return []
    events = obj if isinstance(obj, list) else [obj]
    rows = []
    for ev in events:
        row = force_order_row(ev, received_ms)
        if row is not None:
            rows.append(row)
    return rows


# ─── depth ────────────────────────────────────────────────────────────────────

def parse_depth_event(text: str) -> dict | None:
    try:
        obj = unwrap_combined(json.loads(text))
    except ValueError:
        return None
    if isinstance(obj, dict) and obj.get("e") == "depthUpdate" and "s" in obj:
        return obj
    return None


class DepthHandler:
    """Owns the per-symbol books. `on_message` is the socket callback;
    `on_connect` / `on_disconnect` are the wsclient hooks; `resync_loop`
    and `sampler` are the companion asyncio tasks."""

    def __init__(self, symbols=DEPTH_SYMBOLS, *, writer=None, http_get=None,
                 min_gap_s: float = SNAPSHOT_MIN_GAP_S):
        self.books = {s: _book.LocalBook(s) for s in symbols}
        self.writer = writer
        self.http_get = http_get or default_http_get
        self.min_gap_s = min_gap_s
        self.last_snapshot_at: dict[str, float] = {}
        self.failures: dict[str, int] = {}       # consecutive snapshot failures per symbol
        self.hold_until = 0.0                    # monotonic; no REST for any symbol before it
        self.connected = False
        self.snapshots = 0
        self.snapshot_failures = 0
        self.samples = 0

    # ─── socket hooks ────────────────────────────────────────────────────────

    def on_connect(self) -> None:
        """A fresh socket has a fresh update-id chain: every book resyncs.
        Snapshots are only fetched once this has run — a snapshot taken
        before the socket is up is wasted (seen on the first dry run)."""
        self.connected = True
        self.failures.clear()                    # fresh socket, fresh chain (the IP hold stays)
        for b in self.books.values():
            b.mark_out_of_sync("connect")
        log.info("depth socket connected — resyncing " + ",".join(self.books))

    def on_disconnect(self) -> None:
        """Socket gone: no event flow, so the sequence rules no longer hold
        and a snapshot could not be continued. Idempotent — it also fires
        when connect() itself failed and on_connect never ran; then there is
        nothing to mark."""
        if not self.connected:
            return
        self.connected = False
        for b in self.books.values():
            b.mark_out_of_sync("disconnect")
        log.warning("depth socket disconnected — depth_1s rows flagged in_sync=0 until resync")

    def on_message(self, text: str) -> int:
        ev = parse_depth_event(text)
        if ev is None:
            return 0
        b = self.books.get(ev["s"])
        if b is None:
            return 0
        was_synced = b.in_sync
        b.apply_event(ev)
        if was_synced and not b.in_sync:
            log.warning(f"[{b.symbol}] book out of sync: {b.last_reason} — resync")
        return 0

    # ─── snapshots ───────────────────────────────────────────────────────────

    def snapshot_url(self, symbol: str) -> str:
        return SNAPSHOT_URL.format(symbol=symbol)

    def snapshot_gap_s(self, symbol: str) -> float:
        """Seconds between snapshot attempts for `symbol`: min_gap doubling
        per consecutive failure, capped."""
        return min(SNAPSHOT_BACKOFF_MAX_S, self.min_gap_s * 2 ** self.failures.get(symbol, 0))

    def rest_hold_s(self) -> float:
        """Seconds left on the IP-wide hold after a 429/418 (0 when none)."""
        return max(0.0, self.hold_until - time.monotonic())

    def _failed(self, symbol: str) -> None:
        self.snapshot_failures += 1
        self.failures[symbol] = self.failures.get(symbol, 0) + 1

    async def resync_once(self, symbol: str) -> bool:
        b = self.books[symbol]
        loop = asyncio.get_running_loop()
        try:
            snap = await loop.run_in_executor(None, self.http_get, self.snapshot_url(symbol))
        except RateLimited as e:
            hold = max(e.retry_after_s, RATE_LIMIT_HOLD_MIN_S)
            self.hold_until = time.monotonic() + hold
            self._failed(symbol)
            log.error(f"[{symbol}] snapshot rate-limited ({e}) — no REST snapshots for "
                      f"{hold:.0f}s (the IP is shared with feed.py)")
            return False
        except Exception as e:  # noqa: BLE001
            self._failed(symbol)
            log.warning(f"[{symbol}] snapshot failed: {e!r} — next try in "
                        f"{self.snapshot_gap_s(symbol):.0f}s")
            return False
        if not self.connected:                   # the socket dropped while we fetched
            log.info(f"[{symbol}] snapshot discarded: depth socket is down")
            return False
        self.snapshots += 1
        try:
            ok = b.apply_snapshot(snap)
        except (KeyError, TypeError, ValueError) as e:
            self._failed(symbol)
            log.warning(f"[{symbol}] snapshot malformed: {e!r} — next try in "
                        f"{self.snapshot_gap_s(symbol):.0f}s")
            return False
        if ok:
            self.failures.pop(symbol, None)
        else:
            self._failed(symbol)
        log.info(f"[{symbol}] snapshot lastUpdateId={b.last_update_id} levels="
                 f"{len(b.bids)}/{len(b.asks)} in_sync={ok}")
        return ok

    async def resync_loop(self, stop: asyncio.Event, poll_s: float = 0.25) -> None:
        """Fetch a snapshot for every book that needs one, honouring the
        per-symbol gap and the IP-wide hold. The poll cadence never sleeps
        for the hold itself, so `stop` stays responsive."""
        while not stop.is_set():
            now = time.monotonic()
            if self.connected and now >= self.hold_until:
                for symbol, b in self.books.items():
                    if not b.need_snapshot or not self.connected:
                        continue
                    if now - self.last_snapshot_at.get(symbol, -1e9) < self.snapshot_gap_s(symbol):
                        continue
                    self.last_snapshot_at[symbol] = now
                    await self.resync_once(symbol)
                    if time.monotonic() < self.hold_until:
                        break                    # rate-limited: no more calls this pass
            await asyncio.sleep(poll_s)

    # ─── samples ─────────────────────────────────────────────────────────────

    def take_sample(self, symbol: str, now_ms: int | None = None) -> tuple | None:
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        row = self.books[symbol].sample(now_ms - now_ms % 1000, now_ms)
        if row is not None and self.writer is not None:
            self.writer.put("depth_1s", row)
        if row is not None:
            self.samples += 1
        return row

    async def sampler(self, symbol: str, stop: asyncio.Event, status=None) -> None:
        """One sample per wall-clock second, taken just after the boundary."""
        while not stop.is_set():
            now = time.time()
            await asyncio.sleep(1.0 - (now % 1.0) + 0.02)
            if stop.is_set():
                break
            row = self.take_sample(symbol)
            if status is not None and row is not None:
                status.rows += 1

    def as_dict(self) -> dict:
        """Diagnostics for the status file."""
        return {
            "connected": self.connected,
            "snapshots": self.snapshots,
            "snapshot_failures": self.snapshot_failures,
            "samples": self.samples,
            "rest_hold_s": round(self.rest_hold_s(), 1),
            "failures": dict(self.failures),
            "books": {s: {"in_sync": b.in_sync, "need_snapshot": b.need_snapshot,
                          "levels": [len(b.bids), len(b.asks)], "resyncs": b.resyncs,
                          "snapshot_range": [b.snap_lo_px, b.snap_hi_px],
                          "events": b.events_applied, "buffered": len(b.buffer),
                          "buffer_dropped": b.buffer_dropped, "last_reason": b.last_reason}
                      for s, b in self.books.items()},
        }
