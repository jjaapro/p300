"""data/sources/micro/book.py — Binance local-book sync rules, bucketing, walls,
reach/NaN masking, the pending-buffer cap and the staleness stamp."""
from __future__ import annotations

import numpy as np
import pytest

from data.sources.micro import book as B


def _snap(last_id=100, bids=None, asks=None):
    return {"lastUpdateId": last_id,
            "bids": bids if bids is not None else [["100.0", "1.0"], ["99.5", "2.0"]],
            "asks": asks if asks is not None else [["100.5", "1.5"], ["101.0", "3.0"]]}


def _ev(U, u, pu, b=(), a=()):
    return {"e": "depthUpdate", "s": "BTCUSDT", "U": U, "u": u, "pu": pu,
            "b": [[str(p), str(q)] for p, q in b], "a": [[str(p), str(q)] for p, q in a]}


# ─── sync rules ───────────────────────────────────────────────────────────────

def test_events_before_snapshot_are_buffered_and_replayed():
    bk = B.LocalBook("BTCUSDT")
    assert bk.apply_event(_ev(90, 95, 89)) is False          # buffered (no snapshot yet)
    assert bk.apply_event(_ev(96, 102, 95, b=[(99.0, 5.0)])) is False
    assert len(bk.buffer) == 2
    assert bk.apply_snapshot(_snap(100)) is True
    assert bk.in_sync and not bk.need_snapshot
    assert bk.bids[99.0] == 5.0                              # the bracketing event applied
    assert bk.last_update_id == 102
    assert bk.buffer == []


def test_event_older_than_snapshot_is_dropped():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    bk.first_pending = False
    assert bk.apply_event(_ev(80, 99, 79, b=[(1.0, 1.0)])) is False
    assert 1.0 not in bk.bids and bk.in_sync


def test_first_event_must_bracket_last_update_id():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_event(_ev(105, 110, 104))                       # gap: 101..104 missing
    assert bk.apply_snapshot(_snap(100)) is False
    assert bk.need_snapshot and not bk.in_sync
    assert bk.resyncs == 1
    assert bk.buffer == [_ev(105, 110, 104)]                 # kept for the next snapshot


def test_event_ending_exactly_on_snapshot_id_is_applied():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    assert bk.apply_event(_ev(96, 100, 95, b=[(99.0, 5.0)])) is True   # u == lastUpdateId: kept and brackets
    assert bk.bids[99.0] == 5.0 and bk.last_update_id == 100 and bk.in_sync
    assert bk.apply_event(_ev(101, 105, 100)) is True                   # chain continues from u=100


def test_single_id_event_equal_to_snapshot_id_is_applied():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    assert bk.apply_event(_ev(100, 100, 99, a=[(101.5, 2.0)])) is True  # U == u == lastUpdateId
    assert bk.asks[101.5] == 2.0 and bk.last_update_id == 100 and bk.in_sync


def test_pu_mismatch_marks_out_of_sync_and_requests_resync():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    assert bk.apply_event(_ev(100, 105, 99, b=[(99.9, 1.0)])) is True
    assert bk.apply_event(_ev(106, 110, 105)) is True
    assert bk.apply_event(_ev(115, 120, 114)) is False        # pu 114 != 110
    assert not bk.in_sync and bk.need_snapshot
    assert bk.buffer == [_ev(115, 120, 114)]
    # a new snapshot that the buffered event brackets restores sync
    assert bk.apply_snapshot(_snap(118)) is True
    assert bk.last_update_id == 120


def test_zero_quantity_removes_level():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    bk.apply_event(_ev(100, 101, 99, b=[(99.5, 0)], a=[(101.0, "0.000")]))
    assert 99.5 not in bk.bids and 101.0 not in bk.asks
    assert bk.best_bid() == 100.0 and bk.best_ask() == 100.5 and bk.mid() == 100.25


def test_pending_buffer_is_capped_oldest_first_and_still_resyncs():
    bk = B.LocalBook("BTCUSDT")
    n = B.BUFFER_MAX + 10
    for i in range(n):
        bk.apply_event(_ev(2 * i, 2 * i + 1, 2 * i - 1))     # a continuous chain, no snapshot yet
    assert len(bk.buffer) == B.BUFFER_MAX and bk.buffer_dropped == 10
    assert bk.buffer[0]["U"] == 20                           # the ten oldest went
    assert bk.last_reason == ""                              # overflow is not a desync reason
    luid = 2 * (n - 3) + 1                                   # newer than everything dropped
    assert bk.apply_snapshot(_snap(luid)) is True            # the bracketing event was kept
    assert bk.last_update_id == 2 * (n - 1) + 1 and bk.buffer == []


def test_malformed_snapshot_raises_and_leaves_book_untouched():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))
    with pytest.raises(KeyError):
        bk.apply_snapshot({"code": -1, "msg": "Invalid symbol"})
    with pytest.raises(ValueError):
        bk.apply_snapshot({"lastUpdateId": 200, "bids": [["x", "1"]], "asks": []})
    assert bk.bids == {100.0: 1.0, 99.5: 2.0} and bk.last_update_id == 100 and bk.in_sync


# ─── sample: row layout, staleness, reach ─────────────────────────────────────

def test_sample_is_none_on_empty_book_and_flags_in_sync():
    bk = B.LocalBook("BTCUSDT")
    assert bk.sample(1_000, 1_234) is None
    bk.apply_snapshot(_snap(100))
    row = bk.sample(1_000, 1_234)
    assert len(row) == 19
    assert row[0:3] == ("binance", "BTCUSDT", 1000)
    assert row[3:6] == (100.0, 100.5, 100.25)
    assert row[12:14] == (2, 2) and row[14] == 100 and row[15] == 1 and row[18] == 1234
    bk.mark_out_of_sync("test")
    assert bk.sample(2_000, 2_000)[15] == 0                   # row still written, flagged


def test_sample_flags_stale_book_without_mutating_it():
    """A half-open socket delivers nothing for up to ~40 s; after STALE_MS
    without an applied event the row says in_sync=0 while the book itself
    stays in sync (the reconnect path owns resyncing)."""
    bk = B.LocalBook("BTCUSDT")
    t0 = 1_700_000_000_000
    bk.apply_snapshot(_snap(100), now_ms=t0)
    assert bk.last_event_ms == t0
    assert bk.apply_event(_ev(100, 101, 99, b=[(99.9, 1.0)]), now_ms=t0 + 500) is True
    assert bk.last_event_ms == t0 + 500
    assert bk.sample(t0 + 1000, t0 + 1000)[15] == 1
    assert bk.sample(t0 + 6000, t0 + 6000)[15] == 0           # > STALE_MS since the last event
    assert bk.in_sync and not bk.need_snapshot and bk.resyncs == 0
    assert bk.is_stale(t0 + 6000) and not bk.is_stale(t0 + 5500)


def test_sample_reports_exact_reach_and_nans_beyond_the_known_book():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))                             # bids to 99.5, asks to 101.0, mid 100.25
    row = bk.sample(1_000, 1_000)
    reach = 0.75 / 100.25 * 100                               # the snapshot's farthest level: 0.748 % both sides
    assert row[16] == pytest.approx(reach) and row[17] == pytest.approx(reach)
    bids, asks = B.buckets_from_blob(row[6]), B.buckets_from_blob(row[7])
    assert bids[2] == 1.0 and bids[7] == 2.0 and asks[2] == 1.5 and asks[7] == 3.0
    assert not np.isnan(bids[:8]).any() and np.isnan(bids[8:]).all()   # nothing known beyond bucket 7 yet
    assert not np.isnan(asks[:8]).any() and np.isnan(asks[8:]).all()
    # a far bid from the diff stream: the known range moves out to its bucket (a lower
    # bound there, NaN only beyond it) while the exact reach is still the snapshot's
    bk.apply_event(_ev(100, 101, 99, b=[(98.0, 4.0)]))
    row = bk.sample(2_000, 2_000)
    assert row[16] == pytest.approx(reach)
    bids = B.buckets_from_blob(row[6])
    assert bids[22] == 4.0 and np.nansum(bids) == 7.0                   # 98.0 is 2.24 % away: bucket 22
    assert not np.isnan(bids[:23]).any() and np.isnan(bids[23:]).all()
    bk.apply_event(_ev(102, 103, 101, b=[(96.0, 1.0)]))                # beyond 3 %: covers every bucket
    assert not np.isnan(B.buckets_from_blob(bk.sample(3_000, 3_000)[6])).any()


def test_exact_reach_is_zero_once_price_leaves_the_snapshot_range():
    bk = B.LocalBook("BTCUSDT")
    bk.apply_snapshot(_snap(100))                             # snapshot range 99.5 .. 101.0
    bk.apply_event(_ev(100, 101, 99, b=[(101.5, 1.0)], a=[(100.5, 0), (101.0, 0), (102.0, 1.0)]))
    assert bk.mid() == 101.75                                 # above the snapshot's top ask
    row = bk.sample(1_000, 1_000)
    assert row[17] == 0.0                                     # no ask is exact any more
    assert row[16] == pytest.approx((101.75 - 99.5) / 101.75 * 100)   # bids exact down to the snapshot floor
    assert bk.exact_reach(101.75, "ask") == 0.0 and B.LocalBook("X").exact_reach(1.0, "bid") is None


def test_covered_buckets_edges():
    assert B.covered_buckets(-0.1) == 0
    assert B.covered_buckets(0.0) == 1
    assert B.covered_buckets(0.00025) == 1
    assert B.covered_buckets(0.001) == 2                      # a level exactly at 0.1 % sits in bucket 1
    assert B.covered_buckets(0.0025) == 3
    assert B.covered_buckets(0.05) == B.N_BUCKETS


# ─── bucketing ────────────────────────────────────────────────────────────────

def test_bucket_edges_bids_and_asks_mirror():
    mid = 100.0
    bids = {99.95: 1.0,            # 0.05 % -> bucket 0
            mid * (1 - 0.001): 2.0,  # exactly 0.1 % -> bucket 1
            99.85: 4.0,            # 0.15 % -> bucket 1
            97.05: 8.0,            # 2.95 % -> bucket 29
            97.0: 16.0,            # 3.0 % -> excluded
            95.0: 32.0}            # beyond 3 % -> excluded
    asks = {100.05: 1.0, mid * (1 + 0.001): 2.0, 100.15: 4.0, 102.95: 8.0, 103.0: 16.0, 105.0: 32.0}
    b = B.bucketize(bids, mid, "bid")
    a = B.bucketize(asks, mid, "ask")
    assert b.dtype == np.float32 and b.shape == (30,)
    assert b[0] == 1.0 and b[1] == 6.0 and b[29] == 8.0 and b.sum() == 15.0
    assert np.array_equal(a, b)


def test_bucketize_with_known_reach_nans_uncovered_buckets():
    out = B.bucketize({99.9: 1.0}, 100.0, "bid", known=0.001)
    assert out[1] == 1.0 and out[0] == 0.0 and np.isnan(out[2:]).all()
    assert not np.isnan(B.bucketize({99.9: 1.0}, 100.0, "bid")).any()   # no known reach: no masking


def test_crossed_levels_are_ignored():
    assert B.bucketize({100.5: 9.0}, 100.0, "bid").sum() == 0
    assert B.bucketize({99.5: 9.0}, 100.0, "ask").sum() == 0


def test_blob_round_trip_float32_including_nan():
    arr = B.bucketize({99.95: 1.25, 99.0: 3.5}, 100.0, "bid")
    back = B.buckets_from_blob(arr.tobytes())
    assert back.dtype == np.float32 and np.array_equal(back, arr)
    assert len(arr.tobytes()) == 30 * 4
    masked = B.bucketize({99.95: 1.25}, 100.0, "bid", known=0.0005)
    back = B.buckets_from_blob(masked.tobytes())
    assert back[0] == np.float32(1.25) and np.isnan(back[1:]).all()


# ─── walls ────────────────────────────────────────────────────────────────────

def test_wall_is_largest_level_within_one_percent():
    mid = 100.0
    bids = {99.9: 5.0, 99.2: 50.0, 98.5: 500.0}             # 98.5 is 1.5 % away
    assert B.find_wall(bids, mid) == (99.2, 50.0)
    assert B.find_wall({99.0: 7.0}, mid) == (99.0, 7.0)      # exactly 1 % counts
    assert B.find_wall({}, mid) == (None, None)
    assert B.find_wall({97.0: 1.0}, mid) == (None, None)
