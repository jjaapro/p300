"""Tests for services.ai_quant.decision.run_decision.

The Anthropic client is mocked: every test scripts a sequence of
"messages.create" responses and asserts the orchestrator drives the
loop correctly. No network, no DB, no chart rendering — context bundle
and baseline image are injected directly so each test is fast and
deterministic.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.ai_quant import decision, tools as tools_mod


# ─── Mock SDK objects ───────────────────────────────────────────────────────

class MockBlock:
    """Stand-in for Anthropic's TextBlock / ToolUseBlock. Attribute access
    matches what _block_to_dict and the loop's getattr() reads."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self) -> dict:
        return dict(self.__dict__)


def text_block(text: str) -> MockBlock:
    return MockBlock(type="text", text=text)


def tool_use_block(name: str, input_: dict, id_: str = "tu1") -> MockBlock:
    return MockBlock(type="tool_use", id=id_, name=name, input=input_)


class MockUsage:
    def __init__(self, **kw):
        self.input_tokens = kw.get("input_tokens", 0)
        self.output_tokens = kw.get("output_tokens", 0)
        self.cache_creation_input_tokens = kw.get("cache_creation_input_tokens", 0)
        self.cache_read_input_tokens = kw.get("cache_read_input_tokens", 0)


class MockResponse:
    def __init__(self, content: list[MockBlock], stop_reason: str = "tool_use",
                 usage: dict | None = None):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = MockUsage(**(usage or {}))


class _MessagesNamespace:
    def __init__(self, scripted: list[Any]):
        self._scripted = list(scripted)
        self.calls: list[dict] = []

    def create(self, **kw):
        # Snapshot a shallow copy of `messages` — the orchestrator mutates
        # the list in place across turns, so capturing the live reference
        # would let later turns rewrite history we want to inspect.
        snap = dict(kw)
        if "messages" in snap:
            snap["messages"] = list(snap["messages"])
        self.calls.append(snap)
        if not self._scripted:
            raise RuntimeError("MockClient: no more scripted responses")
        nxt = self._scripted.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class MockClient:
    """Anthropic-shaped client whose .messages.create() returns scripted
    responses (or raises a scripted exception). Tracks each call's kwargs
    in .messages.calls for assertion."""

    def __init__(self, scripted: list[Any]):
        self.messages = _MessagesNamespace(scripted)


# ─── Common fixtures ────────────────────────────────────────────────────────

# Tiny PNG so tests don't render a real chart
_DUMMY_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00fake"
_DUMMY_CONTEXT = {"as_of_utc": "2026-05-08T00:00:00+00:00", "asset": "BTC", "fixture": True}


def _typical_decision_input() -> dict:
    return {
        "direction": "LONG", "conviction_0_100": 65, "time_horizon_days": 5,
        "key_drivers": ["funding flipped negative", "F&G fear bucket"],
        "exit_conditions": "close on funding > +0.02% sustained 24h",
        "confidence_caveats": "low news flow this 24h",
        "rationale_md": "Mean-reversion setup off the recent retest.",
    }


# ─── Single-turn happy path ────────────────────────────────────────────────

def test_run_decision_one_turn_submit_returns_decision_and_cost():
    client = MockClient([
        MockResponse(
            content=[
                text_block("Reading the bundle, BTC funding flipped negative; FOMC 40d out."),
                tool_use_block("submit_decision", _typical_decision_input()),
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 5000, "output_tokens": 800,
                   "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 0},
        ),
    ])
    result = decision.run_decision(
        variant_id="p300_test", asset="BTC", client=client,
        include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    assert result.decision is not None
    assert result.error is None
    assert result.turns == 1
    assert result.decision["direction"] == "LONG"
    assert result.decision["conviction_0_100"] == 65
    # Cost: 5000*15 + 800*75 + 4000*18.75 + 0  → /1e6 = 0.21
    assert result.cost_usd == pytest.approx(0.21, rel=1e-3)
    assert result.model_id  # non-empty


def test_run_decision_first_call_includes_system_prompt_with_cache_control():
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    first_call = client.messages.calls[0]
    sys_blocks = first_call["system"]
    assert isinstance(sys_blocks, list) and len(sys_blocks) == 1
    assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "discretionary btc trader" in sys_blocks[0]["text"].lower()


def test_run_decision_initial_user_message_includes_context_and_image():
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle={"as_of_utc": "2026-05-08", "fixture_marker": "abc123"},
        baseline_chart_png=_DUMMY_PNG,
    )
    first_user_msg = client.messages.calls[0]["messages"][0]
    types = [b["type"] for b in first_user_msg["content"]]
    assert types.count("text") == 2
    assert "image" in types
    full_text = " ".join(
        b["text"] for b in first_user_msg["content"] if b["type"] == "text"
    )
    assert "fixture_marker" in full_text


# ─── Multi-turn ────────────────────────────────────────────────────────────

def test_run_decision_multi_turn_chart_then_submit():
    client = MockClient([
        MockResponse(
            content=[
                text_block("Let me look at the 4h chart for context."),
                tool_use_block("render_chart",
                                {"timeframe": "4h", "lookback_bars": 60},
                                id_="t1"),
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 5000, "output_tokens": 200,
                   "cache_creation_input_tokens": 4000},
        ),
        MockResponse(
            content=[
                text_block("Clear setup. Submitting LONG."),
                tool_use_block("submit_decision",
                                _typical_decision_input(), id_="t2"),
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 6000, "output_tokens": 400,
                   "cache_read_input_tokens": 4000},
        ),
    ])
    result = decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    assert result.decision is not None
    assert result.error is None
    assert result.turns == 2
    # Both tool calls logged
    assert [tc["name"] for tc in result.tool_calls] == ["render_chart", "submit_decision"]
    # render_chart input echoed for audit
    assert result.tool_calls[0]["input"]["timeframe"] == "4h"
    # Total usage summed across both turns
    assert result.usage["input_tokens"] == 11000
    assert result.usage["output_tokens"] == 600
    assert result.usage["cache_creation_input_tokens"] == 4000
    assert result.usage["cache_read_input_tokens"] == 4000


def test_run_decision_multiple_tool_uses_in_one_turn_all_dispatched():
    """Model calls render_chart AND query_news in the same turn before
    submitting. The loop must dispatch both and feed both tool_results back."""
    client = MockClient([
        MockResponse(
            content=[
                tool_use_block("render_chart", {"timeframe": "1h", "lookback_bars": 72}, id_="t1"),
                tool_use_block("query_news", {"asset": "BTC", "hours": 12}, id_="t2"),
            ],
            stop_reason="tool_use",
        ),
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input(), id_="t3")],
            stop_reason="tool_use",
        ),
    ])
    # The dispatcher's render_chart needs a working chart — patch it to a stub
    # that returns benign content so the test doesn't require a seeded DB.
    import services.ai_quant.tools as t
    orig_render = t._handle_render_chart
    orig_news = t._handle_query_news
    t._handle_render_chart = lambda inp, **k: [{"type": "text", "text": "fake chart"}]
    t._handle_query_news = lambda inp: '{"count":0,"headlines":[]}'
    try:
        result = decision.run_decision(
            variant_id="p300_test", client=client, include_server_tools=False,
            context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
        )
    finally:
        t._handle_render_chart = orig_render
        t._handle_query_news = orig_news
    assert result.decision is not None
    assert [tc["name"] for tc in result.tool_calls] == [
        "render_chart", "query_news", "submit_decision",
    ]
    # The user message after turn 1 should contain BOTH tool_results
    second_call_messages = client.messages.calls[1]["messages"]
    last_user_msg = second_call_messages[-1]
    assert last_user_msg["role"] == "user"
    tool_result_ids = [
        b["tool_use_id"] for b in last_user_msg["content"] if b["type"] == "tool_result"
    ]
    assert sorted(tool_result_ids) == ["t1", "t2"]


# ─── Failure modes ─────────────────────────────────────────────────────────

def test_run_decision_end_turn_without_submit_returns_error():
    client = MockClient([
        MockResponse(
            content=[text_block("I cannot decide. Insufficient information.")],
            stop_reason="end_turn",
        ),
    ])
    result = decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    assert result.decision is None
    assert result.error is not None
    assert "submit_decision" in result.error


def test_run_decision_hits_max_turns_when_model_never_submits():
    # Every scripted turn makes a render_chart call, never submits.
    scripted = [
        MockResponse(
            content=[tool_use_block("render_chart",
                                     {"timeframe": "1d", "lookback_bars": 30},
                                     id_=f"t{i}")],
            stop_reason="tool_use",
        )
        for i in range(5)
    ]
    client = MockClient(scripted)
    import services.ai_quant.tools as t
    orig = t._handle_render_chart
    t._handle_render_chart = lambda inp, **k: [{"type": "text", "text": "fake"}]
    try:
        result = decision.run_decision(
            variant_id="p300_test", client=client, include_server_tools=False,
            context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
            max_turns=3,
        )
    finally:
        t._handle_render_chart = orig
    assert result.decision is None
    assert "max_turns=3" in result.error
    assert result.turns == 3


def test_run_decision_api_exception_returns_error_result_not_raised():
    client = MockClient([RuntimeError("boom: 500 Internal Server Error")])
    result = decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    assert result.decision is None
    assert "RuntimeError" in result.error
    assert "boom" in result.error
    assert result.turns == 1


def test_run_decision_no_api_key_returns_graceful_error(monkeypatch):
    """When client=None and ANTHROPIC_API_KEY is unset, return an error
    rather than crashing the caller."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = decision.run_decision(
        variant_id="p300_test", client=None, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    assert result.decision is None
    assert "ANTHROPIC_API_KEY" in result.error


def test_run_decision_dispatcher_error_returns_string_to_model_and_continues():
    """If a tool handler raises, we send the error back as the tool_result
    so the model can correct course, rather than abandoning the turn."""
    from services.ai_quant import tools as t

    class FlakyChart:
        def __init__(self):
            self.calls = 0

        def __call__(self, inp, **kw):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic chart failure")
            return [{"type": "text", "text": "ok"}]

    flaky = FlakyChart()
    orig = t._handle_render_chart
    t._handle_render_chart = flaky
    try:
        client = MockClient([
            MockResponse(
                content=[tool_use_block("render_chart",
                                         {"timeframe": "1d", "lookback_bars": 30},
                                         id_="t1")],
                stop_reason="tool_use",
            ),
            MockResponse(
                content=[tool_use_block("submit_decision",
                                         _typical_decision_input(), id_="t2")],
                stop_reason="tool_use",
            ),
        ])
        result = decision.run_decision(
            variant_id="p300_test", client=client, include_server_tools=False,
            context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
        )
    finally:
        t._handle_render_chart = orig
    assert result.decision is not None
    # The next API call's user message should contain the error string
    second_call = client.messages.calls[1]
    tool_result = next(
        b for msg in second_call["messages"] if msg["role"] == "user"
        for b in msg["content"] if b.get("type") == "tool_result"
        and b.get("tool_use_id") == "t1"
    )
    assert "synthetic chart failure" in tool_result["content"]


# ─── Tools toggle ──────────────────────────────────────────────────────────

def test_run_decision_no_web_strips_server_tools_from_request():
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    tools_arg = client.messages.calls[0]["tools"]
    types = {t.get("type") for t in tools_arg if "type" in t}
    assert "web_search_20250305" not in types
    assert "web_fetch_20250910" not in types
    names = {t.get("name") for t in tools_arg}
    assert "submit_decision" in names


def test_run_decision_with_web_tools_includes_server_tools():
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=True,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    tools_arg = client.messages.calls[0]["tools"]
    types = {t.get("type") for t in tools_arg if "type" in t}
    assert "web_search_20250305" in types
    assert "web_fetch_20250910" in types


# ─── Cost ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,usage,expected", [
    # opus 4.7: 1000 input * 15 + 100 output * 75 = 15000 + 7500 = 22500 → /1e6 = 0.0225
    ("claude-opus-4-7",
     {"input_tokens": 1000, "output_tokens": 100}, 0.0225),
    # cache write/read pricing: 4000*18.75 + 1000*1.50 = 75000 + 1500 = 76500 → /1e6 = 0.0765
    ("claude-opus-4-7",
     {"cache_creation_input_tokens": 4000, "cache_read_input_tokens": 1000}, 0.0765),
    # sonnet 4.6: 1M input * 3 + 1M output * 15 = 18 USD
    ("claude-sonnet-4-6",
     {"input_tokens": 1_000_000, "output_tokens": 1_000_000}, 18.0),
    # unknown model → 0
    ("claude-future-99-99", {"input_tokens": 1_000_000}, 0.0),
])
def test_compute_cost_known_pricing_table(model, usage, expected):
    assert decision._compute_cost(model, usage) == pytest.approx(expected, abs=1e-6)


def test_dataclass_is_json_serializable():
    """DecisionResult.to_dict round-trips through json.dumps."""
    import json
    res = decision.DecisionResult(
        decision={"direction": "LONG"}, error=None, turns=1,
        tool_calls=[{"name": "submit_decision", "input": {}}],
        usage={"input_tokens": 100}, cost_usd=0.001, model_id="claude-opus-4-7",
    )
    j = json.dumps(res.to_dict())
    assert "LONG" in j
    assert "claude-opus-4-7" in j


# ─── Extended thinking (reasoning-effort) budget ──────────────────────────

def test_thinking_budget_passed_to_api_when_default(monkeypatch):
    """Default AI_QUANT_THINKING_BUDGET is non-zero, so every API call
    must include a `thinking` parameter."""
    monkeypatch.delenv("AI_QUANT_THINKING_BUDGET", raising=False)
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    call_kwargs = client.messages.calls[0]
    assert "thinking" in call_kwargs
    assert call_kwargs["thinking"]["type"] == "enabled"
    assert call_kwargs["thinking"]["budget_tokens"] == decision.DEFAULT_THINKING_BUDGET
    # max_tokens must accommodate budget + headroom
    assert call_kwargs["max_tokens"] >= (
        decision.DEFAULT_THINKING_BUDGET + decision.MIN_RESPONSE_HEADROOM
    )


def test_thinking_budget_respected_from_env(monkeypatch):
    monkeypatch.setenv("AI_QUANT_THINKING_BUDGET", "16000")
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
    )
    call_kwargs = client.messages.calls[0]
    assert call_kwargs["thinking"]["budget_tokens"] == 16000
    assert call_kwargs["max_tokens"] >= 16000 + decision.MIN_RESPONSE_HEADROOM


def test_thinking_disabled_when_budget_zero(monkeypatch):
    """AI_QUANT_THINKING_BUDGET=0 omits the thinking parameter entirely
    and reverts to the caller's max_tokens."""
    monkeypatch.setenv("AI_QUANT_THINKING_BUDGET", "0")
    client = MockClient([
        MockResponse(
            content=[tool_use_block("submit_decision", _typical_decision_input())],
            stop_reason="tool_use",
        ),
    ])
    decision.run_decision(
        variant_id="p300_test", client=client, include_server_tools=False,
        context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
        max_tokens=4096,
    )
    call_kwargs = client.messages.calls[0]
    assert "thinking" not in call_kwargs
    assert call_kwargs["max_tokens"] == 4096


def test_thinking_budget_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AI_QUANT_THINKING_BUDGET", "not-an-int")
    assert decision._thinking_budget() == decision.DEFAULT_THINKING_BUDGET


def test_thinking_budget_negative_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("AI_QUANT_THINKING_BUDGET", "-100")
    assert decision._thinking_budget() == 0


def test_thinking_budget_persists_across_multi_turn(monkeypatch):
    """When thinking is enabled, every API call in the loop must carry
    the same parameter — not just the first one."""
    monkeypatch.setenv("AI_QUANT_THINKING_BUDGET", "8000")
    client = MockClient([
        MockResponse(
            content=[tool_use_block("render_chart",
                                     {"timeframe": "4h", "lookback_bars": 60},
                                     id_="t1")],
            stop_reason="tool_use",
        ),
        MockResponse(
            content=[tool_use_block("submit_decision",
                                     _typical_decision_input(), id_="t2")],
            stop_reason="tool_use",
        ),
    ])
    import services.ai_quant.tools as t
    orig = t._handle_render_chart
    t._handle_render_chart = lambda inp, **k: [{"type": "text", "text": "stub"}]
    try:
        decision.run_decision(
            variant_id="p300_test", client=client, include_server_tools=False,
            context_bundle=_DUMMY_CONTEXT, baseline_chart_png=_DUMMY_PNG,
        )
    finally:
        t._handle_render_chart = orig
    assert len(client.messages.calls) == 2
    for call in client.messages.calls:
        assert call.get("thinking", {}).get("budget_tokens") == 8000


# ─── Fact-check protocol in system prompt ─────────────────────────────────

def test_system_prompt_contains_fact_check_protocol():
    """The fact-check section must be in the system prompt verbatim so
    the LLM sees it on every turn (and a regression doesn't quietly
    drop it)."""
    from services.ai_quant import prompt as prompt_mod
    sys_text = prompt_mod.SYSTEM_PROMPT
    assert "FACT-CHECK PROTOCOL" in sys_text
    # Anchored-claim language present
    assert "trace" in sys_text.lower()
    assert "anchored" in sys_text.lower() or "anchor" in sys_text.lower()
    # The "internal inconsistency" check is the specific lesson from the
    # first real run's "EMA50/150 bullish cross confirmed" + caveat
    # contradiction; pin it so a future prompt rewrite can't quietly drop it.
    assert "inconsisten" in sys_text.lower()


def test_system_prompt_enumerates_three_anchor_sources():
    """A claim is valid when anchored to one of three sources: a bundle
    field, a bundle news headline, or a web_search/web_fetch URL.
    All three must be explicit in the prompt so the model doesn't
    downweight news-anchored claims (the failure mode that prompted
    the protocol revision)."""
    from services.ai_quant import prompt as prompt_mod
    sys_text = prompt_mod.SYSTEM_PROMPT.lower()
    # Bundle field anchoring
    assert "bundle field" in sys_text or "bundle." in sys_text
    # News-section anchoring — explicit so claims like "10-year record"
    # cited to a CoinDesk headline are recognized as valid.
    assert "news section" in sys_text or "news headline" in sys_text
    # Web-search anchoring
    assert "web_search" in sys_text or "web search" in sys_text


def test_system_prompt_calls_out_ema_ordering_check_specifically():
    """The first live run produced 'EMA50/150 bullish cross confirmed'
    when EMA50 was $5,696 below EMA150 — a directly self-contradicting
    claim. The prompt must call out the EMA-ordering check by name so
    future runs verify it explicitly before claiming a cross."""
    from services.ai_quant import prompt as prompt_mod
    sys_text = prompt_mod.SYSTEM_PROMPT
    # The literal section header
    assert "EMA-ORDERING CHECK" in sys_text
    # Both sides of the comparison referenced
    assert "ema50" in sys_text.lower()
    assert "ema150" in sys_text.lower()


def test_system_prompt_lists_bundle_quantitative_window_limits():
    """When the model wants to make a historical claim ('longest streak
    in N years'), it needs to know which windows the bundle's quant
    fields actually cover so it doesn't invent reach beyond them.
    Pin the four window declarations explicitly."""
    from services.ai_quant import prompt as prompt_mod
    sys_text = prompt_mod.SYSTEM_PROMPT.lower()
    # All four windows the context bundle actually carries
    assert "funding 7d" in sys_text
    assert "oi 7d" in sys_text
    assert "dvol 30d" in sys_text
    assert "returns 30d" in sys_text


def test_system_prompt_includes_concrete_examples_block():
    """The prompt teaches by example: anchored vs unanchored. The
    examples block is the single highest-leverage few lines for
    calibrating the model's claim-anchoring style."""
    from services.ai_quant import prompt as prompt_mod
    sys_text = prompt_mod.SYSTEM_PROMPT
    # Examples block uses ✓ / ✗ markers
    assert "✓" in sys_text
    assert "✗" in sys_text
    # The specific EMA failure mode appears as a ✗ example
    assert "bullish cross confirmed" in sys_text.lower()
