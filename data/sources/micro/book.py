"""Local order book for one Binance USD-M symbol, maintained from the diff
depth stream with the official sync rules ("How to manage a local order book
correctly"), plus the per-second sample that goes into depth_1s.

Sync rules, as implemented:
  1. Events arriving before a snapshot are buffered (at most BUFFER_MAX; the
     oldest are dropped and counted in `buffer_dropped`. Dropping the oldest
     is safe: the snapshot is fetched after they arrived, so `u <
     lastUpdateId` would discard them anyway, and the bracketing event is
     always among the newest).
  2. `apply_snapshot` loads the REST snapshot (lastUpdateId, bids, asks) and
     replays the buffer: events with `u < lastUpdateId` are dropped; the first
     surviving event must bracket it (`U <= lastUpdateId <= u`), otherwise the
     snapshot is stale and a new one is requested. A malformed snapshot raises
     before the book is touched.
  3. Every later event must have `pu == previous u`; a mismatch marks the
     book out of sync (`in_sync=False`, `need_snapshot=True`) and buffering
     resumes until the next snapshot.
  4. A level with quantity 0 is removed.

Bucketing: 30 buckets per side, 10 bp wide, bucket k = base quantity resting
at distance [k x 0.1 %, (k+1) x 0.1 %) from mid — bids below, asks above —
i.e. +/-3 % of mid. Stored as float32 arrays (`tobytes()`).
Reach: the book is exact only inside the REST snapshot's price range
(limit=1000 levels per side: roughly 0.1-0.5 % from mid on BTCUSDT, 0.2-1 %
on ETHUSDT) — every change inside it arrives on the diff stream. Further out
the book holds just the levels the diff stream has touched since the last
snapshot, so those buckets are LOWER BOUNDS that fill in over minutes as
quotes refresh (far resting orders show up within seconds: the first dry run
held bids 99 % below mid after a minute), and the accumulated far levels are
discarded at every resync (chain break, reconnect, 23 h recycle) because
cancels in the missed gap are never replayed. `sample()` therefore reports
per side `bid_reach_pct` / `ask_reach_pct` = how far from the CURRENT mid
(in %) the book is exact — the snapshot's farthest level on that side, 0
once price has left the snapshot range — and writes NaN into every bucket
beyond the farthest level the book holds at all (nothing known there: the
first seconds after a resync). So a bucket within the reach is exact, a
number beyond it is a lower bound, and NaN is "not covered".
Wall: the single largest level within +/-1 % of mid on that side.

Staleness: a socket can be half-open — nothing arrives and nothing fails
for up to ~40 s (websockets ping 20 s + timeout 20 s). Binance pushes BTC/ETH
diffs every 100 ms, so `sample()` stamps `in_sync=0` when no event has been
applied for more than STALE_MS. It does not mutate the book: the reconnect
path (DepthHandler.on_connect/on_disconnect) owns resyncing.
"""
from __future__ import annotations

import math
import time

import numpy as np

N_BUCKETS = 30
BUCKET_FRAC = 0.001            # 10 bp
WALL_FRAC = 0.01               # +/-1 %
BUFFER_MAX = 5000              # events held while a snapshot is pending (~8 min at 100 ms)
STALE_MS = 5000                # no event for this long -> the row is flagged in_sync=0
_EPS = 1e-9                    # (mid - mid*0.999)/mid is 0.000999999...; keep it in bucket 1


def _now_ms() -> int:
    return int(time.time() * 1000)


class LocalBook:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: int | None = None
        self.in_sync = False
        self.need_snapshot = True
        self.first_pending = False     # first event after a snapshot must bracket it
        self.buffer: list[dict] = []
        self.buffer_dropped = 0
        self.resyncs = 0
        self.events_applied = 0
        self.last_reason = ""
        self.last_event_ms: int | None = None   # wall clock of the last applied event/snapshot
        self.snap_lo_px: float | None = None    # the snapshot's price range: exact inside it
        self.snap_hi_px: float | None = None

    # ─── sync ────────────────────────────────────────────────────────────────

    def mark_out_of_sync(self, why: str = "") -> None:
        self.in_sync = False
        self.need_snapshot = True
        self.first_pending = False
        self.resyncs += 1
        self.last_reason = why

    def _buffer(self, ev: dict) -> None:
        self.buffer.append(ev)
        if len(self.buffer) > BUFFER_MAX:
            del self.buffer[0]
            self.buffer_dropped += 1

    def apply_snapshot(self, snap: dict, now_ms: int | None = None) -> bool:
        """Load a REST snapshot and replay the buffered events. Returns True
        when the book is in sync afterwards. A malformed snapshot (no
        lastUpdateId, unparsable levels) raises and leaves the book as it
        was."""
        luid = int(snap["lastUpdateId"])            # validate before touching the book
        bids = _levels(snap.get("bids") or [])
        asks = _levels(snap.get("asks") or [])
        self.bids, self.asks = bids, asks
        self.snap_lo_px = min(bids) if bids else None
        self.snap_hi_px = max(asks) if asks else None
        self.last_update_id = luid
        self.in_sync = True
        self.need_snapshot = False
        self.first_pending = True
        self.last_event_ms = _now_ms() if now_ms is None else int(now_ms)
        pending, self.buffer = self.buffer, []
        for ev in pending:          # once the chain breaks, the rest re-buffers
            self.apply_event(ev, now_ms)
        return self.in_sync

    def apply_event(self, ev: dict, now_ms: int | None = None) -> bool:
        """Apply one depthUpdate. Returns True when the event was applied,
        False when it was buffered, dropped as stale, or broke the chain."""
        if self.need_snapshot or self.last_update_id is None:
            self._buffer(ev)
            return False
        first, final, prev = int(ev["U"]), int(ev["u"]), int(ev.get("pu", -1))
        if final < self.last_update_id:
            return False                                   # older than the snapshot
        if self.first_pending:
            if not (first <= self.last_update_id <= final):
                self.mark_out_of_sync(f"first event {first}..{final} does not bracket "
                                      f"{self.last_update_id}")
                self._buffer(ev)
                return False
            self.first_pending = False
        elif prev != self.last_update_id:
            self.mark_out_of_sync(f"pu {prev} != last u {self.last_update_id}")
            self._buffer(ev)
            return False
        _apply_levels(self.bids, ev.get("b") or [])
        _apply_levels(self.asks, ev.get("a") or [])
        self.last_update_id = final
        self.events_applied += 1
        self.last_event_ms = _now_ms() if now_ms is None else int(now_ms)
        return True

    # ─── views ───────────────────────────────────────────────────────────────

    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def mid(self) -> float | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0

    def is_stale(self, received_ms: int) -> bool:
        """True when no event has been applied for more than STALE_MS."""
        return self.last_event_ms is not None and int(received_ms) - self.last_event_ms > STALE_MS

    def exact_reach(self, mid: float, side: str) -> float | None:
        """Fraction of mid the book is exact for on `side`: the snapshot's
        farthest level there, 0 once price has moved outside the snapshot
        range, None before any snapshot."""
        px = self.snap_lo_px if side == "bid" else self.snap_hi_px
        if px is None:
            return None
        return max(0.0, distance_frac(px, mid, side))

    def sample(self, ts_ms: int, received_ms: int, venue: str = "binance") -> tuple | None:
        """One depth_1s row from the current book, None when it is empty.
        Column order = store.INSERT_SQL["depth_1s"]."""
        mid = self.mid()
        if mid is None or mid <= 0:
            return None
        bid_b = bucketize(self.bids, mid, "bid", known=distance_frac(min(self.bids), mid, "bid"))
        ask_b = bucketize(self.asks, mid, "ask", known=distance_frac(max(self.asks), mid, "ask"))
        wb_px, wb_qty = find_wall(self.bids, mid)
        wa_px, wa_qty = find_wall(self.asks, mid)
        in_sync = 1 if (self.in_sync and not self.is_stale(received_ms)) else 0
        bid_reach = self.exact_reach(mid, "bid")
        ask_reach = self.exact_reach(mid, "ask")
        return (venue, self.symbol, int(ts_ms), self.best_bid(), self.best_ask(), mid,
                bid_b.tobytes(), ask_b.tobytes(), wb_px, wb_qty, wa_px, wa_qty,
                len(self.bids), len(self.asks), self.last_update_id, in_sync,
                None if bid_reach is None else bid_reach * 100.0,
                None if ask_reach is None else ask_reach * 100.0, int(received_ms))


# ─── pure helpers ─────────────────────────────────────────────────────────────

def _levels(pairs) -> dict[float, float]:
    out: dict[float, float] = {}
    for px, qty in pairs:
        q = float(qty)
        if q > 0:
            out[float(px)] = q
    return out


def _apply_levels(side: dict[float, float], pairs) -> None:
    for px, qty in pairs:
        p, q = float(px), float(qty)
        if q <= 0:
            side.pop(p, None)
        else:
            side[p] = q


def distance_frac(px: float, mid: float, side: str) -> float:
    """Signed-away distance from mid as a fraction: positive when the level
    is on its own side of mid (bids below, asks above)."""
    return (mid - px) / mid if side == "bid" else (px - mid) / mid


def covered_buckets(known_frac: float) -> int:
    """How many buckets (from 0) lie within `known_frac` of mid: the bucket
    holding the farthest known level is the last covered one."""
    if known_frac < 0:
        return 0
    return min(N_BUCKETS, int(math.floor(known_frac / BUCKET_FRAC + _EPS)) + 1)


def bucketize(levels: dict[float, float], mid: float, side: str,
              known: float | None = None) -> np.ndarray:
    """float32[N_BUCKETS]: base quantity per 10 bp distance bucket. With
    `known` (fraction from mid of the farthest level the book holds on that
    side) every bucket beyond it is NaN — "not covered", as opposed to a
    zero."""
    out = np.zeros(N_BUCKETS, dtype=np.float32)
    for px, qty in levels.items():
        d = distance_frac(px, mid, side)
        if d < 0:
            continue                                        # crossed level: ignore
        k = int(math.floor(d / BUCKET_FRAC + _EPS))
        if k < N_BUCKETS:
            out[k] += qty
    if known is not None:
        out[covered_buckets(known):] = np.nan
    return out


def find_wall(levels: dict[float, float], mid: float) -> tuple[float | None, float | None]:
    """(price, qty) of the largest level within +/-WALL_FRAC of mid."""
    best_px, best_qty = None, 0.0
    lim = WALL_FRAC + _EPS
    for px, qty in levels.items():
        if abs(px - mid) / mid <= lim and qty > best_qty:
            best_px, best_qty = px, qty
    if best_px is None:
        return None, None
    return best_px, best_qty


def buckets_from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
