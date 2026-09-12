"""The ONE place the sleeve call shape is written down.

This is the design rule that makes the golden-record net survive the refactor
it exists to guard. Every golden calls `decide(key, variant=..., weight_pct=...)`
— the POST-refactor plain-keyword shape — and this module translates that into
whatever the sleeves accept TODAY.

  today   each adapter builds a sleeve_cfg dict and calls
          try_decide_for_variant(variant, cfg)
  after   each adapter calls decide(variant, weight_pct=..., leverage=...)

So the goldens never mention `sleeve_cfg`, `_effective_leverage`,
`try_decide_for_variant` or `try_fire_for_variant`, and the strip edits exactly
one file under tests/. That makes the gate mechanical and reviewable: a strip
commit whose diff touches a golden file is changing behaviour, not shape.

The full sleeve_cfg surface is seven keys, and this module is their complete
inventory:

    _effective_weight_pct / weight_pct   all six
    _effective_leverage                  adx, carry, chento, short_squeeze,
                                         squeeze_bull (NOT r4)
    params.stop_loss_pct                 adx only
    priority                             all six (unobservable on the bot path)
    use_stop                             short_squeeze, squeeze_bull
    count_diag                           short_squeeze
    _effective_gate / _effective_vol_scalar   r4 only

r4 is the odd one out twice over: it never reads `_effective_leverage`, and the
bot calls its four window deciders directly plus the PRIVATE `_r4_execute` — a
name nothing outside bots/r4/runner.py pins today, which is precisely why it is
the sleeve the strip most endangers.
"""
from __future__ import annotations

from . import _golden_guard  # noqa: F401  — sets the diag env before imports

KEYS = ("adx", "carry", "chento_btc", "chento_eth", "short_squeeze",
        "squeeze_bull", "r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2")

R4_KEYS = {"r4_btc": "r4_btc_decide", "r4_eth": "r4_eth_decide",
           "r4_btc_v2": "r4_btc_v2_decide", "r4_eth_v2": "r4_eth_v2_decide"}

#: Sentinel meaning "whatever this bot actually passes". The five non-r4 bots
#: pass weight_pct 100.0; bots/r4/runner.py passes ONLY {"priority": 100}, so
#: r4's weight must be ABSENT — its fallback arm (the timing-anomaly weights
#: table, including the bear-regime zero) is the live behaviour, and injecting
#: _effective_weight_pct silently overrides the regime gate.
AS_BOT = object()


def _module(key: str):
    if key == "adx":
        from strategies.sleeves.adx import signal
        return signal
    if key == "carry":
        from strategies.sleeves.carry import signal
        return signal
    if key in ("chento_btc", "chento_eth"):
        from strategies.sleeves import chento_triple_v3
        return chento_triple_v3
    if key == "short_squeeze":
        from strategies.sleeves.short_squeeze import signal
        return signal
    if key == "squeeze_bull":
        from strategies.sleeves.squeeze_bull import signal
        return signal
    if key in R4_KEYS:
        from strategies.sleeves.timing_anomalies.internal.r4 import signal
        return signal
    raise KeyError(key)


def _state_module(key: str):
    """Where the per-sleeve process state actually lives.

    For every sleeve but chento this is the same module the bot calls. chento's
    bot imports the PACKAGE (`strategies.sleeves.chento_triple_v3`), which
    re-exports the three entry points but not the caches — so resetting the
    package silently resets nothing, and the second golden in a file then runs
    against a warm feature cache and a stale `_last_trigger_ts`. That produced
    a `cooldown` where a `no_triple` was expected, intermittently, depending on
    test order.
    """
    if key in ("chento_btc", "chento_eth"):
        from strategies.sleeves.chento_triple_v3 import signal
        return signal
    return _module(key)


def _cfg(key: str, *, weight_pct, leverage, priority, params,
         use_stop, count_diag, gate, vol_scalar) -> dict:
    """TODAY's shape. After the strip this function disappears and each branch
    below passes plain keywords instead."""
    cfg: dict = {"weight_pct": weight_pct, "priority": priority}
    if weight_pct is not None:
        cfg["_effective_weight_pct"] = weight_pct
    if key in R4_KEYS:
        # r4 never reads _effective_leverage; it reads the gate and the vol
        # scalar, and its FALLBACK arms are the live behaviour because
        # bots/r4/runner.py passes only {"priority": 100}.
        if gate is not None:
            cfg["_effective_gate"] = gate
        if vol_scalar is not None:
            cfg["_effective_vol_scalar"] = vol_scalar
        return cfg
    cfg["_effective_leverage"] = leverage
    if key == "adx":
        cfg["params"] = dict(params or {})
    if key in ("short_squeeze", "squeeze_bull"):
        cfg["use_stop"] = use_stop
    if key == "short_squeeze":
        cfg["count_diag"] = count_diag
    return cfg


def _bot_weight(key: str, weight_pct):
    """Resolve the AS_BOT sentinel to what that bot really passes."""
    if weight_pct is not AS_BOT:
        return weight_pct
    return None if key in R4_KEYS else 100.0


def decide(key: str, *, variant: dict, weight_pct=AS_BOT,
           leverage: float = 1.0, priority: float = 100.0,
           params: dict | None = None, use_stop: bool = True,
           count_diag: bool = True, gate=None, vol_scalar=None):
    """Returns (intents, status), exactly as the bot runners consume it."""
    mod = _module(key)
    w = _bot_weight(key, weight_pct)
    # --- migrated sleeves: called with plain keywords, the target shape ---
    if key == "squeeze_bull":
        return mod.decide(variant, weight_pct=(w or 0.0), leverage=leverage,
                          priority=priority, use_stop=use_stop)
    # --- not yet migrated: build the cfg dict ---
    cfg = _cfg(key, weight_pct=w, leverage=leverage,
               priority=priority, params=params, use_stop=use_stop,
               count_diag=count_diag, gate=gate, vol_scalar=vol_scalar)
    if key in R4_KEYS:
        return getattr(mod, R4_KEYS[key])(variant, cfg)
    return mod.try_decide_for_variant(variant, cfg)


def execute(key: str, *, variant: dict, intent, weight_pct=AS_BOT,
            leverage: float = 1.0, priority: float = 100.0,
            params: dict | None = None, use_stop: bool = True,
            count_diag: bool = True, gate=None, vol_scalar=None):
    """Phase 2. r4 routes through the PRIVATE _r4_execute, which is what
    bots/r4/runner.py calls."""
    mod = _module(key)
    if key == "squeeze_bull":
        return mod.execute(variant, intent)
    cfg = _cfg(key, weight_pct=_bot_weight(key, weight_pct), leverage=leverage,
               priority=priority, params=params, use_stop=use_stop,
               count_diag=count_diag, gate=gate, vol_scalar=vol_scalar)
    if key in R4_KEYS:
        return mod._r4_execute(variant, cfg, intent)
    return mod.execute_for_variant(variant, cfg, intent)


def reset_module_state(key: str) -> None:
    """Clear the per-sleeve process state that would otherwise make the second
    golden in a file not a golden. Three different mechanisms exist and a
    helper that only clears dicts misses the DB-backed one:

      module dicts   chento's feature cache and _last_eval_bar_ts,
                     short_squeeze's distribution + macro caches,
                     squeeze_bull's _last_eval_hour, adx's _trend_block_logged
      date cache     jplus_inputs caches today_inputs by UTC date
      DB-backed      adx's _adx_trade_exists_today and carry's
                     _carry_action_today query the trades table — those are
                     fixture state, not module state, and are NOT reset here
    """
    mod = _state_module(key)
    for name in ("_cache_date", "_cache_built_at", "_cached_features",
                 "_cached_obs", "_last_trigger_ts", "_last_loss_ts",
                 "_last_eval_bar_ts", "_diag_current_day", "_diag_counters",
                 "_diag_near_misses", "_diag_b5_last", "_dist_cache_date",
                 "_perp_cvd_dist", "_divergence_dist", "_macro_cache",
                 "_diag_state", "_last_eval_hour", "_trend_block_logged"):
        if not hasattr(mod, name):
            continue
        cur = getattr(mod, name)
        if isinstance(cur, dict):
            cur.clear()
        elif isinstance(cur, list):
            cur.clear()
        else:
            setattr(mod, name, None)
    try:
        from strategies.support import jplus_inputs
        jplus_inputs._invalidate_today_inputs_cache()
    except Exception:  # noqa: BLE001 — not every sleeve pulls this in
        pass
