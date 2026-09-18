"""data/sources/micro/hyperliquid.py — parsers and pollers with injected HTTP,
the poll grid (bucket keys, boundary sleeping) and the leaderboard failure path."""
from __future__ import annotations

import asyncio

import pytest

from data.sources.micro import hyperliquid as HL
from data.sources.micro import wsclient

RECV = 1_758_067_260_500          # 2025-09-17 00:01:00.5 UTC-ish; only floors matter

META_AND_CTXS = [
    {"universe": [{"name": "BTC", "szDecimals": 5, "maxLeverage": 40},
                  {"name": "ETH", "szDecimals": 4, "maxLeverage": 25},
                  {"name": "XYZ", "szDecimals": 0, "maxLeverage": 3}]},
    [{"funding": "0.0000125", "openInterest": "12345.5", "prevDayPx": "60000", "dayNtlVlm": "1.5e9",
      "premium": "0.0001", "oraclePx": "60010.0", "markPx": "60012.5", "midPx": "60011.0",
      "impactPxs": ["60010", "60013"], "dayBaseVlm": "25000"},
     {"funding": "-0.00001", "openInterest": "100.0", "prevDayPx": "3000", "dayNtlVlm": "2e8",
      "premium": None, "oraclePx": "3001", "markPx": "3002", "midPx": None,
      "impactPxs": None, "dayBaseVlm": "1"},
     {"funding": "0", "openInterest": "0", "prevDayPx": "1", "dayNtlVlm": "0",
      "premium": "0", "oraclePx": "1", "markPx": "1", "midPx": "1", "impactPxs": None, "dayBaseVlm": "0"}],
]


def _lb_row(addr, value, day=1.0):
    return {"ethAddress": addr, "accountValue": str(value), "prize": 0, "displayName": None,
            "windowPerformances": [["day", {"pnl": str(day), "roi": "0.1", "vlm": "1"}],
                                   ["week", {"pnl": "2", "roi": "0.1", "vlm": "1"}],
                                   ["month", {"pnl": "3", "roi": "0.1", "vlm": "1"}],
                                   ["allTime", {"pnl": "4", "roi": "0.1", "vlm": "1"}]]}


def _state(addr_positions):
    aps = []
    for coin, szi, liq in addr_positions:
        aps.append({"type": "oneWay", "position": {
            "coin": coin, "szi": str(szi), "leverage": {"type": "cross", "value": 10},
            "entryPx": "100.0", "positionValue": str(abs(szi) * 100), "unrealizedPnl": "5",
            "returnOnEquity": "0.05", "liquidationPx": liq, "marginUsed": "10",
            "maxLeverage": 40, "cumFunding": {"allTime": "0"}}})
    return {"marginSummary": {"accountValue": "1000.5", "totalNtlPos": "500", "totalRawUsd": "1000",
                              "totalMarginUsed": "50"},
            "assetPositions": aps, "time": RECV}


class W:
    def __init__(self):
        self.rows = {}

    def put_many(self, table, rows):
        self.rows.setdefault(table, []).extend(rows)
        return len(rows)


# ─── grid ─────────────────────────────────────────────────────────────────────

def test_bucket_ms_follows_the_poll_interval_with_a_one_second_floor():
    assert HL._bucket_ms(1_700_000_110_000, 300) == 1_700_000_100_000      # spec grid at the default
    assert HL._bucket_ms(1_700_000_070_000, 60) != HL._bucket_ms(1_700_000_010_000, 60)
    assert HL._bucket_ms(1_700_000_000_400, 0.05) == 1_700_000_000_000     # never below 1 s
    assert HL._bucket_ms(RECV, 300) == RECV - RECV % 300_000 == 1_758_067_200_000
    assert HL._bucket_ms(RECV, 60) == RECV - RECV % 60_000 == 1_758_067_260_000


def test_next_boundary_never_skips_a_bucket_under_request_latency():
    """250 cycles with a 0.3 s request: keying each poll to the next grid
    boundary yields consecutive minutes; sleeping the interval after the
    request (the old loop) drifts 0.3 s/cycle and skips a minute by ~200."""
    t = 1_700_000_017.0
    keys = []
    for _ in range(250):
        boundary = HL.next_boundary_s(t, 60)
        keys.append(int(boundary))
        t = boundary + HL.BOUNDARY_MARGIN_S + 0.3            # woke after the boundary, request took 0.3 s
    assert all(b - a == 60 for a, b in zip(keys, keys[1:]))
    t, old = 1_700_000_017.0, []
    for _ in range(250):
        old.append(int(t // 60) * 60)
        t += 0.3 + 60
    assert any(b - a == 120 for a, b in zip(old, old[1:]))
    assert HL.next_boundary_s(100.0, 60) == 120.0 and HL.next_boundary_s(120.0, 60) == 180.0
    assert HL.next_boundary_s(5.5, 0.05) == 6.0                # grid never finer than 1 s


# ─── metaAndAssetCtxs ─────────────────────────────────────────────────────────

def test_asset_ctxs_parse_all_coins_floored_to_minute_with_nulls():
    calls = []

    def http_post(url, body):
        calls.append((url, body))
        return META_AND_CTXS

    rows = HL.fetch_asset_ctxs(http_post=http_post, now_ms=RECV)
    assert calls == [(HL.INFO_URL, {"type": "metaAndAssetCtxs"})]
    assert len(rows) == 3
    ts, coin, oi, funding, oracle, mark, mid, premium, vlm, recv = rows[0]
    assert ts == RECV - RECV % 60_000 and recv == RECV
    assert (coin, oi, funding, oracle, mark, mid, premium, vlm) == (
        "BTC", 12345.5, 0.0000125, 60010.0, 60012.5, 60011.0, 0.0001, 1.5e9)
    assert rows[1][1] == "ETH" and rows[1][6] is None and rows[1][7] is None
    assert HL.parse_asset_ctxs({"bad": 1}, 0, 0) == []


def test_asset_ctxs_key_is_the_grid_boundary_or_the_interval_floor():
    http_post = lambda url, body: META_AND_CTXS  # noqa: E731
    rows = HL.fetch_asset_ctxs(http_post=http_post, now_ms=RECV, ts_ms=1_758_067_320_000)
    assert rows[0][0] == 1_758_067_320_000 and rows[0][9] == RECV     # boundary key, raw receive clock
    rows = HL.fetch_asset_ctxs(http_post=http_post, now_ms=RECV, interval_s=300)
    assert rows[0][0] == RECV - RECV % 300_000


# ─── leaderboard ──────────────────────────────────────────────────────────────

def test_leaderboard_top_n_by_numeric_account_value():
    payload = {"leaderboardRows": [_lb_row(f"0x{i:040x}", value) for i, value in
                                   enumerate([5.0, 1e7, 250.0, 9e6, 3e6, 42.0])]}
    rows = HL.fetch_leaderboard(http_get=lambda url: payload, now_ms=RECV, top=3)
    assert [r[2] for r in rows] == [1, 2, 3]
    assert [r[3] for r in rows] == [1e7, 9e6, 3e6]            # numeric, not string order
    assert rows[0][1] == f"0x{1:040x}" and rows[0][0] == HL.utc_day(RECV)
    assert rows[0][4:8] == (1.0, 2.0, 3.0, 4.0)
    assert rows[0][8] == RECV
    tracker = HL.Tracker(track_top=2)
    tracker.update(rows)
    assert tracker.addresses == [f"0x{1:040x}", f"0x{3:040x}"]
    assert tracker.snapshot_day == HL.utc_day(RECV)
    tracker.update([])                                          # failure keeps the previous list
    assert len(tracker.addresses) == 2


@pytest.mark.parametrize("payload", [
    {}, None, [1, 2], {"leaderboardRows": []}, {"leaderboardRows": [{"foo": 1}]},
    {"rows": [_lb_row("0xa", 1)]}, {"error": "maintenance"},
])
def test_leaderboard_without_usable_rows_is_an_error_not_an_empty_success(payload):
    with pytest.raises(ValueError, match="leaderboard"):
        HL.parse_leaderboard(payload, "2025-09-17", RECV)
    with pytest.raises(ValueError):
        HL.fetch_leaderboard(http_get=lambda url: payload, now_ms=RECV)


def test_leaderboard_poller_fetches_once_on_a_rowless_reply_and_retries_after_the_window(monkeypatch):
    calls = []

    def http_get(url):
        calls.append(url)
        return {"unexpected": 1}

    w = W()
    tracker = HL.Tracker(track_top=2)
    tracker.addresses = ["0xold"]                              # from a previous day
    st = wsclient.StreamStatus("hl_leaderboard")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(HL.poll_leaderboard(tracker, w, st, stop, http_get=http_get, check_s=0.01))
        await asyncio.sleep(0.2)                               # ~20 check ticks
        stop.set()
        await task

    asyncio.run(scenario())
    assert len(calls) == 1                                     # not refetched every tick
    assert st.errors == 1 and st.msgs == 0 and "leaderboardRows" in st.last_error
    assert "hl_leaderboard" not in w.rows
    assert tracker.addresses == ["0xold"] and tracker.snapshot_day is None
    monkeypatch.setattr(HL, "LEADERBOARD_RETRY_S", 0.05)      # the hourly retry, shrunk
    calls.clear()
    asyncio.run(scenario())
    assert len(calls) >= 2


# ─── positions ────────────────────────────────────────────────────────────────

def test_positions_poll_one_account_row_per_address_and_null_liq_px():
    states = {"0xa": _state([("BTC", 1.5, "95000.0"), ("ETH", -2.0, None)]),
              "0xb": _state([]),
              "0xc": None}
    seen = []

    def http_post(url, body):
        seen.append(body)
        assert body["type"] == "clearinghouseState"
        if body["user"] == "0xc":
            raise RuntimeError("boom")
        return states[body["user"]]

    accounts, positions = asyncio.run(HL.fetch_positions(
        ["0xa", "0xb", "0xc"], 1_000, http_post=http_post, concurrency=2, gap_s=0, now_ms=RECV))
    assert len(seen) == 3
    assert sorted(a[1] for a in accounts) == ["0xa", "0xb"]      # 0xc failed, others survive
    by_addr = {a[1]: a for a in accounts}
    assert by_addr["0xa"] == (1000, "0xa", 1000.5, 500.0, 50.0, 2, RECV)
    assert by_addr["0xb"][5] == 0                                 # zero positions still a row
    assert len(positions) == 2
    btc = next(p for p in positions if p[2] == "BTC")
    eth = next(p for p in positions if p[2] == "ETH")
    assert btc == (1000, "0xa", "BTC", 1.5, 100.0, 150.0, 95000.0, "cross", 10.0, 5.0, 10.0, RECV)
    assert eth[3] == -2.0 and eth[6] is None


def test_positions_poll_returns_partial_pass_when_stopped():
    """A full pass is ~100 s; shutdown must not wait for it (the first dry
    run sat 111 s in the positions poll after --once-seconds elapsed)."""
    served = []

    async def scenario():
        stop = asyncio.Event()

        def http_post(url, body):
            served.append(body["user"])
            stop.set()                        # stop lands while the first request is in flight
            return _state([])

        return await asyncio.wait_for(HL.fetch_positions(
            [f"0x{i}" for i in range(50)], 1_000, http_post=http_post, concurrency=1,
            gap_s=3600.0, stop=stop), timeout=5)

    accounts, positions = asyncio.run(scenario())
    assert served == ["0x0"] and len(accounts) == 1 and positions == []


def test_pollers_write_through_writer_and_stop(monkeypatch):
    monkeypatch.setattr(HL, "_now_ms", lambda: RECV)           # the poll clock; the grid sleep uses time.time()
    w = W()
    tracker = HL.Tracker(track_top=1)
    lb_payload = {"leaderboardRows": [_lb_row("0xa", 100), _lb_row("0xb", 50)]}
    st_ctx, st_lb, st_pos = (wsclient.StreamStatus(n) for n in ("hl_ctx", "hl_leaderboard", "hl_positions"))

    def http_post(url, body):
        if body["type"] == "metaAndAssetCtxs":
            return META_AND_CTXS
        return _state([("BTC", 1.0, "1")])

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(HL.poll_ctx(w, st_ctx, stop, http_post=http_post, interval_s=0.05)),
                 asyncio.create_task(HL.poll_leaderboard(tracker, w, st_lb, stop,
                                                         http_get=lambda url: lb_payload, check_s=0.05)),
                 asyncio.create_task(HL.poll_positions(tracker, w, st_pos, stop, http_post=http_post,
                                                       interval_s=0.05, gap_s=0))]
        for _ in range(120):                                   # the grid floors at 1 s: ~2.1 s
            await asyncio.sleep(0.05)
            if w.rows.get("hl_positions") and len({r[0] for r in w.rows.get("hl_asset_ctx", [])}) >= 3:
                break
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    ctx = w.rows["hl_asset_ctx"]
    assert len({r[0] for r in ctx}) >= 3                       # one key per grid boundary, no collisions
    assert ctx[0][0] == RECV - RECV % 1000 and ctx[0][9] == RECV
    assert [r[1] for r in w.rows["hl_leaderboard"]] == ["0xa", "0xb"]
    assert tracker.addresses == ["0xa"]
    acc, pos = w.rows["hl_accounts"][0], w.rows["hl_positions"][0]
    assert acc[1] == "0xa" and pos[2] == "BTC"
    assert acc[0] == pos[0] == RECV - RECV % 1000               # first pass: the current grid bucket
    assert acc[6] == pos[11] == RECV                            # received_ms is the raw poll clock
    assert st_ctx.msgs >= 3 and st_lb.msgs == 1 and st_pos.msgs >= 1
    assert st_ctx.state == st_lb.state == st_pos.state == "stopped"
