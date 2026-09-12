"""One place that translates the orchestrator's `sleeve_cfg` dict into the
plain keywords the sleeves now take.

The cfg dict is an ORCHESTRATOR concept — a bag of `_effective_*` values the
composition loop injects before dispatch. After the strip (BACKLOG.md, "Step 1
re-planned") the sleeves no longer speak it: each exposes `decide(variant, *,
weight_pct, ...)` and `execute(variant, intent)`. The translation has to live
somewhere, and the right side of the boundary is this one, not six copies
inside the sleeves.

That is what this module is: the six `_unpack` helpers that phase C left in
the sleeve modules, gathered here, so phase D can delete them there.

The unpackers are written out one per sleeve rather than driven from a table.
Their differences are real — ADX reads a nested params dict, the two squeeze
sleeves carry per-variant flags, and r4 must pass ABSENCE through — and a
table that flattened them would hide exactly the distinctions that matter.
"""
from __future__ import annotations

from typing import Callable

#: Every key read out of a sleeve_cfg — or, for `stop_loss_pct`, out of the
#: nested `params` dict. Documentation of the full surface, checked against
#: the source in tests/test_cfg_adapter.py so it cannot rot.
KNOWN_KEYS = (
    "weight_pct", "_effective_weight_pct", "_effective_leverage",
    "_effective_gate", "_effective_vol_scalar", "priority", "params",
    "use_stop", "count_diag",
    "stop_loss_pct",        # nested under params, ADX only
)


def _common(cfg: dict) -> dict:
    """weight_pct / leverage / priority — what five of the six sleeves read.

    `_effective_weight_pct` wins over `weight_pct`: that is the orchestrator's
    per-regime injection overriding the composition's static weight. The
    fallbacks are the sleeves' own historical defaults — weight 0.0, NOT
    100.0. A sleeve dispatched with no weight opened nothing, and "tidying"
    that to 100.0 would turn a no-op into a full-size trade.
    """
    return {
        "weight_pct": float(cfg.get("_effective_weight_pct",
                                     cfg.get("weight_pct", 0.0))),
        "leverage": float(cfg.get("_effective_leverage", 1.0)),
        "priority": float(cfg.get("priority", 100)),
    }


def adx(cfg: dict) -> dict:
    """ADX alone reads a nested params dict, and alone has a leverage that is
    load-bearing rather than decorative — under P300_STOP_SEMANTICS=margin it
    sets the threshold stop_path reads back off the trade notes."""
    params = cfg.get("params") or {}
    return {**_common(cfg),
            "stop_loss_pct": float(params.get("stop_loss_pct", 10.0))}


def carry(cfg: dict) -> dict:
    return _common(cfg)


def chento(cfg: dict) -> dict:
    return _common(cfg)


def short_squeeze(cfg: dict) -> dict:
    """Plus the two per-variant flags that separate its live paper twins."""
    return {**_common(cfg),
            "use_stop": bool(cfg.get("use_stop", True)),
            "count_diag": bool(cfg.get("count_diag", True))}


def squeeze_bull(cfg: dict) -> dict:
    return {**_common(cfg), "use_stop": bool(cfg.get("use_stop", True))}


def r4(cfg: dict) -> dict:
    """r4 is the exception, and the reason this is not a table.

    Its weight / gate / vol_scalar pass through as None when ABSENT, because
    absence is the live path: `bots/r4/runner.py` supplies none of them, so
    the sleeve falls back to the timing-anomaly weights table (including its
    bear-regime zero), the gated inner leverage and the vol leverage. Coercing
    any of them to a number here would disable the regime gate.

    It also never reads a leverage keyword at all.
    """
    return {
        "weight_pct": cfg.get("_effective_weight_pct"),
        "gate": cfg.get("_effective_gate"),
        "vol_scalar": cfg.get("_effective_vol_scalar"),
        "priority": float(cfg.get("priority", 100)),
    }


# ─── Dispatch builders ────────────────────────────────────────────────────
# The orchestrator and backtest_runner call (variant, sleeve_cfg); the sleeves
# take keywords. These close over an unpacker to bridge the two.

def decide_entry(fn: Callable, unpack: Callable[[dict], dict]) -> Callable:
    def _decide(variant: dict, sleeve_cfg: dict):
        return fn(variant, **unpack(sleeve_cfg))
    _decide.__name__ = f"decide_entry({getattr(fn, '__name__', '?')})"
    return _decide


def execute_entry(fn: Callable) -> Callable:
    """execute() needs no cfg — everything is already on the Intent."""
    def _execute(variant: dict, sleeve_cfg: dict, intent):
        return fn(variant, intent)
    _execute.__name__ = f"execute_entry({getattr(fn, '__name__', '?')})"
    return _execute


def fire_entry(fn_decide: Callable, fn_execute: Callable,
               unpack: Callable[[dict], dict], *,
               merge_status: bool = False) -> Callable:
    """Single-call decide-then-execute, for `STRATEGY_DISPATCH`.

    `merge_status` reproduces each sleeve's own historical return shape:
    squeeze_bull merged the decide status into the execute result, the others
    returned the execute result alone. Only a log line in the orchestrator
    reads this — `backtest_runner` discards it entirely — but reproducing it
    keeps the switch to these closures a pure no-op.
    """
    def _fire(variant: dict, sleeve_cfg: dict):
        intents, status = fn_decide(variant, **unpack(sleeve_cfg))
        if not intents:
            return status
        res = fn_execute(variant, intents[0])
        return {**status, **res} if merge_status else res
    return _fire
