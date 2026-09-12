"""A dry run must be isolated — the DB *and* the diagnostics.

`--db <copy>` is what upgrades a refactor gate from "the heartbeat says ok" to
"the trade row is byte-identical", so it has to be trustworthy. Redirecting the
database alone is not isolation: the sleeves and two of the runners append
JSONL diagnostics that the dashboard reads, and a dry run that appends to those
has written outside its copy.

That is not hypothetical. The first run of the step-3 gate (2026-09-12) exited
0 for every bot and still modified `bots/squeeze_bull/logs/diag.jsonl`, because
that file is written from a config constant rather than the env var the first
version of this redirect covered.
"""
from __future__ import annotations

import os
import sqlite3
import types
from pathlib import Path

import pytest

import botlib
from strategies.support import db as db_mod
from strategies.support import trade_db


@pytest.fixture
def restore_globals(monkeypatch, tmp_path):
    """point_at_db_copy mutates module globals and os.environ by design."""
    monkeypatch.setattr(db_mod, "PROD_DB", db_mod.PROD_DB)
    monkeypatch.setattr(db_mod, "DASH_DB", db_mod.DASH_DB)
    monkeypatch.setattr(db_mod, "TRADER_DB", db_mod.TRADER_DB)
    monkeypatch.setattr(trade_db, "DB_PATH", trade_db.DB_PATH)
    monkeypatch.setenv("CHENTO_V3_DIAG_PATH", "live/chento.jsonl")
    monkeypatch.setenv("SSQ_DIAG_PATH", "live/ssq.jsonl")
    copy = tmp_path / "copy.db"
    sqlite3.connect(str(copy)).close()
    return copy


def test_refuses_the_live_prod_db(restore_globals):
    with pytest.raises(SystemExit, match="live prod.db"):
        botlib.point_at_db_copy(db_mod.PROD_DB)


def test_refuses_a_path_that_does_not_exist(tmp_path, restore_globals):
    with pytest.raises(SystemExit, match="does not exist"):
        botlib.point_at_db_copy(tmp_path / "nope.db")


def test_redirects_every_db_constant(restore_globals):
    copy = restore_globals
    botlib.point_at_db_copy(copy)
    assert Path(db_mod.PROD_DB) == copy.resolve()
    assert Path(db_mod.DASH_DB) == copy.resolve()
    assert Path(db_mod.TRADER_DB) == copy.resolve()
    assert Path(trade_db.DB_PATH) == copy.resolve()


def test_redirects_the_sleeve_diag_env_vars(restore_globals):
    copy = restore_globals
    botlib.point_at_db_copy(copy)
    for var in ("CHENTO_V3_DIAG_PATH", "SSQ_DIAG_PATH"):
        assert not os.environ[var].startswith("live/"), \
            f"{var} still points at the live diagnostics file"
        assert copy.parent.name in os.environ[var] or \
            str(copy.parent) in os.environ[var]


def test_redirects_runner_level_diag_paths(restore_globals):
    """The squeeze_bull / r4 case: DIAG_PATH is a config constant, not an env
    var, so the env redirect cannot reach it."""
    copy = restore_globals
    live_logs = Path("bots/squeeze_bull/logs")
    cfg = types.SimpleNamespace(LOGS_DIR=live_logs,
                                DIAG_PATH=live_logs / "diag.jsonl")
    botlib.point_at_db_copy(copy, botcfg=cfg)
    assert live_logs not in Path(cfg.DIAG_PATH).parents
    assert Path(cfg.DIAG_PATH).parent == Path(cfg.LOGS_DIR)
    assert Path(cfg.DIAG_PATH).name == "diag.jsonl"


@pytest.mark.parametrize("orig, kind", [
    ("bots/x/logs/diag.jsonl", str),
    (Path("bots/x/logs/diag.jsonl"), Path),
])
def test_preserves_the_diag_path_type(restore_globals, orig, kind):
    """short_squeeze and chento pass DIAG_PATH straight into os.environ, which
    rejects a Path; squeeze_bull and r4 open() it. Both must keep working.

    Parametrized rather than looped because the helper refuses to run twice in
    one process — after the first call the copy IS the live PROD_DB, which is
    the guard doing its job."""
    cfg = types.SimpleNamespace(DIAG_PATH=orig, LOGS_DIR=None)
    botlib.point_at_db_copy(restore_globals, botcfg=cfg)
    assert isinstance(cfg.DIAG_PATH, kind)
    assert Path(cfg.DIAG_PATH).name == "diag.jsonl"


def test_refuses_to_redirect_twice(restore_globals):
    """Second call: the copy is now PROD_DB, so the prod-db guard fires. Pins
    that the guard is evaluated against the CURRENT constant, not a captured
    one — a dry run must never be able to walk back onto the live file."""
    botlib.point_at_db_copy(restore_globals)
    with pytest.raises(SystemExit, match="live prod.db"):
        botlib.point_at_db_copy(restore_globals)


def test_every_runner_offers_the_dry_run_flags_and_passes_its_config():
    """All six runner mains must wire the shared helper — a bot without --db
    cannot be gated by a byte-identical trade row during the refactor, and one
    that omits its botcfg leaks diagnostics."""
    repo = Path(botlib.__file__).resolve().parent
    for bot in ("adx", "carry", "chento_v3", "r4", "short_squeeze",
                "squeeze_bull"):
        src = (repo / "bots" / bot / "runner.py").read_text(encoding="utf-8")
        assert "botlib.add_dry_run_flags(ap)" in src, f"{bot} has no --db flag"
        assert "botlib.apply_dry_run_flags(ap, args, botcfg)" in src, \
            f"{bot} does not pass its config, so its diagnostics would leak"


def test_dry_run_flags_require_once():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    botlib.add_dry_run_flags(ap)
    args = ap.parse_args(["--db", "whatever.db"])
    with pytest.raises(SystemExit):
        botlib.apply_dry_run_flags(ap, args)
