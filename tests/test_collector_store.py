"""data/sources/micro/store.py (schema, Writer, status file, disk guard) plus
the fleet registration of the collector unit. Everything runs under
tmp_path; prod.db and the real microstructure.db are never touched."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from data.sources.micro import store

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def micro_db(tmp_path, monkeypatch):
    path = tmp_path / "microstructure.db"
    monkeypatch.setattr(store, "MICRO_DB", path)
    return path


def _liq(ts, price=100.0, qty=1.0):
    return ("bybit", "BTCUSDT", ts, "long", "sell", price, qty, price * qty, None, ts)


def _depth(ts, blob=b""):
    return ("binance", "BTCUSDT", ts, None, None, None, blob, blob, None, None,
            None, None, 0, 0, None, 0, None, None, ts)


def _pk(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})") if r[5]]


def _hold_write_lock(path):
    """A second connection holding the write lock (BEGIN IMMEDIATE + one write)."""
    other = sqlite3.connect(str(path), timeout=0.1)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO collector_runs VALUES (99, 1, 'lock', 'held')")
    return other


# ─── schema ───────────────────────────────────────────────────────────────────

def test_schema_created_under_tmp_path_with_explicit_pks(micro_db):
    con = store.connect()
    assert micro_db.exists()
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert _pk(con, "liquidations") == ["venue", "symbol", "ts_ms", "pos_side", "price", "qty"]
    assert _pk(con, "depth_1s") == ["venue", "symbol", "ts_ms"]
    assert _pk(con, "hl_asset_ctx") == ["ts_ms", "coin"]
    assert _pk(con, "hl_leaderboard") == ["snapshot_day", "address"]
    assert _pk(con, "hl_accounts") == ["ts_ms", "address"]
    assert _pk(con, "hl_positions") == ["ts_ms", "address", "coin"]
    assert _pk(con, "okx_instruments") == ["inst_id"]
    assert _pk(con, "collector_runs") == ["started_ms"]
    idx = {r[1] for r in con.execute("PRAGMA index_list(hl_positions)")}
    assert "ix_hl_positions_coin_ts" in idx
    depth_cols = [r[1] for r in con.execute("PRAGMA table_info(depth_1s)")]
    assert depth_cols[-4:] == ["in_sync", "bid_reach_pct", "ask_reach_pct", "received_ms"]
    for t, sql in store.INSERT_SQL.items():
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        named = [c.strip() for c in re.search(r"\(([^)]*)\) VALUES", sql).group(1).split(",")]
        assert set(named) <= cols, t                        # every INSERT column exists
        assert sql.count("?") == len(named), t               # placeholders match the column list
    con.close()


def test_insert_or_ignore_dedupes_on_pk(micro_db):
    w = store.Writer()
    w.put("liquidations", _liq(1))
    w.put("liquidations", _liq(1))          # exact duplicate
    w.put("liquidations", _liq(2))
    assert w.flush() == 2
    assert w.flush() == 0
    w.put("liquidations", _liq(1))          # replayed after a reconnect
    assert w.flush() == 0
    assert w.inserted["liquidations"] == 2
    assert w.last_flush_ok_ms is not None and w.flushes == 2
    w.close()
    con = sqlite3.connect(str(micro_db))
    assert con.execute("SELECT COUNT(*) FROM liquidations").fetchone()[0] == 2
    con.close()


def test_okx_instruments_replace_and_depth_row_round_trip(micro_db):
    w = store.Writer()
    w.put("okx_instruments", ("BTC-USDT-SWAP", 0.01, "BTC", 1.0, "USDT", 1))
    w.put("okx_instruments", ("BTC-USDT-SWAP", 0.02, "BTC", 1.0, "USDT", 2))
    arr = np.arange(30, dtype=np.float32)
    arr[25:] = np.nan
    w.put("depth_1s", ("binance", "BTCUSDT", 1000, 99.0, 101.0, 100.0, arr.tobytes(), arr.tobytes(),
                       99.0, 5.0, 101.0, 6.0, 1, 1, 42, 1, 2.45, 2.5, 1001))
    w.flush()
    w.close()
    con = sqlite3.connect(str(micro_db))
    assert con.execute("SELECT ct_val, fetched_ms FROM okx_instruments").fetchall() == [(0.02, 2)]
    blob, reach_b, reach_a, recv = con.execute(
        "SELECT bids, bid_reach_pct, ask_reach_pct, received_ms FROM depth_1s").fetchone()
    back = np.frombuffer(blob, dtype=np.float32)
    assert np.array_equal(back[:25], arr[:25]) and np.isnan(back[25:]).all()
    assert (reach_b, reach_a, recv) == (2.45, 2.5, 1001)
    con.close()


def test_load_okx_instruments_round_trips_the_persisted_map(micro_db):
    w = store.Writer()
    w.put("okx_instruments", ("BTC-USDT-SWAP", 0.01, "BTC", 1.0, "USDT", 1))
    w.put("okx_instruments", ("BTC-USD-SWAP", 100.0, "USD", None, "BTC", 1))
    w.flush()
    w.close()
    w2 = store.Writer()
    assert w2.load_okx_instruments() == {
        "BTC-USDT-SWAP": {"ct_val": 0.01, "ct_val_ccy": "BTC", "ct_mult": 1.0, "settle_ccy": "USDT"},
        "BTC-USD-SWAP": {"ct_val": 100.0, "ct_val_ccy": "USD", "ct_mult": 1.0, "settle_ccy": "BTC"},
    }
    w2.close()
    assert store.Writer(micro_db.with_name("empty.db")).load_okx_instruments() == {}


# ─── queue ────────────────────────────────────────────────────────────────────

def test_bounded_queue_drops_oldest_depth_first(micro_db):
    w = store.Writer(max_queue=3)
    w.put("liquidations", _liq(1))
    w.put("depth_1s", _depth(1000))
    w.put("liquidations", _liq(2))
    w.put("liquidations", _liq(3))          # full: the depth row goes, not the liquidation
    assert w.dropped == 1 and len(w) == 3
    assert [t for t, _ in w.queued()] == ["liquidations"] * 3
    w.put("liquidations", _liq(4))          # no depth left: oldest of anything
    assert w.dropped == 2 and w.queued()[0][1][2] == 2


def test_overflow_put_is_constant_time(micro_db):
    """Dropping the oldest depth row must not scan the queue: a wedged
    writer used to cost ~9 ms per put once depth rows sat at the back."""
    w = store.Writer(max_queue=50_000)
    for i in range(50_000):
        w.put("liquidations", _liq(i))
    t0 = time.perf_counter()
    for i in range(1000):
        w.put("liquidations", _liq(100_000 + i))
    dt = time.perf_counter() - t0
    assert w.dropped == 1000 and len(w) == 50_000
    assert dt < 0.5, f"1000 overflow puts took {dt:.3f}s"      # O(queue) took seconds


def test_depth_pause_drops_depth_rows_only_and_resumes(micro_db):
    w = store.Writer()
    w.depth_paused = True
    w.put("depth_1s", _depth(1000))
    w.put("liquidations", _liq(1))
    assert w.dropped_disk == 1 and w.dropped == 0 and [t for t, _ in w.queued()] == ["liquidations"]
    w.depth_paused = False
    w.put("depth_1s", _depth(2000))
    assert w.dropped_disk == 1 and len(w) == 2
    assert w.stats()["dropped_disk"] == 1 and w.stats()["depth_paused"] is False


def test_should_pause_depth_hysteresis():
    gb = 1_000_000_000
    assert store.should_pause_depth(4 * gb, False) is True
    assert store.should_pause_depth(6 * gb, False) is False       # above 5 GB: keep going
    assert store.should_pause_depth(6 * gb, True) is True         # paused stays paused below 8 GB
    assert store.should_pause_depth(8 * gb + 1, True) is False    # resumes above 8 GB
    assert store.should_pause_depth(None, True) is True           # unreadable: keep state
    assert store.should_pause_depth(None, False) is False
    assert store.free_bytes(Path.cwd()) > 0


# ─── flush: worker thread, locked database, failures ──────────────────────────

def test_locked_database_requeues_batch_in_order_and_retries(micro_db):
    w = store.Writer(busy_timeout_s=0.2)
    w.open()
    other = _hold_write_lock(micro_db)
    w.put("liquidations", _liq(1))
    w.put("depth_1s", _depth(1000))
    w.put("liquidations", _liq(2))
    t0 = time.monotonic()
    assert w.flush() == 0
    assert time.monotonic() - t0 < 2.0
    assert w.flush_retries == 1 and w.flush_failures == 0 and w.lost_rows == 0
    assert w.inserted["liquidations"] == 0
    assert [(t, r[2]) for t, r in w.queued()] == [("liquidations", 1), ("liquidations", 2), ("depth_1s", 1000)]
    w.put("liquidations", _liq(3))                          # new rows queue behind the requeued ones
    assert [r[2] for t, r in w.queued() if t == "liquidations"] == [1, 2, 3]
    other.commit()
    other.close()
    assert w.flush() == 4
    assert w.inserted == {**w.inserted, "liquidations": 3, "depth_1s": 1}
    w.close()
    con = sqlite3.connect(str(micro_db))
    assert con.execute("SELECT ts_ms FROM liquidations ORDER BY ts_ms").fetchall() == [(1,), (2,), (3,)]
    con.close()


def test_run_loop_stays_responsive_while_database_is_locked(micro_db):
    """The busy wait happens on the writer thread: the event loop must keep
    ticking while another process holds the write lock, and the rows land
    once it is released."""
    w = store.Writer(flush_interval_s=0.05, busy_timeout_s=0.5)
    w.open()
    gaps = []

    async def ticker(stop):
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    async def scenario():
        stop = asyncio.Event()
        other = _hold_write_lock(micro_db)
        tasks = [asyncio.create_task(w.run(stop)), asyncio.create_task(ticker(stop))]
        for i in range(20):
            w.put("liquidations", _liq(i))
        await asyncio.sleep(1.2)                            # two or three busy waits of 0.5 s
        assert w.inserted["liquidations"] == 0 and w.flush_retries >= 1
        other.commit()
        other.close()
        await asyncio.sleep(0.8)
        assert w.inserted["liquidations"] == 20
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    w.close()
    assert max(gaps) < 0.45, max(gaps)


def test_non_busy_error_drops_batch_counts_it_and_does_not_wedge(micro_db, monkeypatch):
    w = store.Writer()
    w.open()
    w.put("liquidations", _liq(1))
    w.put("liquidations", _liq(2))
    monkeypatch.setitem(store.INSERT_SQL, "liquidations", "INSERT INTO nope (x) VALUES (?)")
    assert w.flush() == 0
    assert w.flush_failures == 1 and w.lost_rows == 2 and len(w) == 0 and w.dropped == 0
    assert "OperationalError" in w.last_flush_error and w.flush_retries == 0
    monkeypatch.undo()
    w.put("liquidations", _liq(3))
    assert w.flush() == 1                                   # the next flush works
    w.close()


def test_run_loop_survives_a_non_sqlite_flush_error(micro_db, monkeypatch):
    w = store.Writer(flush_interval_s=0.05)

    class Bad:
        def __conform__(self, protocol):
            raise TypeError("not adaptable")

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        w.put("liquidations", _liq(1)[:8] + (Bad(), 1))      # sqlite3.InterfaceError / ProgrammingError
        await asyncio.sleep(0.3)
        assert not task.done() and w.flush_failures == 1 and w.lost_rows == 1
        w.put("liquidations", _liq(2))
        await asyncio.sleep(0.3)
        assert w.inserted["liquidations"] == 1
        stop.set()
        await task

    asyncio.run(scenario())
    w.close()


def test_run_loop_flushes_by_time_and_drains_on_stop(micro_db):
    w = store.Writer(flush_interval_s=0.05)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        w.put("liquidations", _liq(1))
        await asyncio.sleep(0.3)
        assert w.inserted["liquidations"] == 1        # flushed by the timer
        w.put("liquidations", _liq(2))
        stop.set()
        await task
        assert w.inserted["liquidations"] == 2        # drained on stop

    asyncio.run(scenario())
    w.close()
    con = sqlite3.connect(str(micro_db))
    assert con.execute("SELECT ts_ms FROM liquidations ORDER BY ts_ms").fetchall() == [(1,), (2,)]
    con.close()


def test_rows_queued_after_stop_are_kept_by_close(micro_db):
    """Producers can finish after stop (a positions poll returning its
    partial pass); the owner's close() must write them. The first dry run
    lost 1,073 hl rows this way."""
    w = store.Writer(flush_interval_s=0.05)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await task
        w.put("hl_accounts", (1, "0xa", 1.0, 0.0, 0.0, 0, 1))   # late producer
        w.close()

    asyncio.run(scenario())
    con = sqlite3.connect(str(micro_db))
    assert con.execute("SELECT address FROM hl_accounts").fetchall() == [("0xa",)]
    con.close()


def test_batch_flush_writes_at_batch_rows(micro_db):
    w = store.Writer(batch_rows=5, flush_interval_s=10.0)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(w.run(stop))
        for i in range(5):
            w.put("liquidations", _liq(i))
        await asyncio.sleep(0.3)
        assert w.inserted["liquidations"] == 5        # long before the 10 s timer
        stop.set()
        await task

    asyncio.run(scenario())
    w.close()


# ─── status file ──────────────────────────────────────────────────────────────

def test_status_written_atomically(tmp_path):
    path = tmp_path / "diag" / "collector_last.json"
    assert store.write_status({"schema": 1, "pid": 7}, path) is True
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema": 1, "pid": 7}
    assert list(path.parent.glob("*.tmp")) == []
    assert store.write_status({"schema": 1, "pid": 8}, path) is True
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == 8


def test_status_default_path_and_db_bytes(tmp_path, monkeypatch, micro_db):
    from strategies.support import db as _db
    monkeypatch.setattr(_db, "DATA_DIR", tmp_path)
    assert store.status_path() == tmp_path / "diagnostics" / "collector_last.json"
    assert store.db_bytes() == 0
    store.connect().close()
    assert store.db_bytes() > 0


# ─── registration ─────────────────────────────────────────────────────────────

def test_collector_registered_in_procscan_and_fleet_script():
    from dashboard import procscan, queries
    assert procscan.UNIT_SCRIPTS["collector"] == "collector.py"
    assert "collector" in queries.UNITS
    text = (REPO / "start_fleet.ps1").read_text(encoding="utf-8")
    assert 'collector     = @{ Script = "collector.py"' in text
    assert '"feed", "collector"' in text
    assert "[switch]$ForceCollector" in text and '$collectorArgs += "--force-start"' in text
    req = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert "websockets>=15.0" in req
    ops = (REPO / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "| collector | collector.py |" in ops


def test_importing_collector_has_no_side_effects(tmp_path):
    """Fresh interpreter with audit hooks: importing collector.py and the
    micro package must open no sqlite connection and write no file."""
    probe = r'''
import sys, os
hits = []
def hook(event, args):
    if event == "sqlite3.connect":
        hits.append("sqlite3.connect " + str(args[0]))
    if event == "open" and isinstance(args[1], str) and any(c in args[1] for c in "wax+"):
        hits.append("open-write " + str(args[0]))
sys.addaudithook(hook)
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import collector
import data.sources.micro.store, data.sources.micro.book, data.sources.micro.wsclient
import data.sources.micro.binance_ws, data.sources.micro.bybit_ws, data.sources.micro.okx_ws
import data.sources.micro.hyperliquid
print("HITS=" + repr(hits))
'''
    p = subprocess.run([sys.executable, "-c", probe, str(REPO)], capture_output=True, text=True,
                       cwd=str(tmp_path), env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                       timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "HITS=[]" in p.stdout, p.stdout
