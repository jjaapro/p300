"""Internal substrategy registry for TIMING_ANOMALIES.

Each substrategy is identified by an uppercase name and provides two
callables: a decide function returning ``(list[Intent], dict)`` and an
execute function that opens the trade described by a single Intent.

This file deliberately *delegates* to the existing sleeve modules
(strategies.sleeves.{fomc,thu_bear,pdo,cpr,r4}.signal) rather than moving
their logic here. The delegation approach has several benefits:

  1. **Zero refactor risk** — existing sleeve tests keep passing, existing
     code paths (orchestrator legacy dispatch, direct-import callers) keep
     working.
  2. **Single source of truth for signal logic** — the actual
     decision/execute code lives where it always has; this registry is
     pure plumbing.
  3. **Incremental migration path** — a follow-up commit can physically
     move each sub-strategy's code into this directory if desired, with
     the existing sleeve dirs becoming thin re-export shims. The
     contract here doesn't change.

Adding a new substrategy: add an entry to ``_RESOLVERS`` returning the
(decide_fn, execute_fn) tuple. Lazy-loaded so a broken sub-module doesn't
prevent the meta-sleeve from importing.
"""
from __future__ import annotations

from typing import Callable

# Each resolver is a callable returning (decide_fn, execute_fn).
# Lazy-imported so circular-import edge cases don't break module load.

def _resolve_fomc() -> tuple[Callable, Callable]:
    from strategies.sleeves.fomc import signal as fomc
    return fomc.try_decide_for_variant, fomc.execute_for_variant


def _resolve_thu_bear() -> tuple[Callable, Callable]:
    from strategies.sleeves.thu_bear import signal as thu_bear
    return thu_bear.try_decide_for_variant, thu_bear.execute_for_variant


def _resolve_pdo() -> tuple[Callable, Callable]:
    from strategies.sleeves.pdo import signal as pdo
    return pdo.try_decide_for_variant, pdo.execute_for_variant


def _resolve_cpr() -> tuple[Callable, Callable]:
    from strategies.sleeves.cpr import signal as cpr
    return cpr.try_decide_for_variant, cpr.execute_for_variant


def _resolve_r4_btc() -> tuple[Callable, Callable]:
    from strategies.sleeves.r4 import signal as r4
    return r4.r4_btc_decide, r4._r4_execute


def _resolve_r4_eth() -> tuple[Callable, Callable]:
    from strategies.sleeves.r4 import signal as r4
    return r4.r4_eth_decide, r4._r4_execute


def _resolve_r4_btc_v2() -> tuple[Callable, Callable]:
    from strategies.sleeves.r4 import signal as r4
    return r4.r4_btc_v2_decide, r4._r4_execute


def _resolve_r4_eth_v2() -> tuple[Callable, Callable]:
    from strategies.sleeves.r4 import signal as r4
    return r4.r4_eth_v2_decide, r4._r4_execute


_RESOLVERS: dict[str, Callable[[], tuple[Callable, Callable]]] = {
    "FOMC":      _resolve_fomc,
    "THU_BEAR":  _resolve_thu_bear,
    "PDO_L_RF":  _resolve_pdo,
    "CPR":       _resolve_cpr,
    "R4_BTC":    _resolve_r4_btc,
    "R4_ETH":    _resolve_r4_eth,
    "R4_BTC_V2": _resolve_r4_btc_v2,
    "R4_ETH_V2": _resolve_r4_eth_v2,
}

# Cache populated on first lookup so each substrategy module is imported once.
_DISPATCH_CACHE: dict[str, tuple[Callable, Callable]] = {}


def get_dispatch(substrategy_name: str) -> tuple[Callable, Callable] | None:
    """Return ``(decide_fn, execute_fn)`` for ``substrategy_name``, or
    None if unknown. Imports the underlying sleeve module lazily."""
    name = substrategy_name.upper()
    if name in _DISPATCH_CACHE:
        return _DISPATCH_CACHE[name]
    resolver = _RESOLVERS.get(name)
    if resolver is None:
        return None
    try:
        funcs = resolver()
    except Exception:
        return None
    _DISPATCH_CACHE[name] = funcs
    return funcs


def known_substrategies() -> list[str]:
    """Return the canonical list of substrategy names this registry knows
    about (regardless of whether they're loaded yet)."""
    return sorted(_RESOLVERS.keys())
