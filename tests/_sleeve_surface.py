"""The ONE place the sleeve call shape is written down.

This is the design rule that makes the golden-record net survive the refactor
it exists to guard. Every golden calls `decide(key, variant=..., weight_pct=...)`
— the post-strip plain-keyword shape — and this module translates.

All six running modules are now migrated (phase C steps 9-14), so this file no
longer builds a `sleeve_cfg` dict for anything: it calls each sleeve's real
`decide()` / `execute()` directly. The cfg-dict form survives only as the
legacy adapters inside each sleeve, which `bots/*/runner.py` and
`backtest_runner` still call and which
`tests/test_sleeve_adapter_equivalence.py` pins.

The full cfg surface these keywords replaced was seven keys:

    _effective_weight_pct / weight_pct   all six
    _effective_leverage                  adx, carry, chento, short_squeeze,
                                         squeeze_bull (NOT r4)
    params.stop_loss_pct                 adx only
    priority                             all six (unobservable on the bot path)
    use_stop                             short_squeeze, squeeze_bull
    count_diag                           short_squeeze
    _effective_gate / _effective_vol_scalar   r4 only

r4 is the odd one out twice over: it never reads a leverage keyword, and
`weight_pct=None` means ABSENT rather than zero — its fallback arms (the
timing-anomaly weights table, the gated inner leverage, the vol leverage) are
what the live bot actually runs, because `bots/r4/runner.py` passes none of
them.
"""
from __future__ import annotations

from . import _golden_guard  # noqa: F401  — sets the diag env before imports

KEYS = ("adx", "carry", "chento_btc", "chento_eth", "short_squeeze",
        "squeeze_bull", "r4_btc", "r4_eth", "r4_btc_v2", "r4_eth_v2")

#: golden key -> the sleeve's decider name.
R4_KEYS = {"r4_btc": "decide_btc", "r4_eth": "decide_eth",
           "r4_btc_v2": "decide_btc_v2", "r4_eth_v2": "decide_eth_v2"}

#: Sentinel meaning "whatever this bot actually passes". The five non-r4 bots
#: pass weight_pct 100.0; bots/r4/runner.py passes nothing, so r4's weight must
#: stay None — supplying one overrides the regime gate, including its
#: bear-regime zero.
AS_BOT = object()


def _module(key: str):
    if key == "adx":
        from bots.adx.strategy import signal
        return signal
    if key == "carry":
        from bots.carry.strategy import signal
        return signal
    if key in ("chento_btc", "chento_eth"):
        from bots.chento_v3 import strategy as chento
        return chento
    if key == "short_squeeze":
        from bots.short_squeeze.strategy import signal
        return signal
    if key == "squeeze_bull":
        from bots.squeeze_bull.strategy import signal
        return signal
    if key in R4_KEYS:
        from bots.r4.strategy import signal
        return signal
    raise KeyError(key)


def _state_module(key: str):
    """Where the per-sleeve process state actually lives.

    For every sleeve but chento this is the module the bot calls. chento's bot
    imports the PACKAGE, which re-exports the entry points but not the caches —
    so resetting the package silently resets nothing, and the second golden in
    a file then runs against a warm feature cache and a stale
    `_last_trigger_ts`. That produced a `cooldown` where a `no_triple` was
    expected, intermittently, depending on test order.
    """
    if key in ("chento_btc", "chento_eth"):
        from bots.chento_v3.strategy import signal
        return signal
    return _module(key)


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

    if key in R4_KEYS:
        return getattr(mod, R4_KEYS[key])(
            variant, weight_pct=w, gate=gate, vol_scalar=vol_scalar,
            priority=priority)
    if key == "adx":
        return mod.decide(variant, weight_pct=(w or 0.0), leverage=leverage,
                          priority=priority,
                          stop_loss_pct=float((params or {}).get(
                              "stop_loss_pct", 10.0)))
    if key == "short_squeeze":
        return mod.decide(variant, weight_pct=(w or 0.0), leverage=leverage,
                          priority=priority, use_stop=use_stop,
                          count_diag=count_diag)
    if key == "squeeze_bull":
        return mod.decide(variant, weight_pct=(w or 0.0), leverage=leverage,
                          priority=priority, use_stop=use_stop)
    if key in ("carry", "chento_btc", "chento_eth"):
        return mod.decide(variant, weight_pct=(w or 0.0), leverage=leverage,
                          priority=priority)
    raise KeyError(key)


def execute(key: str, *, variant: dict, intent, **_ignored):
    """Phase 2. Every sleeve's execute() takes only (variant, intent) — all
    the sizing is already on the Intent."""
    return _module(key).execute(variant, intent)


def reset_module_state(key: str) -> None:
    """Clear the per-sleeve process state that would otherwise make the second
    golden in a file not a golden. Three different mechanisms exist and a
    helper that only clears dicts misses the DB-backed one:

      module dicts   chento's feature cache and _last_eval_bar_ts,
                     short_squeeze's distribution + macro caches,
                     squeeze_bull's _last_eval_hour, adx's _trend_block_logged
      date cache     jplus_inputs caches today_inputs by UTC date
      DB-backed      adx's _adx_trade_exists_today, carry's
                     _carry_action_today and r4's _has_trade_for_day query the
                     trades table — fixture state, not module state, and NOT
                     reset here
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
        if isinstance(cur, (dict, list)):
            cur.clear()
        else:
            setattr(mod, name, None)
    try:
        from strategies.support import jplus_inputs
        jplus_inputs._invalidate_today_inputs_cache()
    except Exception:  # noqa: BLE001 — not every sleeve pulls this in
        pass
