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
"""
from __future__ import annotations

from tests import _golden_guard  # noqa: F401  — MUST be first

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

#: The four sleeves that were NOT refactored. Dormant — dispatched only by the
#: orchestrator, which nothing runs — and they keep the old interface until
#: they are archived (BACKLOG.md step 4).
NOT_MIGRATED = ("strategies.sleeves.ai_quant.signal",
                "strategies.sleeves.ema.signal",
                "strategies.sleeves.eth_daily.signal")


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
        f"{modpath} grew {banned} back. The cfg->kwargs translation belongs "
        f"in strategies/support/cfg_adapter.py, on the orchestrator's side of "
        f"the boundary — not in the sleeve.")


@pytest.mark.parametrize("modpath", NOT_MIGRATED)
def test_unmigrated_sleeves_keep_the_old_interface(modpath):
    """Guards the other direction: these four were deliberately left alone, so
    a stray "consistency" edit stripping them would break the orchestrator
    path with no golden watching."""
    mod = importlib.import_module(modpath)
    assert any(hasattr(mod, n) for n in
               ("try_fire_for_variant", "ema_btc_try_fire",
                "eth_daily_try_fire")), \
        f"{modpath} lost its orchestrator entry point"


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
    again — that is the orchestrator's vocabulary, and translating it is
    cfg_adapter's job.

    Scoped to the six deliberately: the dormant sleeves (ai_quant, ema,
    eth_daily, and the timing-anomaly substrategies other than r4) still read
    these keys, which is correct — they were never refactored, and BACKLOG
    step 4 archives them rather than migrating them."""
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
