"""Every sleeve a replay will dispatch must resolve, and a miss must be loud.

`backtest_runner.tick_replay_variant` resolves sleeves ONLY through
`orchestrator.STRATEGY_DISPATCH` — it never consults
`STRATEGY_TWO_PHASE_DISPATCH` — and until 2026-09-12 it skipped an unresolved
sleeve with a bare `continue` and no log. A replay of that sleeve then produced
zero trades, printed a full report and exited 0.

That is the failure mode standing between the orchestrator-strip refactor
(BACKLOG.md, "Step 1 re-planned") and a silently zeroed backtest: deleting a
sleeve's `try_fire_for_variant` wrapper and its dispatch entry passes the whole
suite green while destroying its research replay. These tests make that
deletion fail loudly instead.
"""
from __future__ import annotations

import pytest

import backtest_runner
from strategies import orchestrator
from strategies.sleeves.timing_anomalies import internal


def _variant(composition: list[dict]) -> dict:
    return {"id": "v_test", "capital_usdt": 10_000.0,
            "spec": {"composition": composition}}


# ── the registries themselves ──────────────────────────────────────────────

def test_every_dispatch_entry_is_callable():
    orchestrator._load_dispatch()
    assert orchestrator.STRATEGY_DISPATCH, "dispatch registry is empty"
    for sid, fn in orchestrator.STRATEGY_DISPATCH.items():
        assert callable(fn), f"{sid} dispatch entry is not callable"


def test_every_two_phase_entry_is_a_callable_pair():
    orchestrator._load_dispatch()
    for sid, pair in orchestrator.STRATEGY_TWO_PHASE_DISPATCH.items():
        assert isinstance(pair, tuple) and len(pair) == 2, \
            f"{sid} two-phase entry is not a (decide, execute) pair"
        assert all(callable(f) for f in pair), f"{sid} pair is not callable"


def test_two_phase_sleeves_also_have_a_single_phase_entry():
    """The replay path reads only STRATEGY_DISPATCH. A sleeve that migrated to
    two-phase and dropped its try_fire wrapper would still tick live and still
    replay as zero trades — the exact asymmetry this file exists for."""
    orchestrator._load_dispatch()
    missing = sorted(set(orchestrator.STRATEGY_TWO_PHASE_DISPATCH)
                     - set(orchestrator.STRATEGY_DISPATCH))
    assert not missing, (
        f"{missing} dispatch live via two-phase but have no STRATEGY_DISPATCH "
        f"entry, so backtest_runner would replay them as zero trades")


def test_every_known_substrategy_resolves_to_a_pair():
    for name in internal.known_substrategies():
        pair = internal.get_dispatch(name)
        assert pair is not None, f"substrategy {name} does not resolve"
        assert len(pair) == 2 and all(callable(f) for f in pair)


def test_an_unresolvable_substrategy_is_logged_not_swallowed(caplog, monkeypatch):
    """`get_dispatch` still returns None — callers treat that as 'not wired' —
    but it may not be silent. A dormant sleeve's ImportError used to be
    indistinguishable from a live sleeve's typo."""
    import logging

    monkeypatch.setitem(internal._RESOLVERS, "BOOM",
                        lambda: (_ for _ in ()).throw(ImportError("boom")))
    internal._DISPATCH_CACHE.pop("BOOM", None)
    with caplog.at_level(logging.ERROR):
        assert internal.get_dispatch("BOOM") is None
    assert any("BOOM" in r.getMessage() for r in caplog.records), \
        "an unresolvable substrategy resolved to None with nothing logged"


# ── the replay path ────────────────────────────────────────────────────────

def test_tick_replay_raises_on_an_unwired_sleeve():
    orchestrator._load_dispatch()
    with pytest.raises(RuntimeError, match="NO_SUCH_SLEEVE"):
        backtest_runner.tick_replay_variant(
            _variant([{"strategy_id": "NO_SUCH_SLEEVE", "weight_pct": 10.0}]))


def test_validate_dispatch_accepts_a_fully_wired_composition():
    orchestrator._load_dispatch()
    wired = sorted(orchestrator.STRATEGY_DISPATCH)[:2]
    backtest_runner.validate_dispatch(
        _variant([{"strategy_id": s, "weight_pct": 10.0} for s in wired]))


def test_validate_dispatch_aborts_before_the_first_tick():
    with pytest.raises(SystemExit, match="NO_SUCH_SLEEVE"):
        backtest_runner.validate_dispatch(
            _variant([{"strategy_id": "NO_SUCH_SLEEVE", "weight_pct": 10.0}]))


def test_validation_honours_skip_so_it_cannot_abort_a_legitimate_run(monkeypatch):
    """`--skip X` means the replay never dispatches X, so an unwired X must
    not abort the run the operator asked for."""
    monkeypatch.setattr(backtest_runner, "SKIP_STRATEGIES", {"NO_SUCH_SLEEVE"})
    backtest_runner.validate_dispatch(
        _variant([{"strategy_id": "NO_SUCH_SLEEVE", "weight_pct": 10.0}]))


def test_validation_ignores_non_deterministic_sleeves():
    """AI_QUANT carries params.deterministic = False and is skipped by the
    replay loop, so it must not be validated either."""
    backtest_runner.validate_dispatch(_variant([{
        "strategy_id": "NO_SUCH_SLEEVE", "weight_pct": 10.0,
        "params": {"deterministic": False}}]))


def test_validation_ignores_portfolio_rows():
    backtest_runner.validate_dispatch(_variant([{
        "portfolio_id": "p1", "strategy_id": "NO_SUCH_SLEEVE"}]))


def test_with_fomc_now_aborts_instead_of_silently_dropping_fomc():
    """`--with-fomc` appends {"strategy_id": "FOMC"} to the composition
    (backtest_runner.ensure_replay_variant), but FOMC is not a top-level
    STRATEGY_DISPATCH key — it dispatches only as a TIMING_ANOMALIES
    substrategy. Every `--with-fomc` run therefore silently produced a run
    WITHOUT FOMC, and reported success. It must abort instead."""
    orchestrator._load_dispatch()
    assert "FOMC" not in orchestrator.STRATEGY_DISPATCH
    with pytest.raises(SystemExit, match="FOMC"):
        backtest_runner.validate_dispatch(_variant([
            {"strategy_id": "FOMC", "weight_pct": 5.0,
             "params": {"leverage": 5.0}}]))


def test_the_live_composition_is_fully_wired():
    """The real p300_aggressive_v2_v1_0 composition, straight from prod.db.
    Skipped when prod.db is absent (CI)."""
    import sqlite3
    import json
    import pathlib
    from strategies.support import db

    p = pathlib.Path(db.DASH_DB)
    if not p.exists():
        pytest.skip("prod.db not present")
    con = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("SELECT spec_json FROM variants WHERE id = ?",
                          ("p300_aggressive_v2_v1_0",)).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        pytest.skip("legacy variant row absent")
    backtest_runner.validate_dispatch(
        {"id": "p300_aggressive_v2_v1_0", "spec": json.loads(row[0])})
