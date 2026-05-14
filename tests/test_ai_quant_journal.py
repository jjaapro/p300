"""Tests for strategies.sleeves.ai_quant.journal.

Cover: schema bootstrap is idempotent, save_decision persists every
DecisionResult field correctly (including ERROR rows when the model
fails), get_today_decision honours the per-variant per-UTC-day grain,
get_today_cost_usd sums correctly, and get_recent_decisions returns
the right window in DESC order.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from services import clock
from strategies.sleeves.ai_quant import journal
from strategies.sleeves.ai_quant.decision import DecisionResult


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Empty dashboard.db; journal._ensure_schema creates the table on
    first call. Clock pinned to 2026-05-08 12:00 UTC."""
    p = tmp_path / "dashboard.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.DASH_DB", p)
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    yield p


# ─── Schema ─────────────────────────────────────────────────────────────────

def test_ensure_schema_is_idempotent(fixture_db):
    con = sqlite3.connect(str(fixture_db))
    try:
        journal._ensure_schema(con)
        journal._ensure_schema(con)  # second call must not raise
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='ai_quant_decisions'"
        ).fetchall()
        assert len(rows) == 1
    finally:
        con.close()


def test_ensure_schema_columns_match_writer_expectations(fixture_db):
    """Every column the writer references must exist."""
    con = sqlite3.connect(str(fixture_db))
    try:
        journal._ensure_schema(con)
        info = con.execute("PRAGMA table_info(ai_quant_decisions)").fetchall()
    finally:
        con.close()
    cols = {r[1] for r in info}
    expected = {
        "id", "decision_utc", "decision_date", "variant_id", "asset",
        "decided", "conviction", "time_horizon_days", "key_drivers_json",
        "exit_conditions", "confidence_caveats", "rationale_md",
        "context_json", "tool_calls_json", "model_id",
        "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens",
        "cost_usd", "turns", "trade_action", "error",
        "defer_until_utc", "created_at",
    }
    assert expected.issubset(cols), f"missing columns: {expected - cols}"


def test_ensure_schema_migrates_legacy_table_without_defer_until(fixture_db):
    """A DB created before the defer feature is missing defer_until_utc;
    _ensure_schema must ALTER TABLE to add it without dropping data."""
    con = sqlite3.connect(str(fixture_db))
    try:
        # Build the legacy shape (defer_until_utc deliberately omitted).
        con.execute("""
            CREATE TABLE ai_quant_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_utc INTEGER NOT NULL,
                decision_date TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                decided TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.execute(
            "INSERT INTO ai_quant_decisions "
            "(decision_utc, decision_date, variant_id, asset, decided) "
            "VALUES (?, ?, ?, ?, ?)",
            (1715169600, "2026-05-08", "v1", "BTC", "LONG"),
        )
        con.commit()
        journal._ensure_schema(con)
        cols = {r[1] for r in con.execute(
            "PRAGMA table_info(ai_quant_decisions)").fetchall()}
        assert "defer_until_utc" in cols
        # Pre-existing row survives the migration.
        rows = con.execute(
            "SELECT decided FROM ai_quant_decisions").fetchall()
        assert rows == [("LONG",)]
    finally:
        con.close()


def test_trade_db_init_db_creates_ai_quant_decisions_table(tmp_path, monkeypatch):
    """Production wiring: services.trade_db.init_db is the canonical
    schema entry point. Re-run it on a fresh DB and confirm the AI_QUANT
    table is among the tables created."""
    p = tmp_path / "dashboard.db"
    monkeypatch.setattr("services.trade_db.DB_PATH", p)
    from services import trade_db
    trade_db.init_db()
    con = sqlite3.connect(str(p))
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='ai_quant_decisions'"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1


# ─── save_decision happy path ──────────────────────────────────────────────

def _result_with_decision(**overrides) -> DecisionResult:
    payload = {
        "direction": "LONG", "conviction_0_100": 65, "time_horizon_days": 5,
        "key_drivers": ["funding flipped negative", "F&G fear"],
        "exit_conditions": "close on funding > +0.02%",
        "confidence_caveats": "low news flow",
        "rationale_md": "Setup looks clean.",
    }
    return DecisionResult(
        decision=overrides.get("decision", payload),
        error=overrides.get("error"),
        turns=overrides.get("turns", 2),
        tool_calls=overrides.get("tool_calls",
                                  [{"name": "render_chart", "input": {"timeframe": "4h"}},
                                   {"name": "submit_decision", "input": {}}]),
        usage=overrides.get("usage",
                             {"input_tokens": 5000, "output_tokens": 800,
                              "cache_creation_input_tokens": 4000,
                              "cache_read_input_tokens": 0}),
        cost_usd=overrides.get("cost_usd", 0.21),
        model_id=overrides.get("model_id", "claude-opus-4-7"),
    )


def test_save_decision_persists_every_field(fixture_db):
    res = _result_with_decision()
    rid = journal.save_decision(
        variant_id="p300_test", asset="BTC", decision_result=res,
        context_bundle={"as_of_utc": "2026-05-08T12:00:00Z", "fixture": True},
        trade_action="opened:SJ-1234",
    )
    assert isinstance(rid, int) and rid > 0
    con = sqlite3.connect(str(fixture_db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM ai_quant_decisions WHERE id=?",
                          (rid,)).fetchone()
    finally:
        con.close()
    r = dict(row)
    assert r["variant_id"] == "p300_test"
    assert r["asset"] == "BTC"
    assert r["decided"] == "LONG"
    assert r["conviction"] == 65
    assert r["time_horizon_days"] == 5
    assert "funding flipped negative" in r["key_drivers_json"]
    assert r["exit_conditions"].startswith("close on funding")
    assert r["model_id"] == "claude-opus-4-7"
    assert r["input_tokens"] == 5000
    assert r["output_tokens"] == 800
    assert r["cache_write_tokens"] == 4000
    assert r["cache_read_tokens"] == 0
    assert r["cost_usd"] == pytest.approx(0.21)
    assert r["turns"] == 2
    assert r["trade_action"] == "opened:SJ-1234"
    assert r["error"] is None
    assert r["decision_date"] == "2026-05-08"
    # context_json round-trips
    assert json.loads(r["context_json"]) == {
        "as_of_utc": "2026-05-08T12:00:00Z", "fixture": True,
    }
    # tool_calls_json round-trips
    parsed_tc = json.loads(r["tool_calls_json"])
    assert [tc["name"] for tc in parsed_tc] == ["render_chart", "submit_decision"]


def test_save_decision_records_error_rows_when_decision_is_none(fixture_db):
    res = DecisionResult(
        decision=None, error="hit max_turns=10 without a decision",
        turns=10, tool_calls=[],
        usage={"input_tokens": 30000, "output_tokens": 2000},
        cost_usd=0.6, model_id="claude-opus-4-7",
    )
    rid = journal.save_decision(
        variant_id="p300_test", asset="BTC", decision_result=res,
        context_bundle={"x": 1}, trade_action="error",
    )
    row = journal.get_today_decision("p300_test")
    assert row is not None
    assert row["id"] == rid
    assert row["decided"] == "ERROR"
    assert row["error"].startswith("hit max_turns")
    assert row["trade_action"] == "error"
    assert row["cost_usd"] == pytest.approx(0.6)


def test_save_decision_truncates_oversized_context_json(fixture_db):
    huge = {"big": "x" * 100_000}
    res = _result_with_decision()
    rid = journal.save_decision(
        variant_id="p300_test", asset="BTC", decision_result=res,
        context_bundle=huge, trade_action="noop",
    )
    con = sqlite3.connect(str(fixture_db))
    try:
        cj = con.execute("SELECT context_json FROM ai_quant_decisions WHERE id=?",
                         (rid,)).fetchone()[0]
    finally:
        con.close()
    assert len(cj) <= journal.CONTEXT_JSON_MAX_CHARS
    assert cj.endswith("[truncated]")


def test_save_decision_handles_no_context_bundle(fixture_db):
    """context_bundle is optional — None should just persist NULL."""
    res = _result_with_decision()
    rid = journal.save_decision(
        variant_id="p300_test", asset="BTC", decision_result=res,
        context_bundle=None, trade_action="noop",
    )
    row = journal.get_today_decision("p300_test")
    assert row is not None
    assert row["context_json"] is None


# ─── get_today_decision ─────────────────────────────────────────────────────

def test_get_today_decision_returns_none_when_no_rows(fixture_db):
    assert journal.get_today_decision("p300_test") is None


def test_get_today_decision_returns_most_recent_row_for_today_only(fixture_db):
    # Yesterday's row (different decision_date) should NOT match today's lookup.
    yesterday = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    clock.set_simulated_now(yesterday)
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=_result_with_decision(decision={
            "direction": "SHORT", "conviction_0_100": 50, "time_horizon_days": 3,
            "key_drivers": ["yesterday's view"], "exit_conditions": "",
            "confidence_caveats": "", "rationale_md": "",
        }), trade_action="opened:SJ-A",
    )
    # Today: two rows, an earlier ERROR and a later success
    today_morning = datetime(2026, 5, 8, 0, 6, tzinfo=timezone.utc)
    clock.set_simulated_now(today_morning)
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=DecisionResult(decision=None, error="API 500", turns=1,
                                        tool_calls=[], usage={}, cost_usd=0.0,
                                        model_id="claude-opus-4-7"),
        trade_action="error",
    )
    today_later = datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc)
    clock.set_simulated_now(today_later)
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=_result_with_decision(),
        trade_action="opened:SJ-B",
    )
    # Lookup with clock at 12:00 — should return the LATER of today's rows
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    row = journal.get_today_decision("p300_test")
    assert row is not None
    assert row["trade_action"] == "opened:SJ-B"
    assert row["decided"] == "LONG"


def test_get_today_decision_filters_by_variant(fixture_db):
    journal.save_decision(
        variant_id="variant_A", asset="BTC",
        decision_result=_result_with_decision(), trade_action="noop",
    )
    journal.save_decision(
        variant_id="variant_B", asset="BTC",
        decision_result=_result_with_decision(), trade_action="opened:SJ-1",
    )
    a = journal.get_today_decision("variant_A")
    b = journal.get_today_decision("variant_B")
    assert a["trade_action"] == "noop"
    assert b["trade_action"] == "opened:SJ-1"


# ─── get_today_cost_usd ────────────────────────────────────────────────────

def test_get_today_cost_usd_sums_today_only(fixture_db):
    # Yesterday's spend doesn't count
    clock.set_simulated_now(datetime(2026, 5, 7, 0, 5, tzinfo=timezone.utc))
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=_result_with_decision(cost_usd=2.50),
        trade_action="opened:SJ-X",
    )
    # Today: two calls, one error then one success
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 6, tzinfo=timezone.utc))
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=DecisionResult(decision=None, error="x", turns=10,
                                        tool_calls=[],
                                        usage={"input_tokens": 1000, "output_tokens": 0},
                                        cost_usd=0.30,
                                        model_id="claude-opus-4-7"),
        trade_action="error",
    )
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc))
    journal.save_decision(
        variant_id="p300_test", asset="BTC",
        decision_result=_result_with_decision(cost_usd=0.45),
        trade_action="opened:SJ-Y",
    )
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    spent = journal.get_today_cost_usd("p300_test")
    assert spent == pytest.approx(0.30 + 0.45)


def test_get_today_cost_usd_returns_zero_when_no_rows(fixture_db):
    assert journal.get_today_cost_usd("p300_test") == 0.0


# ─── get_recent_decisions ──────────────────────────────────────────────────

def test_get_recent_decisions_returns_window_descending(fixture_db):
    """Seed 10 daily decisions over 10 days; assert get_recent(days=5)
    returns the 5 most recent in DESC order."""
    for i in range(10):
        d = datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc) - \
            __import__("datetime").timedelta(days=9 - i)
        clock.set_simulated_now(d)
        journal.save_decision(
            variant_id="p300_test", asset="BTC",
            decision_result=_result_with_decision(decision={
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "conviction_0_100": 40 + i,
                "time_horizon_days": 1, "key_drivers": [f"day {i}"],
                "exit_conditions": "", "confidence_caveats": "", "rationale_md": "",
            }),
            trade_action=f"opened:SJ-{i}",
        )
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    recent = journal.get_recent_decisions("p300_test", days=5)
    assert len(recent) == 5
    # DESC: most recent first
    dates = [r["decision_date"] for r in recent]
    assert dates == sorted(dates, reverse=True)
    # The newest entry's trade_action is the highest index
    assert recent[0]["trade_action"] == "opened:SJ-9"


def test_get_recent_decisions_respects_variant_filter(fixture_db):
    journal.save_decision(
        variant_id="variant_A", asset="BTC",
        decision_result=_result_with_decision(), trade_action="opened:SJ-A",
    )
    journal.save_decision(
        variant_id="variant_B", asset="BTC",
        decision_result=_result_with_decision(), trade_action="opened:SJ-B",
    )
    a = journal.get_recent_decisions("variant_A", days=14)
    b = journal.get_recent_decisions("variant_B", days=14)
    assert len(a) == 1 and a[0]["trade_action"] == "opened:SJ-A"
    assert len(b) == 1 and b[0]["trade_action"] == "opened:SJ-B"


# ─── Defer feature ─────────────────────────────────────────────────────────

def _result_with_defer(retry_h: float = 4.0, **overrides) -> DecisionResult:
    return DecisionResult(
        decision=None,
        deferred={
            "retry_in_hours": retry_h,
            "waiting_for": overrides.get("waiting_for", "CPI 8:30 ET"),
            "reasoning": overrides.get("reasoning", "binary event window"),
        },
        error=None,
        turns=overrides.get("turns", 2),
        tool_calls=overrides.get("tool_calls", []),
        usage=overrides.get("usage", {"input_tokens": 4000, "output_tokens": 300}),
        cost_usd=overrides.get("cost_usd", 0.08),
        model_id=overrides.get("model_id", "claude-opus-4-7"),
    )


def test_save_decision_writes_defer_row_with_defer_until(fixture_db):
    """When the result is deferred, the row's decided='DEFER' and the
    defer_until_utc column is populated. waiting_for and reasoning flow
    into caveats and rationale_md for archive readability."""
    res = _result_with_defer(retry_h=6.0)
    # Service-computed absolute target (would be 6h from now in production).
    target_ts = int(clock.now_utc().timestamp()) + 6 * 3600
    rid = journal.save_decision(
        variant_id="p300_defer_test", asset="BTC", decision_result=res,
        trade_action="deferred",
        defer_until_utc=target_ts,
    )
    con = sqlite3.connect(str(fixture_db))
    con.row_factory = sqlite3.Row
    try:
        r = dict(con.execute(
            "SELECT * FROM ai_quant_decisions WHERE id=?",
            (rid,)).fetchone())
    finally:
        con.close()
    assert r["decided"] == "DEFER"
    assert r["defer_until_utc"] == target_ts
    assert r["trade_action"] == "deferred"
    assert r["conviction"] is None
    assert r["time_horizon_days"] is None
    # waiting_for is stored under confidence_caveats; reasoning under rationale_md
    assert r["confidence_caveats"] == "CPI 8:30 ET"
    assert r["rationale_md"] == "binary event window"


def test_count_today_defers_per_variant(fixture_db):
    """count_today_defers must count only DEFER rows for today and the
    given variant — LONG/SHORT/FLAT/ERROR rows don't count."""
    target_ts = int(clock.now_utc().timestamp()) + 3 * 3600
    # 2 defers for variant_A today
    for _ in range(2):
        journal.save_decision(
            variant_id="variant_A", asset="BTC",
            decision_result=_result_with_defer(), trade_action="deferred",
            defer_until_utc=target_ts,
        )
    # 1 LONG for variant_A — must NOT inflate the defer count
    journal.save_decision(
        variant_id="variant_A", asset="BTC",
        decision_result=_result_with_decision(), trade_action="opened:SJ-A",
    )
    # 1 defer for variant_B
    journal.save_decision(
        variant_id="variant_B", asset="BTC",
        decision_result=_result_with_defer(), trade_action="deferred",
        defer_until_utc=target_ts,
    )
    assert journal.count_today_defers("variant_A") == 2
    assert journal.count_today_defers("variant_B") == 1
    assert journal.count_today_defers("variant_C_unknown") == 0
