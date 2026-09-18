"""Microstructure collector: a fleet unit that records public market data that
cannot be bought back later — liquidations (Binance, Bybit, OKX), 1-second
depth samples (Binance BTC/ETH) and Hyperliquid asset contexts, leaderboard
and whale positions — into its OWN database, data/databases/microstructure.db.

Research only: no bot reads it, backup.py does not copy it, and this process
never writes prod.db except the one `collector` heartbeat row that monitor.py
watches (same as feed.py's). Library code lives in data/sources/micro/.

Usage:
  python collector.py                    # run until Ctrl+C / SIGTERM
  python collector.py --once-seconds 90  # smoke test: run 90 s; exit 0, or 1 if any task died
  python collector.py --db PATH          # write elsewhere (no heartbeat, no instance guard)
  python collector.py --no-heartbeat     # never touch prod.db (no instance guard either)
  python collector.py --force-start      # skip the single-instance guard after a crash
  python collector.py --verbose          # DEBUG logging

Exit codes: 0 on a clean stop (--once-seconds elapsed, SIGINT/SIGTERM);
1 when a stream task died and was not restarted (the heartbeat says `error`
too); 3 when another collector's heartbeat is fresh (see below).

Single instance: with the heartbeat on, startup refuses to run while the
`collector` heartbeat row is younger than 2 x its interval (feed.py's guard;
`--force-start` / start_fleet.ps1 -ForceCollector skips it). --db and
--no-heartbeat runs are undetected by design — they never touch prod.db —
so run smoke tests with one of those while the fleet is up. If
botlib.heartbeat later reports another process writing the row, this one
logs it and keeps running; the row carries status `error` until it clears
and the dashboard shows DUPLICATE. The operator decides which to kill.

Status file: data/diagnostics/collector_last.json every 60 s (per-stream
last message, counts, reconnects; rows per table; writer counters; depth
book state; disk; DB size).
Heartbeat: every 60 s. `degraded` names the streams silent past their
allowance (empty markets are quiet, so the allowances are generous), writer
trouble (rows dropped, flush failures, a flush stalled on a locked database)
and the low-disk pause; `error` when a task died.
Disk: microstructure.db grows ~170-230 MB/day and is not backed up. Below
5 GB free on its drive the depth_1s rows are paused (resumed above 8 GB;
`depth_paused_low_disk` in the status file, heartbeat degraded); every
other table keeps recording.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

log = logging.getLogger("collector")

VERSION = "2026-09-18"
HEARTBEAT_INTERVAL_S = 60
STATUS_INTERVAL_S = 60
WRITER_STALL_S = 60                # queued rows and no successful flush for this long -> degraded
OKX_MAP_WAIT_S = 35                # first run: wait this long for one instrument-map attempt

# stream name -> seconds of silence tolerated before the heartbeat degrades
ALLOWANCE_S: dict[str, int] = {
    "binance_depth": 30,
    "binance_forceorder": 30 * 60,
    "bybit_liq": 60 * 60,
    "okx_liq": 30 * 60,
    "hl_ctx": 5 * 60,
    "hl_positions": 15 * 60,
}
STREAM_NAMES = tuple(ALLOWANCE_S) + ("hl_leaderboard",)

_stop_flag = threading.Event()


def _signal_handler(signum, frame):
    log.info(f"signal {signum} received — stopping")
    _stop_flag.set()


# ─── pure helpers (tested) ────────────────────────────────────────────────────

def silent_streams(statuses: dict, now_ms: int, started_ms: int) -> list[str]:
    """Names of streams whose last message (or, before any, the start) is
    older than their allowance."""
    out = []
    for name, allowance in ALLOWANCE_S.items():
        st = statuses.get(name)
        if st is None:
            continue
        ref = st.last_msg_ms if st.last_msg_ms is not None else started_ms
        if now_ms - ref > allowance * 1000:
            out.append(name)
    return out


def fresh_heartbeat_age(rows: list[dict], now: datetime, name: str = "collector") -> float | None:
    """Age in seconds of another live instance's heartbeat row — one younger
    than 2 x its interval — else None. Same rule as feed.py's guard: an
    unparsable last_tick_utc counts as no instance."""
    for row in rows:
        if row.get("name") != name:
            continue
        try:
            last = datetime.fromisoformat(row["last_tick_utc"])
            age = (now - last).total_seconds()
        except (TypeError, ValueError, KeyError):
            return None
        interval = row.get("interval_s") or HEARTBEAT_INTERVAL_S
        return age if age < 2 * interval else None
    return None


def writer_notes(writer, prev: tuple[int, int], now_ms: int) -> tuple[list[str], tuple[int, int]]:
    """Degraded-reasons from the writer since the previous tick: rows
    dropped, flush failures, a flush stalled on a locked database. Returns
    (notes, counters to pass as `prev` next tick)."""
    cur = (writer.dropped, writer.flush_failures)
    notes = []
    parts = []
    if cur[0] > prev[0]:
        parts.append(f"+{cur[0] - prev[0]} dropped")
    if cur[1] > prev[1]:
        parts.append(f"+{cur[1] - prev[1]} flush failures ({writer.last_flush_error})")
    if parts:
        notes.append("writer: " + ", ".join(parts))
    last_ok = writer.last_flush_ok_ms
    if len(writer) and last_ok is not None and now_ms - last_ok > WRITER_STALL_S * 1000:
        notes.append(f"writer stalled: {len(writer)} rows queued, no flush for "
                     f"{(now_ms - last_ok) / 1000:.0f}s (database locked?)")
    return notes, cur


def disk_note(free: int | None, paused: bool) -> str:
    if not paused:
        return ""
    from data.sources.micro import store
    gb = "?" if free is None else f"{free / 1e9:.1f}"
    return f"disk_low: {gb} GB free < {store.PAUSE_FREE_BYTES / 1e9:.0f} GB, depth_1s paused"


def derive_status(errors: list[str], silent: list[str], notes: list[str]) -> tuple[str, str]:
    """Heartbeat (status, note): a dead task wins; otherwise any silent
    stream, writer trouble or disk pause degrades; else ok."""
    if errors:
        return "error", "; ".join(errors)[:500]
    parts = []
    if silent:
        parts.append("silent: " + ",".join(silent))
    parts.extend(n for n in notes if n)
    if parts:
        return "degraded", "; ".join(parts)[:500]
    return "ok", ""


def build_status_payload(*, pid: int, started_ms: int, statuses: dict, writer,
                         db_path: Path, now_ms: int | None = None, depth=None,
                         extra: dict | None = None) -> dict:
    from data.sources.micro import store
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()  # noqa: E731
    payload = {
        "schema": 1,
        "pid": pid,
        "started_utc": iso(started_ms),
        "now_utc": iso(now_ms),
        "streams": {name: st.as_dict(now_ms) for name, st in statuses.items()},
        "tables": dict(writer.inserted),
        "dropped_rows": writer.dropped,
        "lost_rows": writer.lost_rows,
        "flush_failures": writer.flush_failures,
        "flush_retries": writer.flush_retries,
        "last_flush_error": writer.last_flush_error,
        "queued_rows": len(writer),
        "writer": writer.stats(),
        "depth_paused_low_disk": writer.depth_paused,
        "dropped_depth_low_disk": writer.dropped_disk,
        "db_bytes": store.db_bytes(db_path),
    }
    if depth is not None:
        payload["depth"] = depth.as_dict()
    if extra:
        payload.update(extra)
    return payload


# ─── tasks ────────────────────────────────────────────────────────────────────

async def _guarded(coro, name: str, errors: list[str]) -> None:
    """Run one task; an unhandled exception is recorded (heartbeat -> error,
    exit code 1) and logged, never silently lost."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        errors.append(f"{name}: {e!r}")
        log.exception(f"task {name} died: {e!r}")


async def _watch_stop(stop: asyncio.Event, once_seconds: float | None) -> None:
    deadline = None if once_seconds is None else time.monotonic() + once_seconds
    while not stop.is_set():
        if _stop_flag.is_set():
            stop.set()
            break
        if deadline is not None and time.monotonic() >= deadline:
            log.info(f"--once-seconds {once_seconds:g} elapsed — stopping")
            stop.set()
            break
        await asyncio.sleep(0.5)


async def _okx_instruments_loop(instruments: dict, writer, stop: asyncio.Event, http_get,
                                ready: asyncio.Event | None = None) -> None:
    """Fill the ctVal map at start (retrying every 60 s until it works),
    then refresh daily. `ready` is set after the first attempt, success or
    not, so the OKX socket waits for at most one request timeout."""
    from data.sources.micro import okx_ws
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        try:
            payload = await loop.run_in_executor(None, http_get, okx_ws.INSTRUMENTS_URL)
            fresh = okx_ws.parse_instruments(payload)
            if fresh:
                instruments.clear()
                instruments.update(fresh)
                writer.put_many("okx_instruments", okx_ws.instrument_rows(fresh, int(time.time() * 1000)))
                log.info(f"okx instruments: {len(fresh)} swaps")
                wait = okx_ws.INSTRUMENTS_REFRESH_S
            else:
                wait = 60
        except Exception as e:  # noqa: BLE001
            log.warning(f"okx instruments fetch failed (retry in 60s): {e!r}")
            wait = 60
        finally:
            if ready is not None and not ready.is_set():
                if not instruments:
                    log.warning("okx instrument map unavailable — liquidations will be stored "
                                "in contracts (repairable, see okx_ws.py) until it loads")
                ready.set()
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass


async def _okx_liq_stream(ready: asyncio.Event, **stream_kw) -> None:
    """The OKX socket starts only once the instrument map has been seeded
    (from the table) or tried once (first run ever), so liquidations are
    not stored in contracts for the first seconds after every start."""
    from data.sources.micro import wsclient
    if not ready.is_set():
        try:
            await asyncio.wait_for(ready.wait(), timeout=OKX_MAP_WAIT_S)
        except asyncio.TimeoutError:
            log.warning(f"okx instrument map still pending after {OKX_MAP_WAIT_S}s — "
                        "streaming anyway")
    await wsclient.run_stream(**stream_kw)


async def _housekeeping(*, stop: asyncio.Event, statuses: dict, writer, db_path: Path,
                        started_ms: int, errors: list[str], heartbeat: bool,
                        depth=None, disk_free=None) -> None:
    """Every 60 s: disk guard, heartbeat, status file."""
    from data.sources.micro import store
    botlib = None
    if heartbeat:
        import botlib as _botlib
        botlib = _botlib
    disk_free = disk_free or (lambda: store.free_bytes(db_path))
    pid = os.getpid()
    prev = (writer.dropped, writer.flush_failures)
    duplicate = False
    while True:
        now_ms = int(time.time() * 1000)
        free = disk_free()
        was_paused = writer.depth_paused
        writer.depth_paused = store.should_pause_depth(free, was_paused)
        if writer.depth_paused != was_paused:
            log.warning("depth_1s " + ("PAUSED: low disk" if writer.depth_paused else "resumed")
                        + (f" ({free / 1e9:.1f} GB free)" if free is not None else ""))
        notes, prev = writer_notes(writer, prev, now_ms)
        notes.append(disk_note(free, writer.depth_paused))
        silent = silent_streams(statuses, now_ms, started_ms)
        status, note = derive_status(errors, silent, notes)
        if botlib is not None:
            try:
                ok = botlib.heartbeat("collector", status=status, note=note,
                                      interval_s=HEARTBEAT_INTERVAL_S)
            except Exception as e:  # noqa: BLE001
                log.warning(f"heartbeat write failed: {e!r}")
            else:
                if ok is False and not duplicate:
                    # botlib forced status=error + a DUPLICATE INSTANCE note on
                    # the row; keep running and let the operator pick (dashboard
                    # shows DUPLICATE either way).
                    log.error("another process is writing the 'collector' heartbeat — "
                              "keeping this instance running; kill one")
                elif ok is not False and duplicate:
                    log.warning("duplicate collector heartbeat cleared")
                duplicate = ok is False
        payload = build_status_payload(
            pid=pid, started_ms=started_ms, statuses=statuses, writer=writer, db_path=db_path,
            now_ms=now_ms, depth=depth,
            extra={"status": status, "note": note, "disk_free_bytes": free,
                   "duplicate_instance": duplicate})
        store.write_status(payload)
        if status != "ok":
            log.warning(f"status {status}: {note}")
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=STATUS_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


async def run(args) -> int:
    from data.sources.micro import binance_ws, bybit_ws, hyperliquid, okx_ws, store, wsclient

    db_path = Path(args.db).resolve() if args.db else store.MICRO_DB
    heartbeat = not (args.db or args.no_heartbeat)
    started_ms = int(time.time() * 1000)
    stop = asyncio.Event()
    errors: list[str] = []

    writer = store.Writer(db_path)
    writer.open()
    writer.put("collector_runs", (started_ms, os.getpid(), VERSION, " ".join(sys.argv[1:])))
    writer.flush()
    log.info(f"microstructure db: {db_path} ({store.db_bytes(db_path) / 1e6:.1f} MB)")

    statuses = {name: wsclient.StreamStatus(name) for name in STREAM_NAMES}
    okx_instruments = writer.load_okx_instruments()     # last daily refresh, if any
    okx_ready = asyncio.Event()
    if okx_instruments:
        okx_ready.set()
    log.info(f"okx instruments: {len(okx_instruments)} from db")
    tracker = hyperliquid.Tracker()
    depth = binance_ws.DepthHandler(binance_ws.DEPTH_SYMBOLS, writer=writer)

    def on_force_order(text: str) -> int:
        return writer.put_many("liquidations", binance_ws.parse_force_order(text))

    def on_bybit(text: str) -> int:
        return writer.put_many("liquidations", bybit_ws.parse_message(text))

    def on_okx(text: str) -> int:
        return writer.put_many("liquidations", okx_ws.parse_message(text, okx_instruments))

    tasks = {
        "writer": writer.run(stop),
        "stop_watch": _watch_stop(stop, args.once_seconds),
        "binance_forceorder": wsclient.run_stream(
            "binance_forceorder", binance_ws.FORCE_ORDER_URL, on_message=on_force_order,
            max_age_s=binance_ws.MAX_AGE_S, status=statuses["binance_forceorder"], stop=stop),
        "binance_depth": wsclient.run_stream(
            "binance_depth", binance_ws.depth_url(), on_message=depth.on_message,
            on_connect=depth.on_connect, on_disconnect=depth.on_disconnect,
            max_age_s=binance_ws.MAX_AGE_S, status=statuses["binance_depth"], stop=stop),
        "binance_resync": depth.resync_loop(stop),
        "bybit_liq": wsclient.run_stream(
            "bybit_liq", bybit_ws.URL, on_message=on_bybit,
            subscribe=bybit_ws.subscribe_messages(), ping=bybit_ws.PING,
            ping_interval_s=bybit_ws.PING_INTERVAL_S, status=statuses["bybit_liq"], stop=stop),
        "okx_liq": _okx_liq_stream(
            okx_ready, name="okx_liq", url=okx_ws.URL, on_message=on_okx,
            subscribe=[okx_ws.SUBSCRIBE], ping=okx_ws.PING,
            ping_interval_s=okx_ws.PING_INTERVAL_S, status=statuses["okx_liq"], stop=stop),
        "okx_instruments": _okx_instruments_loop(okx_instruments, writer, stop,
                                                 okx_ws.default_http_get, okx_ready),
        "hl_ctx": hyperliquid.poll_ctx(writer, statuses["hl_ctx"], stop),
        "hl_leaderboard": hyperliquid.poll_leaderboard(tracker, writer, statuses["hl_leaderboard"], stop),
        "hl_positions": hyperliquid.poll_positions(tracker, writer, statuses["hl_positions"], stop),
        "housekeeping": _housekeeping(stop=stop, statuses=statuses, writer=writer, db_path=db_path,
                                      started_ms=started_ms, errors=errors, heartbeat=heartbeat,
                                      depth=depth),
    }
    for sym in binance_ws.DEPTH_SYMBOLS:
        tasks[f"sample_{sym}"] = depth.sampler(sym, stop, statuses["binance_depth"])

    log.info(f"collector starting: {len(tasks)} tasks, heartbeat={'on' if heartbeat else 'off'}")
    await asyncio.gather(*(_guarded(coro, name, errors) for name, coro in tasks.items()))
    writer.close()
    log.info("collector stopped: " + ", ".join(
        f"{t}:{n}" for t, n in writer.inserted.items() if n) +
        (f", dropped {writer.dropped}" if writer.dropped else "") +
        (f", dropped_disk {writer.dropped_disk}" if writer.dropped_disk else "") +
        (f", lost {writer.lost_rows} in {writer.flush_failures} failed flushes"
         if writer.lost_rows else "") +
        (f", {len(errors)} task(s) died" if errors else ""))
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P-300 microstructure collector")
    ap.add_argument("--once-seconds", type=float, default=None,
                    help="Run for N seconds then exit: 0 on a clean stop, 1 if any stream "
                         "task died and was not restarted (smoke test).")
    ap.add_argument("--db", default=None,
                    help="Database path override (implies --no-heartbeat).")
    ap.add_argument("--no-heartbeat", action="store_true",
                    help="Do not write the prod.db heartbeat row (also skips the "
                         "single-instance guard).")
    ap.add_argument("--force-start", action="store_true",
                    help="Skip the single-instance guard (use after a crash if the stale "
                         "heartbeat blocks a fast restart).")
    ap.add_argument("--verbose", action="store_true", help="DEBUG logging.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)

    if not (args.db or args.no_heartbeat):
        import botlib
        botlib.init_heartbeat_schema()
        # Single-instance guard (feed.py's): a second collector doubles every
        # socket and the shared Binance REST budget. Applies to --once-seconds
        # too — run smoke tests with --db or --no-heartbeat while the fleet is up.
        if not args.force_start:
            age = fresh_heartbeat_age(botlib.get_heartbeats(), datetime.now(timezone.utc))
            if age is not None:
                log.error(f"another collector appears alive (heartbeat {age:.0f}s ago) — "
                          f"refusing to start. Pass --force-start to override.")
                return 3

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
