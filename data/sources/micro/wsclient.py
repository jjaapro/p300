"""Generic reconnecting websocket reader used by every stream in collector.py.

One `run_stream` task per stream: connect, send the subscribe messages, keep
an application-level ping going when the venue wants one (Bybit `{"op":
"ping"}`, OKX text `ping`), read frames and hand each to a synchronous
`on_message(text)` — the venue parser, which returns the number of rows it
enqueued. Any exception or close logs, bumps `reconnects`, sleeps the backoff
(1, 2, 4 ... 60 s; reset once a connection lived > 60 s) and reconnects.
`max_age_s` recycles a connection proactively (Binance drops sockets at 24 h).
Stops cleanly when the `stop` event is set.

Hooks: `on_connect()` runs once the socket is open, before the subscribe
messages; `on_disconnect()` runs whenever that session ends — reader
exception, venue close, the max-age recycle or stop — but not when
`connect()` itself failed (on_connect never ran). Both are best-effort: a
hook that raises is logged and never breaks the reconnect loop. The depth
handler uses them to mark its books out of sync while there is no socket.

`connect` is injectable so tests use a fake factory; the default is
`websockets.connect` with the protocol-level ping enabled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("micro.ws")

BACKOFF_MAX_S = 60.0
BACKOFF_RESET_AFTER_S = 60.0
MAX_SIZE = 8 * 1024 * 1024


def backoff_schedule(attempt: int) -> float:
    """1, 2, 4, 8 ... capped at BACKOFF_MAX_S. attempt 0 = first retry."""
    if attempt < 0:
        attempt = 0
    return float(min(BACKOFF_MAX_S, 2 ** attempt))


@dataclass
class StreamStatus:
    name: str
    state: str = "init"                # init | connecting | connected | backoff | stopped
    last_msg_ms: int | None = None
    msgs: int = 0
    rows: int = 0
    reconnects: int = 0
    errors: int = 0
    last_error: str = ""
    connected_ms: int | None = None
    extra: dict = field(default_factory=dict)

    def note_message(self, rows: int = 0) -> None:
        self.msgs += 1
        self.rows += int(rows or 0)
        self.last_msg_ms = int(time.time() * 1000)

    def as_dict(self, now_ms: int | None = None) -> dict:
        from datetime import datetime, timezone
        last = None
        if self.last_msg_ms is not None:
            last = datetime.fromtimestamp(self.last_msg_ms / 1000, timezone.utc).isoformat()
        return {"last_msg_utc": last, "msgs": self.msgs, "rows": self.rows,
                "reconnects": self.reconnects, "state": self.state,
                "errors": self.errors, "last_error": self.last_error, **self.extra}


def default_connect(url: str):
    import websockets
    # close_timeout: a venue that ignores the close handshake would otherwise
    # hold shutdown for the 10 s default (seen on the dry run).
    return websockets.connect(url, ping_interval=20, ping_timeout=20,
                              max_size=MAX_SIZE, open_timeout=20, close_timeout=3)


async def _wait_stop(stop: asyncio.Event, timeout: float) -> bool:
    """True when `stop` was set within `timeout` seconds."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _fire(hook, name: str, what: str) -> None:
    """Call a best-effort hook; a hook bug must not kill the stream."""
    if hook is None:
        return
    try:
        hook()
    except Exception as e:  # noqa: BLE001
        log.warning(f"[{name}] {what} hook failed: {e!r}")


async def _pinger(ws, ping: str, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        await ws.send(ping)


async def _reader(ws, on_message, status: StreamStatus) -> None:
    async for frame in ws:
        text = frame if isinstance(frame, str) else frame.decode("utf-8", "replace")
        try:
            n = on_message(text)
        except Exception as e:  # noqa: BLE001 — one bad frame must not drop the socket
            status.errors += 1
            status.last_error = repr(e)
            log.warning(f"[{status.name}] on_message failed: {e!r}")
            n = 0
        status.note_message(n)


async def _session(url, *, on_message, subscribe, ping, ping_interval_s, max_age_s,
                   status, stop, connect, on_connect, on_disconnect) -> None:
    async with connect(url) as ws:
        status.state = "connected"
        status.connected_ms = int(time.time() * 1000)
        try:
            _fire(on_connect, status.name, "on_connect")
            for msg in subscribe or []:
                await ws.send(msg)
            tasks = [asyncio.create_task(_reader(ws, on_message, status)),
                     asyncio.create_task(stop.wait())]
            if ping is not None:
                tasks.append(asyncio.create_task(_pinger(ws, ping, ping_interval_s or 20)))
            if max_age_s is not None:
                tasks.append(asyncio.create_task(asyncio.sleep(max_age_s)))
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            for t in done:
                t.result()                # re-raise the reader/pinger failure
        finally:
            # every way out of an opened session: exception, venue close,
            # max-age recycle, stop — the socket is gone either way
            _fire(on_disconnect, status.name, "on_disconnect")


async def run_stream(name: str, url: str, *, on_message, subscribe: list[str] | None = None,
                     ping: str | None = None, ping_interval_s: float | None = None,
                     max_age_s: float | None = None, status: StreamStatus, stop: asyncio.Event,
                     connect=None, on_connect=None, on_disconnect=None) -> None:
    connect = connect or default_connect
    attempt = 0
    while not stop.is_set():
        status.state = "connecting"
        t0 = time.monotonic()
        try:
            await _session(url, on_message=on_message, subscribe=subscribe, ping=ping,
                           ping_interval_s=ping_interval_s, max_age_s=max_age_s,
                           status=status, stop=stop, connect=connect, on_connect=on_connect,
                           on_disconnect=on_disconnect)
            if stop.is_set():
                break
            log.info(f"[{name}] connection closed (age {time.monotonic() - t0:.0f}s) — reconnecting")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            status.errors += 1
            status.last_error = repr(e)
            log.warning(f"[{name}] {e!r} after {time.monotonic() - t0:.0f}s")
        if stop.is_set():
            break
        if time.monotonic() - t0 > BACKOFF_RESET_AFTER_S:
            attempt = 0
        status.reconnects += 1
        status.state = "backoff"
        delay = backoff_schedule(attempt)
        attempt += 1
        if await _wait_stop(stop, delay):
            break
    status.state = "stopped"
