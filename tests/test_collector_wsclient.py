"""data/sources/micro/wsclient.py — backoff schedule, reconnect after a fake
connection fails, the connect/disconnect hooks, clean stop — and collector.py:
its pure helpers, the housekeeping/heartbeat tick, the single-instance guard,
the exit code and end-to-end runs on fake sockets (no network, no prod.db)."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from data.sources.micro import wsclient

OKX_LIQ = ('{"arg":{"channel":"liquidation-orders","instType":"SWAP"},"data":[{"details":[{"bkLoss":"0",'
           '"bkPx":"0.09859","ccy":"","posSide":"short","side":"buy","sz":"10975","ts":"1789678209470"}],'
           '"instFamily":"MINA-USDT","instId":"MINA-USDT-SWAP","instType":"SWAP","uly":"MINA-USDT"}]}')


def _now_ms():
    return int(time.time() * 1000)


def test_backoff_schedule_doubles_and_caps_at_60():
    assert [wsclient.backoff_schedule(a) for a in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    assert wsclient.backoff_schedule(-3) == 1
    assert wsclient.backoff_schedule(50) == 60


class FakeWS:
    """Async-context websocket: yields `messages`, then either raises
    (`fail`), closes (`close`) — after `delay` seconds — or hangs until
    cancelled."""

    def __init__(self, messages, *, fail=False, close=False, delay=0.0, log=None):
        self.messages = list(messages)
        self.fail = fail
        self.close = close
        self.delay = delay
        self.sent = []
        self.log = log if log is not None else []

    async def __aenter__(self):
        self.log.append("open")
        return self

    async def __aexit__(self, *exc):
        self.log.append("close")
        return False

    async def send(self, msg):
        self.sent.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return self.messages.pop(0)
        if self.fail or self.close:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise ConnectionError("socket dropped")
            raise StopAsyncIteration
        await asyncio.sleep(3600)
        raise StopAsyncIteration


def _factory(connections: list, log: list, urls: list):
    def connect(url):
        urls.append(url)
        ws = connections.pop(0)
        ws.log = log
        return ws
    return connect


def _depth_frame(U, u, pu, b=(), a=(), sym="BTCUSDT"):
    return json.dumps({"stream": f"{sym.lower()}@depth@100ms", "data": {
        "e": "depthUpdate", "s": sym, "U": U, "u": u, "pu": pu,
        "b": [[str(p), str(q)] for p, q in b], "a": [[str(p), str(q)] for p, q in a]}})


# ─── wsclient ─────────────────────────────────────────────────────────────────

def test_reconnects_after_failure_and_resends_subscribe(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    log, urls, seen = [], [], []
    status = wsclient.StreamStatus("t")
    conns = [FakeWS(["a", "b"], fail=True), FakeWS(["c"], close=True), FakeWS(["d"])]
    made = list(conns)

    async def scenario():
        stop = asyncio.Event()

        def on_message(text):
            seen.append(text)
            if text == "d":
                stop.set()
            return 2

        await asyncio.wait_for(wsclient.run_stream(
            "t", "wss://x", on_message=on_message, subscribe=['{"op":"subscribe"}'],
            status=status, stop=stop, connect=_factory(conns, log, urls)), timeout=5)

    asyncio.run(scenario())
    assert seen == ["a", "b", "c", "d"]
    assert urls == ["wss://x"] * 3
    assert log == ["open", "close"] * 3                  # every session closed its socket
    assert status.reconnects == 2 and status.errors == 1
    assert "socket dropped" in status.last_error
    assert status.msgs == 4 and status.rows == 8
    assert status.state == "stopped"
    assert all(c.sent == ['{"op":"subscribe"}'] for c in made)   # re-subscribed each time


def test_subscribe_ping_and_on_connect_hooks(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    ws = FakeWS(["x"])
    events = []
    status = wsclient.StreamStatus("t")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(wsclient.run_stream(
            "t", "wss://x", on_message=lambda t: 0, subscribe=["s1", "s2"], ping="ping",
            ping_interval_s=0.05, status=status, stop=stop, connect=lambda url: ws,
            on_connect=lambda: events.append("connect"), on_disconnect=lambda: events.append("disc")))
        await asyncio.sleep(0.3)
        assert events == ["connect"]
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert ws.sent[:2] == ["s1", "s2"]
    assert ws.sent.count("ping") >= 3
    assert events == ["connect", "disc"]                  # stop ends the session: hook fires
    assert status.msgs == 1 and status.reconnects == 0


def test_on_disconnect_fires_once_per_opened_session(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    events, log, urls = [], [], []
    conns = [FakeWS(["a"], fail=True), FakeWS(["b"], close=True), FakeWS(["c"])]
    status = wsclient.StreamStatus("t")

    async def scenario():
        stop = asyncio.Event()

        def on_message(text):
            if text == "c":
                stop.set()
            return 0

        await asyncio.wait_for(wsclient.run_stream(
            "t", "wss://x", on_message=on_message, status=status, stop=stop,
            connect=_factory(conns, log, urls), on_connect=lambda: events.append("connect"),
            on_disconnect=lambda: events.append("disc")), timeout=5)

    asyncio.run(scenario())
    assert events == ["connect", "disc"] * 3              # exception, clean close, stop
    assert status.reconnects == 2


def test_on_disconnect_fires_on_max_age_recycle_and_a_raising_hook_is_harmless(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    events, log, urls = [], [], []
    conns = [FakeWS(["a"]), FakeWS(["b"])]
    status = wsclient.StreamStatus("t")

    def disc():
        events.append("disc")
        raise RuntimeError("hook bug")

    async def scenario():
        stop = asyncio.Event()

        def on_message(text):
            if text == "b":
                stop.set()
            return 0

        await asyncio.wait_for(wsclient.run_stream(
            "t", "wss://x", on_message=on_message, max_age_s=0.1, status=status, stop=stop,
            connect=_factory(conns, log, urls), on_disconnect=disc), timeout=5)

    asyncio.run(scenario())
    assert events == ["disc", "disc"] and len(urls) == 2
    assert status.errors == 0 and status.reconnects == 1  # the hook's exception never counted


def test_failed_open_does_not_fire_on_disconnect(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.01)
    events = []
    status = wsclient.StreamStatus("t")

    def connect(url):
        raise OSError("dns")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(wsclient.run_stream(
            "t", "wss://x", on_message=lambda t: 0, status=status, stop=stop, connect=connect,
            on_connect=lambda: events.append("connect"), on_disconnect=lambda: events.append("disc")))
        await asyncio.sleep(0.15)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert events == [] and status.errors >= 2 and "dns" in status.last_error


def test_stop_during_backoff_ends_loop_promptly(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 3600.0)
    status = wsclient.StreamStatus("t")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(wsclient.run_stream(
            "t", "wss://x", on_message=lambda t: 0, status=status, stop=stop,
            connect=lambda url: FakeWS([], fail=True)))
        await asyncio.sleep(0.2)
        assert status.state == "backoff"
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert status.state == "stopped" and status.reconnects == 1


def test_bad_on_message_does_not_drop_socket():
    status = wsclient.StreamStatus("t")

    def on_message(text):
        if text == "bad":
            raise ValueError("parser bug")
        return 1

    async def scenario():
        stop = asyncio.Event()
        ws = FakeWS(["bad", "ok"])
        task = asyncio.create_task(wsclient.run_stream(
            "t", "wss://x", on_message=on_message, status=status, stop=stop, connect=lambda url: ws))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert status.msgs == 2 and status.rows == 1 and status.errors == 1
    assert status.reconnects == 0


def test_max_age_recycles_connection(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    log, urls = [], []
    conns = [FakeWS(["a"]), FakeWS(["b"])]
    status = wsclient.StreamStatus("t")

    async def scenario():
        stop = asyncio.Event()

        def on_message(text):
            if text == "b":
                stop.set()
            return 0

        await asyncio.wait_for(wsclient.run_stream(
            "t", "wss://x", on_message=on_message, max_age_s=0.1, status=status, stop=stop,
            connect=_factory(conns, log, urls)), timeout=5)

    asyncio.run(scenario())
    assert len(urls) == 2 and status.errors == 0 and status.reconnects == 1


# ─── depth stream + handler through a socket outage ───────────────────────────

def _depth_handler(snap_id, snaps):
    from data.sources.micro import binance_ws

    def http_get(url):
        snaps.append(url)
        return {"lastUpdateId": snap_id[0], "bids": [["100", "1"]], "asks": [["101", "1"]]}

    return binance_ws.DepthHandler(("BTCUSDT",), http_get=http_get, min_gap_s=0)


def test_depth_rows_flag_out_of_sync_while_socket_is_down(monkeypatch):
    """Socket fails -> no REST during the backoff, samples carry in_sync=0;
    reconnect -> one snapshot, the chain resumes, samples say 1 again."""
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.3)
    snap_id, snaps = [100], []
    h = _depth_handler(snap_id, snaps)
    b = h.books["BTCUSDT"]
    ev1 = _depth_frame(99, 101, 98, b=[(99.5, 2)])
    ev2 = _depth_frame(102, 103, 101)
    conns = [FakeWS([ev1], fail=True, delay=0.25), FakeWS([ev2])]
    status = wsclient.StreamStatus("binance_depth")

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(wsclient.run_stream(
                     "binance_depth", "wss://x", on_message=h.on_message, on_connect=h.on_connect,
                     on_disconnect=h.on_disconnect, status=status, stop=stop,
                     connect=_factory(conns, [], []))),
                 asyncio.create_task(h.resync_loop(stop, poll_s=0.01))]
        await asyncio.sleep(0.12)
        assert h.connected and b.in_sync and b.last_update_id == 101 and len(snaps) == 1
        assert h.take_sample("BTCUSDT", now_ms=_now_ms())[15] == 1
        await asyncio.sleep(0.25)                            # socket failed at 0.25 s; backoff runs to 0.55 s
        assert status.state == "backoff" and not h.connected and b.need_snapshot and not b.in_sync
        row = h.take_sample("BTCUSDT", now_ms=_now_ms())
        assert row is not None and row[15] == 0              # written, flagged
        assert len(snaps) == 1                               # no REST during the outage
        snap_id[0] = 102
        await asyncio.sleep(0.35)
        assert h.connected and b.in_sync and b.last_update_id == 103 and len(snaps) == 2
        assert h.take_sample("BTCUSDT", now_ms=_now_ms())[15] == 1
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert status.reconnects == 1 and status.errors == 1


def test_depth_recycle_at_max_age_resyncs_cleanly(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.0)
    snap_id, snaps = [100], []
    h = _depth_handler(snap_id, snaps)
    b = h.books["BTCUSDT"]
    conns = [FakeWS([_depth_frame(99, 101, 98)]), FakeWS([_depth_frame(102, 103, 101)])]
    status = wsclient.StreamStatus("binance_depth")

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(wsclient.run_stream(
                     "binance_depth", "wss://x", on_message=h.on_message, on_connect=h.on_connect,
                     on_disconnect=h.on_disconnect, max_age_s=0.2, status=status, stop=stop,
                     connect=_factory(conns, [], []))),
                 asyncio.create_task(h.resync_loop(stop, poll_s=0.01))]
        await asyncio.sleep(0.1)
        assert b.in_sync and b.last_update_id == 101
        snap_id[0] = 102
        await asyncio.sleep(0.2)                             # recycled at 0.2 s; stop before the next one at 0.4 s
        assert b.in_sync and b.last_update_id == 103 and len(snaps) == 2
        assert b.resyncs == 3                                # connect, disconnect, connect
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert status.errors == 0 and status.reconnects == 1 and not conns


def test_depth_failed_open_never_touches_books_or_rest(monkeypatch):
    monkeypatch.setattr(wsclient, "backoff_schedule", lambda attempt: 0.01)
    snaps = []
    h = _depth_handler([100], snaps)
    status = wsclient.StreamStatus("binance_depth")

    async def scenario():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(wsclient.run_stream(
                     "binance_depth", "wss://x", on_message=h.on_message, on_connect=h.on_connect,
                     on_disconnect=h.on_disconnect, status=status, stop=stop,
                     connect=lambda url: (_ for _ in ()).throw(OSError("dns")))),
                 asyncio.create_task(h.resync_loop(stop, poll_s=0.01))]
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert h.books["BTCUSDT"].resyncs == 0 and h.snapshots == 0 and not h.connected


# ─── collector.py: pure helpers ───────────────────────────────────────────────

def test_collector_silent_streams_and_status_payload(tmp_path):
    import collector
    from data.sources.micro import binance_ws, store
    started = 1_000_000
    statuses = {n: wsclient.StreamStatus(n) for n in collector.STREAM_NAMES}
    # nothing yet: measured from start; depth allowance 30 s, others longer
    assert collector.silent_streams(statuses, started + 31_000, started) == ["binance_depth"]
    statuses["binance_depth"].last_msg_ms = started + 20_000
    assert collector.silent_streams(statuses, started + 31_000, started) == []
    statuses["binance_depth"].last_msg_ms = started + 5 * 60_000
    assert collector.silent_streams(statuses, started + 5 * 60_000 + 1, started) == ["hl_ctx"]
    w = store.Writer(tmp_path / "m.db")
    depth = binance_ws.DepthHandler(("BTCUSDT",), writer=w)
    payload = collector.build_status_payload(pid=1, started_ms=started, statuses=statuses, writer=w,
                                             db_path=tmp_path / "m.db", now_ms=started + 1000,
                                             depth=depth, extra={"status": "ok"})
    assert payload["schema"] == 1 and payload["db_bytes"] == 0 and payload["dropped_rows"] == 0
    assert set(payload["streams"]) == set(collector.STREAM_NAMES)
    assert payload["streams"]["binance_depth"]["last_msg_utc"].startswith("1970-01-01T00:21:40")
    assert payload["streams"]["hl_ctx"]["last_msg_utc"] is None
    assert payload["tables"]["depth_1s"] == 0
    assert payload["writer"]["flush_retries"] == 0 and payload["lost_rows"] == 0
    assert payload["depth_paused_low_disk"] is False and payload["depth"]["connected"] is False
    assert payload["status"] == "ok"


def test_fresh_heartbeat_age():
    import collector
    now = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

    def row(age_s, interval=60, name="collector", tick=None):
        return {"name": name, "interval_s": interval,
                "last_tick_utc": (now - timedelta(seconds=age_s)).isoformat() if tick is None else tick}

    assert collector.fresh_heartbeat_age([row(30)], now) == 30.0
    assert collector.fresh_heartbeat_age([row(119)], now) == 119.0
    assert collector.fresh_heartbeat_age([row(121)], now) is None                 # stale: a restart is fine
    assert collector.fresh_heartbeat_age([row(200, interval=300)], now) == 200.0
    assert collector.fresh_heartbeat_age([row(30, interval=None)], now) == 30.0   # falls back to 60 s
    assert collector.fresh_heartbeat_age([row(30, name="feed")], now) is None
    assert collector.fresh_heartbeat_age([row(0, tick="garbage")], now) is None   # unparsable: no instance
    assert collector.fresh_heartbeat_age([{"name": "collector", "last_tick_utc": None}], now) is None
    assert collector.fresh_heartbeat_age([], now) is None


def test_derive_status_and_writer_notes(tmp_path):
    import collector
    from data.sources.micro import store
    assert collector.derive_status([], [], []) == ("ok", "")
    assert collector.derive_status([], ["binance_depth"], []) == ("degraded", "silent: binance_depth")
    assert collector.derive_status([], [], ["writer: +3 dropped"]) == ("degraded", "writer: +3 dropped")
    assert collector.derive_status([], ["hl_ctx"], ["", "disk_low: x"]) == ("degraded", "silent: hl_ctx; disk_low: x")
    assert collector.derive_status(["bybit_liq: RuntimeError('x')"], ["hl_ctx"], ["writer: +1 dropped"]) == (
        "error", "bybit_liq: RuntimeError('x')")
    w = store.Writer(tmp_path / "m.db")
    now = 1_000_000
    notes, prev = collector.writer_notes(w, (0, 0), now)
    assert notes == [] and prev == (0, 0)
    w.dropped, w.flush_failures = 3, 1
    w.last_flush_error = "OperationalError('disk I/O error')"
    notes, prev = collector.writer_notes(w, prev, now)
    assert notes == ["writer: +3 dropped, +1 flush failures (OperationalError('disk I/O error'))"]
    assert prev == (3, 1)
    assert collector.writer_notes(w, prev, now)[0] == []             # no growth since: no note
    w.put("liquidations", ("bybit", "BTCUSDT", 1, "long", "sell", 1.0, 1.0, 1.0, None, 1))
    w.last_flush_ok_ms = now - 61_000
    assert collector.writer_notes(w, prev, now)[0] == [
        "writer stalled: 1 rows queued, no flush for 61s (database locked?)"]
    w.last_flush_ok_ms = now - 10_000
    assert collector.writer_notes(w, prev, now)[0] == []
    assert collector.disk_note(4_100_000_000, True) == "disk_low: 4.1 GB free < 5 GB, depth_1s paused"
    assert collector.disk_note(4_100_000_000, False) == ""


# ─── collector.py: housekeeping tick ──────────────────────────────────────────

@pytest.mark.parametrize("case", ["ok", "degraded", "error", "hb_off", "hb_raises", "duplicate", "disk_low"])
def test_collector_housekeeping_tick(case, tmp_path, monkeypatch):
    import botlib
    import collector
    from data.sources.micro import store
    beats = []

    def fake_hb(name, **kw):
        beats.append((name, kw))
        if case == "hb_raises":
            raise RuntimeError("db locked")
        return case != "duplicate"

    monkeypatch.setattr(botlib, "heartbeat", fake_hb)
    monkeypatch.setattr(store, "status_path", lambda: tmp_path / "collector_last.json")
    now = int(time.time() * 1000)
    statuses = {n: wsclient.StreamStatus(n) for n in collector.STREAM_NAMES}
    for st in statuses.values():
        st.last_msg_ms = now
    errors = []
    if case in ("degraded", "error"):
        statuses["binance_depth"].last_msg_ms = now - 60_000
    if case == "error":
        errors.append("x")
    w = store.Writer(tmp_path / "m.db")
    stop = asyncio.Event()
    stop.set()                                                    # exactly one tick
    free = 1_000_000_000 if case == "disk_low" else 20_000_000_000
    asyncio.run(collector._housekeeping(
        stop=stop, statuses=statuses, writer=w, db_path=tmp_path / "m.db", started_ms=now,
        errors=errors, heartbeat=(case != "hb_off"), disk_free=lambda: free))
    payload = json.loads((tmp_path / "collector_last.json").read_text())
    assert payload["schema"] == 1 and set(payload["streams"]) == set(collector.STREAM_NAMES)
    assert payload["disk_free_bytes"] == free
    assert payload["depth_paused_low_disk"] is (case == "disk_low") and w.depth_paused is (case == "disk_low")
    assert payload["duplicate_instance"] is (case == "duplicate")
    expect = {"ok": ("ok", ""), "degraded": ("degraded", "silent: binance_depth"),
              "error": ("error", "x"), "hb_raises": ("ok", ""), "hb_off": ("ok", ""),
              "duplicate": ("ok", ""),
              "disk_low": ("degraded", "disk_low: 1.0 GB free < 5 GB, depth_1s paused")}[case]
    assert (payload["status"], payload["note"]) == expect
    if case == "hb_off":
        assert beats == []
        return
    name, kw = beats[0]
    assert len(beats) == 1 and name == "collector" and kw["interval_s"] == 60
    assert (kw["status"], kw["note"]) == expect


def test_collector_housekeeping_flags_writer_trouble_between_ticks(tmp_path, monkeypatch):
    import botlib
    import collector
    from data.sources.micro import store
    beats = []
    monkeypatch.setattr(botlib, "heartbeat", lambda name, **kw: beats.append(kw) or True)
    monkeypatch.setattr(store, "status_path", lambda: tmp_path / "collector_last.json")
    monkeypatch.setattr(collector, "STATUS_INTERVAL_S", 0.05)
    now = int(time.time() * 1000)
    statuses = {n: wsclient.StreamStatus(n) for n in collector.STREAM_NAMES}
    for st in statuses.values():
        st.last_msg_ms = now
    w = store.Writer(tmp_path / "m.db")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(collector._housekeeping(
            stop=stop, statuses=statuses, writer=w, db_path=tmp_path / "m.db", started_ms=now,
            errors=[], heartbeat=True, disk_free=lambda: 20_000_000_000))
        await asyncio.sleep(0.03)
        w.flush_failures += 1
        w.lost_rows += 7
        w.last_flush_error = "OperationalError('disk I/O error')"
        await asyncio.sleep(0.15)
        stop.set()
        await task

    asyncio.run(scenario())
    degraded = [kw for kw in beats if kw["status"] == "degraded"]
    assert degraded and degraded[0]["note"] == "writer: +1 flush failures (OperationalError('disk I/O error'))"
    assert beats[0]["status"] == "ok" and beats[-1]["status"] == "ok"   # a one-off degrades one tick


# ─── collector.py: argparse, guard, exit code ─────────────────────────────────

def test_collector_argparse_and_heartbeat_gate(monkeypatch):
    """--db / --no-heartbeat must keep botlib out of the picture entirely;
    the default path initialises the schema and runs the guard."""
    import botlib
    import collector
    calls = []
    monkeypatch.setattr(collector.asyncio, "run", lambda coro: (coro.close(), calls.append("run"), 0)[2])
    monkeypatch.setattr(collector.signal, "signal", lambda *a: None)   # keep pytest's handlers
    monkeypatch.setattr(botlib, "init_heartbeat_schema", lambda: calls.append("hb"))
    monkeypatch.setattr(botlib, "get_heartbeats", lambda: calls.append("rows") or [])
    assert collector.main(["--db", "x.db", "--once-seconds", "1"]) == 0
    assert collector.main(["--no-heartbeat"]) == 0
    assert calls == ["run", "run"]
    assert collector.main([]) == 0
    assert calls == ["run", "run", "hb", "rows", "run"]
    assert collector.main(["--force-start"]) == 0                        # guard skipped
    assert calls == ["run", "run", "hb", "rows", "run", "hb", "run"]
    with pytest.raises(SystemExit):
        collector.main(["--bogus"])


def test_collector_refuses_to_start_next_to_a_live_instance(monkeypatch):
    import botlib
    import collector
    runs = []
    monkeypatch.setattr(collector.asyncio, "run", lambda coro: (coro.close(), runs.append(1), 0)[2])
    monkeypatch.setattr(collector.signal, "signal", lambda *a: None)
    monkeypatch.setattr(botlib, "init_heartbeat_schema", lambda: None)
    rows = [{"name": "collector", "interval_s": 60,
             "last_tick_utc": (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()}]
    monkeypatch.setattr(botlib, "get_heartbeats", lambda: rows)
    assert collector.main([]) == 3 and runs == []
    assert collector.main(["--once-seconds", "5"]) == 3 and runs == []   # a smoke test doubles sockets too
    assert collector.main(["--force-start"]) == 0 and runs == [1]
    rows[0]["last_tick_utc"] = (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat()
    assert collector.main([]) == 0 and runs == [1, 1]                     # stale row: normal restart


def test_guarded_records_exceptions_and_propagates_cancel():
    import collector
    errors = []

    async def boom():
        raise RuntimeError("x")

    async def cancelled():
        raise asyncio.CancelledError()

    async def scenario():
        await collector._guarded(boom(), "bybit_liq", errors)
        with pytest.raises(asyncio.CancelledError):
            await collector._guarded(cancelled(), "s", errors)

    asyncio.run(scenario())
    assert errors == ["bybit_liq: RuntimeError('x')"]


def _stub_streams(monkeypatch, tmp_path, *, raising_stream=None):
    """Every socket and poller just awaits `stop` (one stream may raise);
    no network, status file under tmp_path."""
    import collector
    from data.sources.micro import binance_ws, hyperliquid, okx_ws, store

    async def wait_stop(*a, **k):
        stop = k.get("stop") or next(x for x in a if isinstance(x, asyncio.Event))
        await stop.wait()

    async def fake_stream(*a, **k):
        name = a[0] if a else k["name"]
        if name == raising_stream:
            raise RuntimeError("socket library exploded")
        await k["stop"].wait()

    async def wait_stop_method(self, *a, **k):
        await wait_stop(*a, **k)

    monkeypatch.setattr(wsclient, "run_stream", fake_stream)
    for fn in ("poll_ctx", "poll_leaderboard", "poll_positions"):
        monkeypatch.setattr(hyperliquid, fn, wait_stop)
    monkeypatch.setattr(binance_ws.DepthHandler, "resync_loop", wait_stop_method)
    monkeypatch.setattr(binance_ws.DepthHandler, "sampler", wait_stop_method)
    monkeypatch.setattr(okx_ws, "default_http_get", lambda url: (_ for _ in ()).throw(OSError("no network")))
    monkeypatch.setattr(collector.signal, "signal", lambda *a: None)
    monkeypatch.setattr(store, "status_path", lambda: tmp_path / "collector_last.json")


def test_collector_exit_code_reflects_dead_tasks(monkeypatch, tmp_path):
    import collector
    _stub_streams(monkeypatch, tmp_path, raising_stream="bybit_liq")
    assert collector.main(["--db", str(tmp_path / "m.db"), "--once-seconds", "1.0"]) == 1
    payload = json.loads((tmp_path / "collector_last.json").read_text())
    assert payload["status"] == "error" and "bybit_liq: RuntimeError" in payload["note"]
    _stub_streams(monkeypatch, tmp_path)
    assert collector.main(["--db", str(tmp_path / "m2.db"), "--once-seconds", "1.0"]) == 0
    payload = json.loads((tmp_path / "collector_last.json").read_text())
    assert payload["status"] == "ok"
    con = sqlite3.connect(str(tmp_path / "m2.db"))
    assert con.execute("SELECT COUNT(*) FROM collector_runs").fetchone()[0] == 1
    con.close()


# ─── collector.py: OKX instrument map before the socket ───────────────────────

def _fake_sockets_no_network(monkeypatch, tmp_path, okx_http_get, okx_messages):
    import collector
    from data.sources.micro import binance_ws, hyperliquid, okx_ws, store

    def boom(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(wsclient, "default_connect",
                        lambda url: FakeWS(list(okx_messages) if url == okx_ws.URL else []))
    monkeypatch.setattr(binance_ws, "default_http_get", boom)
    monkeypatch.setattr(hyperliquid, "default_http_get", boom)
    monkeypatch.setattr(hyperliquid, "default_http_post", boom)
    monkeypatch.setattr(okx_ws, "default_http_get", okx_http_get)
    monkeypatch.setattr(collector.signal, "signal", lambda *a: None)
    monkeypatch.setattr(store, "status_path", lambda: tmp_path / "collector_last.json")


def _okx_rows(db):
    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT qty, notional, extra FROM liquidations WHERE venue='okx'").fetchall()
    con.close()
    return rows


def test_okx_rows_use_the_persisted_instrument_map_when_rest_fails(monkeypatch, tmp_path):
    import collector
    from data.sources.micro import store
    db = tmp_path / "m.db"
    w = store.Writer(db)
    w.put("okx_instruments", ("MINA-USDT-SWAP", 1.0, "MINA", 1.0, "USDT", 1))
    w.flush()
    w.close()

    def failing_get(url):
        raise OSError("okx rest down")

    _fake_sockets_no_network(monkeypatch, tmp_path, failing_get, [OKX_LIQ])
    assert collector.main(["--db", str(db), "--once-seconds", "1.0"]) == 0
    rows = _okx_rows(db)
    assert len(rows) == 1 and rows[0][0] == 10975.0 and rows[0][1] == 10975.0 * 0.09859
    assert json.loads(rows[0][2])["ct_val"] == 1.0


def test_okx_stream_waits_for_the_first_instrument_fetch(monkeypatch, tmp_path):
    """First run ever (empty table): the liquidation arrives at connect,
    the instrument map 0.4 s later — the socket must wait for that attempt."""
    import collector
    db = tmp_path / "m.db"

    def slow_get(url):
        time.sleep(0.4)
        return {"data": [{"instId": "MINA-USDT-SWAP", "ctVal": "1", "ctValCcy": "MINA",
                          "ctMult": "1", "settleCcy": "USDT"}]}

    _fake_sockets_no_network(monkeypatch, tmp_path, slow_get, [OKX_LIQ])
    assert collector.main(["--db", str(db), "--once-seconds", "1.2"]) == 0
    rows = _okx_rows(db)
    assert len(rows) == 1 and rows[0][1] is not None and json.loads(rows[0][2])["ct_val"] == 1.0
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT ct_val FROM okx_instruments").fetchall() == [(1.0,)]
    con.close()
