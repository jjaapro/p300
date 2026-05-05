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


def _trade_gross_pct(con: sqlite3.Connection, strategy: str,
                     trigger_date: str) -> float | None:
    """Return the GROSS realized PnL (= trade.pnl_usdt + sum of fees from
    the trade's adjustment events) for the most-recent closed trade matching
    (strategy, trigger_date), as a percentage of capital.

    Why gross: as of Step 5/7 the simulator emits gross window returns and
    fees are recorded on trade-event rows. The parity contract is therefore
    ``trade_gross == sim_gross`` rather than the older ``trade_net ==
    sim_net``. The trigger_date is the date the simulator attributes the
    contribution to (R4_BTC's same-day; R4_ETH's the Wed)."""
    rows = con.execute(
        "SELECT id, pnl_usdt, notes FROM trades "
        "WHERE strategy=? AND status='closed' "
        "ORDER BY actual_entry_time DESC",
        (strategy,),
    ).fetchall()
    for tid, pnl, notes in rows:
        if trigger_date in (notes or ""):
            fee_total = con.execute(
                "SELECT COALESCE(SUM(fee_usdt), 0) FROM trade_adjustments "
                "WHERE trade_id=?", (tid,),
            ).fetchone()[0]
            gross = float(pnl or 0.0) + float(fee_total or 0.0)
            return (gross / CAPITAL_USDT) * 100.0
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
            trade_pct = _trade_gross_pct(con, "JPLUS_R4_BTC", date_iso)
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
            trade_pct = _trade_gross_pct(con, "JPLUS_R4_ETH", date_iso)
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


# ─── ETH_DAILY parity ───────────────────────────────────────────────────────

@pytest.mark.slow
def test_eth_daily_parity_with_simulator(emitter_env):
    """For each closed ETH_DAILY trade, the trade's pnl_usdt must equal the
    sum of the simulator's eth_daily_contrib_1x_pct × lev × capital across
    the trade's open dates, within 5bp tolerance.

    The math: position notional N[d] = capital × weight[d] × lev[d] is
    daily-rebalanced. Total realized + final close P&L on the trade =
    sum_d N[d] × er[d] = sum_d sim's daily contribution. (See plan §
    'cumulative parity'.)"""
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
        trades_rows = con.execute(
            "SELECT id, actual_entry_time, actual_exit_time, pnl_usdt "
            "FROM trades WHERE strategy='JPLUS_ETH_DAILY' AND status='closed' "
            "ORDER BY actual_entry_time"
        ).fetchall()
    finally:
        con.close()

    assert trades_rows, "no ETH_DAILY trades closed during the window"

    diffs: list[tuple] = []
    for tid, ent, exit_iso, trade_pnl in trades_rows:
        # The trade was open from ent (start of date_open) to exit (start
        # of date_close). Simulator's contribution accrues on dates in
        # [date_open, date_close - 1] inclusive — i.e. all dates the
        # position was held overnight at least once. The first day's
        # contribution is from open_price → date_open close.
        date_open = datetime.fromisoformat(ent).date()
        date_close = datetime.fromisoformat(exit_iso).date()
        cum_sim_pct = 0.0
        cur = date_open
        while cur < date_close:
            d_iso = cur.isoformat()
            rec = series.get(d_iso)
            if rec is not None:
                cum_sim_pct += float(rec.get("eth_daily_contrib_1x_pct", 0.0)) \
                                * float(rec.get("lev", 1.0))
            cur = cur + (date_close - cur)  # advance — simpler one-shot below
            break
        # Linear iteration (clearer):
        cum_sim_pct = 0.0
        from datetime import timedelta
        cur = date_open
        while cur < date_close:
            d_iso = cur.isoformat()
            rec = series.get(d_iso)
            if rec is not None:
                cum_sim_pct += float(rec.get("eth_daily_contrib_1x_pct", 0.0)) \
                                * float(rec.get("lev", 1.0))
            cur = cur + timedelta(days=1)
        cum_sim_usdt = cum_sim_pct / 100.0 * CAPITAL_USDT
        # Tolerance: 5bp of capital ($5 on $10k). Daily-rebalanced
        # close-to-close model has small price-bar timing jitter at
        # rebalance moments because we read prior-day close from eth_1m
        # which can be up to a minute off the reference.
        if abs(float(trade_pnl) - cum_sim_usdt) > 5.0:
            diffs.append((tid, ent, exit_iso, trade_pnl, cum_sim_usdt))

    assert not diffs, f"ETH_DAILY cumulative parity mismatches (first 3): {diffs[:3]}"


@pytest.mark.slow
def test_eth_daily_only_active_in_bull(emitter_env):
    """ETH_DAILY trades must only have entry days in {strong_bull, mild_bull}
    regimes."""
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
            "SELECT id, actual_entry_time, regime FROM trades "
            "WHERE strategy='JPLUS_ETH_DAILY'"
        ).fetchall()
    finally:
        con.close()
    assert rows, "no ETH_DAILY trades emitted"
    for tid, ent, regime in rows:
        assert regime in ("strong_bull", "mild_bull"), \
            f"ETH_DAILY {tid} entered in non-bull regime {regime!r}"


# ─── EMA_BTC parity ─────────────────────────────────────────────────────────

@pytest.mark.slow
def test_ema_btc_parity_with_simulator(emitter_env):
    """EMA_BTC accounting parity: sum of all EMA_BTC trade pnl_usdt for the
    window must equal sum of simulator's ema_contrib_1x_pct × lev × capital
    over all dates in the window, within 5bp/capital. EMA_BTC is continuous
    (open every day after warmup); we aggregate across all closed trades
    plus any final open trade's unrealized."""
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

    # Sum simulator contributions for any date covered by an EMA_BTC trade.
    sim_total_usdt = 0.0
    con = sqlite3.connect(str(emitter_env))
    try:
        ema_trades = con.execute(
            "SELECT id, actual_entry_time, actual_exit_time, status, "
            "       pnl_usdt, current_qty, avg_entry_price, current_leverage, "
            "       direction, realized_pnl_usdt "
            "FROM trades WHERE strategy='JPLUS_EMA_BTC' "
            "ORDER BY actual_entry_time"
        ).fetchall()
    finally:
        con.close()

    assert ema_trades, "no EMA_BTC trades emitted"

    # Figure out total dates covered: from first OPEN to either last CLOSE
    # (if all closed) or the last sim date (open trade still active).
    first_entry = datetime.fromisoformat(ema_trades[0][1]).date()
    from datetime import timedelta
    last_sim = max(datetime.fromisoformat(d).date() for d in series.keys())
    cur = first_entry
    while cur <= last_sim:
        rec = series.get(cur.isoformat())
        if rec is not None:
            sim_total_usdt += (float(rec.get("ema_contrib_1x_pct", 0.0))
                               * float(rec.get("lev", 1.0))) / 100.0 * CAPITAL_USDT
        cur = cur + timedelta(days=1)

    # Trade-derived total = sum of closed pnl_usdt (cumulative on closed
    # trades) + sum of (realized_pnl_usdt + final_unrealized) on open trades.
    trade_total = 0.0
    for (tid, ent, exit_iso, status, pnl, cur_qty, avg_e, cur_lev,
         direction, realized) in ema_trades:
        if status == "closed":
            trade_total += float(pnl or 0.0)
        else:
            # Open: prior realized from SCALE_DOWN events + MTM at last sim close.
            last_open = emitter._btc_day_open_price(
                (last_sim + timedelta(days=1)).isoformat())
            if last_open is None:
                last_open = emitter._btc_day_open_price(last_sim.isoformat())
            assert last_open is not None
            qty = float(cur_qty or 0.0)
            basis = float(avg_e or 0.0)
            move = last_open - basis
            if (direction or "").upper() == "SHORT":
                move = -move
            trade_total += float(realized or 0.0) + qty * move

    diff = trade_total - sim_total_usdt
    # 5bp on capital tolerance — small price-bar timing jitter at rebalance.
    assert abs(diff) < 5.0, \
        f"EMA_BTC parity off: trade={trade_total:.4f} sim={sim_total_usdt:.4f} diff={diff:.4f}"


@pytest.mark.slow
def test_ema_btc_flip_on_weekly_cross(emitter_env):
    """When ema_p flips sign across consecutive days, the emitter must
    produce a FLIP event (closing the old trade and opening an opposite-
    direction one with parent_position_id linkage)."""
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

    # Find ema_p sign flips in the series
    prev_sign = None
    flip_dates: list[str] = []
    for d in sorted(series.keys()):
        ep = int(series[d].get("ema_p") or 0)
        sign = 1 if ep > 0 else (-1 if ep < 0 else 0)
        if prev_sign is not None and sign != 0 and prev_sign != 0 and sign != prev_sign:
            flip_dates.append(d)
        prev_sign = sign

    con = sqlite3.connect(str(emitter_env))
    try:
        flip_events = con.execute(
            "SELECT a.event_date, a.trade_id "
            "FROM trade_adjustments a JOIN trades t ON a.trade_id=t.id "
            "WHERE t.strategy='JPLUS_EMA_BTC' AND a.event_type='FLIP' "
            "ORDER BY a.event_date"
        ).fetchall()
        # Number of trades with a parent (= opened via flip)
        n_with_parent = con.execute(
            "SELECT COUNT(*) FROM trades "
            "WHERE strategy='JPLUS_EMA_BTC' AND parent_position_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        con.close()

    if flip_dates:
        # Each flip-date should have at most one FLIP event keyed to it.
        flip_event_dates = {ev[0] for ev in flip_events}
        for fd in flip_dates:
            assert fd in flip_event_dates, \
                f"expected FLIP event at {fd}; got dates {flip_event_dates}"
        assert n_with_parent == len(flip_dates), \
            f"expected {len(flip_dates)} flip-children, got {n_with_parent}"
