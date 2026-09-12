"""Golden record for SQUEEZE_BULL's decide/execute, both variants.

Two live paper variants run on the SAME signals in one process, differing only
in the exit: `bot_squeeze_bull_v1` (-2 % stop) and `bot_squeeze_bull_nostop_v1`
(target + 48 h only), with a pre-registered paired re-cut at n = 20 / 30. The
`use_stop` flag is what separates them, and it reaches the sleeve through the
cfg dict the strip is deleting — so a strip that drops or mis-threads it
silently changes the notional and the `_stop_price` written into the reason
blob of a live paper twin. Both variants are therefore inside this net; the
re-plan called out their absence as a required fix.

The anchor 2026-09-11T18:00:31Z is SJ-4250's real entry. The carved fixture
reproduces its oi_chg_4h, px_chg_4h and ret_30d_backonly to full float
precision.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests import _sleeve_surface as surface
from tests._golden_fixture import make_env
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock, price_feed

GOLDENS = Path(__file__).resolve().parent / "goldens"
VARIANT = {"id": "bot_squeeze_bull_test", "capital_usdt": 10_000.0}

FIRE = datetime(2026, 9, 11, 18, 0, 31, tzinfo=timezone.utc)   # SJ-4250
ENTRY_PRICE = 77493.9


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = make_env("squeeze_bull", tmp_path, monkeypatch,
                  sleeve_keys=("squeeze_bull",))
    # The sweep prices exits off the live 1m feed, which the fixture does not
    # carry. Pin it so the sweep is deterministic rather than absent.
    monkeypatch.setattr(price_feed, "get_current_price", lambda a: ENTRY_PRICE)
    yield db
    clock.set_simulated_now(None)


def _check(name, at, db_path, *, execute=False, **kw):
    clock.set_simulated_now(at)
    intents, status = surface.decide("squeeze_bull", variant=VARIANT, **kw)
    if execute and intents:
        surface.execute("squeeze_bull", variant=VARIANT, intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"squeeze_bull_{name}.json", doc)
    assert doc == expected
    return doc


def test_golden_squeeze_bull_fires_at_the_production_anchor(env):
    """Reproduces SJ-4250: the flush, the price drop and the backward-only
    30-day return that passed the bull gate."""
    doc = _check("fire_stop", FIRE, env, execute=True)
    assert doc["status"]["status"] == "decided"
    r = doc["intents"][0]["reason"]
    assert r["regime"] == "bull_30d"
    assert r["bar_ts"] == 1789146000
    assert r["oi_chg_4h"] < -0.02 and r["px_chg_4h"] < -0.005
    assert r["ret_30d_backonly"] > 0.10


def test_golden_squeeze_bull_stop_variant_writes_a_stop_price(env):
    doc = _check("fire_stop_levels", FIRE, env, execute=True, use_stop=True)
    r = doc["intents"][0]["reason"]
    assert r["_stop_price"] is not None
    assert r["_target_price"] is not None
    assert r["exit_policy"] != "time_only" if "exit_policy" in r else True
    # R is measured against the 2% reference distance for BOTH variants.
    assert r["_reference_stop_price"] == pytest.approx(r["_stop_price"])


def test_golden_squeeze_bull_nostop_twin_has_no_stop_but_keeps_the_reference(env):
    """The live no-stop twin. `_stop_price` must be None (nothing to sweep on)
    while `_reference_stop_price` survives, because the re-cut compares both
    ledgers in the same R units."""
    doc = _check("fire_nostop", FIRE, env, execute=True, use_stop=False)
    r = doc["intents"][0]["reason"]
    assert r["_stop_price"] is None, "the no-stop twin must not carry a stop"
    assert r["_target_price"] is not None, "it keeps the +3% target"
    assert r["_reference_stop_price"] is not None, \
        "R is measured against the 2% reference distance for both variants"


def test_golden_squeeze_bull_both_variants_see_the_same_signal(env):
    """The two variants differ ONLY in the exit. If a strip changed the entry
    for one of them, the paired re-cut would be comparing different trades."""
    a = _check("pair_stop", FIRE, env, use_stop=True)
    surface.reset_module_state("squeeze_bull")
    b = _check("pair_nostop", FIRE, env, use_stop=False)
    for key in ("bar_ts", "oi_chg_4h", "px_chg_4h", "ret_30d_backonly",
                "regime"):
        assert a["intents"][0]["reason"][key] == b["intents"][0]["reason"][key]
    assert a["intents"][0]["allocation_pct"] == b["intents"][0]["allocation_pct"]


# ── negative twins ─────────────────────────────────────────────────────────

def test_golden_squeeze_bull_rejects_a_bar_with_no_flush(env):
    at = FIRE.replace(day=9, hour=12, minute=0, second=31)
    doc = _check("no_flush", at, env)
    assert doc["status"]["status"] in ("no_flush", "cooldown",
                                       "regime_not_bull")
    assert doc["intents"] == []


def test_golden_squeeze_bull_holds_the_two_percent_flush_threshold(env):
    """A bar that clears every OTHER gate and fails only on the flush size:
    bull regime, price down 1.58 %, but open interest down just 1.12 % — so
    -2 % refuses it and -1 % would not.

    Added because the step-7 drill loosened FLUSH_THRESHOLD and every golden
    stayed green: the firing anchor clears the looser bar too, and the other
    twins reject for different reasons. Without this, the threshold that
    defines the sleeve was unpinned."""
    at = datetime(2026, 8, 25, 11, 0, 31, tzinfo=timezone.utc)
    doc = _check("flush_too_small", at, env)
    assert doc["status"]["status"] == "no_flush"
    assert -0.02 < doc["status"]["oi_chg_4h"] <= -0.011, \
        "anchor drifted: it must sit between the two thresholds"
    assert doc["status"]["px_chg_4h"] <= -0.005, \
        "anchor must fail ONLY on the flush size, not on the price leg"
    assert doc["intents"] == []


def test_golden_squeeze_bull_rejects_a_kept_flush_outside_the_bull_regime(env):
    """The bull gate is the whole sleeve — it is what the 2026-09-09
    re-validation chose (backward-only daily, +0.246 R) over the study's
    peeking construction. This anchor is a KEPT FLUSH whose regime is not
    bull, so it reaches the gate and is refused there.

    Added because the step-7 mutation drill deleted the gate and every other
    squeeze_bull golden stayed green: the firing anchor IS bull, and the
    other negative twin rejects earlier in the ladder (no_flush / cooldown),
    so nothing exercised this branch."""
    at = datetime(2026, 8, 21, 13, 0, 31, tzinfo=timezone.utc)
    doc = _check("regime_not_bull", at, env)
    assert doc["status"]["status"] == "regime_not_bull"
    assert doc["status"]["regime"] != "bull_30d"
    assert doc["intents"] == []
    assert doc["db_after"]["n_trades"] == 0


def test_golden_squeeze_bull_status_dict_keeps_its_diag_keys(env):
    """decide() merges diag FIRST so a trailing **diag cannot clobber
    'status' — that ordering is behaviour, and only a full-dict comparison
    catches its loss."""
    doc = _check("status_shape", FIRE, env)
    assert doc["status"]["status"] == "decided"
    for k in ("bar_ts", "bar_iso", "oi_chg_4h", "px_chg_4h", "regime"):
        assert k in doc["status"], f"status lost its {k} diagnostic"


def test_golden_squeeze_bull_second_call_in_the_same_hour_is_skipped(env):
    """`_last_eval_hour` is set BEFORE the entry check but AFTER the no-bars
    return. The live bot ticks every 60 s against warm globals, so this is the
    common path, not an edge case."""
    _check("hour_guard_first", FIRE, env, execute=True)
    doc = _check("hour_guard_second", FIRE.replace(second=45), env)
    assert doc["status"]["status"] == "already_evaluated_this_hour"
    assert doc["db_after"]["n_trades"] == 1


def test_goldens_exist():
    assert len(list(GOLDENS.glob("squeeze_bull_*.json"))) >= 8
