"""Golden record for SHORT_SQUEEZE's decide/execute, both variants.

**Read this before treating a passing run as good news.** SHORT_SQUEEZE has
never fired in production — zero rows in `trades` for any variant, ever — and
its macro gate rejected every bar for the 72 days before 2026-09-12. The
anchor below is therefore a DISCOVERED historical trigger, not a ledger row,
and a green golden here is evidence that a refactor preserved behaviour. It is
NOT evidence that the sleeve works: the 2026-09 execution study measured it at
−0.32 R per trade under its own coded costs, and its fate is pre-registered at
the n = 30 re-cut (both variants ≤ 0 → retire the sleeve).

Both live variants are pinned. `use_stop` reaches the sleeve through the cfg
dict the strip deletes, and `count_diag` decides which of the two variants
counts the per-day gate diagnostics — the runner passes `count_diag = (k == 0)`
so exactly one variant per tick counts. A strip that dropped either flag would
change a live paper twin silently.

Anchor 2025-10-14T10:00:05Z: perp_cvd_pct 0.0833, divergence_pct 0.8990.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests import _sleeve_surface as surface
from tests._golden_fixture import make_env
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock

GOLDENS = Path(__file__).resolve().parent / "goldens"
VARIANT = {"id": "bot_short_squeeze_test", "capital_usdt": 10_000.0}

FIRE = datetime(2025, 10, 14, 10, 0, 5, tzinfo=timezone.utc)
SWEEP_PRICE = 111_237.4


@pytest.fixture
def env(tmp_path, monkeypatch):
    from strategies.support import price_feed

    db = make_env("short_squeeze", tmp_path, monkeypatch,
                  sleeve_keys=("short_squeeze",))
    monkeypatch.setattr(price_feed, "get_current_price", lambda a=None: SWEEP_PRICE)
    yield db
    clock.set_simulated_now(None)


def _check(name, at, db_path, *, execute=False, **kw):
    clock.set_simulated_now(at)
    intents, status = surface.decide("short_squeeze", variant=VARIANT, **kw)
    if execute and intents:
        surface.execute("short_squeeze", variant=VARIANT, intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"short_squeeze_{name}.json", doc)
    assert doc == expected
    return doc


def test_golden_short_squeeze_fires_at_the_discovered_anchor(env):
    doc = _check("fire_stop", FIRE, env, execute=True)
    assert doc["status"]["status"] == "decided"
    assert doc["status"]["perp_pct"] == pytest.approx(0.0833, abs=0.001)
    assert doc["status"]["div_pct"] == pytest.approx(0.8990, abs=0.001)
    r = doc["intents"][0]["reason"]
    assert r["trigger"] == "short_squeeze_long"
    assert r["sleeve"] == "SHORT_SQUEEZE"
    assert doc["intents"][0]["direction"] == "LONG"


def test_golden_short_squeeze_keys_on_the_bar_it_actually_records(env):
    """The idempotency key reads `bar_ts_utc` — the name this reason dict
    carries. It read `bar_ts`, which was never written, from deployment on
    2026-07-21 until 2026-09-12, so every key silently fell back to the fill
    instant and the UNIQUE index protected nothing."""
    doc = _check("fire_row", FIRE, env, execute=True)
    r = doc["intents"][0]["reason"]
    assert "bar_ts_utc" in r and r.get("bar_ts") is None
    row = doc["db_after"]["trades"][0]
    assert row["unique_key_suffix"] == f"SHORT_SQUEEZE|BTC|{r['bar_ts_utc']}"


def test_golden_short_squeeze_stop_variant_carries_stop_and_target(env):
    doc = _check("levels_stop", FIRE, env, execute=True, use_stop=True)
    r = doc["intents"][0]["reason"]
    assert r["_stop_price"] is not None and r["_target_price"] is not None
    assert r["exit_policy"] == "stop_target_time"
    assert r["_reference_stop_price"] == pytest.approx(r["_stop_price"])


def test_golden_short_squeeze_nostop_twin_keeps_only_the_time_stop(env):
    """The live 1x no-stop variant: no stop, NO target, 6 h time stop only."""
    doc = _check("levels_nostop", FIRE, env, execute=True, use_stop=False)
    r = doc["intents"][0]["reason"]
    assert r["_stop_price"] is None
    assert r["_target_price"] is None, \
        "short_squeeze's no-stop twin drops the target too, unlike squeeze_bull"
    assert r["exit_policy"] == "time_only"
    assert r["_reference_stop_price"] is not None, \
        "R is measured against the swept-low reference for both variants"
    assert r["_time_stop_iso"] is not None


def test_golden_short_squeeze_both_variants_see_the_same_signal(env):
    a = _check("pair_stop", FIRE, env, use_stop=True)
    surface.reset_module_state("short_squeeze")
    b = _check("pair_nostop", FIRE, env, use_stop=False)
    for key in ("bar_ts_utc", "bar_low", "bar_close", "perp_cvd",
                "divergence", "close_in_range", "prior_low_24x15m"):
        assert a["intents"][0]["reason"][key] == b["intents"][0]["reason"][key]


# ── negative twins ─────────────────────────────────────────────────────────

def test_golden_short_squeeze_rejects_a_non_boundary_minute(env):
    """Entry is evaluated only on closed 15m boundaries; the live bot ticks
    every 60 s, so this is the status it returns most of the time."""
    doc = _check("not_boundary", FIRE.replace(minute=7), env)
    assert doc["status"]["status"] == "not_15m_boundary"
    assert doc["intents"] == []


def test_golden_short_squeeze_macro_gate_rejects_when_not_short(env):
    """The macro gate is why this sleeve has been silent for months — it
    rejected every bar for the 72 days before 2026-09-12. A strip that
    dropped it would turn a dormant sleeve into an active one.

    This asserts the EXACT status. An earlier version accepted any of nine
    plausible rejections, which meant deleting the gate still passed: the bar
    simply failed at the next gate down the ladder. A twin that cannot fail is
    the trap this whole exercise exists to avoid."""
    at = datetime(2025, 10, 13, 8, 0, 5, tzinfo=timezone.utc)
    doc = _check("macro_not_short", at, env)
    assert doc["status"]["status"] == "macro_not_short"
    assert doc["status"]["macro"]["is_short_macro"] is False
    assert doc["intents"] == []


def test_golden_short_squeeze_macro_not_ready_before_asia_closes(env):
    """Distinct from macro_not_short: the macro is computed once the asia
    session ends, so before that the sleeve refuses for a different reason."""
    at = datetime(2025, 10, 14, 0, 0, 5, tzinfo=timezone.utc)
    doc = _check("macro_not_ready", at, env)
    assert doc["status"]["status"] == "macro_not_ready"
    assert doc["intents"] == []


def test_golden_short_squeeze_perp_percentile_gate(env):
    """A bar that clears sweep and macro but fails the perp-CVD percentile —
    30 minutes after the firing bar, so the only difference is the gate."""
    at = datetime(2025, 10, 14, 10, 30, 5, tzinfo=timezone.utc)
    doc = _check("perp_pct_too_high", at, env)
    assert doc["status"]["status"] == "perp_pct_too_high"
    assert doc["intents"] == []


def test_golden_short_squeeze_status_keeps_its_diag_keys(env):
    doc = _check("status_shape", FIRE, env)
    for k in ("swept", "perp_pct", "div_pct"):
        assert k in doc["status"], f"status lost its {k} diagnostic"


def test_golden_short_squeeze_single_open_guard_is_db_backed(env):
    """`_get_open_short_squeeze_trades` reads the trades table, so a restart
    cannot double-enter — module-state resets do not clear it."""
    _check("open_guard_first", FIRE, env, execute=True)
    surface.reset_module_state("short_squeeze")
    doc = _check("open_guard_second", FIRE, env)
    assert doc["status"]["status"] == "position_open"
    assert doc["db_after"]["n_trades"] == 1


def test_goldens_exist():
    assert len(list(GOLDENS.glob("short_squeeze_*.json"))) >= 10
