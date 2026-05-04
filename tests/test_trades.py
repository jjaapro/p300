"""services.trades — close mechanics for shadow trades."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services import clock, trades


# ─── compute_perp_close (pure function) ──────────────────────────────────────

def _entry_exit_dts(hours: int = 1):
    e = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    return e, e + timedelta(hours=hours)


def test_compute_long_pnl_increases_with_price_rise():
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=False,
    )
    # Price PnL: (110-100)*1 = 10. Cost: 100 * 10/10000 = 0.1. Net: 9.9.
    assert out.price_pnl_usdt == pytest.approx(10.0)
    assert out.cost_usdt == pytest.approx(0.1)
    assert out.cost_pct == pytest.approx(0.10)  # bp -> pct
    assert out.funding_pct == 0.0
    assert out.pnl_usdt == pytest.approx(9.9)
    assert out.pnl_pct == pytest.approx(9.9)  # 9.9 / 100 * 100


def test_compute_short_pnl_increases_with_price_drop():
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="SHORT", entry_price=100.0, exit_price=90.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=False,
    )
    # Price PnL: (100-90)*1 = 10. Cost: 0.1. Net: 9.9.
    assert out.price_pnl_usdt == pytest.approx(10.0)
    assert out.pnl_usdt == pytest.approx(9.9)


def test_compute_short_with_unfavorable_move():
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="SHORT", entry_price=100.0, exit_price=110.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=False,
    )
    # SHORT loses when price rises: (100-110)*1 = -10, plus 0.1 fee = -10.1.
    assert out.price_pnl_usdt == pytest.approx(-10.0)
    assert out.pnl_usdt == pytest.approx(-10.1)


def test_compute_invalid_direction_raises():
    e_dt, x_dt = _entry_exit_dts()
    with pytest.raises(ValueError, match="LONG or SHORT"):
        trades.compute_perp_close(
            direction="DELTA_NEUTRAL", entry_price=100.0, exit_price=110.0,
            qty=1.0, size_usdt=100.0, asset="BTC",
            entry_dt=e_dt, exit_dt=x_dt, apply_funding=False,
        )


def test_compute_size_zero_returns_zero_pnl_pct_safely():
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        qty=0.0, size_usdt=0.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=False,
    )
    # Degenerate: qty 0 -> price PnL 0; size 0 -> pnl_pct fallback 0.
    assert out.pnl_pct == 0.0


def test_compute_applies_funding_when_requested(monkeypatch):
    """When apply_funding=True, the funding_pct from services.funding is
    included in pnl. We stub the funding module to avoid DB dependency."""
    from services import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a, **k: -1.5)
    e_dt, x_dt = _entry_exit_dts(hours=24)
    out = trades.compute_perp_close(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=True,
    )
    # Price PnL 10, cost 0.1, funding -1.5% on 100 = -1.5 USDT.
    # Net: 10 - 0.1 - 1.5 = 8.4.
    assert out.funding_pct == pytest.approx(-1.5)
    assert out.funding_usdt == pytest.approx(-1.5)
    assert out.pnl_usdt == pytest.approx(8.4)


def test_compute_funding_swallowed_on_typeerror(monkeypatch):
    """If funding lookup raises (e.g. malformed dates), funding_pct must
    fall back to 0.0 rather than propagating the exception."""
    from services import funding
    def _broken(*a, **k): raise TypeError("simulated bad date")
    monkeypatch.setattr(funding, "accrued_pct", _broken)
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=True,
    )
    assert out.funding_pct == 0.0
    assert out.pnl_usdt == pytest.approx(9.9)  # same as no-funding case


def test_compute_custom_cost_bp():
    """CARRY-style higher cost rate is configurable (though CARRY uses its
    own close path; this just validates the parameterization)."""
    e_dt, x_dt = _entry_exit_dts()
    out = trades.compute_perp_close(
        direction="LONG", entry_price=100.0, exit_price=110.0,
        qty=1.0, size_usdt=100.0, asset="BTC",
        entry_dt=e_dt, exit_dt=x_dt, apply_funding=False, cost_bp_rt=20.0,
    )
    assert out.cost_usdt == pytest.approx(0.2)
    assert out.cost_pct == pytest.approx(0.20)
    assert out.pnl_usdt == pytest.approx(9.8)


# ─── persist_close + close_perp_trade (DB-touching) ──────────────────────────

@pytest.fixture
def trades_db(tmp_path, monkeypatch):
    """Tmp dashboard.db with a single open trade. Patches services.trades.DASH_DB
    to point at it."""
    db = tmp_path / "dashboard.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE trades (
            id TEXT PRIMARY KEY, series TEXT, asset TEXT, direction TEXT,
            strategy TEXT, regime TEXT, allocation_pct REAL, leverage REAL,
            entry_time TEXT, exit_time TEXT, status TEXT, execution_mode TEXT,
            strategy_variant TEXT, actual_entry_time TEXT,
            actual_exit_time TEXT, entry_price REAL, exit_price REAL,
            size_usdt REAL, qty REAL, pnl_usdt REAL, pnl_pct REAL,
            resolution TEXT, order_ids TEXT, notes TEXT
        )
    """)
    con.execute("""
        INSERT INTO trades VALUES
        ('TX-1','SJ','BTC','LONG','TEST',NULL,5.0,1.0,
         '2024-01-01T00:00:00+00:00',NULL,'open','SHADOW',
         'test_variant','2024-01-01T00:00:00+00:00',NULL,
         100.0,NULL,1000.0,10.0,NULL,NULL,NULL,NULL,'')
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(trades, "DASH_DB", db)
    return db


def test_persist_close_writes_status_and_pnl(trades_db):
    row_before = trades.persist_close(
        "TX-1", exit_price=110.0, exit_time_iso="2024-01-01T01:00:00+00:00",
        pnl_usdt=99.0, pnl_pct=9.9, notes_suffix="\nTEST_EXIT: ok",
    )
    assert row_before is not None
    assert row_before["entry_price"] == 100.0
    con = sqlite3.connect(str(trades_db))
    r = con.execute(
        "SELECT status, exit_price, pnl_usdt, pnl_pct, resolution, notes "
        "FROM trades WHERE id='TX-1'"
    ).fetchone()
    con.close()
    assert r[0] == "closed"
    assert r[1] == 110.0
    assert r[2] == 99.0
    assert r[3] == 9.9
    assert r[4] == "filled_closed"
    assert "TEST_EXIT: ok" in r[5]


def test_persist_close_unknown_id_returns_none(trades_db):
    row = trades.persist_close(
        "NO-SUCH-ID", exit_price=110.0,
        exit_time_iso="2024-01-01T01:00:00+00:00",
        pnl_usdt=0.0, pnl_pct=0.0, notes_suffix="",
    )
    assert row is None


def test_close_perp_trade_end_to_end(trades_db, monkeypatch):
    """Drives the full close pipeline: read row, compute, persist, log."""
    from services import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a, **k: 0.0)
    clock.set_simulated_now(datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc))
    try:
        trades.close_perp_trade("TX-1", exit_price=110.0, reason="test_close",
                                 sleeve_name="UNIT", apply_funding=True)
    finally:
        clock.set_simulated_now(None)

    con = sqlite3.connect(str(trades_db))
    r = con.execute(
        "SELECT status, exit_price, pnl_usdt, notes FROM trades WHERE id='TX-1'"
    ).fetchone()
    con.close()
    assert r[0] == "closed"
    assert r[1] == 110.0
    # Fixture: LONG @100 -> 110, qty=10, size=1000 → price PnL 100, cost 1, net 99.
    assert r[2] == pytest.approx(99.0)
    assert "UNIT_EXIT: test_close" in r[3]


def test_close_perp_trade_unknown_id_silent(trades_db):
    """Unknown trade id is a no-op (matches legacy _close_X_shadow behavior)."""
    clock.set_simulated_now(datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc))
    try:
        trades.close_perp_trade("DOES-NOT-EXIST", exit_price=110.0,
                                 reason="x", sleeve_name="UNIT")
    finally:
        clock.set_simulated_now(None)
    # Existing trade unchanged.
    con = sqlite3.connect(str(trades_db))
    r = con.execute("SELECT status FROM trades WHERE id='TX-1'").fetchone()
    con.close()
    assert r[0] == "open"


def test_close_carry_trade_uses_funding_minus_fees(trades_db, monkeypatch):
    """CARRY: P&L = funding collected − cost_pct (no price PnL component)."""
    from services import funding
    monkeypatch.setattr(funding, "accrued_pct", lambda *a, **k: 0.50)
    clock.set_simulated_now(datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc))
    try:
        trades.close_carry_trade("TX-1", exit_price=110.0,
                                  reason="window_end", cost_pct=0.20)
    finally:
        clock.set_simulated_now(None)
    con = sqlite3.connect(str(trades_db))
    r = con.execute("SELECT pnl_usdt, pnl_pct, notes FROM trades "
                    "WHERE id='TX-1'").fetchone()
    con.close()
    # net_pct = 0.50 - 0.20 = 0.30%; pnl_usdt = 1000 * 0.30/100 = 3.0
    assert r[0] == pytest.approx(3.0)
    assert r[1] == pytest.approx(0.30)
    assert "CARRY_EXIT" in r[2]
    assert "funding=0.500%" in r[2]


def test_close_perp_trade_disabled_funding_omits_funding_in_notes(trades_db):
    """When apply_funding=False, the funding= field shouldn't appear in notes."""
    clock.set_simulated_now(datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc))
    try:
        trades.close_perp_trade("TX-1", exit_price=110.0, reason="t",
                                 sleeve_name="CPR", apply_funding=False)
    finally:
        clock.set_simulated_now(None)
    con = sqlite3.connect(str(trades_db))
    r = con.execute("SELECT notes FROM trades WHERE id='TX-1'").fetchone()
    con.close()
    assert "fees=10bp RT" in r[0]
    assert "funding=" not in r[0]


# ─── open_shadow_trade ───────────────────────────────────────────────────────

@pytest.fixture
def empty_trades_db(tmp_path, monkeypatch):
    """Tmp dashboard.db with the trades schema but no rows. For testing the
    open path."""
    db = tmp_path / "dashboard.db"
    con = sqlite3.connect(str(db))
    con.execute("""
        CREATE TABLE trades (
            id TEXT PRIMARY KEY, series TEXT, asset TEXT, direction TEXT,
            strategy TEXT, regime TEXT, allocation_pct REAL, leverage REAL,
            entry_time TEXT, exit_time TEXT, status TEXT, execution_mode TEXT,
            strategy_variant TEXT, actual_entry_time TEXT,
            actual_exit_time TEXT, entry_price REAL, exit_price REAL,
            size_usdt REAL, qty REAL, pnl_usdt REAL, pnl_pct REAL,
            resolution TEXT, order_ids TEXT, notes TEXT, created_at TEXT
        )
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(trades, "DASH_DB", db)
    return db


def _stub_paper_account_default(monkeypatch):
    """Make trade_db.get_config('paper_account_usdt') return a known value
    so the test doesn't depend on whatever's in dashboard.db's config table."""
    from services import trade_db
    monkeypatch.setattr(trade_db, "get_config",
                         lambda key: 10000.0 if key == "paper_account_usdt" else None)


def test_open_shadow_trade_inserts_row_with_expected_fields(empty_trades_db, monkeypatch):
    _stub_paper_account_default(monkeypatch)
    clock.set_simulated_now(datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    try:
        tid = trades.open_shadow_trade(
            variant={"id": "test_variant", "capital_usdt": 50000.0},
            sleeve_name="ADX",
            asset="BTC", direction="LONG",
            entry_price=70000.0, allocation_pct=15.0, leverage=5.0,
            reason={"trigger": "ADX_cross_up", "regime": "uncertain"},
        )
    finally:
        clock.set_simulated_now(None)
    assert tid == "SJ-0001"
    con = sqlite3.connect(str(empty_trades_db))
    r = con.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    cols = [c[1] for c in sqlite3.connect(str(empty_trades_db)).execute(
        "PRAGMA table_info(trades)").fetchall()]
    row = dict(zip(cols, r))
    # Sizing: 50_000 × 15% × 5x = 37,500 notional. qty = 37500 / 70000.
    assert row["size_usdt"] == pytest.approx(37500.0)
    assert row["qty"] == pytest.approx(37500.0 / 70000.0)
    assert row["asset"] == "BTC"
    assert row["direction"] == "LONG"
    assert row["strategy"] == "ADX"
    assert row["regime"] == "uncertain"
    assert row["status"] == "open"
    assert row["execution_mode"] == "SHADOW"
    assert row["strategy_variant"] == "test_variant"
    assert row["allocation_pct"] == 15.0
    assert row["leverage"] == 5.0
    # Open-ended trade: scheduled exit is the distant-future sentinel.
    assert row["exit_time"].startswith("2099")


def test_open_shadow_trade_with_scheduled_exit(empty_trades_db, monkeypatch):
    _stub_paper_account_default(monkeypatch)
    clock.set_simulated_now(datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    exit_dt = datetime(2024, 6, 15, 19, 30, tzinfo=timezone.utc)
    try:
        tid = trades.open_shadow_trade(
            variant={"id": "fomc_test", "capital_usdt": 10000.0},
            sleeve_name="FOMC",
            asset="BTC", direction="LONG",
            entry_price=68000.0, allocation_pct=5.0, leverage=10.0,
            reason={"phase": "T-10h"},
            scheduled_exit_dt=exit_dt,
            regime_value="T-10h",
        )
    finally:
        clock.set_simulated_now(None)
    con = sqlite3.connect(str(empty_trades_db))
    r = con.execute("SELECT exit_time, regime FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert r[0] == "2024-06-15T19:30:00+00:00"
    assert r[1] == "T-10h"  # regime_value override (not reason["regime"])


def test_open_shadow_trade_id_increments(empty_trades_db, monkeypatch):
    """Two opens get sequential SJ ids; third skips to SJ-0003."""
    _stub_paper_account_default(monkeypatch)
    clock.set_simulated_now(datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    try:
        ids = [
            trades.open_shadow_trade(
                variant={"id": "v", "capital_usdt": 10000.0},
                sleeve_name="ADX", asset="BTC", direction="LONG",
                entry_price=70000.0, allocation_pct=10.0, leverage=1.0,
                reason={},
            )
            for _ in range(3)
        ]
    finally:
        clock.set_simulated_now(None)
    assert ids == ["SJ-0001", "SJ-0002", "SJ-0003"]


def test_open_shadow_trade_zero_entry_price_yields_zero_qty(empty_trades_db, monkeypatch):
    """Defensive: bad price input must not crash with ZeroDivisionError."""
    _stub_paper_account_default(monkeypatch)
    clock.set_simulated_now(datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    try:
        tid = trades.open_shadow_trade(
            variant={"id": "v", "capital_usdt": 10000.0},
            sleeve_name="ADX", asset="BTC", direction="LONG",
            entry_price=0.0, allocation_pct=10.0, leverage=1.0,
            reason={},
        )
    finally:
        clock.set_simulated_now(None)
    con = sqlite3.connect(str(empty_trades_db))
    r = con.execute("SELECT qty, size_usdt FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    assert r[0] == 0.0
    assert r[1] == pytest.approx(1000.0)  # 10000 × 10% × 1


def test_open_shadow_trade_falls_back_to_paper_account_default(empty_trades_db, monkeypatch):
    """If variant has no capital_usdt, falls back to paper_account_usdt config."""
    _stub_paper_account_default(monkeypatch)
    clock.set_simulated_now(datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc))
    try:
        tid = trades.open_shadow_trade(
            variant={"id": "novalue"},  # no capital_usdt
            sleeve_name="ADX", asset="BTC", direction="LONG",
            entry_price=100.0, allocation_pct=10.0, leverage=1.0,
            reason={},
        )
    finally:
        clock.set_simulated_now(None)
    con = sqlite3.connect(str(empty_trades_db))
    r = con.execute("SELECT size_usdt FROM trades WHERE id=?", (tid,)).fetchone()
    con.close()
    # 10000 (stub default) × 10% × 1 = 1000.
    assert r[0] == pytest.approx(1000.0)
