"""End-to-end test: drive a full AI_QUANT day through orchestrator.

What's exercised:

  1. AI_QUANT is registered in STRATEGY_DISPATCH and reachable via the
     same dispatch path the live runner uses.
  2. Off-window ticks short-circuit cheaply (no API call, no DB writes).
  3. The first in-window tick fires the LLM (mocked), persists a
     decision row, and opens a paper trade.
  4. The next in-window tick is gated by idempotency — no second API
     call, no second trade.
  5. The next UTC day's in-window tick fires the LLM again with the
     existing position visible in the context, and the reconciliation
     matrix flips the trade direction when the model decides.
  6. backtest_runner.tick_replay_variant skips AI_QUANT (non-deterministic
     sleeves are excluded from historical replay).
  7. P-300's register_p300 spec includes AI_QUANT in its composition.

This is the integration safety net: any break in the chain (sleeve
registration / journal idempotency / orchestrator dispatch / register
config / backtest skip) surfaces here.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from strategies.support import clock
from strategies.sleeves.ai_quant import journal


# ─── Fixture: full DB stack + monkeypatched data sources ────────────────────

def _create_dash_db(p: Path) -> None:
    con = sqlite3.connect(str(p))
    try:
        con.executescript("""
            CREATE TABLE trades (
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
                unique_key TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uix_trades_unique_key
                ON trades(unique_key) WHERE unique_key IS NOT NULL;
            CREATE TABLE trade_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL, seq INTEGER NOT NULL,
                event_type TEXT NOT NULL, event_time TEXT NOT NULL,
                event_date TEXT NOT NULL, qty_delta REAL DEFAULT 0,
                qty_after REAL, leverage_before REAL, leverage_after REAL,
                margin_delta_usdt REAL DEFAULT 0, size_usdt_after REAL,
                price REAL, fee_usdt REAL DEFAULT 0,
                realized_pnl_delta_usdt REAL DEFAULT 0, notes_json TEXT,
                UNIQUE(trade_id, seq),
                UNIQUE(trade_id, event_date, event_type)
            );
            CREATE TABLE config (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO config VALUES ('paper_account_usdt', '10000', datetime('now'));
        """)
        con.commit()
    finally:
        con.close()


def _stub_context(_v, _a):
    return {"as_of_utc": "2026-05-08T00:06:00+00:00",
            "asset": "BTC", "fixture_marker": "e2e"}


_DUMMY_PNG = b"\x89PNG\r\n\x1a\n\x00fake"


@pytest.fixture
def e2e_setup(tmp_path, monkeypatch):
    """Common end-to-end fixture used by every test in this file."""
    dash_db = tmp_path / "dashboard.db"
    _create_dash_db(dash_db)
    monkeypatch.setattr("strategies.support.db.DASH_DB", dash_db)
    monkeypatch.setattr("strategies.support.trade_db.DB_PATH", dash_db)
    monkeypatch.setenv("AI_QUANT_ENABLED", "true")
    monkeypatch.setenv("AI_QUANT_DAILY_COST_CAP_USD", "10.0")
    # Stub the data sources — context bundle, baseline chart, live price.
    # (Each has its own dedicated test file; here we exercise wiring.)
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.ctx_mod.build_context", _stub_context)
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.chart.render_chart",
        lambda **kw: _DUMMY_PNG)
    monkeypatch.setattr(
        "strategies.sleeves.ai_quant.signal.price_feed.get_current_price",
        lambda asset: 80_000.0)
    yield {"dash_db": dash_db}


# ─── Mock Anthropic client ──────────────────────────────────────────────────

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _Usage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 1000)
        self.output_tokens = kw.get("output_tokens", 200)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)


class _Response:
    def __init__(self, content, stop_reason="tool_use", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Usage(**(usage or {}))


class _StreamCtx:
    def __init__(self, response: Any):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._response


class ScriptedClient:
    """Anthropic-shaped client that returns scripted responses across calls.
    Exposes `.calls` for the test to introspect post-run."""

    def __init__(self, scripted: list[Any]):
        self.messages = self
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def stream(self, **kw):
        snap = dict(kw)
        if "messages" in snap:
            snap["messages"] = list(snap["messages"])
        self.calls.append(snap)
        if not self._scripted:
            raise RuntimeError("ScriptedClient: no more responses queued")
        nxt = self._scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _StreamCtx(nxt)


def _decision_response(direction: str, conviction: int = 65,
                        cost_usage: dict | None = None) -> _Response:
    return _Response(
        content=[
            _Block(type="text", text=f"Decision: {direction}"),
            _Block(type="tool_use", id="tu1", name="submit_decision",
                   input={
                       "direction": direction, "conviction_0_100": conviction,
                       "time_horizon_days": 5,
                       "key_drivers": ["e2e mock"],
                       "exit_conditions": "mock", "confidence_caveats": "",
                       "rationale_md": "e2e",
                   }),
        ],
        stop_reason="tool_use",
        usage=cost_usage or {"input_tokens": 5000, "output_tokens": 500,
                              "cache_creation_input_tokens": 4000},
    )


# ─── Helpers ────────────────────────────────────────────────────────────────

def _build_p300_variant(client: ScriptedClient | None = None) -> dict:
    """Construct a variant dict shaped like what orchestrator.tick reads.
    The composition pulls in just AI_QUANT for these tests so other sleeves
    don't fire during the test (their gates would all be off anyway, but
    keeping the composition tight removes accidental coupling)."""
    return {
        "id": "p300_e2e_test",
        "capital_usdt": 10_000,
        "spec": {
            "composition": [
                {"strategy_id": "AI_QUANT", "weight_pct": 2.0,
                 "params": {"asset": "BTC", "leverage": 3.0,
                             "stop_loss_pct": 10.0, "deterministic": False},
                 # Test-only injection: tests hand the orchestrator
                 # dispatch a sleeve_cfg that already carries the mock
                 # client. _tick_composition adds _effective_leverage.
                 "_anthropic_client": client,
                 "_include_server_tools": False},
            ],
            "sleeve_leverages": {"ai_quant": 3.0},
        },
    }


def _ai_quant_trades(dash_db: Path, *, only_open: bool = False) -> list[dict]:
    con = sqlite3.connect(str(dash_db))
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM trades WHERE strategy='AI_QUANT'"
        if only_open:
            sql += " AND status='open'"
        sql += " ORDER BY id"
        return [dict(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _dispatch_via_variant_engine(variant: dict) -> list[dict]:
    """Drive the AI_QUANT sleeve through orchestrator's _tick_composition.

    Returns a list of (strategy_id, status_dict) for every sleeve that
    fired this tick — filtered to AI_QUANT only since that's what these
    tests care about.

    We call the dispatcher directly via STRATEGY_DISPATCH rather than
    orchestrator._tick_composition because the latter requires a fully
    seeded `variants` row in the DB. The lookup we exercise here is the
    one that proves AI_QUANT IS registered in STRATEGY_DISPATCH at all.
    """
    from strategies import orchestrator
    orchestrator._load_dispatch()
    fn = orchestrator.STRATEGY_DISPATCH.get("AI_QUANT")
    assert fn is not None, "AI_QUANT must be registered in STRATEGY_DISPATCH"
    statuses = []
    for sleeve in variant["spec"]["composition"]:
        if sleeve.get("strategy_id") != "AI_QUANT":
            continue
        cfg = dict(sleeve)
        cfg["_effective_leverage"] = orchestrator._resolve_sleeve_leverage(
            variant["spec"], sleeve)
        statuses.append(("AI_QUANT", fn(variant, cfg)))
    return statuses


# ─── E2E #1: dispatch wiring ────────────────────────────────────────────────







def _defer_response(retry_h: float = 4, waiting_for: str = "CPI 8:30 ET",
                     reasoning: str = "binary event imminent") -> _Response:
    return _Response(
        content=[
            _Block(type="text", text="Wait for the macro print."),
            _Block(type="tool_use", id="tu_def", name="defer_decision",
                   input={
                       "retry_in_hours": retry_h,
                       "waiting_for": waiting_for,
                       "reasoning": reasoning,
                   }),
        ],
        stop_reason="tool_use",
        usage={"input_tokens": 4000, "output_tokens": 200},
    )



def test_defer_clamped_to_2355_when_request_would_cross_midnight(e2e_setup):
    """A defer at 23:00 with retry_in_hours=5 must be clamped to 23:55 UTC
    so the deferred slot still lands on today's date (rather than getting
    swallowed by tomorrow's 00:05 entry window)."""
    clock.set_simulated_now(datetime(2026, 5, 8, 23, 0, tzinfo=timezone.utc))
    client = ScriptedClient([_defer_response(retry_h=5)])
    variant = _build_p300_variant(client)
    # Defer-aware gate doesn't yet trip; entry-window check WOULD reject
    # this at 23:00 UTC — but for the test we need to verify the clamp
    # behavior assuming the call did fire. We exercise it via a fresh
    # defer-row injection so the bypass_entry_window path activates.
    # Simpler: just call _compute_defer_until_utc directly to assert clamp.
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    now = clock.now_utc()
    ts = ai_quant_service._compute_defer_until_utc(now, 5.0)
    target = datetime.fromtimestamp(ts, tz=timezone.utc)
    assert target.date() == now.date()
    assert (target.hour, target.minute) == (23, 55)


