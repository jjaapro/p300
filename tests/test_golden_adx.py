"""Golden record for ADX's decide/execute — the sleeve with the live coupling.

ADX is the one sleeve whose `_effective_leverage` is not merely overwritten by
its runner. It feeds `effective_price_move_sl_pct(stop_loss_pct, leverage)`,
whose result is persisted into the trade's reason blob as
`sl_semantic_price_thresh_pct`, and `strategies/support/stop_path.py` reads
that back out of the notes on EVERY close-path stop check — including for
SJ-4247, open since 2026-08-22.

The coupling is invisible at the default env. `risk_config.sl_semantic()`
divides by leverage only when `P300_STOP_SEMANTICS=margin`; the default is
'price_move', `.env` does not set it and tests/conftest.py deletes it. So a
golden run only at default settings would let a strip delete the leverage
argument and stay green, while breaking the live close path for anyone who
ever flips the env. Hence `test_golden_adx_margin_semantics_*` below, built on
THE SAME firing fixture as the default case so its assertion actually runs —
the re-plan flagged the alternative as a false-green trap.

Anchor 2026-08-22T00:00:05Z is SJ-4247's real entry: the carved 372-day window
reproduces adx 25.31, ema50 65428.68, trend_ema 69332.66, close 78338.03 and
funding_z 0.663.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests import _sleeve_surface as surface
from tests._golden_fixture import make_env
from tests._golden_normalize import golden_document, load_or_write
from strategies.support import clock

GOLDENS = Path(__file__).resolve().parent / "goldens"
VARIANT = {"id": "bot_adx_test", "capital_usdt": 10_000.0}

FIRE = datetime(2026, 8, 22, 0, 0, 5, tzinfo=timezone.utc)     # SJ-4247
STOP_LOSS_PCT = 10.0


#: SJ-4247's real entry price. ADX prices entries off the live 1m feed, which
#: the fixture cannot carry (372 days of btc_1m is ~535k rows), and the minute
#: path is not part of the signal logic being characterized — stop_path has its
#: own tests. Pinning it to the production value is what lets the golden
#: reproduce SJ-4247's row rather than an arbitrary one.
ENTRY_PRICE = 78328.01


@pytest.fixture
def env(tmp_path, monkeypatch):
    from strategies.support import price_feed

    db = make_env("adx", tmp_path, monkeypatch, sleeve_keys=("adx",))
    monkeypatch.setattr(price_feed, "_get_current_price",
                        lambda a=None: ENTRY_PRICE)
    monkeypatch.setattr(price_feed, "get_current_price",
                        lambda a=None: ENTRY_PRICE)
    yield db
    clock.set_simulated_now(None)


def _check(name, at, db_path, *, execute=False, **kw):
    kw.setdefault("params", {"stop_loss_pct": STOP_LOSS_PCT})
    clock.set_simulated_now(at)
    intents, status = surface.decide("adx", variant=VARIANT, **kw)
    if execute and intents:
        surface.execute("adx", variant=VARIANT, intent=intents[0], **kw)
    doc = golden_document(intents=intents, status=status, db_path=db_path)
    expected = load_or_write(GOLDENS / f"adx_{name}.json", doc)
    assert doc == expected
    return doc


def test_golden_adx_fires_at_the_production_anchor(env):
    """Reproduces SJ-4247's signal, byte-for-byte against the ledger."""
    doc = _check("fire", FIRE, env, execute=True)
    assert doc["status"]["status"] == "decided"
    r = doc["intents"][0]["reason"]
    assert r["adx"] == pytest.approx(25.31, abs=0.01)
    assert r["ema50"] == pytest.approx(65428.68, abs=0.01)
    assert r["trend_ema"] == pytest.approx(69332.66, abs=0.01)
    assert r["close"] == pytest.approx(78338.03, abs=0.01)
    assert r["funding_z"] == pytest.approx(0.663, abs=0.001)
    assert doc["intents"][0]["direction"] == "LONG"


def test_golden_adx_writes_the_day_keyed_idempotency_key(env):
    """The key is the DECISION day, not the signal bar's day: the clock is
    2026-08-22T00:00:05Z acting on the 2026-08-21 daily close. That matches
    `_adx_trade_exists_today`, which is the in-process guard the DB index now
    backs — one ADX open per variant per UTC day."""
    doc = _check("fire_row", FIRE, env, execute=True)
    row = doc["db_after"]["trades"][0]
    assert row["strategy"] == "ADX"
    assert row["unique_key_suffix"] == "ADX|BTC|2026-08-22"
    assert row["notes"]["close"] == pytest.approx(78338.03, abs=0.01), \
        "the signal bar is 2026-08-21's close even though the key is 08-22"


# ── the leverage coupling, which the default env hides ─────────────────────

def test_golden_adx_default_semantics_ignore_leverage(env):
    """At the default 'price_move' semantic the threshold equals the
    configured stop, whatever the leverage. Pinned so the NEXT test's
    difference is unambiguous."""
    doc = _check("sl_default_lev1", FIRE, env, leverage=1.0)
    r = doc["intents"][0]["reason"]
    assert r["stop_loss_pct"] == STOP_LOSS_PCT
    assert r["sl_semantic_price_thresh_pct"] == STOP_LOSS_PCT


def test_golden_adx_margin_semantics_divide_by_leverage(env, monkeypatch):
    """THE test that makes deleting ADX's leverage argument fail.

    Built on the same firing fixture as the default case, so the assertion
    actually reaches a persisted value: under margin semantics the recorded
    threshold is stop_loss_pct / leverage, and stop_path.entry_stop_pct reads
    exactly that field back on the live close path."""
    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    surface.reset_module_state("adx")
    doc = _check("sl_margin_lev4", FIRE, env, leverage=4.0)
    r = doc["intents"][0]["reason"]
    assert r["stop_loss_pct"] == STOP_LOSS_PCT
    assert r["sl_semantic_price_thresh_pct"] == pytest.approx(
        STOP_LOSS_PCT / 4.0), \
        "the leverage argument stopped reaching effective_price_move_sl_pct"


def test_stop_path_reads_back_what_the_sleeve_recorded(env, monkeypatch):
    """Closes the loop: the value the sleeve persists is the value the live
    close path uses. If a strip changes one side, this fails."""
    from strategies.support.stop_path import entry_stop_pct

    monkeypatch.setenv("P300_STOP_SEMANTICS", "margin")
    surface.reset_module_state("adx")
    doc = _check("sl_margin_roundtrip", FIRE, env, leverage=4.0, execute=True)
    row = doc["db_after"]["trades"][0]
    trade = {"id": "SJ-golden", "notes": __import__("json").dumps(row["notes"]),
             "leverage": row["leverage"]}
    assert entry_stop_pct(trade, STOP_LOSS_PCT) == pytest.approx(
        STOP_LOSS_PCT / 4.0)


# ── negative twins ─────────────────────────────────────────────────────────

def test_golden_adx_once_per_day_guard_is_db_backed(env):
    """`_adx_trade_exists_today` queries the trades table, so resetting module
    state does not clear it — a different mechanism from squeeze_bull's hour
    guard and chento's bar guard."""
    _check("day_guard_first", FIRE, env, execute=True)
    surface.reset_module_state("adx")
    doc = _check("day_guard_second", FIRE, env)
    assert doc["status"]["status"] == "already_fired_today"
    assert doc["db_after"]["n_trades"] == 1


def test_golden_adx_trend_filter_blocks_a_long_below_the_slow_ema(env):
    """The symmetric trend filter is the Tier-2 calibration that took maxDD
    from -27% to -15% and MAR from 1.78 to 3.09. At this anchor ADX is 25.34
    (a valid trend) and the close is 77371.32 against EMA(150) 80426.15, so
    the signal is real and the FILTER is what refuses it.

    Added because the step-7 drill deleted the filter and every other ADX
    golden stayed green — the firing anchor is above its slow EMA, so nothing
    exercised the blocking branch."""
    at = datetime(2026, 4, 28, 0, 0, 5, tzinfo=timezone.utc)
    doc = _check("trend_filter_block", at, env)
    assert doc["status"]["status"] == "trend_filter_block"
    assert doc["status"]["close"] < doc["status"]["trend_ema"], \
        "anchor drifted: the close must sit BELOW the slow EMA"
    assert doc["status"]["adx"] >= 20, \
        "anchor must fail ONLY on the trend filter, not on ADX strength"
    assert doc["intents"] == []
    assert doc["db_after"]["n_trades"] == 0


def test_golden_adx_funding_veto_blocks_a_crowded_long(env, monkeypatch):
    """"Don't long over-crowded leverage" — the Tier-2 funding veto.

    Unlike the other twins this one FORCES its input: no bar in the carved
    60-day funding window produces z > 1.5 (I searched all 62), because the
    veto is rare by design. Forcing `_funding_z` pins the BRANCH, which is
    what a strip would delete; the z-score's own arithmetic is covered by
    test_adx_parity. Labelled so nobody later reads this as a historical
    event."""
    from bots.adx.strategy import signal as adx_sig
    from bots.adx.strategy.config import FUNDING_VETO_Z

    monkeypatch.setattr(adx_sig, "_funding_z",
                        lambda candles: FUNDING_VETO_Z + 0.5)
    surface.reset_module_state("adx")
    doc = _check("funding_veto_block", FIRE, env)
    assert doc["status"]["status"] == "funding_veto_block"
    assert doc["status"]["funding_z"] == pytest.approx(FUNDING_VETO_Z + 0.5)
    assert doc["intents"] == []
    assert doc["db_after"]["n_trades"] == 0


def test_golden_adx_warmup_on_a_truncated_window(env):
    """Far enough back that the carve cannot supply the 361 bars the loader
    asks for. Pins the refusal rather than letting a short window produce
    plausible wrong numbers."""
    at = datetime(2025, 9, 10, 0, 0, 5, tzinfo=timezone.utc)
    doc = _check("warmup", at, env)
    assert doc["status"]["status"] in ("warmup", "no_action", "price_missing")
    assert doc["intents"] == []


def test_goldens_exist():
    assert len(list(GOLDENS.glob("adx_*.json"))) >= 8
