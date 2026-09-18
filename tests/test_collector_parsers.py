"""Venue parsers against the documented fixture payloads (no network), plus
the depth handler's snapshot policy (backoff, rate-limit hold, disconnect)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from data.sources.micro import binance_ws, bybit_ws, okx_ws

RECV = 1_700_000_000_000

BINANCE_FORCE = ('{"e":"forceOrder","E":1568014460893,"o":{"s":"BTCUSDT","S":"SELL","o":"LIMIT",'
                 '"f":"IOC","q":"0.014","p":"9910","ap":"9910","X":"FILLED","l":"0.014","z":"0.014",'
                 '"T":1568014460893}}')
BYBIT_LIQ = ('{"topic":"allLiquidation.BTCUSDT","type":"snapshot","ts":1739502303204,'
             '"data":[{"T":1739502302929,"s":"BTCUSDT","S":"Sell","v":"0.001","p":"96176.50"}]}')
OKX_LIQ = ('{"arg":{"channel":"liquidation-orders","instType":"SWAP"},"data":[{"details":[{"bkLoss":"0",'
           '"bkPx":"0.09859","ccy":"","posSide":"short","side":"buy","sz":"10975","ts":"1789678209470"}],'
           '"instFamily":"MINA-USDT","instId":"MINA-USDT-SWAP","instType":"SWAP","uly":"MINA-USDT"}]}')

SNAP_URL = "https://fapi.binance.com/fapi/v1/depth?symbol={}&limit=1000"


def _depth_frame(sym, U, u, pu, b=(), a=()):
    return json.dumps({"stream": f"{sym.lower()}@depth@100ms", "data": {
        "e": "depthUpdate", "s": sym, "U": U, "u": u, "pu": pu,
        "b": [[str(p), str(q)] for p, q in b], "a": [[str(p), str(q)] for p, q in a]}})


def _snapshot(luid=10):
    return {"lastUpdateId": luid, "bids": [["100", "1"]], "asks": [["101", "1"]]}


def _now_ms():
    return int(time.time() * 1000)


# ─── Binance ──────────────────────────────────────────────────────────────────

def test_binance_force_order_sell_is_long_liquidated_at_ap():
    rows = binance_ws.parse_force_order(BINANCE_FORCE, RECV)
    assert len(rows) == 1
    venue, sym, ts, pos, side, price, qty, notional, extra, recv = rows[0]
    assert (venue, sym, ts, pos, side) == ("binance", "BTCUSDT", 1568014460893, "long", "sell")
    assert (price, qty, notional, recv) == (9910.0, 0.014, 9910.0 * 0.014, RECV)
    assert json.loads(extra) == {"o": "LIMIT", "f": "IOC", "X": "FILLED", "l": "0.014",
                                 "z": "0.014", "ap": "9910"}


def test_binance_force_order_prefers_avg_price_and_filled_qty():
    """The documented fixture has ap == p and z == q, so it cannot pin the
    preference; distinct values do."""
    ev = json.loads(BINANCE_FORCE)
    ev["o"].update({"ap": "9905", "p": "9910", "z": "0.010", "q": "0.014", "l": "0.010",
                    "X": "PARTIALLY_FILLED"})
    (row,) = binance_ws.parse_force_order(json.dumps(ev), RECV)
    assert row[5:8] == (9905.0, 0.010, 9905.0 * 0.010)
    assert json.loads(row[8])["ap"] == "9905" and json.loads(row[8])["z"] == "0.010"


def test_binance_force_order_buy_is_short_and_falls_back_to_p_and_q():
    ev = json.loads(BINANCE_FORCE)
    ev["o"].update({"S": "BUY", "ap": "0", "z": "0", "p": "10000", "q": "0.5"})
    (row,) = binance_ws.parse_force_order(json.dumps(ev), RECV)
    assert row[3:7] == ("short", "buy", 10000.0, 0.5)


def test_binance_combined_stream_unwrapped_and_garbage_ignored():
    wrapped = json.dumps({"stream": "!forceOrder@arr", "data": json.loads(BINANCE_FORCE)})
    assert len(binance_ws.parse_force_order(wrapped, RECV)) == 1
    assert binance_ws.parse_force_order("not json", RECV) == []
    assert binance_ws.parse_force_order('{"e":"other"}', RECV) == []


def test_binance_depth_event_parses_combined_frame_only_for_depth_updates():
    ev = {"e": "depthUpdate", "E": 1, "T": 1, "s": "BTCUSDT", "U": 1, "u": 2, "pu": 0,
          "b": [["100", "1"]], "a": []}
    frame = json.dumps({"stream": "btcusdt@depth@100ms", "data": ev})
    assert binance_ws.parse_depth_event(frame) == ev
    assert binance_ws.parse_depth_event(json.dumps({"result": None, "id": 1})) is None
    assert binance_ws.depth_url(("BTCUSDT", "ETHUSDT")).endswith(
        "streams=btcusdt@depth@100ms/ethusdt@depth@100ms")


def test_depth_handler_applies_events_and_resyncs_via_injected_snapshot():
    class W:
        rows = []

        def put(self, table, row):
            self.rows.append((table, row))

    calls = []

    def http_get(url):
        calls.append(url)
        return {"lastUpdateId": 10, "bids": [["100", "1"]], "asks": [["101", "1"]]}

    h = binance_ws.DepthHandler(("BTCUSDT",), writer=W(), http_get=http_get, min_gap_s=0)
    h.on_connect()
    h.on_message(_depth_frame("BTCUSDT", 9, 12, 8, b=[(99.5, 2)]))
    assert h.books["BTCUSDT"].need_snapshot
    assert asyncio.run(h.resync_once("BTCUSDT")) is True
    assert calls == [SNAP_URL.format("BTCUSDT")]
    assert h.books["BTCUSDT"].bids == {100.0: 1.0, 99.5: 2.0}
    row = h.take_sample("BTCUSDT", now_ms=_now_ms() // 1000 * 1000 + 750)
    assert row[2] % 1000 == 0 and row[15] == 1
    assert W.rows[-1][0] == "depth_1s"
    h.on_connect()                                          # a new socket restarts the chain
    assert not h.books["BTCUSDT"].in_sync


def test_resync_loop_waits_for_the_socket_before_fetching_snapshots():
    """A snapshot fetched before the depth socket is up is wasted (the chain
    restarts at connect): the loop must not call REST until on_connect."""
    calls = []

    def http_get(url):
        calls.append(url)
        return _snapshot(1)

    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=http_get, min_gap_s=0)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        await asyncio.sleep(0.1)
        assert calls == []
        h.on_connect()
        await asyncio.sleep(0.1)
        stop.set()
        await task

    asyncio.run(scenario())
    assert calls == [SNAP_URL.format("BTCUSDT")]
    assert h.books["BTCUSDT"].in_sync


def test_depth_handler_disconnect_marks_books_and_stops_snapshots():
    calls = []

    def http_get(url):
        calls.append(url)
        return _snapshot(10)

    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=http_get, min_gap_s=0)
    b = h.books["BTCUSDT"]

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        h.on_connect()
        await asyncio.sleep(0.1)
        assert len(calls) == 1 and b.in_sync
        h.on_message(_depth_frame("BTCUSDT", 10, 12, 9, b=[(99.5, 2)]))
        assert h.take_sample("BTCUSDT", now_ms=_now_ms())[15] == 1
        h.on_disconnect()
        assert h.connected is False and not b.in_sync and b.need_snapshot
        assert b.last_reason == "disconnect" and b.resyncs == 2
        row = h.take_sample("BTCUSDT", now_ms=_now_ms())
        assert row is not None and row[15] == 0             # still written, flagged
        await asyncio.sleep(0.1)
        assert len(calls) == 1                              # no REST while the socket is down
        h.on_disconnect()                                   # idempotent
        assert b.resyncs == 2
        h.on_connect()
        await asyncio.sleep(0.1)
        assert len(calls) == 2 and b.in_sync and h.connected
        assert h.take_sample("BTCUSDT", now_ms=_now_ms())[15] == 1
        stop.set()
        await task

    asyncio.run(scenario())
    assert h.snapshots == 2


def test_disconnect_before_connect_is_a_no_op():
    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=lambda url: _snapshot(), min_gap_s=0)
    h.on_disconnect()
    assert h.books["BTCUSDT"].resyncs == 0 and not h.connected


def test_take_sample_flags_stale_book_after_five_seconds():
    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=lambda url: _snapshot(10), min_gap_s=0)
    h.on_connect()
    assert asyncio.run(h.resync_once("BTCUSDT")) is True
    b = h.books["BTCUSDT"]
    t = b.last_event_ms
    assert h.take_sample("BTCUSDT", now_ms=t + 1000)[15] == 1
    assert h.take_sample("BTCUSDT", now_ms=t + 6000)[15] == 0
    assert b.in_sync and not b.need_snapshot                # the sampler never mutates the book


def test_snapshot_gap_doubles_per_failure_and_caps():
    h = binance_ws.DepthHandler(("BTCUSDT",), min_gap_s=2.0)
    assert h.snapshot_gap_s("BTCUSDT") == 2.0
    for n, want in [(1, 4.0), (2, 8.0), (5, 64.0), (6, 120.0), (20, 120.0)]:
        h.failures["BTCUSDT"] = n
        assert h.snapshot_gap_s("BTCUSDT") == want
    assert h.snapshot_gap_s("ETHUSDT") == 2.0               # per symbol


def test_failing_snapshots_back_off_instead_of_hammering_rest():
    calls = []

    def http_get(url):
        calls.append(time.monotonic())
        raise RuntimeError("boom")

    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=http_get, min_gap_s=0.05)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        h.on_connect()
        await asyncio.sleep(0.8)                            # min-gap cadence would allow ~16 calls
        stop.set()
        await task

    asyncio.run(scenario())
    gaps = [b - a for a, b in zip(calls, calls[1:])]
    assert 3 <= len(calls) <= 5, calls                      # 0, 0.1, 0.3, 0.7 s
    assert all(later >= earlier * 1.5 for earlier, later in zip(gaps, gaps[1:])), gaps
    assert h.failures["BTCUSDT"] == len(calls) == h.snapshot_failures
    assert not h.books["BTCUSDT"].in_sync


def test_rate_limit_holds_every_symbol_then_resumes(monkeypatch):
    monkeypatch.setattr(binance_ws, "RATE_LIMIT_HOLD_MIN_S", 0.0)
    calls = []

    def http_get(url):
        calls.append(url)
        if len(calls) == 1:
            raise binance_ws.RateLimited(429, retry_after_s=0.3)
        return _snapshot(1)

    h = binance_ws.DepthHandler(("BTCUSDT", "ETHUSDT"), http_get=http_get, min_gap_s=0)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        h.on_connect()
        await asyncio.sleep(0.15)
        assert len(calls) == 1                              # the hold is IP-wide: ETH waited too
        assert 0 < h.rest_hold_s() <= 0.3
        await asyncio.sleep(0.4)
        assert len(calls) >= 3 and h.rest_hold_s() == 0.0
        assert all(b.in_sync for b in h.books.values())
        stop.set()
        await task

    asyncio.run(scenario())
    assert h.snapshot_failures == 1


def test_rate_limit_hold_is_at_least_the_minimum():
    h = binance_ws.DepthHandler(("BTCUSDT",), min_gap_s=0,
                                http_get=lambda url: (_ for _ in ()).throw(
                                    binance_ws.RateLimited(418, retry_after_s=5)))
    h.on_connect()
    assert asyncio.run(h.resync_once("BTCUSDT")) is False
    assert binance_ws.RATE_LIMIT_HOLD_MIN_S - 1 < h.rest_hold_s() <= binance_ws.RATE_LIMIT_HOLD_MIN_S
    assert h.failures["BTCUSDT"] == 1


def test_non_bracketing_snapshot_widens_gap_and_bracketing_one_resets_it():
    snap_id = [100]
    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=lambda url: _snapshot(snap_id[0]), min_gap_s=0.05)
    h.on_connect()
    h.on_message(_depth_frame("BTCUSDT", 105, 110, 104))    # 101..104 missing
    assert asyncio.run(h.resync_once("BTCUSDT")) is False
    assert h.failures["BTCUSDT"] == 1 and h.snapshot_gap_s("BTCUSDT") == 0.1
    snap_id[0] = 106                                        # brackets the buffered event
    assert asyncio.run(h.resync_once("BTCUSDT")) is True
    assert h.failures.get("BTCUSDT", 0) == 0 and h.snapshot_gap_s("BTCUSDT") == 0.05


def test_on_connect_resets_failures_but_keeps_the_hold():
    h = binance_ws.DepthHandler(("BTCUSDT",), min_gap_s=0)
    h.failures["BTCUSDT"] = 3
    h.hold_until = time.monotonic() + 100
    h.on_connect()
    assert h.failures == {} and h.rest_hold_s() > 90


def test_stop_during_rest_hold_ends_resync_loop_promptly():
    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=lambda url: _snapshot(), min_gap_s=0)
    h.hold_until = time.monotonic() + 3600

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        h.on_connect()
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(scenario())
    assert h.snapshots == 0


def test_malformed_snapshot_is_a_retried_failure_not_a_crash():
    calls = []

    def http_get(url):
        calls.append(url)
        return {"code": -1121, "msg": "Invalid symbol."}

    h = binance_ws.DepthHandler(("BTCUSDT",), http_get=http_get, min_gap_s=0)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(h.resync_loop(stop, poll_s=0.01))
        h.on_connect()
        await asyncio.sleep(0.1)
        assert not task.done()                              # the loop survived the bad body
        stop.set()
        await task

    asyncio.run(scenario())
    assert len(calls) >= 2 and h.snapshot_failures == len(calls)
    assert h.books["BTCUSDT"].bids == {} and h.books["BTCUSDT"].need_snapshot


def test_snapshot_fetched_while_socket_dropped_is_discarded():
    h = binance_ws.DepthHandler(("BTCUSDT",), min_gap_s=0,
                                http_get=lambda url: (h.on_disconnect(), _snapshot())[1])
    h.on_connect()
    assert asyncio.run(h.resync_once("BTCUSDT")) is False
    assert h.books["BTCUSDT"].bids == {} and not h.books["BTCUSDT"].in_sync


def test_default_http_get_raises_rate_limited_on_429_and_418(monkeypatch):
    import requests

    class Resp:
        def __init__(self, code, headers=None):
            self.status_code = code
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

        def json(self):
            return {"ok": 1}

    responses = []
    monkeypatch.setattr(requests, "get", lambda url, **kw: responses.pop(0))
    responses.append(Resp(429, {"Retry-After": "7"}))
    with pytest.raises(binance_ws.RateLimited) as e:
        binance_ws.default_http_get("http://x")
    assert e.value.status_code == 429 and e.value.retry_after_s == 7.0
    responses.append(Resp(418))
    with pytest.raises(binance_ws.RateLimited) as e:
        binance_ws.default_http_get("http://x")
    assert e.value.status_code == 418 and e.value.retry_after_s == 0.0
    responses.append(Resp(500))
    with pytest.raises(requests.HTTPError):
        binance_ws.default_http_get("http://x")
    responses.append(Resp(200))
    assert binance_ws.default_http_get("http://x") == {"ok": 1}
    assert binance_ws.retry_after_seconds("12") == 12.0
    assert binance_ws.retry_after_seconds(None) == 0.0
    assert binance_ws.retry_after_seconds("soon") == 0.0
    assert binance_ws.retry_after_seconds("-3") == 0.0


def test_depth_handler_status_dict():
    h = binance_ws.DepthHandler(("BTCUSDT",), min_gap_s=0)
    d = h.as_dict()
    assert d["connected"] is False and d["rest_hold_s"] == 0.0 and d["failures"] == {}
    assert d["books"]["BTCUSDT"]["need_snapshot"] is True and d["books"]["BTCUSDT"]["buffer_dropped"] == 0


# ─── Bybit ────────────────────────────────────────────────────────────────────

def test_bybit_sell_is_short_liquidated_and_buy_is_long():
    (row,) = bybit_ws.parse_message(BYBIT_LIQ, RECV)
    assert row == ("bybit", "BTCUSDT", 1739502302929, "short", "buy", 96176.5, 0.001,
                   96176.5 * 0.001, None, RECV)
    obj = json.loads(BYBIT_LIQ)
    obj["data"][0]["S"] = "Buy"
    (row,) = bybit_ws.parse_message(json.dumps(obj), RECV)
    assert row[3:5] == ("long", "sell")


def test_bybit_non_data_frames_return_no_rows():
    assert bybit_ws.parse_message('{"op":"pong","success":true,"ret_msg":"pong","conn_id":"x"}', RECV) == []
    assert bybit_ws.parse_message('{"success":true,"ret_msg":"","op":"subscribe","conn_id":"x"}', RECV) == []
    assert bybit_ws.parse_message('{"success":false,"ret_msg":"Invalid symbol","op":"subscribe"}', RECV) == []
    assert bybit_ws.parse_message("pong", RECV) == []


def test_bybit_subscribe_batches_of_ten():
    msgs = bybit_ws.subscribe_messages(bybit_ws.DEFAULT_SYMBOLS)
    assert len(msgs) == 2
    first, second = (json.loads(m) for m in msgs)
    assert first["op"] == "subscribe" and len(first["args"]) == 10 and len(second["args"]) == 2
    assert first["args"][0] == "allLiquidation.BTCUSDT"
    assert json.loads(bybit_ws.PING) == {"op": "ping"}


# ─── OKX ──────────────────────────────────────────────────────────────────────

INSTS = okx_ws.parse_instruments({"data": [
    {"instId": "MINA-USDT-SWAP", "ctVal": "1", "ctValCcy": "MINA", "ctMult": "1", "settleCcy": "USDT"},
    {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC", "ctMult": "1", "settleCcy": "USDT"},
    {"instId": "BTC-USD-SWAP", "ctVal": "100", "ctValCcy": "USD", "ctMult": "1", "settleCcy": "BTC"},
]})


def test_okx_contracts_times_ct_val():
    (row,) = okx_ws.parse_message(OKX_LIQ, INSTS, RECV)
    venue, sym, ts, pos, side, price, qty, notional, extra, recv = row
    assert (venue, sym, ts, pos, side, price) == ("okx", "MINA-USDT-SWAP", 1789678209470,
                                                  "short", "buy", 0.09859)
    assert qty == 10975.0 and notional == 10975.0 * 0.09859
    assert json.loads(extra)["sz_contracts"] == 10975.0 and json.loads(extra)["ct_val"] == 1.0
    obj = json.loads(OKX_LIQ)
    obj["data"][0]["instId"] = "BTC-USDT-SWAP"
    obj["data"][0]["details"][0].update({"bkPx": "50000", "sz": "10"})
    (row,) = okx_ws.parse_message(json.dumps(obj), INSTS, RECV)
    assert row[6] == 0.1 and row[7] == 5000.0                # 10 x 0.01 BTC


def test_okx_ct_mult_scales_face_value_on_both_branches():
    insts = okx_ws.parse_instruments({"data": [
        {"instId": "XYZ-USDT-SWAP", "ctVal": "10", "ctValCcy": "XYZ", "ctMult": "2", "settleCcy": "USDT"},
        {"instId": "XYZ-USD-SWAP", "ctVal": "10", "ctValCcy": "USD", "ctMult": "2", "settleCcy": "XYZ"},
        {"instId": "NOMULT-USDT-SWAP", "ctVal": "10", "ctValCcy": "NOMULT", "settleCcy": "USDT"},
    ]})
    assert insts["XYZ-USDT-SWAP"]["ct_mult"] == 2.0 and insts["NOMULT-USDT-SWAP"]["ct_mult"] == 1.0
    obj = json.loads(OKX_LIQ)
    obj["data"][0]["details"][0].update({"bkPx": "4", "sz": "3"})
    obj["data"][0]["instId"] = "XYZ-USDT-SWAP"                # linear: qty = sz x ctVal x ctMult
    (row,) = okx_ws.parse_message(json.dumps(obj), insts, RECV)
    assert row[6] == 60.0 and row[7] == 240.0 and json.loads(row[8])["ct_val"] == 20.0
    obj["data"][0]["instId"] = "XYZ-USD-SWAP"                 # inverse: notional = sz x ctVal x ctMult
    (row,) = okx_ws.parse_message(json.dumps(obj), insts, RECV)
    assert row[7] == 60.0 and row[6] == 15.0 and json.loads(row[8])["ct_val"] == 20.0
    obj["data"][0]["instId"] = "NOMULT-USDT-SWAP"             # absent ctMult defaults to 1
    (row,) = okx_ws.parse_message(json.dumps(obj), insts, RECV)
    assert row[6] == 30.0 and row[7] == 120.0


def test_okx_usd_margined_contract_notional_rule():
    obj = json.loads(OKX_LIQ)
    obj["data"][0]["instId"] = "BTC-USD-SWAP"
    obj["data"][0]["details"][0].update({"bkPx": "50000", "sz": "10", "posSide": "long", "side": "sell"})
    (row,) = okx_ws.parse_message(json.dumps(obj), INSTS, RECV)
    assert row[3:5] == ("long", "sell")
    assert row[7] == 1000.0                                  # 10 x 100 USD
    assert row[6] == 1000.0 / 50000


def test_okx_net_pos_side_derived_from_order_side():
    obj = json.loads(OKX_LIQ)
    obj["data"][0]["details"][0].update({"posSide": "net", "side": "sell"})
    (row,) = okx_ws.parse_message(json.dumps(obj), INSTS, RECV)
    assert row[3:5] == ("long", "sell")
    obj["data"][0]["details"][0].update({"posSide": "net", "side": "buy"})
    (row,) = okx_ws.parse_message(json.dumps(obj), INSTS, RECV)
    assert row[3:5] == ("short", "buy")


def test_okx_unknown_instrument_keeps_contracts_and_null_notional():
    (row,) = okx_ws.parse_message(OKX_LIQ, {}, RECV)
    assert row[6] == 10975.0 and row[7] is None
    assert json.loads(row[8])["ct_val"] is None


def test_okx_non_data_frames_and_instrument_rows():
    assert okx_ws.parse_message("pong", INSTS, RECV) == []
    assert okx_ws.parse_message('{"event":"subscribe","arg":{"channel":"liquidation-orders"}}', INSTS, RECV) == []
    assert okx_ws.parse_message('{"event":"error","code":"60012","msg":"x"}', INSTS, RECV) == []
    rows = okx_ws.instrument_rows(INSTS, 5)
    assert ("BTC-USD-SWAP", 100.0, "USD", 1.0, "BTC", 5) in rows and len(rows) == 3
    assert json.loads(okx_ws.SUBSCRIBE)["args"] == [{"channel": "liquidation-orders", "instType": "SWAP"}]
