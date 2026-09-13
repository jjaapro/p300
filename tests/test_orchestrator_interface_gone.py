"""The orchestrator interface is gone from the six running sleeves.

Replaces tests/test_sleeve_adapter_equivalence.py, which for phase C pinned
the legacy `(variant, sleeve_cfg)` adapters as pure passthroughs — the
evidence that made deleting them safe. Phase D step 23 deleted them, so the
assertions invert: the legacy names must now be ABSENT, the plain surface
present, and no sleeve may speak the orchestrator's vocabulary. The
cfg->kwargs translator that phase D centralized went with the orchestrator
itself on 2026-09-13 — nothing in production translates a cfg dict now.

Kept as a standing guard rather than dropped, because the easiest way to
"fix" a future dispatch problem is to add a wrapper back into a sleeve — which
would restore the second system this refactor removed.

Step 4 (2026-09-13) added the archive invariant below. The guard it replaced
pinned the OLD interface on the dormant sleeves, so that a stray "consistency"
edit could not strip entry points the orchestrator still needed. Archiving
those sleeves is the event that retires it, and the useful assertion inverts
with it: nothing that runs may import the archive at all.
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

import ast
import importlib
import pathlib
import re

import pytest

#: The six modules the fleet runs, and the entry points each must expose.
MIGRATED = {
    "bots.adx.strategy.signal": ("decide", "execute"),
    "bots.carry.strategy.signal": ("decide", "execute"),
    "bots.chento_v3.strategy.signal": ("decide", "execute"),
    "bots.short_squeeze.strategy.signal": ("decide", "execute"),
    "bots.squeeze_bull.strategy.signal": ("decide", "execute"),
    "bots.r4.strategy.signal": (
        "decide_btc", "decide_eth", "decide_btc_v2", "decide_eth_v2",
        "execute"),
}

#: Names that must not come back. `_unpack` is on the list because six copies
#: of it are exactly what step 21 centralized.
BANNED = ("try_decide_for_variant", "execute_for_variant",
          "try_fire_for_variant", "_r4_execute", "_unpack",
          "r4_btc_decide", "r4_eth_decide", "r4_btc_v2_decide",
          "r4_eth_v2_decide", "r4_btc_try_fire", "r4_eth_try_fire",
          "r4_btc_v2_try_fire", "r4_eth_v2_try_fire", "_r4_v2_try_fire")

#: Everything that runs, or that a running process imports. Nothing here may
#: reach into the archive.
LIVE_TREES = ("bots", "dashboard", "data", "strategies",
              "botlib.py", "feed.py", "monitor.py", "health.py",
              "bootstrap.py", "backup.py")


@pytest.mark.parametrize("modpath, entries", list(MIGRATED.items()))
def test_migrated_sleeve_exposes_the_plain_surface(modpath, entries):
    mod = importlib.import_module(modpath)
    for name in entries:
        assert callable(getattr(mod, name, None)), f"{modpath} lost {name}"


@pytest.mark.parametrize("modpath", list(MIGRATED))
@pytest.mark.parametrize("banned", BANNED)
def test_migrated_sleeve_has_no_legacy_entry_point(modpath, banned):
    mod = importlib.import_module(modpath)
    assert not hasattr(mod, banned), (
        f"{modpath} grew {banned} back. Nothing translates a cfg dict any "
        f"more — the orchestrator and its adapter were both retired on "
        f"2026-09-13. A bot calls its strategy with plain keywords.")


def _live_py_files():
    repo = pathlib.Path(__file__).resolve().parents[1]
    for name in LIVE_TREES:
        p = repo / name
        if p.is_file():
            yield p.relative_to(repo), p.read_text(encoding="utf-8")
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if "__pycache__" in f.parts:
                    continue
                yield f.relative_to(repo), f.read_text(encoding="utf-8")


def _imported_modules(src: str):
    """Every dotted module name this file IMPORTS. Parsed, not grepped —
    a string scan trips over the prose that legitimately names these paths."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            for a in node.names:
                yield f"{node.module}.{a.name}"


def test_no_live_code_imports_the_archive():
    """The layering invariant step 4 bought, made permanent.

    Three live edges into the dormant sleeves existed on 2026-09-13, and the
    dangerous one was silent: strategies/support/jplus_inputs.py imported
    strategies/sleeves/ema/math.py at module scope, on the live r4 bot's
    sizing path. Archiving it would have raised ImportError inside a runner
    that catches ImportError, writes a degraded heartbeat and keeps ticking —
    a bot that reports healthy and evaluates nothing.

    So this is not a tidiness rule. Nothing that runs may import
    studies.material.archive, ever.
    """
    hits = [f"{rel}: {m}" for rel, src in _live_py_files()
            for m in _imported_modules(src)
            if m.startswith("studies.material.archive")]
    assert not hits, (
        "live code imports the archive:\n  " + "\n  ".join(hits) +
        "\nThe archive is unsupported code. Move what is needed down to "
        "strategies/support/ instead, the way ema_position.py was.")


def test_nothing_imports_the_retired_sleeves_package():
    """`strategies.sleeves` no longer exists. A resurrected import would fail
    loudly here rather than inside a runner's except-ImportError."""
    hits = [f"{rel}: {m}" for rel, src in _live_py_files()
            for m in _imported_modules(src)
            if m == "strategies.sleeves" or m.startswith("strategies.sleeves.")]
    assert not hits, f"strategies.sleeves is gone, but: {hits}"


def test_chento_package_reexports_only_the_plain_surface():
    """The bot imports the PACKAGE, so its re-exports are the real contract."""
    from bots.chento_v3 import strategy as pkg
    assert pkg.decide is importlib.import_module(
        "bots.chento_v3.strategy.signal").decide
    assert callable(pkg.execute)
    for banned in ("try_decide_for_variant", "execute_for_variant",
                   "try_fire_for_variant"):
        assert not hasattr(pkg, banned), f"chento package re-exports {banned}"


def test_no_runner_speaks_the_cfg_dict():
    """The visible result of the refactor: the fleet no longer builds or
    passes a sleeve_cfg anywhere."""
    repo = pathlib.Path(__file__).resolve().parents[1]
    offenders = [r.relative_to(repo).as_posix()
                 for r in sorted((repo / "bots").glob("*/runner.py"))
                 if "sleeve_cfg" in r.read_text(encoding="utf-8")]
    assert not offenders, f"runners still carrying a sleeve_cfg: {offenders}"


#: The read idiom — `.get("_effective_x")` — as opposed to a docstring that
#: merely describes the orchestrator's flow. Several modules legitimately
#: document it without reading it.
_READS_EFFECTIVE = re.compile(r"""\.get\(\s*["']_effective_""")
_TAKES_CFG = re.compile(r"\bsleeve_cfg\b\s*[:=)]")


def test_no_migrated_sleeve_reads_an_effective_key():
    """Six copies of `_unpack` were what step 21 removed. None of the six
    migrated modules may read an `_effective_*` key or take a `sleeve_cfg`
    again — that is the orchestrator's vocabulary, and there is no longer
    anything on the other side of it to speak to.

    Still scoped to the six, though every sleeve now qualifies: the dormant
    ones did keep reading these keys, and step 4 archived them unchanged on
    2026-09-13 rather than migrating code nobody runs. Whatever remains under
    studies/material/archive/ is out of scope by construction."""
    offenders = []
    for modpath in MIGRATED:
        src = pathlib.Path(
            importlib.import_module(modpath).__file__).read_text(encoding="utf-8")
        if _READS_EFFECTIVE.search(src):
            offenders.append(f"{modpath}: reads an _effective_* key")
        if _TAKES_CFG.search(src):
            offenders.append(f"{modpath}: takes a sleeve_cfg")
    assert not offenders, (
        "migrated sleeves are speaking the orchestrator's vocabulary again: "
        f"{offenders}")


def test_nothing_in_support_translates_an_orchestrator_key():
    """`strategies/support/` is live-path code. With the orchestrator retired
    nothing injects `_effective_*` any more, so nothing may read one — not
    even the translator that used to be allowed to."""
    repo = pathlib.Path(__file__).resolve().parents[1]
    hits = {p.relative_to(repo).as_posix()
            for p in sorted((repo / "strategies" / "support").rglob("*.py"))
            if _READS_EFFECTIVE.search(p.read_text(encoding="utf-8"))}
    assert not hits, f"_effective_* read in support/: {sorted(hits)}"
