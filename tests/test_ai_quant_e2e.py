"""End-to-end test: drive a full AI_QUANT day through variant_engine.

What's exercised:

  1. AI_QUANT is registered in STRATEGY_DISPATCH and reachable via the
     same dispatch path the live runner uses.
  2. Off-window ticks short-circuit cheaply (no API call, no DB writes).
  3. The first in-window tick fires the LLM (mocked), persists a
     decision row, and opens a shadow trade.
  4. The next in-window tick is gated by idempotency — no second API
     call, no second trade.
  5. The next UTC day's in-window tick fires the LLM again with the
     existing position visible in the context, and the reconciliation
     matrix flips the trade direction when the model decides.
  6. backtest_runner.tick_replay_variant skips AI_QUANT (non-deterministic
     sleeves are excluded from historical replay).
  7. P-300's register_p300 spec includes AI_QUANT in its composition.

This is the integration safety net: any break in the chain (sleeve
registration / journal idempotency / variant_engine dispatch / register
config / backtest skip) surfaces here.
"""
from __future__ import annotations

import importlib
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
                avg_entry_price REAL
            );
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
    """Construct a variant dict shaped like what variant_engine.tick reads.
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
                 # Test-only injection: tests hand the variant_engine
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
    """Drive the AI_QUANT sleeve through variant_engine's _tick_composition.

    Returns a list of (strategy_id, status_dict) for every sleeve that
    fired this tick — filtered to AI_QUANT only since that's what these
    tests care about.

    We call the dispatcher directly via STRATEGY_DISPATCH rather than
    variant_engine._tick_composition because the latter requires a fully
    seeded `variants` row in the DB. The lookup we exercise here is the
    one that proves AI_QUANT IS registered in STRATEGY_DISPATCH at all.
    """
    from services import variant_engine
    variant_engine._load_dispatch()
    fn = variant_engine.STRATEGY_DISPATCH.get("AI_QUANT")
    assert fn is not None, "AI_QUANT must be registered in STRATEGY_DISPATCH"
    statuses = []
    for sleeve in variant["spec"]["composition"]:
        if sleeve.get("strategy_id") != "AI_QUANT":
            continue
        cfg = dict(sleeve)
        cfg["_effective_leverage"] = variant_engine._resolve_sleeve_leverage(
            variant["spec"], sleeve)
        statuses.append(("AI_QUANT", fn(variant, cfg)))
    return statuses


# ─── E2E #1: dispatch wiring ────────────────────────────────────────────────

def test_strategy_dispatch_includes_ai_quant():
    """Sanity: the dispatch registry has the new sleeve. Exact assertion
    so a missed import would surface here."""
    from services import variant_engine
    variant_engine._load_dispatch()
    assert "AI_QUANT" in variant_engine.STRATEGY_DISPATCH
    fn = variant_engine.STRATEGY_DISPATCH["AI_QUANT"]
    assert callable(fn)
    # And it's the right callable, not some old test patch
    from strategies.sleeves.ai_quant import signal as ai_quant_service
    assert fn is ai_quant_service.try_fire_for_variant


# ─── E2E #2: off-window tick is a cheap no-op ──────────────────────────────

def test_off_window_tick_short_circuits_no_api_no_writes(e2e_setup):
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    client = ScriptedClient([])  # any API call would raise
    variant = _build_p300_variant(client)
    statuses = _dispatch_via_variant_engine(variant)
    assert statuses == [("AI_QUANT", {"status": "off_window"})]
    assert client.calls == []
    # No journal row, no trade
    assert journal.get_today_decision("p300_e2e_test") is None
    assert _ai_quant_trades(e2e_setup["dash_db"]) == []


# ─── E2E #3: in-window day-1 LONG opens a trade and journals ───────────────

def test_in_window_first_tick_fires_long_opens_trade_journals(e2e_setup):
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    client = ScriptedClient([_decision_response("LONG", conviction=70)])
    variant = _build_p300_variant(client)
    statuses = _dispatch_via_variant_engine(variant)

    # Sleeve reports 'decided' with an opened trade
    sid, status = statuses[0]
    assert sid == "AI_QUANT"
    assert status["status"] == "decided"
    assert status["decision"] == "LONG"
    assert status["trade_action"].startswith("opened:SJ-")

    # Anthropic was actually called
    assert len(client.calls) == 1

    # Trade row created with the right shape
    trades = _ai_quant_trades(e2e_setup["dash_db"], only_open=True)
    assert len(trades) == 1
    t = trades[0]
    assert t["direction"] == "LONG"
    assert t["entry_price"] == 80_000.0
    assert t["leverage"] == 3.0
    # weight 2.0 × 70/100 = 1.4
    assert t["allocation_pct"] == pytest.approx(1.4, rel=1e-6)
    assert t["strategy_variant"] == "p300_e2e_test"
    assert t["execution_mode"] == "SHADOW"

    # Journal row mirrors the trade action
    j = journal.get_today_decision("p300_e2e_test")
    assert j is not None
    assert j["decided"] == "LONG"
    assert j["conviction"] == 70
    assert j["trade_action"] == status["trade_action"]


# ─── E2E #4: idempotency on the second in-window tick ──────────────────────

def test_idempotency_blocks_second_tick_same_day(e2e_setup):
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    client = ScriptedClient([_decision_response("LONG", conviction=70)])
    variant = _build_p300_variant(client)
    _dispatch_via_variant_engine(variant)
    n_calls_after_first = len(client.calls)
    n_trades_after_first = len(_ai_quant_trades(e2e_setup["dash_db"]))
    assert n_calls_after_first == 1
    assert n_trades_after_first == 1

    # Second tick same minute later in the window
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc))
    statuses = _dispatch_via_variant_engine(variant)
    assert statuses[0][1]["status"] == "already_fired_today"

    # No new API calls, no new trades
    assert len(client.calls) == n_calls_after_first
    assert len(_ai_quant_trades(e2e_setup["dash_db"])) == n_trades_after_first


# ─── E2E #5: next UTC day re-fires; reconciliation flips the position ──────

def test_next_day_in_window_reconciles_with_existing_position_via_flip(e2e_setup):
    # Day 1: in-window LONG opens
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    day_1_client = ScriptedClient([_decision_response("LONG", conviction=70)])
    variant = _build_p300_variant(day_1_client)
    _dispatch_via_variant_engine(variant)
    open_after_d1 = _ai_quant_trades(e2e_setup["dash_db"], only_open=True)
    assert len(open_after_d1) == 1
    long_tid = open_after_d1[0]["id"]

    # Day 2: same window, but the LLM decides SHORT — flip
    clock.set_simulated_now(datetime(2026, 5, 9, 0, 7, tzinfo=timezone.utc))
    day_2_client = ScriptedClient([_decision_response("SHORT", conviction=80)])
    variant_d2 = _build_p300_variant(day_2_client)
    statuses = _dispatch_via_variant_engine(variant_d2)

    assert statuses[0][1]["status"] == "decided"
    assert statuses[0][1]["trade_action"].startswith(f"flipped:{long_tid}->SJ-")

    # Old LONG closed, new SHORT open
    all_trades = _ai_quant_trades(e2e_setup["dash_db"])
    by_id = {t["id"]: t for t in all_trades}
    assert by_id[long_tid]["status"] == "closed"
    new_short = next(t for t in all_trades
                     if t["id"] != long_tid and t["status"] == "open")
    assert new_short["direction"] == "SHORT"
    assert new_short["entry_price"] == 80_000.0

    # Two journal rows now exist (one per day) — get_recent_decisions
    # returns both
    recent = journal.get_recent_decisions("p300_e2e_test", days=7)
    assert len(recent) == 2
    assert recent[0]["decided"] == "SHORT"  # most recent
    assert recent[1]["decided"] == "LONG"


# ─── E2E #6: error path persists ERROR row, idempotency still triggers ────

def test_error_path_persists_error_and_blocks_retry_same_day(e2e_setup):
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    # First call raises, second call would never run
    client = ScriptedClient([RuntimeError("Anthropic 503"), _decision_response("LONG")])
    variant = _build_p300_variant(client)
    statuses = _dispatch_via_variant_engine(variant)
    assert statuses[0][1]["status"] == "decision_error"

    j = journal.get_today_decision("p300_e2e_test")
    assert j is not None
    assert j["decided"] == "ERROR"
    assert "Anthropic 503" in j["error"]

    # Second tick: idempotency catches the error row, no retry
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 14, tzinfo=timezone.utc))
    statuses_2 = _dispatch_via_variant_engine(variant)
    assert statuses_2[0][1]["status"] == "already_fired_today"
    # Only the first call was made — no retry of the second scripted response
    assert len(client.calls) == 1
    # No trade was opened
    assert _ai_quant_trades(e2e_setup["dash_db"]) == []


# ─── E2E #7: backtest_runner skips non-deterministic sleeves ───────────────

def test_backtest_runner_skips_non_deterministic_sleeves(monkeypatch):
    """tick_replay_variant must NOT dispatch sleeves with
    params.deterministic=False — that's how AI_QUANT is excluded from
    historical replay."""
    import backtest_runner

    fired: list[str] = []

    def fake_dispatcher(variant, sleeve_cfg):
        fired.append(sleeve_cfg.get("strategy_id", "?"))
        return {"status": "test"}

    # Inject a fake AI_QUANT dispatcher into STRATEGY_DISPATCH so we
    # could detect a leak. If backtest_runner calls it, the test fails.
    from services import variant_engine
    variant_engine._load_dispatch()
    monkeypatch.setitem(variant_engine.STRATEGY_DISPATCH, "AI_QUANT", fake_dispatcher)

    # Also a non-skipping deterministic sleeve so we know dispatch IS
    # working in general
    def detector(variant, sleeve_cfg):
        fired.append(sleeve_cfg.get("strategy_id", "?"))
        return {"status": "test"}

    monkeypatch.setitem(variant_engine.STRATEGY_DISPATCH, "DET_TEST", detector)

    variant = {
        "id": "v",
        "spec": {
            "composition": [
                {"strategy_id": "AI_QUANT",
                 "params": {"deterministic": False}},
                {"strategy_id": "DET_TEST", "params": {}},
            ],
        },
    }
    backtest_runner.tick_replay_variant(variant)
    assert "AI_QUANT" not in fired, "AI_QUANT should be skipped in backtest"
    assert "DET_TEST" in fired, "deterministic sleeves should still fire"


# ─── E2E #8: register_p300 spec includes AI_QUANT ──────────────────────────

def test_register_p300_spec_includes_ai_quant_with_correct_shape():
    """Confirm the production composition spec has AI_QUANT, with
    deterministic=False and the expected weight."""
    spec = _import_register_p300().build_spec()
    comp = spec["composition"]
    aq = next((c for c in comp if c.get("strategy_id") == "AI_QUANT"), None)
    assert aq is not None, "register_p300 spec must include AI_QUANT"
    assert aq["weight_pct"] == 2.0
    assert aq["params"]["asset"] == "BTC"
    assert aq["params"]["deterministic"] is False
    assert aq["params"]["leverage"] == 3.0
    # Listed in sleeves_live for the runtime banner
    assert "AI_QUANT" in spec["sleeves_live"]


def _import_register_p300():
    """register_p300 lives at repo root, not inside a package; load via
    importlib so the test works no matter the cwd."""
    repo = Path(__file__).resolve().parent.parent
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    if "register_p300" in sys.modules:
        return importlib.reload(sys.modules["register_p300"])
    return importlib.import_module("register_p300")


# ─── E2E #9: defer flow — defer at 00:07, wait, re-fire later in the day ──

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


def test_defer_then_wait_then_refire_full_flow(e2e_setup):
    """First in-window call defers for 4h. Next minute's tick is gated
    by 'deferred_waiting'. After 4h passes the next tick fires the LLM
    again — this time submitting LONG — and the entry-window check is
    bypassed because today already has an expired defer row."""
    # 00:07 UTC — first call defers
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    client = ScriptedClient([
        _defer_response(retry_h=4, waiting_for="BTC > 83k"),
        _decision_response("LONG", conviction=72),
    ])
    variant = _build_p300_variant(client)
    statuses_1 = _dispatch_via_variant_engine(variant)
    s1 = statuses_1[0][1]
    assert s1["status"] == "deferred"
    assert s1["waiting_for"] == "BTC > 83k"
    assert s1["defers_today"] == 1
    # No trade emitted on a defer
    assert _ai_quant_trades(e2e_setup["dash_db"]) == []
    # Journal row exists with decided='DEFER' and defer_until_utc set
    j = journal.get_today_decision("p300_e2e_test")
    assert j["decided"] == "DEFER"
    assert j["defer_until_utc"] is not None

    # 00:12 — still inside the entry window but defer is active → blocked
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc))
    statuses_2 = _dispatch_via_variant_engine(variant)
    assert statuses_2[0][1]["status"] == "deferred_waiting"
    # No new API call burned while waiting
    assert len(client.calls) == 1

    # 04:08 — past the 4h target, outside the entry window. Defer-aware
    # gate must bypass the window and let the call fire.
    clock.set_simulated_now(datetime(2026, 5, 8, 4, 8, tzinfo=timezone.utc))
    statuses_3 = _dispatch_via_variant_engine(variant)
    s3 = statuses_3[0][1]
    assert s3["status"] == "decided"
    assert s3["decision"] == "LONG"
    assert s3["trade_action"].startswith("opened:SJ-")
    # The second scripted API call was consumed
    assert len(client.calls) == 2


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


def test_defer_chain_cap_strips_defer_tool_on_fourth_call(e2e_setup):
    """After 3 defers today, the 4th run_decision call must NOT include
    the defer_decision tool — forcing the model to commit. We assert on
    the tools list passed to the API client to prove this."""
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 7, tzinfo=timezone.utc))
    # 4 calls: 3 defers, then a forced LONG (the model is told it has
    # no defer tool available)
    client = ScriptedClient([
        _defer_response(retry_h=1),  # 00:07 -> retry at 01:07
        _defer_response(retry_h=1),  # 01:07 -> retry at 02:07
        _defer_response(retry_h=1),  # 02:07 -> retry at 03:07
        _decision_response("LONG", conviction=60),  # 03:07 forced commit
    ])
    variant = _build_p300_variant(client)

    # 00:07 first defer
    _dispatch_via_variant_engine(variant)
    # 01:08 second
    clock.set_simulated_now(datetime(2026, 5, 8, 1, 8, tzinfo=timezone.utc))
    _dispatch_via_variant_engine(variant)
    # 02:08 third
    clock.set_simulated_now(datetime(2026, 5, 8, 2, 8, tzinfo=timezone.utc))
    _dispatch_via_variant_engine(variant)
    # 03:08 fourth — defer tool must be stripped
    clock.set_simulated_now(datetime(2026, 5, 8, 3, 8, tzinfo=timezone.utc))
    statuses_4 = _dispatch_via_variant_engine(variant)

    assert statuses_4[0][1]["status"] == "decided"
    assert statuses_4[0][1]["decision"] == "LONG"

    # The 4th API call's tools list must NOT include defer_decision
    fourth_call_tools = {t["name"] for t in client.calls[3]["tools"]
                          if "name" in t}
    assert "defer_decision" not in fourth_call_tools, (
        "After 3 defers, the defer tool must be stripped to force commit")
    # And it WAS present on the first 3 calls
    for i in range(3):
        names = {t["name"] for t in client.calls[i]["tools"] if "name" in t}
        assert "defer_decision" in names, f"call {i} should have defer tool"
