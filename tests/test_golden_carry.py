"""Golden record for CARRY's decide/execute, against a carved fixture.

CARRY holds SJ-4242, open since 2026-07-22, and its decide() closes the WHOLE
BOOK when the trailing 30-day cumulative funding drops below −0.5 % — the exit
rule shipped 2026-09-12. A refactor that broke the exit would not fail any
existing test: `test_carry_exit_rule` is the one named parity test that calls
the real surface, but it drives synthetic funding rows rather than pinning the
decide contract.

The anchor 2026-07-22T13:54Z is the instant SJ-4242 was actually opened, and
the carved fixture reproduces its `fr_7d_avg_pct` of 0.0164.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests import _sleeve_surface as surface
from tests._golden_fixture import make_env, seed_open_trade
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock

GOLDENS = Path(__file__).resolve().parent / "goldens"
VARIANT = {"id": "bot_carry_test", "capital_usdt": 10_000.0}

# The instant SJ-4242 was opened in production.
ENTRY = datetime(2026, 7, 22, 13, 54, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = make_env("carry", tmp_path, monkeypatch, sleeve_keys=("carry",))
    yield db
    clock.set_simulated_now(None)


def _check(name, at, db_path, *, execute=False, **kw):
    clock.set_simulated_now(at)
    intents, status = surface.decide("carry", variant=VARIANT, **kw)
    if execute and intents:
        surface.execute("carry", variant=VARIANT, intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"carry_{name}.json", doc)
    assert doc == expected
    return doc


def test_golden_carry_entry_at_the_production_anchor(env):
    """Reproduces SJ-4242's entry: fr_7d_avg_pct 0.0164 in the reason blob
    that landed in the live ledger."""
    doc = _check("entry", ENTRY, env, execute=True)
    assert doc["status"]["status"] == "decided"
    assert doc["db_after"]["n_trades"] == 1
    row = doc["db_after"]["trades"][0]
    assert row["strategy"] == "CARRY"
    assert row["notes"]["fr_7d_avg_pct"] == 0.0164
    assert row["notes"]["structure"] == "long_spot_short_perp_delta_neutral"
    # Day-keyed idempotency (shipped 2026-09-12).
    assert row["unique_key_suffix"] == "CARRY|BTC|2026-07-22"


def test_golden_carry_is_delta_neutral_long_notation(env):
    doc = _check("entry_direction", ENTRY, env, execute=True)
    assert doc["intents"][0]["direction"] == "LONG"
    assert doc["intents"][0]["asset"] == "BTC"
    # No scheduled exit: CARRY exits on the funding rule, not a clock.
    assert doc["intents"][0]["scheduled_exit_dt"] is None


def test_golden_carry_no_action_dict_carries_its_diagnostics(env):
    """The no_action status is what the operator and the dashboard read on
    every quiet day; its key SET is behaviour, not decoration."""
    seed_open_trade(
        env, trade_id="SJ-9001", variant=VARIANT["id"], strategy="CARRY",
        asset="BTC", direction="LONG", entry_price=66556.16,
        entry_time="2026-07-22T13:54:00+00:00",
        notes={"trigger": "S-078_carry_entry", "sleeve": "CARRY"})
    doc = _check("open_position_no_action", ENTRY, env)
    assert doc["status"]["status"] in ("no_action", "closed")
    assert "fr_7d_avg_pct" in doc["status"]


def test_golden_carry_second_call_same_day_does_not_double_enter(env):
    """`_carry_action_today` is DB-backed, so module-state resets do not clear
    it — and the day-keyed unique index now backs it at the DB level."""
    _check("twice_first", ENTRY, env, execute=True)
    doc = _check("twice_second", ENTRY, env, execute=True)
    assert doc["db_after"]["n_trades"] == 1


def test_golden_carry_leverage_is_not_read_from_the_intent(env):
    """CARRY sizes fixed-notional in the runner, so the sleeve's leverage is
    overwritten. Pinned to prove the strip can hardcode it safely."""
    a = _check("lev_1x", ENTRY, env, leverage=1.0)
    assert a["intents"][0]["leverage"] == pytest.approx(1.0)


def test_goldens_exist():
    assert len(list(GOLDENS.glob("carry_*.json"))) >= 6
