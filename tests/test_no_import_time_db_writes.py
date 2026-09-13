"""Importing code must never write to the live prod.db. BACKLOG item 10.

Until 2026-09-13 `strategies/support/trade_db.py` and
`strategies/support/variant_registry.py` ran their schema DDL at module scope.
A bare `import strategies.support.trade_db` executed BEGIN + CREATE ... IF NOT
EXISTS + INSERT OR IGNORE against whatever db.PROD_DB pointed at, which during
pytest collection was the prod.db the running fleet writes. On today's schema
that was a logical no-op holding a write lock; the first run after init_db()
gained a new column or unique index would have migrated the live ledger from a
test run, with nothing restarted and nothing to say so.

It also leaked out of pytest: botlib.point_at_db_copy imported trade_db BEFORE
repointing, so every `runner.py --db <copy>` dry run — "isolated" by design —
ran that DDL against the live file first.

HOW THE CHECK WORKS. Each check runs in a FRESH interpreter: an import side
effect fires once per process, and this one has already imported everything.
The probe installs two audit hooks that REFUSE, before anything is opened:
  * any read-write sqlite3.connect to the live prod.db path, and
  * any file opened for writing inside the repo.
So on a regression the probe itself writes nothing — it records the stack and
fails. The second hook matters because an "import everything" sweep is itself
dangerous here: tests/fixtures/mutation_drill.py runs its mutate-and-restore
loop at MODULE scope, and bots/chento_v3_eth/runner.py forces CHENTO_V3_DIAG=1
pointed at the live ETH diagnostics file.

It must not pass by accident. A probe that crashes early, or imports nothing,
would record zero hits. So the probe writes a completion sentinel with the
number of modules it imported and pytest's own return code and item count, and
the test asserts on those as well as on the hits. Both escapes were found by
adversarial review before this shipped: a module outside the original package
list, and an `__init__` raising a non-ImportError that made walk_packages
abort before importing anything — each left the first draft green.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = REPO / "data" / "databases" / "prod.db"

#: Never import these in the sweep: scripts whose MODULE body does real work.
#: studies/notebooks, studies/material and studies/reports are standalone
#: scripts, not libraries — studies/material/paladin/scripts/download_images.py
#: reads sys.argv at import and starts a thread pool of HTTP downloads. The
#: notebook LIBRARIES that tests do import are still covered, by the collection
#: probe, which imports exactly what the suite imports.
_SKIP_PREFIXES = ("tests/test_", "tests/fixtures/", "studies/notebooks/",
                  "studies/material/", "studies/reports/", "venv/")
_SKIP_FILES = {"tests/conftest.py"}

PROBE = r'''
import json, os, sqlite3, sys, traceback
live, out, mode, repo = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
norm = lambda p: os.path.normcase(os.path.realpath(p))
LIVE, REPO = norm(live), norm(repo)
TMP = norm(os.path.dirname(out))
hits, state = [], {"done": False, "imported": 0, "failed_imports": 0,
                   "pytest_rc": None, "collected": None}

def _stack():
    return "".join(traceback.format_stack(limit=14)[:-2])

def hook(event, args):
    if event == "sqlite3.connect":
        s = os.fsdecode(args[0])
        if s.startswith("file:"):
            path, _, query = s[5:].partition("?")
            if "mode=ro" in query:
                return
        else:
            path = s
        if path in ("", ":memory:") or norm(path) != LIVE:
            return
        hits.append("sqlite read-write connect to LIVE prod.db\n" + _stack())
        raise PermissionError("read-write connect to the live prod.db")
    if event == "open":
        path, mode_s, flags = args[0], args[1], args[2]
        if not isinstance(path, (str, bytes)):
            return
        writing = (mode_s is not None and any(c in mode_s for c in "wax+")) or \
                  (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
        if not writing:
            return
        p = norm(os.fsdecode(path))
        if p.startswith(TMP) or not p.startswith(REPO + os.sep):
            return
        hits.append(f"file write inside the repo: {p}\n" + _stack())
        raise PermissionError(f"write inside the repo during import: {p}")

sys.addaudithook(hook)
sys.dont_write_bytecode = True
sys.path.insert(0, repo)
# Scripts that read sys.argv at import would otherwise act on THIS probe's
# arguments: the first draft of this guard had download_images.py treat the
# live prod.db as its input and the result file as its output directory.
extra = sys.argv[5] if len(sys.argv) > 5 else None
sys.argv = [sys.argv[0]]

try:
    if mode == "collect":
        import pytest
        class Count:
            def pytest_collection_finish(self, session):
                state["collected"] = len(session.items)
        state["pytest_rc"] = int(pytest.main(
            ["--collect-only", "-q", "-p", "no:cacheprovider",
             os.path.join(repo, "tests")], plugins=[Count()]))
    elif mode == "import":
        import importlib
        for name in json.loads(extra):
            try:
                importlib.import_module(name)
                state["imported"] += 1
            except PermissionError:
                raise                      # our own hook: that IS the finding
            except BaseException:
                state["failed_imports"] += 1   # unrelated breakage, not this test
    elif mode == "dry_run":
        import botlib
        botlib.point_at_db_copy(extra)
        from strategies.support import trade_db, variant_registry
        trade_db.init_db()
        variant_registry.init_schema()
        state["imported"] = 1
    state["done"] = True
finally:
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"hits": hits, **state}, f)
'''


def _modules() -> list[str]:
    """Every tracked importable module outside tests, notebooks and scripts —
    from git, not from walk_packages, which silently skips anything outside
    the package list it was given (fetch_*.py, pipelines/, data/migrations/)."""
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=str(REPO),
                           capture_output=True, text=True, check=True).stdout
    names = []
    for rel in files.split():
        rel = rel.replace("\\", "/")
        if rel in _SKIP_FILES or rel.startswith(_SKIP_PREFIXES):
            continue
        mod = rel[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        names.append(mod)
    return names


def _probe(tmp_path, mode, *extra) -> dict:
    out = tmp_path / f"probe_{mode}.json"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               CHENTO_V3_DIAG="0", SSQ_DIAG="0")
    p = subprocess.run([sys.executable, "-c", PROBE, str(LIVE), str(out), mode,
                        str(REPO), *extra], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=900)
    assert out.exists(), f"probe wrote no result — it crashed:\n{p.stderr[-3000:]}"
    r = json.loads(out.read_text(encoding="utf-8"))
    assert r["hits"] == [], (
        f"{len(r['hits'])} forbidden write(s) during {mode}. First:\n{r['hits'][0]}")
    assert r["done"], f"probe did not finish {mode}:\n{p.stderr[-3000:]}"
    return r


def test_collecting_the_suite_does_not_write_the_live_db(tmp_path):
    r = _probe(tmp_path, "collect")
    assert r["pytest_rc"] == 0, (
        f"collection failed (rc={r['pytest_rc']}) — a probe that stops early "
        f"records no hits and would pass")
    assert r["collected"] and r["collected"] > 1000, (
        f"only {r['collected']} tests collected — the sweep did not really run")


def test_importing_every_module_does_not_write(tmp_path):
    names = _modules()
    assert len(names) > 150, f"module list looks wrong: {len(names)}"
    r = _probe(tmp_path, "import", json.dumps(names))
    # Non-vacuity: most modules must actually import, or zero hits means little.
    assert r["imported"] >= 0.9 * len(names), (
        f"imported {r['imported']} of {len(names)} "
        f"({r['failed_imports']} failed) — too few for a clean result to mean "
        f"anything")


def test_dry_run_redirects_before_touching_the_live_db(tmp_path):
    """botlib.point_at_db_copy used to import trade_db before repointing, so
    every `runner.py --db <copy>` run wrote DDL to the live file first."""
    import sqlite3
    copy = tmp_path / "copy.db"
    sqlite3.connect(str(copy)).close()
    _probe(tmp_path, "dry_run", str(copy))
