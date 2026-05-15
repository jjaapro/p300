"""strategies.support.strategy_health — windowed portfolio + per-sleeve metrics.

Tests cover the pure-function primitives (Sharpe, MDD, expectancy,
profit factor, hold-time) plus a small DB-backed integration check for
``sleeve_metrics`` and ``build_report`` against a synthetic trades
fixture. The full ``format_report`` path is exercised indirectly — its
shape is asserted via a minimum-line-count check, not character-level
comparison, since the format is for humans not machines.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from strategies.support import strategy_health as sh


# ─── Pure metric primitives ─────────────────────────────────────────────────


def test_annualized_sharpe_short_series_returns_none():
    assert sh.annualized_sharpe([]) is None
    assert sh.annualized_sharpe([1.0]) is None


def test_annualized_sharpe_zero_variance_returns_none():
    """Constant-return series has zero std → Sharpe undefined, return None."""
    assert sh.annualized_sharpe([0.5, 0.5, 0.5, 0.5]) is None


def test_annualized_sharpe_positive_drift():
    """Mean +1%, std ~0.5% → Sharpe = 2.0 × √365 ≈ 38.2."""
    rets = [0.5, 1.0, 1.5, 1.0, 0.5, 1.0, 1.5]
    s = sh.annualized_sharpe(rets)
    assert s is not None
    assert s > 30  # ballpark; exact depends on sample variance


def test_annualized_sharpe_signs_with_mean():
    """Negative-drift series gives negative Sharpe."""
    rets = [-1.0, -0.5, -1.5, -1.0]
    s = sh.annualized_sharpe(rets)
    assert s is not None and s < 0


def test_max_drawdown_pct_empty_returns_none():
    assert sh.max_drawdown_pct([]) is None


def test_max_drawdown_pct_monotone_up_is_zero():
    assert sh.max_drawdown_pct([1.0, 2.0, 0.5, 3.0]) == 0.0


def test_max_drawdown_pct_simple_drop():
    """+10% then -20% on additive equity: eq 1 → 1.10 → 0.90.
    Peak 1.10, trough 0.90 → DD = (0.90 - 1.10) / 1.10 = -18.1818%.
    (Pre-2026-05-13 this compounded equity and reported -20.0%; the
    additive walk matches how trades_daily_returns sizes off fixed
    capital. See AUDIT_2026_05_13.)"""
    mdd = sh.max_drawdown_pct([10.0, -20.0])
    assert mdd is not None
    assert mdd == pytest.approx(-100.0 * 0.20 / 1.10, abs=1e-9)


def test_cumulative_return_pct():
    """Arithmetic-on-fixed-capital returns sum, they don't compound:
    +10% twice on fixed capital = +20% total realized PnL."""
    assert sh.cumulative_return_pct([10.0, 10.0]) == pytest.approx(20.0, abs=1e-9)
    assert sh.cumulative_return_pct([]) == 0.0


def test_max_drawdown_from_pnl_curve_basic():
    """Capital 100; trades [+20, -30, +10] → equity [120, 90, 100],
    peak 120, trough 90 → DD = -25%."""
    mdd = sh.max_drawdown_from_pnl_curve([20.0, -30.0, 10.0], 100.0)
    assert mdd == pytest.approx(-25.0, abs=1e-9)


def test_max_drawdown_from_pnl_curve_all_wins():
    assert sh.max_drawdown_from_pnl_curve([5.0, 10.0, 3.0], 100.0) == 0.0


def test_max_drawdown_from_pnl_curve_zero_capital():
    assert sh.max_drawdown_from_pnl_curve([5.0], 0.0) is None


def test_hold_hours_basic():
    h = sh._hold_hours("2024-01-01T00:00:00+00:00", "2024-01-02T12:00:00+00:00")
    assert h == 36.0


def test_hold_hours_naive_datetime_treated_as_utc():
    """Some upstream rows may have no tzinfo; helper should still work."""
    h = sh._hold_hours("2024-01-01T00:00:00", "2024-01-01T01:30:00")
    assert h == 1.5


def test_hold_hours_invalid_returns_none():
    assert sh._hold_hours("not-an-iso", "2024-01-01T00:00:00") is None


# ─── Window resolver ────────────────────────────────────────────────────────


def test_resolve_windows_default_end_date_is_yesterday(monkeypatch):
    """Default ``end_date`` should be yesterday by clock — the simulator
    doesn't emit today's row."""
    from strategies.support import clock as clock_mod
    fake_now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    clock_mod.set_simulated_now(fake_now)
    try:
        ws = sh.resolve_windows()
    finally:
        clock_mod.set_simulated_now(None)
    yesterday = "2026-07-14"
    assert ws[0].name == "YTD" and ws[0].start == "2026-01-01" and ws[0].end == yesterday
    assert ws[1].name == "90D" and ws[1].end == yesterday
    assert ws[2].name == "30D" and ws[2].end == yesterday
    # Window starts exactly N-1 days before end (so window length = N days inclusive).
    assert ws[1].start == "2026-04-16"  # 90 days inclusive ending 2026-07-14
    assert ws[2].start == "2026-06-15"  # 30 days inclusive ending 2026-07-14


def test_resolve_windows_explicit_end_date():
    ws = sh.resolve_windows(end_date=date(2025, 6, 30))
    assert ws[0].start == "2025-01-01"
    assert ws[0].end == "2025-06-30"
    assert ws[1].start == "2025-04-02"  # 90d back
    assert ws[2].start == "2025-06-01"  # 30d back


# ─── Formatter helpers ──────────────────────────────────────────────────────


def test_fmt_pf_handles_inf():
    assert sh._fmt_pf(float("inf")) == "inf"
    assert sh._fmt_pf(None) == "n/a"
    assert sh._fmt_pf(2.5) == "2.50"


def test_fmt_hold_switches_to_days_after_24h():
    assert sh._fmt_hold(12.5) == "12.5h"
    assert sh._fmt_hold(48.0) == "2.0d"
    assert sh._fmt_hold(None) == "n/a"


# ─── DB-backed integration ──────────────────────────────────────────────────


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    """Tmp dashboard.db with hand-rolled trades + variant_daily_returns
    rows for a synthetic variant. Patches DASH_DB so the strategy_health
    module reads from this fixture instead of production.

    Note (2026-05-13 audit): no production path writes to
    variant_daily_returns after the trade-emitter migration (Phase 3-5);
    the test still exercises the read-side helpers
    (portfolio_metrics with source='replay') and keeps the schema/code
    path live. If the read-side helpers themselves are retired, this
    fixture's variant_daily_returns block can follow."""
    fixture_db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    # variant_registry.init_schema() owns variant_daily_returns; we don't
    # want its module-import side effect, so create the minimal table inline.
    con = sqlite3.connect(str(fixture_db))
    con.execute("""
        CREATE TABLE IF NOT EXISTS variant_daily_returns (
            variant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            return_1x_pct REAL NOT NULL,
            source TEXT NOT NULL,
            regime TEXT,
            created_at TEXT,
            PRIMARY KEY (variant_id, date)
        )
    """)
    # Three closed trades on strategy ADX, exit times 2024-06-01..06-03.
    # P&L: +50, -20, +30 → win rate 2/3, total 60, expectancy 20,
    # profit factor (50+30)/20 = 4.0.
    for tid, entry, exit_, pnl in [
        ("SJ-1001", "2024-06-01T08:00:00+00:00",
                     "2024-06-01T16:00:00+00:00",  50.0),  # 8h hold
        ("SJ-1002", "2024-06-02T08:00:00+00:00",
                     "2024-06-02T16:00:00+00:00", -20.0),
        ("SJ-1003", "2024-06-03T08:00:00+00:00",
                     "2024-06-03T20:00:00+00:00",  30.0),  # 12h hold
    ]:
        con.execute("""
            INSERT INTO trades
            (id, series, asset, direction, strategy, allocation_pct, leverage,
             entry_time, exit_time, status, execution_mode, strategy_variant,
             actual_entry_time, actual_exit_time,
             entry_price, exit_price, size_usdt, qty, pnl_usdt, pnl_pct,
             current_qty, current_leverage, current_size_usdt, realized_pnl_usdt)
            VALUES (?, 'SJ', 'BTC', 'LONG', 'ADX', 5.0, 1.0,
                    ?, ?, 'closed', 'paper', 'syn_v',
                    ?, ?, 100.0, 110.0, 1000.0, 10.0, ?, ?,
                    0, 1.0, 0, ?)
        """, (tid, entry, exit_, entry, exit_, pnl, pnl/10.0, pnl))
    # Daily returns: 6 days, 4 up + 2 down for portfolio-level metrics.
    for d, r in [
        ("2024-06-01", 0.5), ("2024-06-02", -0.3),
        ("2024-06-03", 0.4), ("2024-06-04", 0.2),
        ("2024-06-05", -0.1), ("2024-06-06", 0.6),
    ]:
        con.execute(
            "INSERT INTO variant_daily_returns "
            "(variant_id, date, return_1x_pct, source, regime, created_at) "
            "VALUES ('syn_v', ?, ?, 'live_computed', 'uncertain', ?)",
            (d, r, d + "T00:00:00+00:00"),
        )
    con.commit()
    con.close()
    return fixture_db


def test_sleeve_metrics_basic_aggregations(synthetic_db):
    window = sh.Window("ALL", "2024-01-01", "2024-12-31")
    m = sh.sleeve_metrics("syn_v", "ADX", window, capital_usdt=10000.0)
    assert m.n_trades == 3
    assert m.total_pnl_usdt == pytest.approx(60.0)
    assert m.win_rate_pct == pytest.approx(200.0 / 3.0)  # 2/3
    assert m.expectancy_usdt == pytest.approx(20.0)
    # Profit factor = (50 + 30) / |-20| = 4.0
    assert m.profit_factor == pytest.approx(4.0)
    # Avg hold = (8 + 8 + 12) / 3 = 9.33h
    assert m.avg_hold_hours == pytest.approx(28.0 / 3.0)


def test_sleeve_metrics_no_losses_gives_inf_profit_factor(synthetic_db):
    """Filter to only the winning ADX trades by truncating the window."""
    # Only 2024-06-01 (+50) — single winning trade.
    window = sh.Window("D1", "2024-06-01", "2024-06-01")
    m = sh.sleeve_metrics("syn_v", "ADX", window, capital_usdt=10000.0)
    assert m.n_trades == 1
    assert math.isinf(m.profit_factor)


def test_sleeve_metrics_no_trades_returns_zeros(synthetic_db):
    """Strategy that never traded returns N=0 with all metrics None."""
    window = sh.Window("ALL", "2024-01-01", "2024-12-31")
    m = sh.sleeve_metrics("syn_v", "FOMC", window, capital_usdt=10000.0)
    assert m.n_trades == 0
    assert m.total_pnl_usdt == 0.0
    assert m.win_rate_pct is None
    assert m.expectancy_usdt is None
    assert m.profit_factor is None


def test_portfolio_metrics_raw_mode_no_capital(synthetic_db):
    """Without capital_usdt, portfolio_metrics reads VDR rows as-is. Used
    for replay-variant inspection where the stored values are already the
    final return."""
    window = sh.Window("ALL", "2024-01-01", "2024-12-31")
    p = sh.portfolio_metrics("syn_v", window)  # capital_usdt=None
    assert p.n_days == 6
    # 4 up days out of 6 = 66.67%.
    assert p.win_rate_pct == pytest.approx(400.0 / 6.0)
    assert p.sharpe is not None
    assert p.max_drawdown_pct is not None
    assert p.total_return_pct > 0


def test_portfolio_metrics_trades_only_realized(synthetic_db):
    """With capital_usdt and source='live_computed', portfolio_metrics
    aggregates closed-trade pnl_usdt by exit date and divides by capital.
    The synthetic fixture has 3 ADX trades on 2024-06-01..03 with
    pnl [+50, -20, +30] → daily returns [+0.5%, -0.2%, +0.3%].

    The 6 daily-returns rows in variant_daily_returns are NOT consulted
    in the live path (Path B: trades are source of truth)."""
    window = sh.Window("ALL", "2024-01-01", "2024-12-31")
    p = sh.portfolio_metrics("syn_v", window, source="live_computed",
                              capital_usdt=10000.0)
    assert p.n_days == 3  # only days with closed trades, not the 6 VDR rows
    # Summed: 0.5 + (-0.2) + 0.3 = +0.6%. Arithmetic-on-fixed-capital
    # returns sum, they don't compound (Jensen-gap fix, AUDIT_2026_05_13).
    assert p.total_return_pct == pytest.approx(0.6, abs=1e-9)
    # Win rate: 2 of 3 days were up.
    assert p.win_rate_pct == pytest.approx(200.0 / 3.0)


def test_trades_daily_returns_includes_jplus_strategies(tmp_path, monkeypatch):
    """All closed trades — Core (JPLUS_*) AND tactical — contribute to
    the realized-P&L daily series. There's no double-counting concern in
    the trades-only model because each trade's pnl_usdt is its own
    fee-net realized P&L."""
    fixture_db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    con = sqlite3.connect(str(fixture_db))
    # JPLUS_R4_BTC +$500 + tactical ADX +$100 on 2024-06-01.
    for tid, strategy, pnl in [
        ("SJ-J1", "JPLUS_R4_BTC", 500.0),
        ("SJ-A1", "ADX",          100.0),
    ]:
        con.execute("""
            INSERT INTO trades
            (id, series, asset, direction, strategy, allocation_pct, leverage,
             entry_time, exit_time, status, execution_mode, strategy_variant,
             actual_entry_time, actual_exit_time,
             entry_price, exit_price, size_usdt, qty, pnl_usdt, pnl_pct,
             current_qty, current_leverage, current_size_usdt, realized_pnl_usdt)
            VALUES (?, 'SJ', 'BTC', 'LONG', ?, 15.0, 5.0,
                    '2024-06-01T06:00:00+00:00', '2024-06-01T18:00:00+00:00',
                    'closed', 'paper', 'vz',
                    '2024-06-01T06:00:00+00:00', '2024-06-01T18:00:00+00:00',
                    100.0, 110.0, 1500.0, 15.0, ?, 0,
                    0, 5.0, 0, ?)
        """, (tid, strategy, pnl, pnl))
    con.commit()
    con.close()
    rets = sh._trades_daily_returns("vz", "2024-01-01", "2024-12-31",
                                      capital_usdt=10000.0)
    # +500 + 100 = +600 / 10000 * 100 = +6.0%
    assert rets == pytest.approx([6.0], abs=1e-6)


def test_trades_daily_returns_returns_empty_when_no_trades(tmp_path, monkeypatch):
    fixture_db = tmp_path / "dashboard.db"
    from strategies.support import trade_db, db as _db_mod
    monkeypatch.setattr(trade_db, "DB_PATH", fixture_db)
    monkeypatch.setattr(_db_mod, "DASH_DB", fixture_db)
    trade_db.init_db()
    rets = sh._trades_daily_returns("nobody", "2024-01-01", "2024-12-31",
                                      capital_usdt=10000.0)
    assert rets == []


def test_build_report_includes_known_sleeves_even_with_zero_trades(synthetic_db):
    """All canonical sleeves appear in the per-sleeve dict, in known order,
    even if they never traded."""
    report = sh.build_report("syn_v", capital_usdt=10000.0)
    keys = list(report.sleeves.keys())
    # ADX (the one that traded) plus all KNOWN_SLEEVES in canonical order.
    for known in sh.KNOWN_SLEEVES:
        assert known in keys
    # Order: tactical first (ADX is index 0), JPLUS_* last.
    assert keys.index("ADX") < keys.index("JPLUS_EMA_BTC")


def test_format_report_renders_without_crashing(synthetic_db):
    report = sh.build_report("syn_v", capital_usdt=10000.0)
    out = sh.format_report(report)
    # Basic shape: contains the variant id, all three window names, and
    # the per-sleeve header at least once.
    assert "syn_v" in out
    assert "YTD" in out and "90D" in out and "30D" in out
    assert "Per-sleeve" in out
    # ADX line must be in there with the right N.
    adx_lines = [ln for ln in out.splitlines() if "ADX" in ln and "|" in ln]
    assert adx_lines, "expected at least one ADX row in the formatted output"
