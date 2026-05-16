"""Tests for strategies.sleeves.ai_quant.signal.try_fire_for_variant.

Coverage strategy:
  - Four gate tests (kill switch / time window / already-fired / cost cap)
    short-circuit before any LLM or DB-write activity. They need only
    DB reads, no fixture seeding for context.
  - Eight reconciliation-matrix tests cover every (current_position ×
    effective_direction) combination, plus edge cases (no_price,
    conviction-floor boundary, conviction=0 on FLAT decision).
  - Three error-path tests cover context_build failure, chart_render
    failure, and decision API failure — each must record an ERROR row
    in the journal so the next-minute idempotency gate fires correctly.
  - Two persistence tests assert the journal row's trade_action matches
    the actual side effects.

Anthropic + chart + price are mocked. Trade emission and journal
persistence use real fixture DBs so we genuinely exercise the
trades.py / journal.py SQL paths.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest

from strategies.support import clock
from strategies.sleeves.ai_quant import journal
from strategies.sleeves.ai_quant.decision import DecisionResult


# ─── Common fixture ─────────────────────────────────────────────────────────

def _setup_dash_db(p) -> None:
    """Create dashboard.db with the trades + ai_quant_decisions tables."""
    from strategies.support import trade_db
    # trade_db.init_db() reads its own DB_PATH; point it at our file
    # before invoking. We monkeypatch in the fixture below; here we just
    # ensure the schema exists in p.
    con = sqlite3.connect(str(p))
    try:
        # Subset of trade_db.init_db sufficient for these tests.
        con.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY, series TEXT, asset TEXT, direction TEXT,
                strategy TEXT, regime TEXT, allocation_pct REAL, leverage REAL,
                entry_time TEXT, exit_time TEXT, actual_entry_time TEXT,
                actual_exit_time TEXT, entry_price REAL, exit_price REAL,
                size_usdt REAL, qty REAL, pnl_usdt REAL, pnl_pct REAL,
                status TEXT DEFAULT 'pending', venue TEXT DEFAULT 'MEXC',
                order_ids TEXT, execution_mode TEXT DEFAULT 'PAPER',
                strategy_variant TEXT DEFAULT 'prod',
                resolution TEXT, notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                parent_position_id TEXT, current_qty REAL, current_leverage REAL,
                current_size_usdt REAL, realized_pnl_usdt REAL DEFAULT 0,
                avg_entry_price REAL,
                ai_quant_decision_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS trade_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_time TEXT NOT NULL,
                event_date TEXT NOT NULL,
                qty_delta REAL DEFAULT 0,
                qty_after REAL,
                leverage_before REAL,
                leverage_after REAL,
                margin_delta_usdt REAL DEFAULT 0,
                size_usdt_after REAL,
                price REAL,
                fee_usdt REAL DEFAULT 0,
                realized_pnl_delta_usdt REAL DEFAULT 0,
                notes_json TEXT,
                UNIQUE(trade_id, seq),
                UNIQUE(trade_id, event_date, event_type)
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO config VALUES ('paper_account_usdt', '10000', datetime('now'));
        """)
        con.commit()
    finally:
        con.close()


def _stub_context_bundle() -> dict:
    return {"as_of_utc": "2026-05-08T00:06:00+00:00",
            "asset": "BTC", "fixture": True}


_DUMMY_PNG = b"\x89PNG\r\n\x1a\n\x00fake"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    """Common setup: empty dashboard.db with schema, AI_QUANT_ENABLED=true,
    clock pinned in the entry window, context+chart+price mocked, default
    cost cap of $5. Returns a dict of useful handles for the test body."""
    dash_db = tmp_path / "dashboard.db"
    _setup_dash_db(dash_db)
    monkeypatch.setattr("strategies.support.db.DASH_DB", dash_db)
    monkeypatch.setattr("strategies.support.trade_db.DB_PATH", dash_db)
    monkeypatch.setenv("AI_QUANT_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_DAILY_COST_CAP_USD", "5.0")
    # Pin clock to 2026-05-08 00:06 UTC (inside entry window).
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 6, tzinfo=timezone.utc))
    # Stub context + chart + live price — none of these tests care about
    # the actual content; they care about the orchestration logic.
    monkeypatch.setattr("strategies.sleeves.ai_quant.signal.ctx_mod.build_context",
                         lambda v, a: _stub_context_bundle())
    monkeypatch.setattr("strategies.sleeves.ai_quant.signal.chart.render_chart",
                         lambda **kw: _DUMMY_PNG)
    monkeypatch.setattr("strategies.sleeves.ai_quant.signal.price_feed.get_current_price",
                         lambda asset: 80_000.0)
    yield {"dash_db": dash_db}


# ─── MockClient (mirrors decision-test pattern) ────────────────────────────

class _StreamCtx:
    """Context-manager stand-in for the SDK's MessageStream."""

    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._response


class _MockMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def stream(self, **kw):
        self.calls.append(kw)
        if not self._scripted:
            raise RuntimeError("MockClient: no more scripted responses")
        nxt = self._scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _StreamCtx(nxt)


class MockClient:
    def __init__(self, scripted):
        self.messages = _MockMessages(scripted)


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Usage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 0)
        self.output_tokens = kw.get("output_tokens", 0)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)


class _Response:
    def __init__(self, content, stop_reason="tool_use", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage(**(usage or {}))


def _scripted_decision(direction: str, conviction: int = 65,
                        cost_usage: dict | None = None) -> _Response:
    """Single-response that calls submit_decision with the given direction."""
    return _Response(
        content=[
            _Block(type="text", text="Setup looks like " + direction),
            _Block(type="tool_use", id="tu1", name="submit_decision",
                   input={
                       "direction": direction,
                       "conviction_0_100": conviction,
                       "time_horizon_days": 5,
                       "key_drivers": ["mocked driver"],
                       "exit_conditions": "mocked", "confidence_caveats": "",
                       "rationale_md": "",
                   }),
        ],
        stop_reason="tool_use",
        usage=cost_usage or {"input_tokens": 1000, "output_tokens": 200},
    )


def _variant() -> dict:
    return {"id": "p300_test_variant", "capital_usdt": 10_000}


def _sleeve_cfg(weight_pct: float = 5.0, leverage: float = 3.0,
                 client: Any | None = None) -> dict:
    return {
        "strategy_id": "AI_QUANT",
        "weight_pct": weight_pct,
        "params": {"leverage": leverage, "deterministic": False},
        "_effective_leverage": leverage,
        "_anthropic_client": client,
        "_include_server_tools": False,
    }


# ─── Gate: kill switch ─────────────────────────────────────────────────────

def test_gate_kill_switch_off_returns_disabled_without_api(fixture, monkeypatch):
    monkeypatch.delenv("AI_QUANT_ENABLED", raising=False)
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([])  # would raise if any call is made
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out == {"status": "disabled"}
    assert client.messages.calls == []


def test_gate_kill_switch_explicit_false_returns_disabled(fixture, monkeypatch):
    monkeypatch.setenv("AI_QUANT_ENABLED", "false")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    out = ai_quant_service.try_fire_for_variant(_variant(), _sleeve_cfg())
    assert out["status"] == "disabled"


# ─── Gate: time window ─────────────────────────────────────────────────────

@pytest.mark.parametrize("hh,mm,expected", [
    (0,  4, "off_window"),    # too early
    (0, 16, "off_window"),    # too late
    (12,  0, "off_window"),   # midday — typical tick
    (23, 30, "off_window"),
    (0,  5, None),            # boundary: in-window
    (0,  6, None),            # in-window
    (0, 15, None),            # boundary: in-window
])
def test_gate_time_window(hh, mm, expected, fixture):
    """Outside the window short-circuits; inside the window proceeds (and
    fails only because no client is wired — we just assert NOT off_window)."""
    clock.set_simulated_now(datetime(2026, 5, 8, hh, mm, tzinfo=timezone.utc))
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    out = ai_quant_service.try_fire_for_variant(
        _variant(),
        _sleeve_cfg(client=MockClient([_scripted_decision("FLAT")])),
    )
    if expected:
        assert out["status"] == expected
    else:
        # In-window paths reach the API; we don't care which specific
        # post-gate status we got, only that we passed the gate.
        assert out["status"] != "off_window"


# ─── Gate: already-fired idempotency ───────────────────────────────────────

def test_gate_already_fired_today_short_circuits(fixture):
    """A prior journal row for today's UTC date blocks re-firing."""
    # Seed an earlier ERROR row from this morning
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 5, tzinfo=timezone.utc))
    journal.save_decision(
        variant_id="p300_test_variant", asset="BTC",
        decision_result=DecisionResult(decision=None, error="prior",
                                        turns=1, tool_calls=[], usage={},
                                        cost_usd=0.0,
                                        model_id="claude-opus-4-7"),
        trade_action="error",
    )
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc))
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["status"] == "already_fired_today"
    assert client.messages.calls == []


# ─── Gate: cost cap ────────────────────────────────────────────────────────

def test_gate_cost_cap_hit_short_circuits(fixture, monkeypatch):
    """A prior row whose cost exceeds the cap blocks today's new API call.
    To exercise this without tripping idempotency we monkeypatch
    get_today_decision to None — leaving the cost-sum lookup intact."""
    monkeypatch.setenv("AI_QUANT_DAILY_COST_CAP_USD", "0.01")
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.journal.get_today_decision",
        lambda variant_id: None,
    )
    # Seed a today's row contributing $5.00 of cost via the production
    # writer (creates schema if missing).
    journal.save_decision(
        variant_id="p300_test_variant", asset="BTC",
        decision_result=DecisionResult(
            decision=None, error="prior burn", turns=10,
            tool_calls=[], usage={"input_tokens": 100_000, "output_tokens": 5_000},
            cost_usd=5.0, model_id="claude-opus-4-7",
        ),
        trade_action="error",
    )
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["status"] == "cost_capped"
    assert out["spent_usd"] >= 5.0
    assert out["cap_usd"] == 0.01
    assert client.messages.calls == []


# ─── Reconciliation: empty position ────────────────────────────────────────

def test_reconcile_no_position_flat_is_noop(fixture):
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("FLAT", conviction=0)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["status"] == "decided"
    assert out["trade_action"] == "noop"
    # No trade row created
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        n = con.execute("SELECT COUNT(*) FROM trades WHERE strategy='AI_QUANT'").fetchone()[0]
    finally:
        con.close()
    assert n == 0


def test_reconcile_no_position_long_high_conviction_opens(fixture):
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=70)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["status"] == "decided"
    assert out["trade_action"].startswith("opened:SJ-")
    assert out["decision"] == "LONG"
    con = sqlite3.connect(str(fixture["dash_db"]))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM trades WHERE strategy='AI_QUANT' AND status='open'"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1
    r = dict(rows[0])
    assert r["direction"] == "LONG"
    assert r["entry_price"] == 80_000.0
    # weight 5.0 × conviction 70/100 = 3.5 → allocation_pct 3.5
    assert r["allocation_pct"] == pytest.approx(3.5, rel=1e-6)
    assert r["leverage"] == 3.0


def test_reconcile_no_position_short_opens(fixture):
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("SHORT", conviction=80)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("opened:SJ-")
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        d = con.execute(
            "SELECT direction FROM trades WHERE strategy='AI_QUANT' AND status='open'"
        ).fetchone()[0]
    finally:
        con.close()
    assert d == "SHORT"


def test_reconcile_low_conviction_treated_as_flat_no_trade(fixture):
    """Conviction below MIN_CONVICTION_FOR_TRADE (30) is FLAT regardless
    of the model's stated direction."""
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=29)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "noop"
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        n = con.execute("SELECT COUNT(*) FROM trades WHERE strategy='AI_QUANT'").fetchone()[0]
    finally:
        con.close()
    assert n == 0


def test_reconcile_conviction_floor_boundary_30_does_open(fixture):
    """Conviction=30 (the threshold) should pass the floor and open."""
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=30)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("opened:SJ-")


# ─── Reconciliation: existing position ─────────────────────────────────────

def _seed_open_trade(dash_db, *, direction: str, variant_id: str = "p300_test_variant",
                      tid: str = "SJ-A001", entry_price: float = 70_000.0):
    con = sqlite3.connect(str(dash_db))
    try:
        con.execute("""
            INSERT INTO trades (id, series, asset, direction, strategy,
                regime, allocation_pct, leverage, entry_time, exit_time,
                actual_entry_time, entry_price, size_usdt, qty, status,
                execution_mode, strategy_variant, current_qty,
                current_leverage, current_size_usdt, realized_pnl_usdt,
                avg_entry_price)
            VALUES (?, 'SJ', 'BTC', ?, 'AI_QUANT', 'test',
                    5.0, 3.0,
                    '2026-05-07T00:06:00+00:00',
                    '9999-12-31T23:59:59+00:00',
                    '2026-05-07T00:06:00+00:00',
                    ?, 1500, 0.0214, 'open', 'paper',
                    ?, 0.0214, 3.0, 1500, 0.0, ?)
        """, (tid, direction, entry_price, variant_id, entry_price))
        con.commit()
    finally:
        con.close()


def test_reconcile_long_position_long_decision_holds(fixture):
    _seed_open_trade(fixture["dash_db"], direction="LONG")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=80)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "held"
    # Existing trade still open, no new row
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        statuses = [r[0] for r in con.execute(
            "SELECT status FROM trades WHERE strategy='AI_QUANT'").fetchall()]
    finally:
        con.close()
    assert statuses == ["open"]


def test_reconcile_long_position_flat_closes(fixture):
    _seed_open_trade(fixture["dash_db"], direction="LONG")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("FLAT", conviction=0)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "closed:SJ-A001"
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        status = con.execute(
            "SELECT status FROM trades WHERE id='SJ-A001'"
        ).fetchone()[0]
    finally:
        con.close()
    assert status == "closed"


def test_reconcile_long_position_short_decision_flips(fixture):
    _seed_open_trade(fixture["dash_db"], direction="LONG")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("SHORT", conviction=70)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("flipped:SJ-A001->SJ-")
    con = sqlite3.connect(str(fixture["dash_db"]))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, direction, status FROM trades WHERE strategy='AI_QUANT' "
            "ORDER BY id"
        ).fetchall()
    finally:
        con.close()
    by_id = {r["id"]: dict(r) for r in rows}
    assert by_id["SJ-A001"]["status"] == "closed"
    new = next(r for r in rows if r["id"] != "SJ-A001")
    assert new["direction"] == "SHORT"
    assert new["status"] == "open"


def test_reconcile_short_position_long_decision_flips(fixture):
    """Mirror of the LONG→SHORT case."""
    _seed_open_trade(fixture["dash_db"], direction="SHORT", tid="SJ-B001")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=70)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("flipped:SJ-B001->SJ-")


def test_reconcile_short_position_flat_closes(fixture):
    _seed_open_trade(fixture["dash_db"], direction="SHORT", tid="SJ-B002")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("FLAT", conviction=0)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "closed:SJ-B002"


# ─── Edge: live_price unavailable ──────────────────────────────────────────

def test_reconcile_skip_open_when_live_price_missing(fixture, monkeypatch):
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.price_feed.get_current_price",
        lambda asset: None,
    )
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=70)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "skipped:no_price"
    # No trade created
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        n = con.execute("SELECT COUNT(*) FROM trades WHERE strategy='AI_QUANT'").fetchone()[0]
    finally:
        con.close()
    assert n == 0


def test_reconcile_skip_close_when_live_price_missing(fixture, monkeypatch):
    _seed_open_trade(fixture["dash_db"], direction="LONG")
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.price_feed.get_current_price",
        lambda asset: None,
    )
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("FLAT", conviction=0)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "skipped:no_price"


# ─── Error paths ────────────────────────────────────────────────────────────

def test_context_build_failure_records_error_row_no_trade(fixture, monkeypatch):
    def boom(_v, _a):
        raise RuntimeError("synthetic context blowup")

    monkeypatch.setattr("strategies.sleeves.ai_quant.signal.ctx_mod.build_context", boom)
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=MockClient([])))
    assert out["status"] == "decision_error"
    assert out["error"] == "context_build"
    row = journal.get_today_decision("p300_test_variant")
    assert row is not None
    assert row["decided"] == "ERROR"
    assert "synthetic context blowup" in row["error"]
    assert row["trade_action"] == "error"


def test_chart_render_failure_records_error_row_no_trade(fixture, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("chart died")

    monkeypatch.setattr("strategies.sleeves.ai_quant.signal.chart.render_chart", boom)
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=MockClient([])))
    assert out["status"] == "decision_error"
    assert out["error"] == "chart_render"
    row = journal.get_today_decision("p300_test_variant")
    assert row is not None
    assert "chart died" in row["error"]


def test_decision_api_failure_records_error_row_no_trade(fixture):
    """run_decision returns a DecisionResult with decision=None on API errors."""
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([RuntimeError("Anthropic 500")])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["status"] == "decision_error"
    row = journal.get_today_decision("p300_test_variant")
    assert row is not None
    assert row["decided"] == "ERROR"
    assert "Anthropic 500" in row["error"]
    assert row["trade_action"] == "error"
    # No trade rows
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        n = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        con.close()
    assert n == 0


# ─── Persistence: journal row mirrors the trade action ─────────────────────

def test_journal_row_records_opened_trade_id(fixture):
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=60)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    row = journal.get_today_decision("p300_test_variant")
    assert row["trade_action"] == out["trade_action"]
    assert row["trade_action"].startswith("opened:SJ-")
    assert row["decided"] == "LONG"
    assert row["conviction"] == 60


def test_journal_row_records_flipped_action_with_both_ids(fixture):
    _seed_open_trade(fixture["dash_db"], direction="LONG", tid="SJ-OLD")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("SHORT", conviction=80)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    row = journal.get_today_decision("p300_test_variant")
    assert row["trade_action"].startswith("flipped:SJ-OLD->SJ-")
    assert row["decided"] == "SHORT"


# ─── Sizing math ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("conviction,weight,expected_alloc", [
    (50,  5.0, 2.5),
    (100, 5.0, 5.0),
    (75,  4.0, 3.0),
    (35,  5.0, 1.75),
    (30,  5.0, 1.5),  # boundary still opens
])
def test_sizing_allocation_pct_matches_conviction_weight_product(
    fixture, conviction, weight, expected_alloc,
):
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=conviction)])
    out = ai_quant_service.try_fire_for_variant(
        _variant(), _sleeve_cfg(weight_pct=weight, client=client),
    )
    assert out["status"] == "decided"
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        alloc = con.execute(
            "SELECT allocation_pct FROM trades WHERE strategy='AI_QUANT' "
            "AND status='open'"
        ).fetchone()[0]
    finally:
        con.close()
    assert alloc == pytest.approx(expected_alloc, rel=1e-6)


# ─── Pure helpers (no DB needed) ───────────────────────────────────────────

def test_effective_direction_drops_low_conviction_to_flat():
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._effective_direction(
        {"direction": "LONG", "conviction_0_100": 29}) == "FLAT"
    assert svc._effective_direction(
        {"direction": "LONG", "conviction_0_100": 30}) == "LONG"
    assert svc._effective_direction(
        {"direction": "FLAT", "conviction_0_100": 99}) == "FLAT"
    assert svc._effective_direction(
        {"direction": "BUY", "conviction_0_100": 99}) == "FLAT"  # unknown → flat


def test_allocation_pct_caps_at_weight():
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._allocation_pct_for(50, 4.0) == pytest.approx(2.0)
    assert svc._allocation_pct_for(150, 4.0) == pytest.approx(4.0)  # clamp
    assert svc._allocation_pct_for(-10, 4.0) == pytest.approx(0.0)


def test_resolve_leverage_prefers_effective_then_params():
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._resolve_leverage(
        {"_effective_leverage": 2.5, "params": {"leverage": 5.0}}) == 2.5
    assert svc._resolve_leverage({"params": {"leverage": 5.0}}) == 5.0
    assert svc._resolve_leverage({}) == 1.0


# ─── M2a — decision↔trade link (ai_quant_decision_id) ──────────────────────

def test_spawned_trade_id_parses_opened():
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._spawned_trade_id("opened:SJ-123") == "SJ-123"


def test_spawned_trade_id_parses_flipped_to_new():
    """flipped:SJ-old->SJ-new returns SJ-new (the decision spawned the
    new direction, not the closed-out old one)."""
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._spawned_trade_id("flipped:SJ-OLD->SJ-NEW") == "SJ-NEW"


def test_spawned_trade_id_none_for_close_held_noop_skipped():
    from strategies.sleeves.ai_quant import signal as svc
    assert svc._spawned_trade_id("closed:SJ-X") is None
    assert svc._spawned_trade_id("held") is None
    assert svc._spawned_trade_id("noop") is None
    assert svc._spawned_trade_id("skipped:no_price") is None
    assert svc._spawned_trade_id("") is None


def test_opened_trade_gets_ai_quant_decision_id(fixture):
    """End-to-end: a fresh LONG opens a trade and the row carries the
    journal decision id back."""
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("LONG", conviction=70)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("opened:SJ-")
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        row = con.execute(
            "SELECT id, ai_quant_decision_id FROM trades "
            "WHERE strategy='AI_QUANT' AND status='open'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    trade_id, decision_id = row
    assert decision_id is not None
    # The decision_id should match the just-written decision row.
    journal_row = journal.get_today_decision("p300_test_variant")
    assert decision_id == journal_row["id"]


def test_flipped_trade_tags_new_trade_only(fixture):
    """flipped:SJ-OLD->SJ-NEW writes the decision id onto SJ-NEW. SJ-OLD
    stays unchanged (its decision_id reflects whichever earlier decision
    spawned it, which here is None — it was seeded directly)."""
    _seed_open_trade(fixture["dash_db"], direction="LONG", tid="SJ-OLD")
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("SHORT", conviction=80)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"].startswith("flipped:SJ-OLD->")
    new_tid = out["trade_action"].split("->", 1)[1]
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        old_id = con.execute(
            "SELECT ai_quant_decision_id FROM trades WHERE id='SJ-OLD'"
        ).fetchone()[0]
        new_id = con.execute(
            "SELECT ai_quant_decision_id FROM trades WHERE id=?", (new_tid,),
        ).fetchone()[0]
    finally:
        con.close()
    # Pre-existing trade keeps its NULL link; the new direction is tagged.
    assert old_id is None
    assert new_id is not None


def test_flat_decision_does_not_tag_anything(fixture):
    """A FLAT decision when no position is open is a noop — no trade is
    spawned, so no UPDATE happens."""
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    client = MockClient([_scripted_decision("FLAT", conviction=80)])
    out = ai_quant_service.try_fire_for_variant(_variant(),
                                                  _sleeve_cfg(client=client))
    assert out["trade_action"] == "noop"
    con = sqlite3.connect(str(fixture["dash_db"]))
    try:
        cnt = con.execute(
            "SELECT COUNT(*) FROM trades WHERE ai_quant_decision_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        con.close()
    assert cnt == 0
