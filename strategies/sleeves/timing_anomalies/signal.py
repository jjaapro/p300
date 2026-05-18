"""TIMING_ANOMALIES meta-sleeve dispatcher.

Single sleeve at the orchestrator level. Internally dispatches to multiple
substrategies in `internal/`; each substrategy decides independently and
the meta-sleeve concatenates their intents for the orchestrator reconcile
pass. Substrategies that produce multiple intents per tick (e.g. PDO with
both BTC+ETH legs) flow through unchanged — the orchestrator already
supports multi-intent emission from a single sleeve.

Trade tags: each substrategy's open path writes its own sleeve_name into
`trades.strategy` (e.g. "FOMC", "PDO-L-RF"), so per-substrategy attribution
is preserved without the meta-sleeve having to namespace them.

Composition contract (variant `spec_json.composition` entry):

    {
      "strategy_id": "TIMING_ANOMALIES",
      "weight_pct": 35.0,             # informational sum-of-children
      "params": {
        "substrategies": {
          "FOMC":     {"enabled": True, "weight_pct": 5.0, "leverage": 10.0,
                       "params": {"stop_loss_pct": 5.0}},
          "THU_BEAR": {"enabled": True, "weight_pct": 6.0, "leverage": 5.0,
                       "params": {"version": "V4_event_conditioned",
                                  "assets": ["BTC","ETH"],
                                  "stop_loss_pct": 5.0}},
          ...
        }
      }
    }

The allocator populates each substrategy's `_effective_weight_pct` and
`_effective_leverage` separately so per-substrategy vol-target and margin-
headroom decisions still apply. The meta-sleeve's top-level `weight_pct`
is informational only — actual sizing is per-substrategy.
"""
from __future__ import annotations

import dataclasses
import logging

from strategies.support.dispatch import Intent

from . import internal as _internal

log = logging.getLogger("p300.timing_anomalies")


def _get_dispatch(name: str):
    """Indirection so tests can patch internal.get_dispatch and have it
    take effect (avoids the from-import name-binding gotcha)."""
    return _internal.get_dispatch(name)


def _resolve_substrategy_weight(sub_name: str, fallback: float) -> float:
    """Per-substrategy regime-adaptive weight lookup.

    Translates substrategy_name to the allocator's strategy_id via
    internal.ALLOCATOR_KEY, then calls allocation.get_weight_pct for the
    current regime. Falls back to `fallback` if either the lookup misses
    (warmup, unknown key) or the allocator returns None.

    The translation step is needed because the meta-sleeve uses clean
    short names ("THU_BEAR") while the allocator was wired up against
    legacy strategy_ids ("S-096"). Without this hop, every substrategy
    would silently fall back to its static composition weight, defeating
    the per-regime sizing.
    """
    from strategies.support import allocation
    legacy_id = _internal.allocator_key_for(sub_name)
    if legacy_id is None:
        return fallback
    try:
        regime = allocation.current_regime()
        if regime is None:
            return fallback
        w = allocation.get_weight_pct(legacy_id, regime)
        if w is None:
            return fallback
        return float(w)
    except Exception:
        log.exception(f"[timing_anomalies] allocator lookup failed for {sub_name}")
        return fallback


def _substrategy_sleeve_cfg(parent_cfg: dict, sub_name: str, sub_cfg: dict) -> dict:
    """Build the per-substrategy `sleeve_cfg` to pass to the underlying
    sleeve's decide/execute functions.

    Per-substrategy `_effective_weight_pct` is resolved via the allocator
    using a name-translation hop (see _resolve_substrategy_weight). This
    preserves the regime-adaptive sizing that flat-composition variants
    got from the orchestrator's _resolve_sleeve_weight pass.

    Leverage falls back to the substrategy's static value because the
    orchestrator's per-tick leverage resolver doesn't recurse into the
    meta-sleeve's params.
    """
    static_weight = sub_cfg.get("weight_pct", 0.0)
    leverage = sub_cfg.get("leverage", 1.0)
    eff_weight = sub_cfg.get(
        "_effective_weight_pct",
        _resolve_substrategy_weight(sub_name, static_weight),
    )
    return {
        "weight_pct": static_weight,
        "leverage": leverage,
        "priority": parent_cfg.get("priority", sub_cfg.get("priority", 100)),
        "params": sub_cfg.get("params", {}) or {},
        "_effective_weight_pct": eff_weight,
        "_effective_leverage": sub_cfg.get("_effective_leverage", leverage),
        # Substrategy ID for trace logs / origin tagging
        "_substrategy_name": sub_name,
    }


def _tag_intent_origin(intent: Intent, substrategy_name: str) -> Intent:
    """Return a copy of ``intent`` with ``_origin_substrategy`` set in
    ``reason`` so execute_for_variant can route it back to the right
    sub-strategy. Intent is a frozen dataclass; uses dataclasses.replace
    with a fresh reason dict."""
    new_reason = dict(intent.reason or {})
    new_reason["_origin_substrategy"] = substrategy_name
    return dataclasses.replace(intent, reason=new_reason)


def try_decide_for_variant(variant: dict, sleeve_cfg: dict):
    """Phase 1 of the two-phase dispatch. Iterates each enabled substrategy
    in `sleeve_cfg["params"]["substrategies"]`, calls its decide function,
    tags every returned intent with the substrategy name, and concatenates
    the lists.

    Returns ``(list[Intent], status_dict)``. Status dict carries a
    per-substrategy result map for observability.
    """
    params = sleeve_cfg.get("params") or {}
    substrategies = params.get("substrategies") or {}

    if not substrategies:
        return [], {"status": "no_substrategies_configured"}

    all_intents: list[Intent] = []
    per_sub_status: dict[str, dict] = {}

    for sub_name, sub_cfg in substrategies.items():
        if not isinstance(sub_cfg, dict):
            per_sub_status[sub_name] = {"status": "invalid_config"}
            continue
        if not sub_cfg.get("enabled", True):
            per_sub_status[sub_name] = {"status": "disabled"}
            continue
        dispatch = _get_dispatch(sub_name)
        if dispatch is None:
            per_sub_status[sub_name] = {"status": "unknown_substrategy"}
            continue
        decide_fn, _ = dispatch

        sub_sleeve_cfg = _substrategy_sleeve_cfg(sleeve_cfg, sub_name, sub_cfg)
        try:
            intents, status = decide_fn(variant, sub_sleeve_cfg)
        except Exception:
            log.exception(f"[timing_anomalies] substrategy {sub_name} "
                          f"decide raised; skipping")
            per_sub_status[sub_name] = {"status": "decide_exception"}
            continue

        tagged = [_tag_intent_origin(i, sub_name) for i in intents]
        all_intents.extend(tagged)
        per_sub_status[sub_name] = status

    return all_intents, {"status": "ok", "per_substrategy": per_sub_status,
                          "n_intents": len(all_intents)}


def execute_for_variant(variant: dict, sleeve_cfg: dict, intent: Intent) -> dict:
    """Phase 2: route the intent back to its origin substrategy's execute_fn.

    Looks up `_origin_substrategy` in the intent's reason payload (set by
    `try_decide_for_variant`) and dispatches accordingly. If the tag is
    missing or the origin is unknown, returns a status dict rather than
    raising — the orchestrator should never crash on a malformed intent.
    """
    origin = (intent.reason or {}).get("_origin_substrategy")
    if not origin:
        log.warning(f"[timing_anomalies] intent missing _origin_substrategy; "
                    f"reason keys: {list((intent.reason or {}).keys())}")
        return {"status": "missing_origin"}

    dispatch = _get_dispatch(origin)
    if dispatch is None:
        log.warning(f"[timing_anomalies] unknown origin {origin!r}")
        return {"status": "unknown_origin", "origin": origin}
    _, execute_fn = dispatch

    # Rebuild the per-substrategy sleeve_cfg the way decide saw it. The
    # caller passes the meta-sleeve's cfg; we need the substrategy's slice.
    substrategies = (sleeve_cfg.get("params") or {}).get("substrategies") or {}
    sub_cfg = substrategies.get(origin, {})
    sub_sleeve_cfg = _substrategy_sleeve_cfg(sleeve_cfg, origin, sub_cfg)

    try:
        result = execute_fn(variant, sub_sleeve_cfg, intent)
    except Exception:
        log.exception(f"[timing_anomalies] substrategy {origin} execute raised")
        return {"status": "execute_exception", "origin": origin}
    return {"status": "executed", "origin": origin, "result": result}


def try_fire_for_variant(variant: dict, sleeve_cfg: dict) -> dict:
    """Single-call entry point for the legacy orchestrator path.

    Combines decide + execute. Decide returns a list of intents; we
    execute each one in turn (no orchestrator-level reconcile here —
    the legacy path is only used for sleeves not on two-phase dispatch).
    For TIMING_ANOMALIES, the two-phase path is the preferred entry point;
    this exists for callers that haven't migrated.
    """
    intents, status = try_decide_for_variant(variant, sleeve_cfg)
    if not intents:
        return status
    results = []
    for intent in intents:
        results.append(execute_for_variant(variant, sleeve_cfg, intent))
    return {"status": "fired", "decide_status": status, "executions": results}
