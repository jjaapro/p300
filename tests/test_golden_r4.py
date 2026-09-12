"""Golden record for R4's four window deciders and its shared executor.

Written BEFORE the orchestrator strip (BACKLOG.md, "Step 1 re-planned") to pin
what these functions do today, because the six tests the BACKLOG named as the
gate cannot see this code at all: five of them exercise only math and loaders,
and none passes a sleeve_cfg.

R4 is the sleeve the strip most endangers, for three reasons:

* ``bots/r4/runner.py`` calls the PRIVATE ``_r4_execute``, a name nothing
  outside that runner pins today.
* Its ``_effective_*`` FALLBACK arms are the live behaviour, not dead weight —
  the runner passes only ``{"priority": 100}``, so the timing-anomaly weights
  table, the gated inner leverage and the vol leverage all come from the
  fallback, and ``stacked_lev`` survives all the way to the trade.
* It has a ~20-day blind window: ``bot_r4_v1`` has never opened a trade and
  its next enabled fire is 2026-10-02, so a regression has no live output to
  diff until October. These goldens are the entire evidence base until then.

Every assertion goes through ``tests/_sleeve_surface.py``, which is the only
file that knows the call shape. That is what lets the strip change the
signature without touching this file.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import botlib
from tests import _sleeve_surface as surface
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock
from strategies.support import db as _db_mod
from strategies.support import jplus_inputs, price_feed, trade_db, variant_registry

GOLDENS = Path(__file__).resolve().parent / "goldens"
CAPITAL = 10_000.0
PRICES = {"BTC": 70_000.0, "ETH": 3_000.0}

# The anchors tests/test_r4_bot.py already uses — real calendar positions.
MON = datetime(2026, 9, 7, 6, 1, tzinfo=timezone.utc)    # Mon wk1, R4_BTC open
TUE = datetime(2026, 9, 8, 20, 1, tzinfo=timezone.utc)   # Tue, R4_ETH open
WED = datetime(2026, 9, 9, 4, 1, tzinfo=timezone.utc)    # Wed, the V2 windows


def _inputs_stub(lev=2.0, gated=False, mode="uncertain", weight=0.1):
    """today_inputs() is expensive (it walks full BTC/ETH daily+hourly+LSR
    history) and is NOT part of the surface being refactored, so stubbing it
    is correct rather than a compromise."""
    keys = ("r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2")
    w = {k: (0.0 if mode == "bear" else weight) for k in keys}
    return {"date": clock.now_utc().date().isoformat(), "mode": mode,
            "lev": lev, "gated": gated, "weights": w}


@pytest.fixture
def env(tmp_path, monkeypatch):
    db_path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, db_path)
    monkeypatch.setattr(trade_db, "DB_PATH", db_path)
    trade_db.init_db()
    variant_registry.init_schema()
    botlib.init_heartbeat_schema()
    _golden_guard.assert_fixture_db()
    _golden_guard.assert_diag_disabled()
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: PRICES[a])
    for key in surface.R4_KEYS:
        surface.reset_module_state(key)
    yield db_path
    clock.set_simulated_now(None)


def _variant():
    return {"id": "bot_r4_test", "capital_usdt": CAPITAL}


def _check(name, key, at, monkeypatch, db_path, *, inputs=None, execute=False,
           **kw):
    monkeypatch.setattr(jplus_inputs, "today_inputs",
                        lambda: (inputs or _inputs_stub()))
    clock.set_simulated_now(at)
    intents, status = surface.decide(key, variant=_variant(), **kw)
    if execute and intents:
        surface.execute(key, variant=_variant(), intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"r4_{name}.json", doc)
    assert doc == expected
    return doc


# ── the four windows, each at its own open ─────────────────────────────────

def test_golden_r4_btc_fires_at_its_window_open(env, monkeypatch):
    doc = _check("btc_open", "r4_btc", MON, monkeypatch, env, execute=True)
    assert doc["status"]["status"] == "decided"
    assert doc["db_after"]["n_trades"] == 1


def test_golden_r4_eth_fires_at_its_window_open(env, monkeypatch):
    doc = _check("eth_open", "r4_eth", TUE, monkeypatch, env, execute=True)
    assert doc["status"]["status"] == "decided"


def test_golden_r4_btc_v2_fires_at_its_window_open(env, monkeypatch):
    _check("btc_v2_open", "r4_btc_v2", WED, monkeypatch, env, execute=True)


def test_golden_r4_eth_v2_fires_at_its_window_open(env, monkeypatch):
    _check("eth_v2_open", "r4_eth_v2", WED, monkeypatch, env, execute=True)


# ── the negative twins ─────────────────────────────────────────────────────
# A golden that only records the firing bar cannot tell "the refactor
# preserved the rule" from "the refactor made it fire on everything".

def test_golden_r4_btc_rejects_the_wrong_weekday(env, monkeypatch):
    doc = _check("btc_not_calendar_day", "r4_btc", TUE, monkeypatch, env)
    assert doc["status"]["status"] == "not_calendar_day"
    assert doc["intents"] == []


def test_golden_r4_btc_rejects_before_its_open_hour(env, monkeypatch):
    at = MON.replace(hour=5, minute=59)
    doc = _check("btc_before_open", "r4_btc", at, monkeypatch, env)
    assert doc["status"]["status"] == "before_open_window"


def test_golden_r4_btc_rejects_after_its_close_hour(env, monkeypatch):
    at = MON.replace(hour=18, minute=1)
    doc = _check("btc_after_close", "r4_btc", at, monkeypatch, env)
    assert doc["status"]["status"] == "after_close_window"


def test_golden_r4_btc_rejects_week_three(env, monkeypatch):
    at = datetime(2026, 9, 21, 6, 1, tzinfo=timezone.utc)   # Monday, day 21
    doc = _check("btc_not_wk_1_2", "r4_btc", at, monkeypatch, env)
    assert doc["status"]["status"] == "not_wk_1_2"


def test_golden_r4_missing_weight_key_refuses_cleanly(env, monkeypatch):
    """The weights lookup is `ti["weights"].get(key, 0.0)`, NOT a subscript.
    A regime dict that omits this window's key must produce a clean
    zero-weight refusal, not a KeyError inside the live decide path.

    Added because the step-7 mutation drill found that swapping `.get` for a
    subscript left every other golden green — the stub always supplies the
    key, so nothing exercised the difference."""
    stub = _inputs_stub()
    stub["weights"] = {k: v for k, v in stub["weights"].items()
                       if k != "r4_btc"}
    doc = _check("btc_missing_weight_key", "r4_btc", MON, monkeypatch, env,
                 inputs=stub)
    assert doc["status"]["status"] == "regime_zero_weight"
    assert doc["intents"] == []
    assert doc["db_after"]["n_trades"] == 0


def test_golden_r4_bear_regime_zeroes_the_weight(env, monkeypatch):
    """The bear gate is a real regime kill switch and lives in the FALLBACK
    arm of the weights lookup — the arm the bot always takes."""
    doc = _check("btc_bear_zero_weight", "r4_btc", MON, monkeypatch, env,
                 inputs=_inputs_stub(mode="bear"))
    assert doc["intents"] == []
    assert doc["db_after"]["n_trades"] == 0


# ── the leverage chain, which the bot actually consumes ────────────────────

def test_golden_r4_stacked_leverage_survives_to_the_intent(env, monkeypatch):
    """Unlike the other five sleeves, r4's leverage is NOT overwritten by the
    runner — bots/r4/runner.py consumes min(intent.leverage, LEV_CAP). So the
    sleeve's stacked_lev is load-bearing and must be pinned exactly."""
    doc = _check("btc_ungated_lev", "r4_btc", MON, monkeypatch, env,
                 inputs=_inputs_stub(lev=2.0, gated=False))
    assert doc["intents"][0]["leverage"] == pytest.approx(5.0)

    surface.reset_module_state("r4_btc")
    gated = _check("btc_gated_lev", "r4_btc", MON, monkeypatch, env,
                   inputs=_inputs_stub(lev=2.0, gated=True))
    assert gated["intents"][0]["leverage"] == pytest.approx(2.0)


def test_golden_r4_gate_arm_multiplies_the_ungated_constant(env, monkeypatch):
    """The `_effective_gate` arm is UNGATED * gate.leverage_mult, not
    GATED * mult. Nothing in the suite covered this before; the strip's own
    plan warns that the obvious rewrite gets it backwards."""
    class _Gate:
        leverage_mult = 0.5

    doc = _check("btc_gate_arm", "r4_btc", MON, monkeypatch, env,
                 gate=_Gate())
    # R4_INNER_LEV_UNGATED (2.5) * 0.5 = 1.25, then * vol lev 2.0 = 2.5
    assert doc["intents"][0]["leverage"] == pytest.approx(2.5)


# ── warm globals: the live bot never calls decide() against virgin state ───

def test_golden_r4_second_call_on_the_same_day_is_already_open(env, monkeypatch):
    """R4's per-day guard is DB-backed (`_has_trade_for_day`), not a module
    dict, so resetting module state does not reset it — which is the point."""
    _check("btc_open_twice_first", "r4_btc", MON, monkeypatch, env,
           execute=True)
    doc = _check("btc_open_twice_second", "r4_btc", MON, monkeypatch, env)
    assert doc["status"]["status"] == "already_open"
    assert doc["db_after"]["n_trades"] == 1


def test_golden_r4_windows_overlap_by_design(env, monkeypatch):
    """Up to three concurrent R4 positions is INTENDED — the calendar
    anomalies genuinely overlap. A refactor that serialised them would look
    like a fix and would be a regression.

    R4_ETH runs Tue 20:00 -> Wed 20:00 and R4_ETH_V2 Wed 04:00 -> 14:00, so
    the overlap is a V1 position entered on Tuesday still open when V2 enters
    on Wednesday morning — two ENTRY times, not one."""
    _check("overlap_eth", "r4_eth", TUE, monkeypatch, env, execute=True)
    doc = _check("overlap_eth_v2", "r4_eth_v2", WED, monkeypatch, env,
                 execute=True)
    assert doc["db_after"]["n_trades"] == 2, \
        "the ETH windows must be able to hold concurrent positions"
    strategies = {t["strategy"] for t in doc["db_after"]["trades"]}
    assert strategies == {"JPLUS_R4_ETH", "JPLUS_R4_ETH_V2"}
    assert all(t["status"] == "open" for t in doc["db_after"]["trades"])


# ── the executor the bot calls by its private name ─────────────────────────

def test_golden_r4_execute_writes_the_expected_row(env, monkeypatch):
    doc = _check("btc_execute_row", "r4_btc", MON, monkeypatch, env,
                 execute=True)
    row = doc["db_after"]["trades"][0]
    assert row["strategy"] == "JPLUS_R4_BTC"
    assert row["asset"] == "BTC" and row["direction"] == "LONG"
    assert row["status"] == "open"
    # The idempotency key is the window + UTC day (shipped 2026-09-12).
    assert row["unique_key_suffix"] == "JPLUS_R4_BTC|BTC|2026-09-07"


def test_private_executor_is_the_name_the_bot_calls():
    """bots/r4/runner.py calls r4._r4_execute. If the strip renames it without
    updating the runner, the bot raises on its first fire — in October, with
    no other coverage."""
    from strategies.sleeves.timing_anomalies.internal.r4 import signal
    assert callable(getattr(signal, "_r4_execute", None))
    src = (Path(__file__).resolve().parents[1]
           / "bots" / "r4" / "runner.py").read_text(encoding="utf-8")
    assert "_r4_execute" in src


def test_goldens_are_committed_and_non_empty():
    """Guards against the net silently degrading to 'writes a golden, asserts
    it equals itself' if the files are ever lost."""
    files = sorted(GOLDENS.glob("r4_*.json"))
    assert len(files) >= 14, f"only {len(files)} r4 goldens on disk"
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert "status" in doc and "db_after" in doc
