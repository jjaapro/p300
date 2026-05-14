"""Anthropic tool-use orchestrator for the AI_QUANT daily decision.

`run_decision(variant_id, asset, ...)` builds the context bundle, renders
the baseline chart, and drives a multi-turn conversation with Claude:

  user      [intro text + JSON context bundle + baseline chart image]
  assistant [maybe text reasoning + tool_use blocks]
  user      [tool_result blocks for each custom tool the model called]
  assistant [more reasoning, more tools, eventually submit_decision]
  ...

The loop terminates when the model calls submit_decision (DecisionSubmitted
exception is raised by the dispatcher and caught here), when stop_reason
is "end_turn" without a submit, when an API error occurs, or when we hit
``max_turns``. Server-side tools (web_search, web_fetch) execute inside
Anthropic's infrastructure and are visible to us only through the
``server_tool_use`` content blocks in the response — we don't dispatch them.

Cost is computed per-call from the response's ``usage`` field and the
hard-coded ``_PRICING`` table. Rates are approximate; treat the value as
a tracking aid, not a billing authority.

Persistence (writing the decision to ``ai_quant_decisions`` and emitting
trades) lives in strategies.sleeves.ai_quant.signal — this module is pure
orchestration and can be exercised end-to-end via the CLI without
touching dashboard.db.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strategies.support import clock
from . import chart, context as ctx_mod, prompt, tools as tools_mod

log = logging.getLogger("p300.ai_quant.decision")

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_TOKENS = 65536

# Per-million-token rates in USD. Tracking aid only; the real bill comes
# from Anthropic. Cache pricing follows Anthropic's standard convention
# (creation = 1.25x base input, read = 0.10x base input). Unknown models
# get cost 0 rather than a guessed rate so a model bump never silently
# under- or over-charges the cost-cap gate.
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.0,
        "output": 75.0,
        "cache_creation_input": 18.75,
        "cache_read_input": 1.50,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_creation_input": 3.75,
        "cache_read_input": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input": 1.0,
        "output": 5.0,
        "cache_creation_input": 1.25,
        "cache_read_input": 0.10,
    },
}


def _compute_cost(model: str, usage: dict) -> float:
    """Compute USD cost from a usage dict using the static pricing table.
    Returns 0.0 for unknown models — preferable to a guessed rate that
    would silently break the daily-cost-cap gate."""
    rates = _PRICING.get(model)
    if rates is None:
        return 0.0
    total = 0.0
    total += rates["input"] * (usage.get("input_tokens") or 0) / 1_000_000
    total += rates["output"] * (usage.get("output_tokens") or 0) / 1_000_000
    total += (rates["cache_creation_input"]
              * (usage.get("cache_creation_input_tokens") or 0) / 1_000_000)
    total += (rates["cache_read_input"]
              * (usage.get("cache_read_input_tokens") or 0) / 1_000_000)
    return total

# Reasoning-effort level for the API call. Opus 4.7's adaptive thinking
# is the gateway to step-by-step verification, but without an explicit
# effort hint the model often doesn't engage it on routine prompts. The
# Anthropic API exposes this via:
#   thinking      = {"type": "adaptive"}
#   output_config = {"effort": "<level>"}
#
# Valid levels (Anthropic API as of 2026): "low" / "medium" / "high" / "xhigh" /
# "max". Higher values let the model think longer before answering;
# the model still adaptively decides how much of that ceiling to use.
#
# Setting AI_QUANT_EFFORT to "" / "none" / "disabled" / "off" omits
# both parameters entirely — the API uses its model defaults (no
# thinking on Opus 4.7's adaptive path).
#
# Override via AI_QUANT_EFFORT env var.
DEFAULT_EFFORT = "xhigh"
_VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_DISABLED_TOKENS = ("", "none", "disabled", "off", "0")


@dataclass
class DecisionResult:
    """Single decision-call outcome. JSON-serialisable for journaling.

    Exactly one of ``decision`` or ``deferred`` is non-None on a successful
    call; both are None when ``error`` is set. The runtime distinguishes
    these three states to route the post-call action: open/close/hold
    (decision), schedule a re-fire later today (deferred), or record an
    ERROR row (error).
    """
    decision: dict | None
    error: str | None
    turns: int
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    model_id: str = ""
    deferred: dict | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ─── Helpers ────────────────────────────────────────────────────────────────

def _model_id() -> str:
    return os.environ.get("AI_QUANT_MODEL") or DEFAULT_MODEL


def _effort_level() -> str | None:
    """Read AI_QUANT_EFFORT; default 'high'. Returns one of
    {'low','medium','high','max'} or None if disabled.

    None means "omit thinking + output_config entirely" — the API uses
    its model defaults. For Opus 4.7's adaptive thinking that means
    the model decides whether to think at all, which on routine
    prompts often resolves to "don't"."""
    raw = os.environ.get("AI_QUANT_EFFORT", DEFAULT_EFFORT).strip().lower()
    if raw in _DISABLED_TOKENS:
        return None
    if raw not in _VALID_EFFORTS:
        log.warning(f"AI_QUANT_EFFORT={raw!r} unrecognized; valid: "
                     f"{_VALID_EFFORTS} or one of {_DISABLED_TOKENS} to "
                     f"disable. Falling back to '{DEFAULT_EFFORT}'.")
        return DEFAULT_EFFORT
    return raw


def _build_initial_messages(context_bundle: dict, baseline_chart_png: bytes) -> list[dict]:
    """First user turn: intro text + context JSON + baseline chart."""
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt.USER_INTRO},
            {
                "type": "text",
                "text": "## Context bundle\n\n```json\n"
                         + json.dumps(context_bundle, indent=2, default=str)
                         + "\n```",
            },
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(baseline_chart_png).decode("ascii"),
                },
            },
        ],
    }]


def _block_to_dict(block: Any) -> dict:
    """SDK content block → plain dict suitable for re-sending in messages.

    The Anthropic SDK returns Pydantic models; we round-trip through dict
    when echoing the assistant turn back into the next request. None-valued
    fields are stripped because some optional fields the API rejects on echo.
    """
    if hasattr(block, "model_dump"):
        d = block.model_dump()
    elif hasattr(block, "dict"):
        d = block.dict()
    elif isinstance(block, dict):
        d = dict(block)
    else:
        d = dict(block.__dict__) if hasattr(block, "__dict__") else {}
    return {k: v for k, v in d.items() if v is not None}


def _summarize_tool_call(name: str, input_: dict) -> dict:
    """Short journal entry — image bytes etc. excluded so the audit row stays small."""
    return {"name": name, "input": input_}


def _accumulate_usage(total: dict, resp_usage: Any) -> None:
    if resp_usage is None:
        return
    for k in ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens"):
        total[k] = total.get(k, 0) + (getattr(resp_usage, k, 0) or 0)


# ─── Orchestrator ──────────────────────────────────────────────────────────

def run_decision(
    *,
    variant_id: str,
    asset: str = "BTC",
    open_positions: list[dict] | None = None,
    client: Any = None,
    include_server_tools: bool = True,
    allow_defer: bool = True,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context_bundle: dict | None = None,
    baseline_chart_png: bytes | None = None,
) -> DecisionResult:
    """Run one daily decision conversation.

    Args:
        variant_id, asset: the sleeve's identity for context lookups.
        open_positions: caller-supplied AI_QUANT open position(s) for the
            chart's overlay. None = no annotation.
        client: an Anthropic SDK client (anthropic.Anthropic or compatible
            mock). When None, we instantiate one from ANTHROPIC_API_KEY.
        include_server_tools: enable web_search and web_fetch.
        max_turns: cap on tool-call iterations before bailing.
        max_tokens: per-response output cap.
        context_bundle, baseline_chart_png: pre-built artefacts. If None,
            we build them here. Tests inject these to avoid DB / chart I/O.
    """
    model = _model_id()

    if context_bundle is None:
        context_bundle = ctx_mod.build_context(variant_id, asset)
    if baseline_chart_png is None:
        baseline_chart_png = chart.render_chart(
            asset=asset, timeframe="1d", lookback_bars=90,
            indicators=None, open_positions=open_positions,
        )

    if client is None:
        try:
            import anthropic
        except ImportError:
            return DecisionResult(
                decision=None, error="anthropic SDK not installed",
                turns=0, model_id=model,
            )
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return DecisionResult(
                decision=None, error="ANTHROPIC_API_KEY unset",
                turns=0, model_id=model,
            )
        client = anthropic.Anthropic(api_key=api_key)

    tools_list = tools_mod.tool_definitions(
        include_server_tools=include_server_tools,
        include_defer=allow_defer,
    )
    dispatcher = tools_mod.make_dispatcher(
        variant_id=variant_id, asset=asset, open_positions=open_positions,
    )
    system_blocks = [{
        "type": "text",
        "text": prompt.SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    messages = _build_initial_messages(context_bundle, baseline_chart_png)

    tool_call_log: list[dict] = []
    usage_total: dict = {}
    decision_payload: dict | None = None
    deferred_payload: dict | None = None
    error_str: str | None = None
    turn_index = 0

    effort = _effort_level()

    for turn_index in range(max_turns):
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_blocks,
            "tools": tools_list,
            "messages": messages,
        }
        if effort is not None:
            # Adaptive thinking + effort level. The model decides
            # internally how long to think; effort is the hint that
            # nudges it toward more (or less) deliberation. With Opus
            # 4.7 this is the way to ensure step-by-step verification
            # actually engages on routine prompts.
            api_kwargs["thinking"] = {"type": "adaptive"}
            api_kwargs["output_config"] = {"effort": effort}
        try:
            # Streaming is required by the SDK for any request that may
            # take longer than 10 minutes. With high effort + adaptive
            # thinking + large max_tokens the SDK refuses messages.create,
            # so we use the stream context manager and pull the final
            # assembled Message at the end.
            with client.messages.stream(**api_kwargs) as stream:
                resp = stream.get_final_message()
        except Exception as e:  # noqa: BLE001
            error_str = f"{type(e).__name__}: {e}"
            log.warning(f"AI_QUANT API call failed on turn {turn_index}: {error_str}")
            break

        _accumulate_usage(usage_total, getattr(resp, "usage", None))

        # Echo the assistant's turn back into the message list.
        content_dicts = [_block_to_dict(b) for b in resp.content]
        messages.append({"role": "assistant", "content": content_dicts})

        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "end_turn":
            error_str = "model ended turn without calling submit_decision"
            break

        # We dispatch only CUSTOM tool_use blocks. Server-side tools (e.g.
        # type="server_tool_use", "web_search_tool_result") are already
        # resolved by Anthropic's runtime and shouldn't be re-executed.
        tool_use_blocks = [
            b for b in resp.content if getattr(b, "type", None) == "tool_use"
        ]
        if not tool_use_blocks:
            error_str = (f"unexpected stop_reason {stop_reason!r} with no "
                          f"custom tool_use blocks")
            break

        tool_results: list[dict] = []
        terminate = False
        for tu in tool_use_blocks:
            tu_id = getattr(tu, "id", None)
            tu_name = getattr(tu, "name", "")
            tu_input = getattr(tu, "input", None) or {}
            tool_call_log.append(_summarize_tool_call(tu_name, tu_input))
            try:
                content = dispatcher(tu_name, tu_input)
            except tools_mod.DecisionSubmitted as ds:
                decision_payload = ds.payload
                content = "Decision recorded; turn ends."
                terminate = True
            except tools_mod.DecisionDeferred as dd:
                deferred_payload = dd.payload
                content = "Defer recorded; turn ends."
                terminate = True
            except Exception as e:  # noqa: BLE001
                content = f"Tool error: {type(e).__name__}: {e}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu_id,
                "content": content,
            })
        messages.append({"role": "user", "content": tool_results})

        if terminate:
            break
    else:
        error_str = f"hit max_turns={max_turns} without a decision"

    is_success = decision_payload is not None or deferred_payload is not None
    return DecisionResult(
        decision=decision_payload,
        deferred=deferred_payload,
        error=error_str if not is_success else None,
        turns=turn_index + 1,
        tool_calls=tool_call_log,
        usage=usage_total,
        cost_usd=_compute_cost(model, usage_total),
        model_id=model,
    )


# ─── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Run one AI_QUANT daily decision (no trade emission).")
    p.add_argument("--variant", default="p300_aggressive_v2_v1_0",
                    help="variant_id to use for the context lookup")
    p.add_argument("--asset", default="BTC")
    p.add_argument("--no-web", action="store_true",
                    help="disable web_search/web_fetch server tools")
    p.add_argument("--print-context", action="store_true",
                    help="build and print the context bundle, do NOT call the API")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--out", default=None,
                    help="write the result JSON to this path (in addition to stdout)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Load .env so ANTHROPIC_API_KEY / AI_QUANT_MODEL / etc. are picked up
    # without requiring an explicit shell export. Existing env values win.
    from strategies.support.env import load_env_file
    load_env_file()

    if args.print_context:
        ctx = ctx_mod.build_context(args.variant, args.asset)
        sys.stdout.write(json.dumps(ctx, indent=2, default=str))
        sys.stdout.write("\n")
        return 0

    result = run_decision(
        variant_id=args.variant,
        asset=args.asset,
        include_server_tools=not args.no_web,
        max_turns=args.max_turns,
    )
    body = json.dumps(result.to_dict(), indent=2, default=str)
    sys.stdout.write(body)
    sys.stdout.write("\n")
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    return 0 if result.decision else 1


if __name__ == "__main__":
    raise SystemExit(main())
