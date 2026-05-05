"""Parity, idempotency, and basic-shape tests for the J+ trade emitter.

Step 2 of the trade-emitter migration covers R4_BTC and R4_ETH only —
the two cleanest sub-sleeves with discrete OPEN/CLOSE pairs. EMA_BTC and
ETH_DAILY emitters arrive in Steps 3 and 4. The tests here therefore
verify R4 parity in isolation (selecting only days where R4 fires and
no other sub-sleeve has emitted).

Parity contract: for any day where R4_BTC fires, the trade-derived
realized P&L (as a percentage of capital) must equal the simulator's
final R4_BTC contribution to the day's return (= ``r4_btc_contrib_1x_pct
× lev``) within 1bp. Same for R4_ETH using its own attribution fields.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import clock


CAPITAL_USDT = 10_000.0
PARITY_TOLERANCE_PCT = 0.01  # 1bp = 0.01%

# Wider window than test_jplus_lookahead so we hit several R4 firings
# within wk1-2 for both BTC and ETH.
PARITY_WINDOW_START = "2024-01-01"
PARITY_WINDOW_END = "2024-04-30"


@pytest.fixture
def emitter_env(tmp_path, monkeypatch):
    """Tmp dashboard.db with the canonical schema. Patches DASH_DB so the
    emitter writes to the fixture DB, not production. trader.db (which the
    simulator reads from) is NOT mocked — we exercise against real BTC/ETH
    bars."""
    fixture_db = tmp_path / "dashboard.db"
    from services import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    return fixture_db


def _variant() -> dict:
    return {"id": "test_emit_v1", "capital_usdt": CAPITAL_USDT}


def _trade_pnl_pct(con: sqlite3.Connection, strategy: str,
                   trigger_date: str) -> float | None:
    """Return the realized PnL of the most-recent closed trade for
    (strategy, trigger_date) as a percentage of capital. The trigger_date
    is the date the simulator attributes the contribution to (R4_BTC's
    same-day; R4_ETH's the Wed). For R4_ETH the entry calendar day is
    Tue but we look up by the trade's reason payload."""
    row = con.execute(
        "SELECT id, pnl_usdt, notes FROM trades "
        "WHERE strategy=? AND status='closed' "
        "ORDER BY actual_entry_time DESC",
        (strategy,),
    ).fetchall()
    for tid, pnl, notes in row:
        if trigger_date in (notes or ""):
            return (float(pnl or 0.0) / CAPITAL_USDT) * 100.0
    return None


# ─── Parity ─────────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_r4_btc_parity_with_simulator(emitter_env):
    """Replay several months and assert trade-derived R4_BTC P&L equals
    the simulator's R4_BTC contribution × vol-target lev within 1bp on
    every day R4_BTC fires."""
    from jplus import simulate as core_sim
    from services import jplus_trade_emitter as emitter

    clock.set_simulated_now(datetime(2024, 5, 1, tzinfo=timezone.utc))
    series = core_sim.simulate(start_date=PARITY_WINDOW_START,
                                end_date=PARITY_WINDOW_END)
    try:
        for date_iso in sorted(series.keys()):
            rec = series[date_iso]
            emitter.emit_for_date(_variant(), date_iso, rec)
    finally:
        clock.set_simulated_now(None)

    # Now check parity for every R4_BTC-fired day
    con = sqlite3.connect(str(emitter_env))
    try:
        diffs: list[tuple] = []
        n_fired = 0
        for date_iso in sorted(series.keys()):
            rec = series[date_iso]
            if not rec.get("r4_btc_fired"):
                continue
            sim_contrib_pct = (float(rec["r4_btc_contrib_1x_pct"])
                               * float(rec["lev"]))
            trade_pct = _trade_pnl_pct(con, "JPLUS_R4_BTC", date_iso)
            if trade_pct is None:
                # Acceptable in bear regime where weight = 0 and emitter
                # short-circuits; skip those days.
                if rec.get("mode") == "bear":
                    continue
                diffs.append((date_iso, "no_trade", sim_contrib_pct, None))
                continue
            n_fired += 1
            if abs(trade_pct - sim_contrib_pct) > PARITY_TOLERANCE_PCT:
                diffs.append((date_iso, "mismatch", sim_contrib_pct, trade_pct))
    finally:
        con.close()

    assert n_fired >= 5, f"need >= 5 R4_BTC firings to mean anything, got {n_fired}"
    assert not diffs, f"R4_BTC parity mismatches (first 5): {diffs[:5]}"


@pytest.mark.slow
def test_r4_eth_parity_with_simulator(emitter_env):
    """Same parity check for R4_ETH. Trade is keyed to Wed; OPEN event_date
    is the Tue. Simulator attributes contribution to Wed using Wed's params."""
    from jplus import simulate as core_sim
    from services import jplus_trade_emitter as emitter

    clock.set_simulated_now(datetime(2024, 5, 1, tzinfo=timezone.utc))
    series = core_sim.simulate(start_date=PARITY_WINDOW_START,
                                end_date=PARITY_WINDOW_END)
    try:
        for date_iso in sorted(series.keys()):
            rec = series[date_iso]
            emitter.emit_for_date(_variant(), date_iso, rec)
    finally:
        clock.set_simulated_now(None)

    con = sqlite3.connect(str(emitter_env))
    try:
        diffs: list[tuple] = []
        n_fired = 0
        for date_iso in sorted(series.keys()):
            rec = series[date_iso]
            if not rec.get("r4_eth_fired"):
                continue
            sim_contrib_pct = (float(rec["r4_eth_contrib_1x_pct"])
                               * float(rec["lev"]))
            trade_pct = _trade_pnl_pct(con, "JPLUS_R4_ETH", date_iso)
            if trade_pct is None:
                if rec.get("mode") == "bear":
                    continue
                diffs.append((date_iso, "no_trade", sim_contrib_pct, None))
                continue
            n_fired += 1
            if abs(trade_pct - sim_contrib_pct) > PARITY_TOLERANCE_PCT:
                diffs.append((date_iso, "mismatch", sim_contrib_pct, trade_pct))
    finally:
        con.close()

    assert n_fired >= 5, f"need >= 5 R4_ETH firings, got {n_fired}"
    assert not diffs, f"R4_ETH parity mismatches (first 5): {diffs[:5]}"


# ─── Idempotency ────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_emit_for_date_is_idempotent(emitter_env):
    """Calling emit_for_date twice for the same R4 fire date must not
    duplicate trade rows or adjustment rows."""
    from jplus import simulate as core_sim
    from services import jplus_trade_emitter as emitter

    clock.set_simulated_now(datetime(2024, 5, 1, tzinfo=timezone.utc))
    series = core_sim.simulate(start_date="2024-01-01",
                                end_date="2024-01-31")
    try:
        # Find a Mon/Wed wk1-2 day where R4_BTC fires
        target_day = next(
            (d for d, r in series.items()
             if r.get("r4_btc_fired") and r.get("mode") != "bear"),
            None,
        )
        assert target_day is not None, "no R4_BTC fire day in window"

        # First emit
        emitter.emit_for_date(_variant(), target_day, series[target_day])
        # Second emit — must be a no-op
        emitter.emit_for_date(_variant(), target_day, series[target_day])
    finally:
        clock.set_simulated_now(None)

    con = sqlite3.connect(str(emitter_env))
    try:
        n_trades = con.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy='JPLUS_R4_BTC'"
        ).fetchone()[0]
        n_open_evs = con.execute(
            "SELECT COUNT(*) FROM trade_adjustments a JOIN trades t ON a.trade_id=t.id "
            "WHERE t.strategy='JPLUS_R4_BTC' AND a.event_type='OPEN'"
        ).fetchone()[0]
        n_close_evs = con.execute(
            "SELECT COUNT(*) FROM trade_adjustments a JOIN trades t ON a.trade_id=t.id "
            "WHERE t.strategy='JPLUS_R4_BTC' AND a.event_type='CLOSE'"
        ).fetchone()[0]
    finally:
        con.close()

    assert n_trades == 1, f"expected exactly 1 trade row after double emit, got {n_trades}"
    assert n_open_evs == 1, f"expected 1 OPEN event, got {n_open_evs}"
    assert n_close_evs == 1, f"expected 1 CLOSE event, got {n_close_evs}"


# ─── Calendar correctness ───────────────────────────────────────────────────

@pytest.mark.slow
def test_r4_btc_only_fires_on_mon_wed_weeks_1_2(emitter_env):
    """R4_BTC trade rows must only have entry days that are Mon (weekday=0)
    or Wed (weekday=2) AND day-of-month <= 14."""
    from jplus import simulate as core_sim
    from services import jplus_trade_emitter as emitter

    clock.set_simulated_now(datetime(2024, 5, 1, tzinfo=timezone.utc))
    series = core_sim.simulate(start_date=PARITY_WINDOW_START,
                                end_date=PARITY_WINDOW_END)
    try:
        for date_iso in sorted(series.keys()):
            emitter.emit_for_date(_variant(), date_iso, series[date_iso])
    finally:
        clock.set_simulated_now(None)

    con = sqlite3.connect(str(emitter_env))
    try:
        rows = con.execute(
            "SELECT id, actual_entry_time FROM trades "
            "WHERE strategy='JPLUS_R4_BTC'"
        ).fetchall()
    finally:
        con.close()

    assert rows, "no R4_BTC trades emitted"
    for tid, ent in rows:
        dt = datetime.fromisoformat(ent)
        assert dt.weekday() in (0, 2), \
            f"R4_BTC {tid} entry {ent} not on Mon/Wed (weekday={dt.weekday()})"
        assert 1 <= dt.day <= 14, \
            f"R4_BTC {tid} entry {ent} day-of-month {dt.day} not in 1-14"
        assert dt.hour == 6, \
            f"R4_BTC {tid} entry hour {dt.hour} != 6"


@pytest.mark.slow
def test_r4_eth_only_fires_on_tue_with_wed_in_weeks_1_2(emitter_env):
    """R4_ETH OPEN must be on Tuesday (weekday=1) at 20:00 UTC, with the
    next-day Wed having day-of-month in 1-14."""
    from datetime import timedelta
    from jplus import simulate as core_sim
    from services import jplus_trade_emitter as emitter

    clock.set_simulated_now(datetime(2024, 5, 1, tzinfo=timezone.utc))
    series = core_sim.simulate(start_date=PARITY_WINDOW_START,
                                end_date=PARITY_WINDOW_END)
    try:
        for date_iso in sorted(series.keys()):
            emitter.emit_for_date(_variant(), date_iso, series[date_iso])
    finally:
        clock.set_simulated_now(None)

    con = sqlite3.connect(str(emitter_env))
    try:
        rows = con.execute(
            "SELECT id, actual_entry_time FROM trades "
            "WHERE strategy='JPLUS_R4_ETH'"
        ).fetchall()
    finally:
        con.close()

    assert rows, "no R4_ETH trades emitted"
    for tid, ent in rows:
        dt = datetime.fromisoformat(ent)
        assert dt.weekday() == 1, \
            f"R4_ETH {tid} entry {ent} not on Tue (weekday={dt.weekday()})"
        assert dt.hour == 20, \
            f"R4_ETH {tid} entry hour {dt.hour} != 20"
        wed = dt + timedelta(days=1)
        assert 1 <= wed.day <= 14, \
            f"R4_ETH {tid} Wed-day {wed.day} not in 1-14"
