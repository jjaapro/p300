"""Tests for AI_QUANT integration in services.strategy_health.

The general windowed-metric primitives (Sharpe, MDD, etc.) are tested in
test_strategy_health.py. This file covers only the AI_QUANT-specific
additions:

  - AI_QUANT is in KNOWN_SLEEVES (so it shows in the per-sleeve table
    even when no trades have been opened yet).
  - ai_quant_window_stats() reads the ai_quant_decisions table and
    aggregates correctly: total counts, breakdown by direction,
    error count, API cost sum, average conviction.
  - Missing ai_quant_decisions table tolerated (returns zeros).
  - HealthReport.ai_quant is populated by build_report.
  - format_report renders the AI_QUANT decision footnote when there
    are decisions, omits it when there are none.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import clock, strategy_health
from strategies.sleeves.ai_quant import journal
from strategies.sleeves.ai_quant.decision import DecisionResult


# ─── Fixture: dashboard.db with trades + ai_quant_decisions schemas ────────

def _seed_minimal_dash(p: Path) -> None:
    con = sqlite3.connect(str(p))
    try:
        con.executescript("""
            CREATE TABLE trades (
                id TEXT PRIMARY KEY, series TEXT, asset TEXT, direction TEXT,
                strategy TEXT, regime TEXT, allocation_pct REAL, leverage REAL,
                entry_time TEXT, exit_time TEXT, actual_entry_time TEXT,
                actual_exit_time TEXT, entry_price REAL, exit_price REAL,
                size_usdt REAL, qty REAL, pnl_usdt REAL, pnl_pct REAL,
                status TEXT, venue TEXT, order_ids TEXT, execution_mode TEXT,
                strategy_variant TEXT, resolution TEXT, notes TEXT,
                created_at TEXT, parent_position_id TEXT,
                current_qty REAL, current_leverage REAL,
                current_size_usdt REAL, realized_pnl_usdt REAL,
                avg_entry_price REAL
            );
            CREATE TABLE variant_daily_returns (
                variant_id TEXT, date TEXT, return_1x_pct REAL,
                source TEXT, regime TEXT, created_at TEXT,
                PRIMARY KEY (variant_id, date, source)
            );
            CREATE TABLE config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT
            );
            INSERT INTO config VALUES ('paper_account_usdt', '10000', datetime('now'));
        """)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def dash_db(tmp_path, monkeypatch):
    p = tmp_path / "dashboard.db"
    _seed_minimal_dash(p)
    monkeypatch.setattr("services.db.DASH_DB", p)
    monkeypatch.setattr("services.trade_db.DB_PATH", p)
    # Pin clock so resolve_windows is deterministic.
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    yield p


# ─── KNOWN_SLEEVES ─────────────────────────────────────────────────────────

def test_known_sleeves_includes_ai_quant():
    assert "AI_QUANT" in strategy_health.KNOWN_SLEEVES


# ─── ai_quant_window_stats ─────────────────────────────────────────────────

def _save_decision(variant_id: str, *, direction: str, conviction: int | None,
                    cost_usd: float, when: datetime) -> None:
    """Helper: persist one ai_quant_decisions row at the given clock time."""
    clock.set_simulated_now(when)
    payload = (None if direction == "ERROR" else {
        "direction": direction,
        "conviction_0_100": conviction or 0,
        "time_horizon_days": 1, "key_drivers": [],
        "exit_conditions": "", "confidence_caveats": "", "rationale_md": "",
    })
    res = DecisionResult(
        decision=payload,
        error=("synthetic" if direction == "ERROR" else None),
        turns=1, tool_calls=[], usage={},
        cost_usd=cost_usd, model_id="claude-opus-4-7",
    )
    journal.save_decision(
        variant_id=variant_id, asset="BTC", decision_result=res,
        trade_action="opened:SJ-X" if direction in ("LONG", "SHORT") else "noop",
    )


def test_ai_quant_window_stats_returns_zeros_when_table_missing(dash_db, monkeypatch):
    """A pristine DB with no ai_quant_decisions table — common for legacy
    installs — must produce all-zero stats rather than crashing."""
    # Drop the table if it was created by anything else
    con = sqlite3.connect(str(dash_db))
    try:
        con.execute("DROP TABLE IF EXISTS ai_quant_decisions")
        con.commit()
    finally:
        con.close()
    window = strategy_health.Window("YTD", "2026-01-01", "2026-05-08")
    stats = strategy_health.ai_quant_window_stats("p300_test", window)
    assert stats.n_decisions == 0
    assert stats.api_cost_usd == 0.0
    assert stats.avg_conviction is None


def test_ai_quant_window_stats_returns_zeros_when_no_rows(dash_db):
    window = strategy_health.Window("YTD", "2026-01-01", "2026-05-08")
    stats = strategy_health.ai_quant_window_stats("p300_test", window)
    assert stats.n_decisions == 0
    assert stats.n_long == 0 and stats.n_short == 0
    assert stats.n_flat == 0 and stats.n_errors == 0
    assert stats.api_cost_usd == 0.0
    assert stats.avg_conviction is None


def test_ai_quant_window_stats_aggregates_breakdown_and_cost(dash_db):
    """Seven decisions across a window: 3 LONG, 2 SHORT, 1 FLAT, 1 ERROR.
    Cost sum, direction counts, and avg conviction (over non-ERROR rows)
    must match exactly."""
    base = datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc)
    _save_decision("v1", direction="LONG", conviction=70, cost_usd=0.50,
                    when=base)
    _save_decision("v1", direction="LONG", conviction=80, cost_usd=0.55,
                    when=base.replace(day=2))
    _save_decision("v1", direction="LONG", conviction=60, cost_usd=0.60,
                    when=base.replace(day=3))
    _save_decision("v1", direction="SHORT", conviction=50, cost_usd=0.70,
                    when=base.replace(day=4))
    _save_decision("v1", direction="SHORT", conviction=40, cost_usd=0.45,
                    when=base.replace(day=5))
    _save_decision("v1", direction="FLAT", conviction=20, cost_usd=0.25,
                    when=base.replace(day=6))
    _save_decision("v1", direction="ERROR", conviction=None, cost_usd=0.10,
                    when=base.replace(day=7))
    # Reset clock for the lookup (the helper moves it)
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    window = strategy_health.Window("MTD", "2026-05-01", "2026-05-08")
    stats = strategy_health.ai_quant_window_stats("v1", window)
    assert stats.n_decisions == 7
    assert stats.n_long == 3
    assert stats.n_short == 2
    assert stats.n_flat == 1
    assert stats.n_errors == 1
    assert stats.api_cost_usd == pytest.approx(
        0.50 + 0.55 + 0.60 + 0.70 + 0.45 + 0.25 + 0.10)
    # avg conviction over non-ERROR rows (6 rows): (70+80+60+50+40+20)/6 = 53.33
    assert stats.avg_conviction == pytest.approx((70+80+60+50+40+20) / 6, abs=0.01)


def test_ai_quant_window_stats_filters_by_variant(dash_db):
    """v1 has 2 decisions; v2 has 3. Each lookup must see only its own."""
    base = datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc)
    _save_decision("v1", direction="LONG", conviction=60,
                    cost_usd=0.5, when=base)
    _save_decision("v1", direction="SHORT", conviction=50,
                    cost_usd=0.6, when=base.replace(day=2))
    _save_decision("v2", direction="FLAT", conviction=10,
                    cost_usd=0.1, when=base.replace(day=2))
    _save_decision("v2", direction="LONG", conviction=70,
                    cost_usd=0.7, when=base.replace(day=3))
    _save_decision("v2", direction="LONG", conviction=80,
                    cost_usd=0.8, when=base.replace(day=4))
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    window = strategy_health.Window("MTD", "2026-05-01", "2026-05-08")
    s1 = strategy_health.ai_quant_window_stats("v1", window)
    s2 = strategy_health.ai_quant_window_stats("v2", window)
    assert s1.n_decisions == 2
    assert s2.n_decisions == 3


def test_ai_quant_window_stats_respects_date_window(dash_db):
    """Rows outside the window are not counted."""
    _save_decision("v1", direction="LONG", conviction=60, cost_usd=0.5,
                    when=datetime(2026, 4, 15, 0, 6, tzinfo=timezone.utc))
    _save_decision("v1", direction="SHORT", conviction=70, cost_usd=0.6,
                    when=datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc))
    _save_decision("v1", direction="LONG", conviction=80, cost_usd=0.7,
                    when=datetime(2026, 5, 5, 0, 6, tzinfo=timezone.utc))
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    # Window covers only May
    may_window = strategy_health.Window("MTD", "2026-05-01", "2026-05-08")
    stats = strategy_health.ai_quant_window_stats("v1", may_window)
    assert stats.n_decisions == 2  # the May rows only


# ─── HealthReport integration ──────────────────────────────────────────────

def test_build_report_populates_ai_quant_field(dash_db):
    """build_report must include AIQuantWindowStats — one per window."""
    report = strategy_health.build_report("v1")
    assert hasattr(report, "ai_quant")
    assert len(report.ai_quant) == len(report.windows)
    # Order matches windows
    for stats, window in zip(report.ai_quant, report.windows):
        assert stats.window == window.name


def test_build_report_ai_quant_zero_when_no_decisions(dash_db):
    report = strategy_health.build_report("v1")
    assert all(s.n_decisions == 0 for s in report.ai_quant)
    assert all(s.api_cost_usd == 0.0 for s in report.ai_quant)


def test_build_report_ai_quant_reflects_seeded_decisions(dash_db):
    base = datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc)
    _save_decision("v1", direction="LONG", conviction=70, cost_usd=0.50,
                    when=base)
    _save_decision("v1", direction="SHORT", conviction=80, cost_usd=0.60,
                    when=base.replace(day=2))
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    report = strategy_health.build_report("v1")
    # YTD covers all rows
    ytd = next(s for s in report.ai_quant if s.window == "YTD")
    assert ytd.n_decisions == 2
    assert ytd.api_cost_usd == pytest.approx(1.10)


# ─── Formatter ─────────────────────────────────────────────────────────────

def test_format_report_includes_ai_quant_footnote_when_decisions_present(dash_db):
    """When the AI_QUANT sleeve has fired in a window, the formatter
    renders an indented sub-line under its row showing decision counts +
    API cost + net P&L."""
    base = datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc)
    _save_decision("v1", direction="LONG", conviction=70, cost_usd=0.50,
                    when=base)
    _save_decision("v1", direction="FLAT", conviction=10, cost_usd=0.30,
                    when=base.replace(day=2))
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    report = strategy_health.build_report("v1")
    text = strategy_health.format_report(report)
    # Footnote line uses the └─ tree-character marker
    assert "└─" in text
    assert "decisions" in text
    assert "API $" in text
    assert "net P&L" in text
    # Direction breakdown shows up
    assert "1L / 0S / 1F / 0E" in text


def test_format_report_omits_footnote_when_no_decisions(dash_db):
    """No AI_QUANT decisions → no footnote line. The AI_QUANT row still
    appears (it's in KNOWN_SLEEVES) but with 0 trades and no sub-line."""
    report = strategy_health.build_report("v1")
    text = strategy_health.format_report(report)
    assert "AI_QUANT" in text
    assert "└─" not in text


def test_format_report_net_pnl_subtracts_api_cost_from_total_pnl(dash_db):
    """The footnote's 'net P&L' = SleeveMetrics.total_pnl_usdt - api_cost_usd."""
    # Seed one $100-PnL closed trade for AI_QUANT
    con = sqlite3.connect(str(dash_db))
    try:
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy,
                allocation_pct, leverage, entry_time, exit_time,
                actual_entry_time, actual_exit_time, entry_price, exit_price,
                size_usdt, qty, pnl_usdt, pnl_pct, status, execution_mode,
                strategy_variant, current_qty, current_leverage,
                current_size_usdt, realized_pnl_usdt, avg_entry_price)
            VALUES ('SJ-AQ1', 'SJ', 'BTC', 'LONG', 'AI_QUANT',
                    2.0, 3.0,
                    '2026-05-01T00:06:00+00:00', '2026-05-02T00:00:00+00:00',
                    '2026-05-01T00:06:00+00:00', '2026-05-02T00:00:00+00:00',
                    80000, 81000, 600, 0.0075, 100.0, 1.25, 'closed', 'SHADOW',
                    'v1', 0, 3.0, 0, 100.0, 80000)
        """)
        con.commit()
    finally:
        con.close()
    # And one decision with $0.40 of API cost
    _save_decision("v1", direction="LONG", conviction=70, cost_usd=0.40,
                    when=datetime(2026, 5, 1, 0, 6, tzinfo=timezone.utc))
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    report = strategy_health.build_report("v1")
    text = strategy_health.format_report(report)
    # Net P&L = $100.00 - $0.40 = $99.60
    assert "net P&L $+99.60" in text


def test_format_report_compatible_when_ai_quant_field_unset(monkeypatch, dash_db):
    """Defensive: if a stale caller hands us a report with ai_quant=[] (no
    rows) we should still format without crashing — the footnote just
    doesn't render."""
    report = strategy_health.build_report("v1")
    # Replace ai_quant with an empty list (no per-window rows)
    fields = {
        "variant_id": report.variant_id, "capital_usdt": report.capital_usdt,
        "as_of": report.as_of, "windows": report.windows,
        "portfolio": report.portfolio, "sleeves": report.sleeves,
        "ai_quant": [],
    }
    stripped = strategy_health.HealthReport(**fields)
    text = strategy_health.format_report(stripped)
    assert "AI_QUANT" in text
    assert "└─" not in text
