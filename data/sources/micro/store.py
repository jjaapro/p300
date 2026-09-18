"""microstructure.db: path, schema, the batched Writer and the status file.

The database is separate from prod.db on purpose: no bot reads it, it is not
in backup.py, and it grows ~170-230 MB/day (~6 GB/month; estimated
2026-09-18 from the row sizes: depth_1s ~75 MB at ~440 B/row x 172,800
rows/day, hl_asset_ctx ~39, hl_positions 40-65, hl_accounts ~9, liquidations
10-40). Every table has an explicit primary key (repo rule) and a
`received_ms` column = the collector's wall clock when the row was built.
All timestamps are UTC epoch milliseconds.

depth_1s semantics: `bids`/`asks` are float32[30] blobs, bucket k = base
quantity resting [k x 0.1 %, (k+1) x 0.1 %) from mid on that side.
`bid_reach_pct` / `ask_reach_pct` = how far from the current mid (in %) the
book is exact on that side: the REST snapshot's farthest level there
(limit=1000 levels per side: ~0.1-0.5 % from mid on BTCUSDT, ~0.2-1 % on
ETHUSDT), 0 once price has left the snapshot range. A bucket whose range
lies within the reach is exact; beyond it the book holds just the levels the
diff stream has touched since the last snapshot, so those buckets are lower
bounds that fill in over minutes and are discarded at every resync
(book.py); a NaN bucket lies beyond the farthest level the book holds at all
("not covered", the first seconds after a resync) as opposed to a zero
("covered, no liquidity"). `in_sync` = 0 in a row written during a resync,
while the depth socket was down, or after > 5 s without a depth event; the
row is written anyway so the gap is visible.

Disk guard: collector.py sets `Writer.depth_paused` from the free space on
the database's drive (pause below PAUSE_FREE_BYTES, resume above
RESUME_FREE_BYTES); while paused `put()` discards depth_1s rows and counts
them in `dropped_disk`, every other table keeps recording.

`MICRO_DB` is read at call time so tests can monkeypatch it; `Writer` also
takes an explicit path (`collector.py --db`).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import shutil
import sqlite3
import time
from collections import deque
from pathlib import Path

from strategies.support import db as _db

log = logging.getLogger("micro.store")

MICRO_DB: Path = _db.DATA_DIR / "databases" / "microstructure.db"
STATUS_NAME = "collector_last.json"
BUSY_TIMEOUT_S = 5.0               # sqlite busy handler; waited on the writer thread, not the loop
PAUSE_FREE_BYTES = 5_000_000_000   # depth_1s pauses below this much free space on the DB's drive
RESUME_FREE_BYTES = 8_000_000_000  # ... and resumes above this (hysteresis)

DDL = [
    """CREATE TABLE IF NOT EXISTS liquidations (
        venue TEXT NOT NULL, symbol TEXT NOT NULL, ts_ms INTEGER NOT NULL,
        pos_side TEXT NOT NULL, order_side TEXT, price REAL NOT NULL, qty REAL NOT NULL,
        notional REAL, extra TEXT, received_ms INTEGER,
        PRIMARY KEY (venue, symbol, ts_ms, pos_side, price, qty))""",
    "CREATE INDEX IF NOT EXISTS ix_liquidations_ts ON liquidations(ts_ms)",
    """CREATE TABLE IF NOT EXISTS depth_1s (
        venue TEXT NOT NULL, symbol TEXT NOT NULL, ts_ms INTEGER NOT NULL,
        best_bid REAL, best_ask REAL, mid REAL, bids BLOB, asks BLOB,
        wall_bid_px REAL, wall_bid_qty REAL, wall_ask_px REAL, wall_ask_qty REAL,
        n_bid_levels INTEGER, n_ask_levels INTEGER, last_update_id INTEGER,
        in_sync INTEGER, bid_reach_pct REAL, ask_reach_pct REAL, received_ms INTEGER,
        PRIMARY KEY (venue, symbol, ts_ms))""",
    "CREATE INDEX IF NOT EXISTS ix_depth_1s_ts ON depth_1s(ts_ms)",
    """CREATE TABLE IF NOT EXISTS hl_asset_ctx (
        ts_ms INTEGER NOT NULL, coin TEXT NOT NULL, open_interest REAL, funding REAL,
        oracle_px REAL, mark_px REAL, mid_px REAL, premium REAL, day_ntl_vlm REAL,
        received_ms INTEGER,
        PRIMARY KEY (ts_ms, coin))""",
    """CREATE TABLE IF NOT EXISTS hl_leaderboard (
        snapshot_day TEXT NOT NULL, address TEXT NOT NULL, rank INTEGER,
        account_value REAL, day_pnl REAL, week_pnl REAL, month_pnl REAL, all_time_pnl REAL,
        received_ms INTEGER,
        PRIMARY KEY (snapshot_day, address))""",
    """CREATE TABLE IF NOT EXISTS hl_accounts (
        ts_ms INTEGER NOT NULL, address TEXT NOT NULL, account_value REAL,
        total_ntl_pos REAL, total_margin_used REAL, n_positions INTEGER, received_ms INTEGER,
        PRIMARY KEY (ts_ms, address))""",
    """CREATE TABLE IF NOT EXISTS hl_positions (
        ts_ms INTEGER NOT NULL, address TEXT NOT NULL, coin TEXT NOT NULL, szi REAL,
        entry_px REAL, position_value REAL, liquidation_px REAL, leverage_type TEXT,
        leverage_value REAL, unrealized_pnl REAL, margin_used REAL, received_ms INTEGER,
        PRIMARY KEY (ts_ms, address, coin))""",
    "CREATE INDEX IF NOT EXISTS ix_hl_positions_coin_ts ON hl_positions(coin, ts_ms)",
    """CREATE TABLE IF NOT EXISTS okx_instruments (
        inst_id TEXT PRIMARY KEY, ct_val REAL, ct_val_ccy TEXT, ct_mult REAL,
        settle_ccy TEXT, fetched_ms INTEGER)""",
    """CREATE TABLE IF NOT EXISTS collector_runs (
        started_ms INTEGER PRIMARY KEY, pid INTEGER, version TEXT, note TEXT)""",
]

# table -> INSERT statement with an explicit column list. okx_instruments is
# refreshed daily, so it replaces; everything else is append-only and dedupes
# on its primary key (a reconnect replays nothing twice).
INSERT_SQL: dict[str, str] = {
    "liquidations": (
        "INSERT OR IGNORE INTO liquidations (venue, symbol, ts_ms, pos_side, order_side, "
        "price, qty, notional, extra, received_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
    "depth_1s": (
        "INSERT OR IGNORE INTO depth_1s (venue, symbol, ts_ms, best_bid, best_ask, mid, "
        "bids, asks, wall_bid_px, wall_bid_qty, wall_ask_px, wall_ask_qty, n_bid_levels, "
        "n_ask_levels, last_update_id, in_sync, bid_reach_pct, ask_reach_pct, received_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
    "hl_asset_ctx": (
        "INSERT OR IGNORE INTO hl_asset_ctx (ts_ms, coin, open_interest, funding, oracle_px, "
        "mark_px, mid_px, premium, day_ntl_vlm, received_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
    "hl_leaderboard": (
        "INSERT OR IGNORE INTO hl_leaderboard (snapshot_day, address, rank, account_value, "
        "day_pnl, week_pnl, month_pnl, all_time_pnl, received_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
    "hl_accounts": (
        "INSERT OR IGNORE INTO hl_accounts (ts_ms, address, account_value, total_ntl_pos, "
        "total_margin_used, n_positions, received_ms) VALUES (?, ?, ?, ?, ?, ?, ?)"),
    "hl_positions": (
        "INSERT OR IGNORE INTO hl_positions (ts_ms, address, coin, szi, entry_px, position_value, "
        "liquidation_px, leverage_type, leverage_value, unrealized_pnl, margin_used, received_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
    "okx_instruments": (
        "INSERT OR REPLACE INTO okx_instruments (inst_id, ct_val, ct_val_ccy, ct_mult, "
        "settle_ccy, fetched_ms) VALUES (?, ?, ?, ?, ?, ?)"),
    "collector_runs": (
        "INSERT OR IGNORE INTO collector_runs (started_ms, pid, version, note) VALUES (?, ?, ?, ?)"),
}

DROP_FIRST_TABLE = "depth_1s"     # the one table whose rows are cheap to lose


def connect(path: Path | None = None, busy_timeout_s: float = BUSY_TIMEOUT_S) -> sqlite3.Connection:
    """Open (creating if needed) the microstructure DB in WAL mode with the
    schema applied. `check_same_thread=False` because the Writer uses the
    connection from exactly one thread at a time by construction: its
    worker thread during `run()`, the event-loop thread only before `run()`
    starts (the startup flush) and after it has returned (`close()`)."""
    p = Path(path) if path is not None else MICRO_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=busy_timeout_s, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    ensure_schema(con)
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    for ddl in DDL:
        con.execute(ddl)
    con.commit()


def db_bytes(path: Path | None = None) -> int:
    """Size of the DB file plus its -wal, 0 when absent."""
    p = Path(path) if path is not None else MICRO_DB
    total = 0
    for f in (p, p.with_name(p.name + "-wal")):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def free_bytes(path: Path | None = None) -> int | None:
    """Free space on the drive holding `path`; None when it cannot be read."""
    p = Path(path) if path is not None else MICRO_DB
    try:
        return shutil.disk_usage(p.resolve().anchor or ".").free
    except OSError:
        return None


def should_pause_depth(free: int | None, paused: bool) -> bool:
    """Hysteresis for the depth_1s pause: pause below PAUSE_FREE_BYTES,
    resume only once free space is above RESUME_FREE_BYTES. An unreadable
    free-space figure keeps the current state."""
    if free is None:
        return paused
    if paused:
        return free <= RESUME_FREE_BYTES
    return free < PAUSE_FREE_BYTES


def load_okx_instruments(con: sqlite3.Connection) -> dict[str, dict]:
    """The persisted OKX contract map (last daily refresh), in the shape of
    okx_ws.parse_instruments, so a restart never stores liquidations in
    contracts while the REST fetch is pending or failing."""
    return {inst_id: {"ct_val": ct_val, "ct_val_ccy": ct_val_ccy, "ct_mult": ct_mult or 1.0,
                      "settle_ccy": settle_ccy}
            for inst_id, ct_val, ct_val_ccy, ct_mult, settle_ccy in con.execute(
                "SELECT inst_id, ct_val, ct_val_ccy, ct_mult, settle_ccy FROM okx_instruments")}


class WriterBusy(Exception):
    """The flush hit SQLITE_BUSY / SQLITE_LOCKED: the batch was rolled back
    and is requeued for the next tick."""


def _is_busy(e: sqlite3.OperationalError) -> bool:
    code = getattr(e, "sqlite_errorcode", None)
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    msg = str(e).lower()
    return "locked" in msg or "busy" in msg


def _group(batch: list[tuple[str, tuple]]) -> dict[str, list[tuple]]:
    by_table: dict[str, list[tuple]] = {}
    for table, row in batch:
        by_table.setdefault(table, []).append(row)
    return by_table


class Writer:
    """Batched INSERT OR IGNORE writer fed by the socket handlers.

    `put(table, row)` is synchronous and never blocks: rows go into a bounded
    in-memory queue, and the `run()` task flushes it every `flush_interval_s`
    or as soon as `batch_rows` are waiting, in one transaction per flush. The
    SQL runs on one dedicated worker thread (so writes stay ordered and the
    sqlite busy wait never stalls the event loop); every queue mutation stays
    on the loop thread. A locked database (another process holding the write
    lock) requeues the batch at the head and retries next tick
    (`flush_retries`); any other error drops the batch (`flush_failures`,
    `lost_rows`, `last_flush_error`) — a permanent error must not wedge the
    queue.

    Two deques: depth_1s rows and everything else, so that when the queue is
    full "drop the oldest depth row first" is O(1) (`dropped`). While
    `depth_paused` (low disk) depth_1s rows are refused at `put()` and
    counted in `dropped_disk`.
    """

    def __init__(self, path: Path | None = None, *, max_queue: int = 200_000,
                 batch_rows: int = 2_000, flush_interval_s: float = 1.0,
                 busy_timeout_s: float = BUSY_TIMEOUT_S):
        self.path = Path(path) if path is not None else MICRO_DB
        self.max_queue = max_queue
        self.batch_rows = batch_rows
        self.flush_interval_s = flush_interval_s
        self.busy_timeout_s = busy_timeout_s
        self._depth: deque[tuple[str, tuple]] = deque()    # depth_1s: dropped first
        self._other: deque[tuple[str, tuple]] = deque()    # every other table
        self.inserted: dict[str, int] = {t: 0 for t in INSERT_SQL}
        self.dropped = 0            # queue overflow
        self.dropped_disk = 0       # depth rows refused while depth_paused
        self.depth_paused = False
        self.flushes = 0
        self.flush_failures = 0     # batches lost to a non-busy error
        self.flush_retries = 0      # batches requeued after a locked database
        self.lost_rows = 0
        self.last_flush_error = ""
        self.last_flush_ok_ms: int | None = None
        self._last_busy_log = 0.0
        self._con: sqlite3.Connection | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    # ─── lifecycle ───────────────────────────────────────────────────────────

    def open(self) -> None:
        if self._con is None:
            self._con = connect(self.path, self.busy_timeout_s)
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="micro-writer")

    @property
    def connection(self) -> sqlite3.Connection:
        """The open connection, for reads before `run()` starts (the OKX
        instrument seed)."""
        self.open()
        return self._con

    def load_okx_instruments(self) -> dict[str, dict]:
        return load_okx_instruments(self.connection)

    def close(self, attempts: int = 3) -> None:
        """Flush whatever is queued (retrying a locked database a few
        times), then close the connection and the worker."""
        for i in range(attempts):
            self.flush()
            if not len(self):
                break
            time.sleep(0.5 * (i + 1))
        left = len(self)
        if left:
            self.lost_rows += left
            self.last_flush_error = "database locked at close"
            log.error(f"{left} rows unwritten at close (database locked)")
            self._depth.clear()
            self._other.clear()
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
        if self._con is not None:
            self._con.close()
            self._con = None

    # ─── queue ───────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._depth) + len(self._other)

    @property
    def queue_len(self) -> int:
        return len(self)

    def queued(self) -> list[tuple[str, tuple]]:
        """Snapshot of the queue in flush order (tests / diagnostics)."""
        return list(self._other) + list(self._depth)

    def put(self, table: str, row: tuple) -> None:
        if table not in INSERT_SQL:
            raise KeyError(f"unknown table {table!r}")
        if table == DROP_FIRST_TABLE:
            if self.depth_paused:
                self.dropped_disk += 1
                return
            q = self._depth
        else:
            q = self._other
        if len(self) >= self.max_queue:
            self._drop_one()
        q.append((table, row))

    def put_many(self, table: str, rows: list[tuple]) -> int:
        for r in rows:
            self.put(table, r)
        return len(rows)

    def _drop_one(self) -> None:
        (self._depth if self._depth else self._other).popleft()
        self.dropped += 1

    def _pop_batch(self) -> list[tuple[str, tuple]]:
        batch = self.queued()
        self._other.clear()
        self._depth.clear()
        return batch

    def _requeue(self, batch: list[tuple[str, tuple]]) -> None:
        """Put a batch back at the head in its original order, then trim to
        max_queue (depth rows first, and they sit at the head)."""
        self._depth.extendleft(reversed([x for x in batch if x[0] == DROP_FIRST_TABLE]))
        self._other.extendleft(reversed([x for x in batch if x[0] != DROP_FIRST_TABLE]))
        while len(self) > self.max_queue:
            self._drop_one()

    # ─── flush ───────────────────────────────────────────────────────────────

    def _rollback(self) -> None:
        try:
            self._con.rollback()
        except sqlite3.Error:
            pass

    def _write(self, by_table: dict[str, list[tuple]]) -> tuple[dict[str, int], int, str]:
        """The SQL half of a flush, in one transaction. Returns (rows
        inserted per table, rows lost, error). Raises WriterBusy when the
        database is locked. Touches neither the queue nor the counters, so
        it can run on the worker thread."""
        con = self._con
        counts: dict[str, int] = {}
        try:
            for table, rows in by_table.items():
                before = con.total_changes
                con.executemany(INSERT_SQL[table], rows)
                counts[table] = con.total_changes - before
            con.commit()
            return counts, 0, ""
        except sqlite3.OperationalError as e:
            self._rollback()
            if _is_busy(e):
                raise WriterBusy(str(e)) from e
            n = sum(len(r) for r in by_table.values())
            log.error(f"flush failed ({n} rows lost): {e!r}")
            return {}, n, repr(e)
        except Exception as e:  # noqa: BLE001 — never wedge the readers
            self._rollback()
            n = sum(len(r) for r in by_table.values())
            log.error(f"flush failed ({n} rows lost): {e!r}")
            return {}, n, repr(e)

    def _on_busy(self, batch: list[tuple[str, tuple]], e: WriterBusy) -> None:
        self._requeue(batch)
        self.flush_retries += 1
        now = time.monotonic()
        if now - self._last_busy_log > 30:
            self._last_busy_log = now
            log.warning(f"flush deferred ({e}): {len(self)} rows queued, retrying next tick")

    def _on_written(self, result: tuple[dict[str, int], int, str]) -> int:
        counts, lost, err = result
        total = 0
        for table, n in counts.items():
            self.inserted[table] += n
            total += n
        self.flushes += 1
        if err:
            self.flush_failures += 1
            self.lost_rows += lost
            self.last_flush_error = err
        else:
            self.last_flush_ok_ms = int(time.time() * 1000)
        return total

    def flush(self) -> int:
        """Write everything queued in one transaction on the calling thread;
        returns rows inserted (ignored duplicates do not count). Used before
        `run()` starts, by `close()` and by tests."""
        if not len(self):
            return 0
        self.open()
        batch = self._pop_batch()
        try:
            result = self._write(_group(batch))
        except WriterBusy as e:
            self._on_busy(batch, e)
            return 0
        return self._on_written(result)

    async def flush_async(self) -> int:
        """Same as flush(), with the SQL on the worker thread."""
        if not len(self):
            return 0
        self.open()
        batch = self._pop_batch()
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._executor, self._write, _group(batch))
        except WriterBusy as e:
            self._on_busy(batch, e)
            return 0
        return self._on_written(result)

    async def run(self, stop) -> None:
        """Flush loop: every `flush_interval_s`, or sooner once `batch_rows`
        are queued. Drains the queue once `stop` is set. It does NOT close:
        producers may still finish after `stop` (a positions poll returning
        its partial pass), so the owner calls `close()` once they are all
        done — that final flush is what keeps their rows."""
        self.open()
        tick = min(0.1, self.flush_interval_s)
        try:
            while not stop.is_set():
                deadline = time.monotonic() + self.flush_interval_s
                while (time.monotonic() < deadline and len(self) < self.batch_rows
                       and not stop.is_set()):
                    await asyncio.sleep(tick)
                await self.flush_async()
        finally:
            await self.flush_async()

    def stats(self) -> dict:
        """Counters for the status file."""
        return {"queued": len(self), "flushes": self.flushes, "dropped": self.dropped,
                "dropped_disk": self.dropped_disk, "depth_paused": self.depth_paused,
                "flush_failures": self.flush_failures, "flush_retries": self.flush_retries,
                "lost_rows": self.lost_rows, "last_flush_error": self.last_flush_error,
                "last_flush_ok_ms": self.last_flush_ok_ms}


# ─── Status file ──────────────────────────────────────────────────────────────

def status_path() -> Path:
    return _db.DATA_DIR / "diagnostics" / STATUS_NAME


def write_status(payload: dict, path: Path | None = None) -> bool:
    """Atomic replace (tmp + os.replace, retried on the Windows
    PermissionError a concurrent reader causes). Never raises."""
    p = Path(path) if path is not None else status_path()
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, p)
                return True
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except Exception as e:  # noqa: BLE001
        log.warning(f"status file {p} not written: {e!r}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return False
