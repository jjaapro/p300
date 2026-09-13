"""Tool-use schemas and Python dispatch for the AI_QUANT decision call.

Two layers:

1. TOOL_DEFINITIONS — list of dicts handed to the Anthropic API as
   the `tools` parameter. Mix of CUSTOM tools (we execute) and SERVER
   tools (Anthropic-hosted: web_search, web_fetch).

2. make_dispatcher(*, variant_id, asset, open_positions) — returns a
   stateful dispatcher closure. Given a (tool_name, tool_input) it
   invokes the right Python handler and returns a tool_result block
   ready to feed back into the conversation. The orchestrator
   (decision.py) drives the multi-turn loop; tools.py only knows how
   to execute one custom tool call.

The submit_decision tool is special: its handler raises DecisionSubmitted
to short-circuit the orchestrator's loop. The orchestrator catches the
exception, extracts the validated payload, and returns it as the final
result. This keeps the decision-extraction path explicit rather than
relying on the model's choice between text and tool-use endings.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Callable

from data.sources import news as news_fetcher
from . import chart

log = logging.getLogger("p300.ai_quant.tools")


class DecisionSubmitted(Exception):
    """Raised by the submit_decision handler to terminate the loop. Carries
    the validated decision payload as ``payload``."""

    def __init__(self, payload: dict):
        super().__init__("decision submitted")
        self.payload = payload


class DecisionDeferred(Exception):
    """Raised by the defer_decision handler to terminate the loop without
    taking a directional position. The runtime schedules another decision
    run later today based on the validated ``payload`` (retry_in_hours,
    waiting_for, reasoning)."""

    def __init__(self, payload: dict):
        super().__init__("decision deferred")
        self.payload = payload


# ─── Tool definitions ───────────────────────────────────────────────────────

# Server-side tools (Anthropic executes; we just declare them).
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}
WEB_FETCH_TOOL = {
    "type": "web_fetch_20250910",
    "name": "web_fetch",
    "max_uses": 5,
}

# Custom tools we execute locally. Schemas are deliberately tight — we
# want the model's tool calls to be unambiguous and unambiguously routable.
CHART_INDICATORS = [
    "ema20", "ema50", "ema150",
    "volume", "rsi14",
    "funding", "lsr",
    "open_positions",
]
CHART_TIMEFRAMES = ["1h", "4h", "1d"]

RENDER_CHART_TOOL = {
    "name": "render_chart",
    "description": (
        "Render a fresh chart of BTC at the specified timeframe and lookback. "
        "Returns a PNG image you can look at to inspect price action, the EMA "
        "structure, recent funding, or the long/short ratio at higher resolution "
        "than the baseline daily chart you received with the context bundle. "
        "Use this when you want to zoom into a particular regime change, a "
        "candle pattern, or compare timeframes."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "timeframe": {
                "type": "string",
                "enum": CHART_TIMEFRAMES,
                "description": "Bar size: '1h' for intraday, '4h' for swing, '1d' for daily.",
            },
            "lookback_bars": {
                "type": "integer",
                "minimum": 5,
                "maximum": 500,
                "description": "How many bars to display. Typical: 60–180 for 1d, "
                                "120–300 for 4h, 168–500 for 1h.",
            },
            "indicators": {
                "type": "array",
                "items": {"type": "string", "enum": CHART_INDICATORS},
                "description": "Subset of overlays/panels. Defaults to all if omitted.",
            },
        },
        "required": ["timeframe", "lookback_bars"],
    },
}

QUERY_NEWS_TOOL = {
    "name": "query_news",
    "description": (
        "Query the local cache of crypto news headlines (CryptoPanic). Faster "
        "and cheaper than web_search for routine market headlines that have "
        "already been ingested. Use web_search when you need today's freshest "
        "news or non-crypto coverage; use this tool for the last day's worth "
        "of BTC/ETH headlines you can scan quickly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "asset": {
                "type": "string",
                "enum": ["BTC", "ETH"],
                "description": "Filter to one asset's headlines. Omit for all (incl. macro).",
            },
            "hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": 168,
                "description": "Look-back window in hours. Default 24.",
            },
            "min_importance": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1,
                "description": "1 = only community-flagged hot headlines.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Max rows. Default 30.",
            },
        },
    },
}

DEFER_DECISION_TOOL = {
    "name": "defer_decision",
    "description": (
        "Defer the decision: do not open or close any position now, and "
        "trigger another decision run later today instead. Use this when "
        "the current moment is structurally a poor entry — e.g., price "
        "sitting at major resistance, minutes before a binary macro release, "
        "or in a low-conviction zone where waiting for confirmation is "
        "the right call. The runtime will re-prompt you after `retry_in_hours` "
        "with fresh context. Max 3 defers per UTC day; on the 4th call of "
        "the day this tool is no longer available and you must submit_decision. "
        "If your retry would cross midnight UTC, it is clamped to 23:55 today."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "retry_in_hours": {
                "type": "number",
                "minimum": 1,
                "maximum": 23,
                "description": "Hours to wait before the next decision run. "
                                "1-23. Pick the smallest value that gets you "
                                "past the trigger you're waiting for.",
            },
            "waiting_for": {
                "type": "string",
                "description": "Short label for the trigger event, e.g. "
                                "'CPI 8:30 ET', 'BTC daily close vs $83k', "
                                "'4h pullback into EMA50'.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-3 sentences: why this moment is wrong and "
                                "what would make it right.",
            },
        },
        "required": ["retry_in_hours", "waiting_for", "reasoning"],
    },
}

SUBMIT_DECISION_TOOL = {
    "name": "submit_decision",
    "description": (
        "Submit your final trading decision and end the turn. Call exactly "
        "once, only after you have a coherent view. The runtime reads the "
        "submitted fields and either opens/closes/holds the AI_QUANT "
        "position accordingly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["LONG", "SHORT", "FLAT"],
                "description": "LONG = expect BTC up; SHORT = expect BTC down; "
                                "FLAT = no position. FLAT is preferred when "
                                "context is unclear.",
            },
            "conviction_0_100": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "description": "Integer 0–100. <30 → runtime treats as FLAT. "
                                ">70 should be rare; reserve for unusually "
                                "clear setups.",
            },
            "time_horizon_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "How many days before re-evaluation; runtime "
                                "re-prompts daily.",
            },
            "key_drivers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 7,
                "description": "2–5 short bullets, load-bearing reasons.",
            },
            "exit_conditions": {
                "type": "string",
                "description": "Concrete triggers, e.g. 'close on funding > "
                                "+0.02% sustained 24h' or 'close if daily close "
                                "< 78000'.",
            },
            "confidence_caveats": {
                "type": "string",
                "description": "What would flip your view; what's weakest.",
            },
            "rationale_md": {
                "type": "string",
                "description": "2–6 paragraphs of reasoning, plain markdown, "
                                "no headers or HRs.",
            },
        },
        "required": [
            "direction", "conviction_0_100", "time_horizon_days",
            "key_drivers", "exit_conditions", "confidence_caveats",
            "rationale_md",
        ],
    },
}


def tool_definitions(
    *,
    include_server_tools: bool = True,
    include_defer: bool = True,
) -> list[dict]:
    """Build the `tools` parameter payload for the Anthropic API.

    The server-side web_search and web_fetch tools are included by default
    so the model can pull fresh outside-world information. Pass
    include_server_tools=False in tests / dry-runs that should not touch
    the public web (the orchestrator also exposes a flag for this).

    `include_defer` controls whether the model can defer the decision. The
    service strips this tool when today's defer-chain cap (3) is exhausted,
    forcing the model to submit_decision on its next call.
    """
    out: list[dict] = [
        RENDER_CHART_TOOL,
        QUERY_NEWS_TOOL,
        SUBMIT_DECISION_TOOL,
    ]
    if include_defer:
        out.append(DEFER_DECISION_TOOL)
    if include_server_tools:
        out.extend([WEB_SEARCH_TOOL, WEB_FETCH_TOOL])
    return out


# ─── Handlers ───────────────────────────────────────────────────────────────

def _handle_render_chart(input_: dict, *, asset: str,
                          open_positions: list[dict] | None) -> list[dict]:
    """Returns a list of content blocks for the tool_result; the LLM sees
    the image directly."""
    timeframe = input_["timeframe"]
    lookback = int(input_["lookback_bars"])
    indicators = input_.get("indicators")
    png = chart.render_chart(
        asset=asset,
        timeframe=timeframe,
        lookback_bars=lookback,
        indicators=indicators,
        open_positions=open_positions,
    )
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii"),
            },
        },
        {
            "type": "text",
            "text": (
                f"Chart rendered: {asset} {timeframe}, {lookback} bars, "
                f"indicators={indicators or 'default'}."
            ),
        },
    ]


def _handle_query_news(input_: dict) -> str:
    """JSON-serialised list of headlines. Returned as text so the LLM can
    quote selectively."""
    headlines = news_fetcher.query(
        asset=input_.get("asset"),
        hours=int(input_.get("hours", 24)),
        min_importance=int(input_.get("min_importance", 0)),
        limit=int(input_.get("limit", 30)),
    )
    slim = [
        {
            "ts_utc": _epoch_to_iso(h["published_utc"]),
            "title": h["title"],
            "source": h["source"],
            "url": h["url"],
            "asset_tag": h.get("asset_tag"),
            "hot": bool(h.get("importance")),
        }
        for h in headlines
    ]
    return json.dumps({"count": len(slim), "headlines": slim}, indent=2)


def _epoch_to_iso(epoch_s: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(epoch_s), tz=timezone.utc).isoformat()


def _handle_submit_decision(input_: dict) -> str:
    """Validate and short-circuit. Raises DecisionSubmitted with the
    validated payload."""
    payload = _validate_decision_payload(input_)
    raise DecisionSubmitted(payload)


def _handle_defer_decision(input_: dict) -> str:
    """Validate and short-circuit. Raises DecisionDeferred with the
    validated payload. The runtime converts retry_in_hours to an absolute
    timestamp at save time."""
    payload = _validate_defer_payload(input_)
    raise DecisionDeferred(payload)


def _validate_defer_payload(input_: dict) -> dict:
    """Defensive validation on the model's defer call. Clamps retry_in_hours
    to [1, 23] and caps reasoning string lengths so the journal row stays
    reasonable. Service layer separately clamps the resulting absolute
    timestamp to 23:55 UTC of today's date."""
    try:
        retry_h = float(input_.get("retry_in_hours", 0))
    except (TypeError, ValueError):
        raise ValueError("defer_decision: retry_in_hours must be a number")
    retry_h = max(1.0, min(23.0, retry_h))
    return {
        "retry_in_hours": retry_h,
        "waiting_for": str(input_.get("waiting_for", ""))[:200],
        "reasoning": str(input_.get("reasoning", ""))[:1000],
    }


def _validate_decision_payload(input_: dict) -> dict:
    """Defensive validation on the model's submission. The Anthropic
    schema validator catches most issues, but we coerce types and clamp
    here so downstream code can trust the payload."""
    direction = str(input_.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT", "FLAT"):
        raise ValueError(f"submit_decision: invalid direction {direction!r}")
    try:
        conviction = int(input_.get("conviction_0_100", 0))
    except (TypeError, ValueError):
        raise ValueError("submit_decision: conviction_0_100 must be int")
    conviction = max(0, min(100, conviction))
    try:
        horizon = int(input_.get("time_horizon_days", 1))
    except (TypeError, ValueError):
        horizon = 1
    horizon = max(1, min(30, horizon))
    drivers = input_.get("key_drivers") or []
    if not isinstance(drivers, list):
        drivers = [str(drivers)]
    drivers = [str(d) for d in drivers][:7]
    return {
        "direction": direction,
        "conviction_0_100": conviction,
        "time_horizon_days": horizon,
        "key_drivers": drivers,
        "exit_conditions": str(input_.get("exit_conditions", "")),
        "confidence_caveats": str(input_.get("confidence_caveats", "")),
        "rationale_md": str(input_.get("rationale_md", "")),
    }


# ─── Dispatcher ─────────────────────────────────────────────────────────────

# Type alias: a tool_result content block as expected by Anthropic.
ToolResultContent = str | list[dict]

Dispatcher = Callable[[str, dict], ToolResultContent]


def make_dispatcher(
    *,
    variant_id: str,
    asset: str,
    open_positions: list[dict] | None = None,
) -> Dispatcher:
    """Return a dispatcher closure (tool_name, tool_input) -> tool_result content.

    The closure carries the per-call context (variant id, asset, current open
    positions for chart annotation) so handlers stay pure-ish. Unknown tool
    names produce a string tool-error result rather than raising, since
    the model may call a not-yet-supported tool name and we'd rather it
    keep going.
    """
    # variant_id is captured but not currently used by any handler; kept
    # in the signature so future tools (get_open_positions, get_recent_pnl)
    # can read it without an API change.
    _ = variant_id

    def dispatch(name: str, input_: dict) -> ToolResultContent:
        if name == "render_chart":
            return _handle_render_chart(input_, asset=asset,
                                         open_positions=open_positions)
        if name == "query_news":
            return _handle_query_news(input_)
        if name == "submit_decision":
            return _handle_submit_decision(input_)  # raises DecisionSubmitted
        if name == "defer_decision":
            return _handle_defer_decision(input_)  # raises DecisionDeferred
        log.warning(f"unknown tool {name!r}; returning error to model")
        return f"Error: unknown tool {name!r}. Available custom tools: render_chart, query_news, submit_decision, defer_decision."

    return dispatch
