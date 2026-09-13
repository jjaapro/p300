"""Golden record for CHENTO_TRIPLE_V3, both assets.

Two things make chento the awkward one.

**The asset is fixed at import.** `CHENTO_V3_ASSET` is read in config.py and
drives the table names AND `FILTER_NO_TILT` (True for BTC, False for ETH), so
the assets are behaviourally different, not merely table-different. One pytest
process cannot hold both goldens: setenv + reload half-works and leaves stale
module globals and a stale feature-cache frame behind, which makes the BTC
golden fail intermittently depending on test order. So the ETH golden runs in
its own interpreter via `_chento_golden_runner.py`, and the asset is recorded
INSIDE the document so a mis-set env fails loudly instead of quietly testing
BTC twice. That import-time coupling is also what the refactor intends to
remove (asset becomes a call-time parameter), which makes these two goldens
the evidence that the move preserved behaviour.

**The live ledger is the ground truth, and these goldens now agree with it.**
Production booked three signals on 2026-08-21/22 (SJ-4243..SJ-4249, doubled by
the 2026-08-15..24 incident) and recorded the `okx_delta_z` each fired on:
1.4609786480 (bar 06:00), 0.1437340057 (19:30), 1.6751492272 (03:45). The
goldens below reproduce all three to ten significant figures.

Until 2026-09-13 they did not, and this paragraph used to explain the gap
away. It said the 06:00 signal "now comes back filter_blocked" because "the
feed upserted those bars after the fires", and warned that anyone seeding a
golden from the notes blob "gets a test that fails on day one". That was
wrong, and the reasoning is worth keeping as a warning: a plausible
explanation in a docstring steered readers AWAY from the one check that
exposed the bug. The real cause was look-ahead. chento's three loaders had no
upper clock bound, so a frozen-clock golden read the fixture's tail — 59
minutes of future OKX data at the 06:00 bar — and recorded okx_delta_z -0.73
for a bar live had actually traded at +1.46. The $1 entry-price difference it
cited is the live fill against the 1m feed; it cannot flip a z-score's sign.

So: when a golden and the live ledger disagree, suspect the golden. BACKLOG 7b.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests import _sleeve_surface as surface
from tests._golden_fixture import fixture_db, make_env
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock

REPO = Path(__file__).resolve().parents[1]
GOLDENS = Path(__file__).resolve().parent / "goldens"
VARIANT = {"id": "bot_chento_btc_test", "capital_usdt": 10_000.0}

# The three bars production actually traded, each reproduced by today's code.
FIRE_0600 = datetime(2026, 8, 21, 6, 0, 5, tzinfo=timezone.utc)     # SJ-4243/4244
FIRE_A = datetime(2026, 8, 21, 19, 30, 5, tzinfo=timezone.utc)      # SJ-4245/4246
FIRE_B = datetime(2026, 8, 22, 3, 45, 5, tzinfo=timezone.utc)       # SJ-4248/4249

#: The okx_delta_z the running bot recorded in trades.notes for each fire.
LIVE_OKX_Z = {FIRE_0600: 1.4609786480063527, FIRE_A: 0.14373400566189817,
              FIRE_B: 1.675149227184317}

# A bar where the OKX gate genuinely blocks. It was 2026-08-21 06:00 until
# 2026-09-13 — but that "block" existed only with future data, and live traded
# the signal (see FIRE_0600). 2026-07-16 06:30 blocks on the causal
# information set (z -2.66) AND blocked on the old peeking one (z -0.59), so it
# is a real gate event rather than a boundary artifact. Needs the fixture's
# 45-day OKX carve.
OKX_BLOCKED = datetime(2026, 7, 16, 6, 30, 5, tzinfo=timezone.utc)


#: The 2026-08-21 close. chento's sweep prices open positions off the live 1m
#: feed, which the fixture cannot carry; the minute path has its own tests.
SWEEP_PRICE = 75_255.8


@pytest.fixture
def env(tmp_path, monkeypatch):
    from bots.chento_v3.strategy import config as ch_cfg
    from strategies.support import price_feed
    if ch_cfg.ASSET != "BTC":
        pytest.skip(f"chento imported as {ch_cfg.ASSET}; BTC goldens need a "
                    f"BTC process (ETH runs via _chento_golden_runner.py)")
    db = make_env("chento", tmp_path, monkeypatch, sleeve_keys=("chento_btc",))
    monkeypatch.setattr(price_feed, "get_current_price", lambda a=None: SWEEP_PRICE)
    monkeypatch.setattr(price_feed, "_get_current_price", lambda a=None: SWEEP_PRICE)
    yield db
    clock.set_simulated_now(None)


def _decided_okx_z(intents) -> float:
    """okx_delta_z for a DECIDED signal. It is not on the status dict — only a
    filter_blocked status carries it at the top level — but on the intent's
    filter diagnostics, the same blob the ledger writes to trades.notes."""
    assert intents, "no intent — the signal did not decide"
    reason = intents[0].reason
    reason = reason if isinstance(reason, dict) else vars(reason)
    return reason["_filter_diag"]["okx_delta_z"]


def _check(name, at, db_path, *, execute=False, **kw):
    surface.reset_module_state("chento_btc")
    clock.set_simulated_now(at)
    intents, status = surface.decide("chento_btc", variant=VARIANT, **kw)
    if execute and intents:
        surface.execute("chento_btc", variant=VARIANT, intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"chento_{name}.json", doc)
    assert doc == expected
    return doc


# ── BTC, in-process ────────────────────────────────────────────────────────

def test_golden_chento_btc_first_signal(env):
    doc = _check("btc_fire_a", FIRE_A, env, execute=True)
    assert doc["status"]["status"] == "decided"
    assert doc["status"]["direction"] == "long"
    assert doc["db_after"]["n_trades"] == 1
    row = doc["db_after"]["trades"][0]
    assert row["strategy"] == "CHENTO_TRIPLE_V3"
    assert row["asset"] == "BTC"


def test_golden_chento_btc_second_signal(env):
    doc = _check("btc_fire_b", FIRE_B, env, execute=True)
    assert doc["status"]["status"] == "decided"
    assert doc["db_after"]["n_trades"] == 1


def test_golden_chento_btc_bar_keyed_idempotency(env):
    """chento keys on the trigger bar, which is what makes the doubled-fleet
    failure impossible now: two processes on the same bar collapse to one row."""
    doc = _check("btc_fire_row", FIRE_A, env, execute=True)
    row = doc["db_after"]["trades"][0]
    assert row["unique_key_suffix"].startswith("CHENTO_TRIPLE_V3|BTC|")
    assert row["notes"]["bar_ts"] is not None


def test_golden_chento_btc_okx_gate_blocks(env):
    """The cross-exchange OKX gate refuses a Triple whose delta z is
    misaligned with the signal direction.

    The VALUE is pinned, not just the key. At this anchor the gate blocks on
    both the causal information set (z -2.66) and the old peeking one (z
    -0.59), so `filter_blocked` alone cannot tell a correctly bounded loader
    from an unbounded one — only the number can. The old assertion here was
    `"okx_delta_z" in doc["status"]`, which passes for any value at all
    (memory feedback_characterization_gates_not_arithmetic).

    The gate's own study claimed -25% drawdown and +34% OOS expectancy, on
    same-hour complete bars from both venues that live can never see; it is
    scheduled for re-validation on the causal information set (BACKLOG).
    """
    doc = _check("btc_okx_blocked", OKX_BLOCKED, env)
    assert doc["status"]["status"] == "filter_blocked"
    assert doc["status"]["reason"] == "okx_misaligned"
    assert doc["status"]["okx_delta_z"] == pytest.approx(-2.6609709957818866,
                                                          abs=1e-9)
    assert doc["intents"] == []


def test_golden_chento_btc_the_signal_the_old_golden_wrongly_blocked(env):
    """2026-08-21 06:00: production TRADED this Triple (SJ-4243/4244). Until
    2026-09-13 the golden at this bar said the OKX gate blocked it, because
    the unbounded loaders let it see 59 minutes of future OKX data.

    This is the most direct guard against that look-ahead coming back: remove
    the loader bounds and this flips to filter_blocked again. It also pins
    agreement with the live ledger, to ten significant figures.
    """
    doc = _check("btc_fire_0600", FIRE_0600, env)
    assert doc["status"]["status"] == "decided"
    assert doc["status"]["direction"] == "long"
    z = doc["intents"][0]["reason"]["_filter_diag"]["okx_delta_z"]
    assert z == pytest.approx(LIVE_OKX_Z[FIRE_0600], rel=1e-9), (
        f"okx_delta_z {z} no longer matches what the live bot recorded "
        f"(SJ-4243: {LIVE_OKX_Z[FIRE_0600]})")


@pytest.mark.parametrize("at", [FIRE_A, FIRE_B])
def test_golden_chento_btc_fires_match_the_live_ledger(env, at):
    """The other two production fires, checked against the okx_delta_z the
    running bot actually recorded — the cross-check the old docstring told
    readers not to make."""
    surface.reset_module_state("chento_btc")
    clock.set_simulated_now(at)
    intents, status = surface.decide("chento_btc", variant=VARIANT)
    assert status["status"] == "decided"
    z = _decided_okx_z(intents)
    assert z == pytest.approx(LIVE_OKX_Z[at], rel=1e-9), (
        f"{at:%Y-%m-%d %H:%M}: okx_delta_z {z} != live ledger {LIVE_OKX_Z[at]}")


def test_golden_chento_btc_no_triple(env):
    at = datetime(2026, 8, 21, 12, 0, 5, tzinfo=timezone.utc)
    doc = _check("btc_no_triple", at, env)
    assert doc["status"]["status"] == "no_triple"
    assert doc["intents"] == []


def test_golden_chento_btc_off_boundary(env):
    doc = _check("btc_off_boundary", FIRE_A.replace(minute=37), env)
    assert doc["status"]["status"] == "not_at_15m_boundary"


def test_golden_chento_btc_second_call_same_bar_does_not_double_book(env):
    """The 2026-08-15..24 incident booked three signals as six rows. With the
    bar-keyed idempotency shipped 2026-09-12, a second execution of the same
    signal collapses to one row."""
    _check("btc_twice_first", FIRE_A, env, execute=True)
    doc = _check("btc_twice_second", FIRE_A, env, execute=True)
    assert doc["db_after"]["n_trades"] == 1


# ── ETH, in its own interpreter ────────────────────────────────────────────

def _run_eth(anchor: datetime, *, execute: bool) -> dict:
    fx = fixture_db("chento")
    cmd = [sys.executable, str(REPO / "tests" / "_chento_golden_runner.py"),
           "--asset", "ETH", "--anchor", anchor.isoformat(),
           "--fixture", str(fx)]
    if execute:
        cmd.append("--execute")
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       timeout=900)
    assert p.returncode == 0, f"ETH runner failed:\n{p.stderr[-2000:]}"
    return json.loads(p.stdout)


def test_golden_chento_eth_runs_as_eth_with_its_own_filter_set(env):
    """FILTER_NO_TILT is True for BTC and False for ETH — the assets are
    behaviourally different, so this is not a table rename."""
    doc = _run_eth(FIRE_A, execute=True)
    assert doc["asset_under_test"] == "ETH"
    assert doc["filter_no_tilt"] is False
    expected = load_or_write(GOLDENS / "chento_eth_fire_a.json", doc)
    assert doc == expected


def test_golden_chento_eth_second_anchor(env):
    doc = _run_eth(FIRE_B, execute=True)
    assert doc["asset_under_test"] == "ETH"
    expected = load_or_write(GOLDENS / "chento_eth_fire_b.json", doc)
    assert doc == expected


def test_golden_chento_btc_and_eth_are_not_the_same_document(env):
    """Guards the process isolation itself: if the subprocess ever leaked the
    BTC env, these would be identical and both goldens would be fiction."""
    btc = json.loads((GOLDENS / "chento_btc_fire_a.json").read_text(encoding="utf-8"))
    eth = json.loads((GOLDENS / "chento_eth_fire_a.json").read_text(encoding="utf-8"))
    assert eth.get("asset_under_test") == "ETH"
    assert btc.get("asset_under_test") in (None, "BTC")
    assert btc["status"] != eth["status"] or btc["intents"] != eth["intents"], \
        "BTC and ETH produced identical documents — the ETH process leaked"


def test_goldens_exist():
    assert len(list(GOLDENS.glob("chento_*.json"))) >= 9
