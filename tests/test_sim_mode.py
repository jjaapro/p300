"""End-to-end test for ``run.py --mode sim``.

Verifies three properties that, together, anchor the sim mode contract:

1. **Fires the right trade at the right time** — running a sim window that
   crosses Mon 2024-06-03 06:00 UTC opens a single JPLUS_R4_BTC trade
   matching the live handler's behavior at that signal moment.
2. **Isolates writes** — the sim run must not touch the live
   ``data/dashboard.db``. Verified by checksumming the file before and
   after.
3. **Is idempotent** — re-running the same sim window over the same sim
   ledger does not duplicate trades. Same per-UTC-day idempotency that
   protects live operation from re-firing within an entry window.

Requires ``data/trader.db`` and ``data/dashboard.db`` to exist; tests
skip if they're absent (e.g. fresh CI checkout). The sim trader.db is
built once per module via ``studies/simulation/build_sim_trader_db.py`` to amortize
its ~5s cost.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from strategies import orchestrator
from strategies.support import sim_loop

REPO = Path(__file__).resolve().parent.parent
LIVE_TRADER_DB = REPO / "data" / "trader.db"
LIVE_DASH_DB = REPO / "data" / "dashboard.db"
LIVE_VARIANT = "p300_aggressive_v2_v1_0"


@pytest.fixture(scope="module")
def sim_trader_db(tmp_path_factory):
    """Build a sim trader.db slice via studies/simulation/build_sim_trader_db.py.
    1-week window + default 400d warmup so today_inputs() has enough
    history for the regime / vol-target / EMA warmup."""
    if not LIVE_TRADER_DB.exists():
        pytest.skip(f"requires {LIVE_TRADER_DB} (run bootstrap.py first)")
    out_dir = tmp_path_factory.mktemp("sim_trader")
    out = out_dir / "trader_sim.db"
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "build_sim_trader_db.py"),
         "--start", "2024-06-01", "--end", "2024-06-08",
         "--source", str(LIVE_TRADER_DB), "--output", str(out)],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if result.returncode != 0:
        pytest.fail(
            f"build_sim_trader_db.py failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return out


@pytest.fixture
def sim_dashboard_db(tmp_path):
    """Fresh dashboard sim DB with the live variant config copied across
    and trade-tables emptied. Per-test scope so tests start clean."""
    if not LIVE_DASH_DB.exists():
        pytest.skip(f"requires {LIVE_DASH_DB} (run register_p300.py first)")
    out = tmp_path / "sim_dash.db"
    shutil.copy(LIVE_DASH_DB, out)
    con = sqlite3.connect(str(out))
    try:
        con.execute("DELETE FROM trades")
        con.execute("DELETE FROM trade_adjustments")
        # variant_daily_returns may or may not exist depending on schema age
        try:
            con.execute("DELETE FROM variant_daily_returns")
        except sqlite3.OperationalError:
            pass
        con.commit()
    finally:
        con.close()
    return out


def _redirect_dbs(monkeypatch, trader_db: Path, dash_db: Path) -> None:
    """Mirror what run.py --mode sim does at startup."""
    from strategies.support import db as _db_mod
    monkeypatch.setattr(_db_mod, "TRADER_DB", trader_db.resolve())
    monkeypatch.setattr(_db_mod, "DASH_DB", dash_db.resolve())


def _trades(dash_db: Path) -> list[dict]:
    con = sqlite3.connect(str(dash_db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, strategy, asset, direction, entry_price, size_usdt, "
            "       leverage, actual_entry_time, status, exit_time "
            "FROM trades WHERE strategy_variant=? "
            "ORDER BY actual_entry_time, id",
            (LIVE_VARIANT,),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _md5(p: Path) -> str:
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ─── Tests ──────────────────────────────────────────────────────────────────


def test_sim_fires_r4_btc_on_known_monday(
    monkeypatch, sim_trader_db, sim_dashboard_db,
):
    """Mon 2024-06-03 is a wk1-2 Monday. R4_BTC must open a LONG at
    06:00 UTC scheduled to close 18:00 UTC same day. Tactical CARRY may
    also fire (always-on while funding regime is positive); we only
    assert on R4_BTC since that's the test's anchor point."""
    _redirect_dbs(monkeypatch, sim_trader_db, sim_dashboard_db)

    start = datetime(2024, 6, 3, 5, 59, 0, tzinfo=timezone.utc)
    end = datetime(2024, 6, 3, 6, 1, 0, tzinfo=timezone.utc)
    n = sim_loop.run_sim(start, end, 60,
                          lambda cur: orchestrator.tick())
    assert n == 3, f"expected 3 ticks (05:59, 06:00, 06:01), got {n}"

    rows = _trades(sim_dashboard_db)
    r4 = [t for t in rows if t["strategy"] == "JPLUS_R4_BTC"]
    assert len(r4) == 1, (
        f"expected 1 R4_BTC trade on 2024-06-03 06:00 UTC, "
        f"got {len(r4)}. All trades: {rows}"
    )
    t = r4[0]
    assert t["asset"] == "BTC"
    assert t["direction"] == "LONG"
    assert t["actual_entry_time"].startswith("2024-06-03T06:00"), t
    assert t["exit_time"].startswith("2024-06-03T18:00"), t
    assert t["status"] == "open"  # Won't close until simulated 18:00
    assert (t["entry_price"] or 0) > 0


def test_sim_does_not_touch_live_dashboard_db(
    monkeypatch, sim_trader_db, sim_dashboard_db,
):
    """The single most important sim invariant: live state must be
    untouched. MD5 the live data/dashboard.db before and after."""
    before = _md5(LIVE_DASH_DB)

    _redirect_dbs(monkeypatch, sim_trader_db, sim_dashboard_db)
    sim_loop.run_sim(
        datetime(2024, 6, 3, 5, 59, 0, tzinfo=timezone.utc),
        datetime(2024, 6, 3, 6, 5, 0, tzinfo=timezone.utc),
        60, lambda cur: orchestrator.tick(),
    )

    after = _md5(LIVE_DASH_DB)
    assert before == after, (
        "data/dashboard.db was modified by a sim run — DB redirection failed"
    )


def test_sim_and_backtest_runner_produce_identical_jplus_trades(
    monkeypatch, sim_trader_db, sim_dashboard_db,
):
    """Parity: ``run.py --mode sim`` and ``backtest_runner.py`` share the
    same dispatch (STRATEGY_DISPATCH) and the same clock primitive
    (strategies.support.sim_loop), so for the same window at the same tick
    cadence they must produce identical trades.

    Run both paths against the same sim dashboard.db at 1h ticks over a
    full Mon 06:00→18:00 UTC R4_BTC window. The live variant fires
    under orchestrator.tick (run.py path); a __replay_parity variant
    fires under backtest_runner.tick_replay_variant. Compare the
    JPLUS_R4_BTC trades by (asset, direction, entry_time, exit_time,
    entry_price, size_usdt) — these must match exactly.

    Tactical sleeves (CARRY, etc.) are not compared because their
    firing depends on tick granularity, not just the calendar / signal
    moment, so they could legitimately differ between paths in
    edge cases. R4_* sleeves fire only on signal moments, so they are
    the right anchor for a parity check."""
    _redirect_dbs(monkeypatch, sim_trader_db, sim_dashboard_db)

    # Full R4_BTC trade lifecycle: entry 06:00 → close 18:00 UTC.
    # 1h ticks so both paths see the same signal-moment ticks.
    start = datetime(2024, 6, 3, 6, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 6, 3, 18, 0, 0, tzinfo=timezone.utc)

    # run.py path — ticks 'p300_aggressive_v2_v1_0' (enabled=1).
    sim_loop.run_sim(start, end, 3600,
                      lambda cur: orchestrator.tick())

    # backtest_runner path — registers a __replay_parity variant
    # (enabled=0; never touched by orchestrator.tick) and runs it
    # scoped via tick_replay_variant.
    import io
    import contextlib
    import backtest_runner
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        backtest_runner.run(start=start, end=end, interval_hours=1,
                              reset=True, tag="parity")

    con = sqlite3.connect(str(sim_dashboard_db))
    con.row_factory = sqlite3.Row
    try:
        def _r4(variant: str) -> list[dict]:
            return [dict(r) for r in con.execute(
                "SELECT strategy, asset, direction, "
                "       actual_entry_time, exit_time, "
                "       ROUND(entry_price, 6) AS ep, "
                "       ROUND(size_usdt, 6) AS sz "
                "FROM trades WHERE strategy_variant=? AND strategy='JPLUS_R4_BTC' "
                "ORDER BY actual_entry_time",
                (variant,),
            ).fetchall()]
        live_r4 = _r4(LIVE_VARIANT)
        replay_r4 = _r4("p300_aggressive_v2_v1_0__replay_parity")
    finally:
        con.close()

    assert len(live_r4) == 1, f"live R4_BTC count != 1: {live_r4}"
    assert len(replay_r4) == 1, f"replay R4_BTC count != 1: {replay_r4}"
    assert live_r4[0] == replay_r4[0], (
        "JPLUS_R4_BTC trade differs between run.py --mode sim and "
        "backtest_runner — dispatch parity broken:\n"
        f"  run.py path = {live_r4[0]}\n"
        f"  backtest    = {replay_r4[0]}"
    )


def test_sim_run_is_idempotent_for_same_window(
    monkeypatch, sim_trader_db, sim_dashboard_db,
):
    """Re-running the same sim window must not duplicate trades. Per-UTC-
    day idempotency in r4._has_trade_for_day and tactical sleeves'
    own checks make this safe — used by sim-resume after a crash."""
    _redirect_dbs(monkeypatch, sim_trader_db, sim_dashboard_db)

    start = datetime(2024, 6, 3, 5, 59, 0, tzinfo=timezone.utc)
    end = datetime(2024, 6, 3, 6, 5, 0, tzinfo=timezone.utc)
    runner = lambda: sim_loop.run_sim(
        start, end, 60, lambda cur: orchestrator.tick()
    )

    runner()
    after_first = _trades(sim_dashboard_db)

    runner()
    after_second = _trades(sim_dashboard_db)

    assert len(after_first) == len(after_second), (
        f"sim was not idempotent: {len(after_first)} trades after first run, "
        f"{len(after_second)} after second. Diff: "
        f"{[t['id'] for t in after_second if t['id'] not in {x['id'] for x in after_first}]}"
    )
    # Trade IDs themselves must match — no fresh IDs minted on re-run.
    assert [t["id"] for t in after_first] == [t["id"] for t in after_second]
