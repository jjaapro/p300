"""Tests for services.ai_quant.tools.

Cover: tool-definition shape (Anthropic API contract), dispatcher routing,
handler outputs (chart returns image content block, news returns JSON,
submit_decision raises with validated payload), and the validator's
clamping/coercion behaviour.
"""
from __future__ import annotations

import base64
import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from services import news_fetcher
from services.ai_quant import tools


# ─── Tool definitions ───────────────────────────────────────────────────────

def test_tool_definitions_includes_required_custom_tools():
    defs = tools.tool_definitions(include_server_tools=False)
    names = {d["name"] for d in defs}
    assert {"render_chart", "query_news", "submit_decision"}.issubset(names)
    # No server-side tools when explicitly disabled
    assert all("type" not in d or d.get("type") not in {
        "web_search_20250305", "web_fetch_20250910",
    } for d in defs)


def test_tool_definitions_includes_server_tools_by_default():
    defs = tools.tool_definitions()
    types = {d.get("type") for d in defs if "type" in d}
    assert "web_search_20250305" in types
    assert "web_fetch_20250910" in types


def test_render_chart_schema_constrains_timeframe_and_indicator_enums():
    schema = tools.RENDER_CHART_TOOL["input_schema"]
    assert schema["properties"]["timeframe"]["enum"] == tools.CHART_TIMEFRAMES
    assert (schema["properties"]["indicators"]["items"]["enum"]
            == tools.CHART_INDICATORS)
    assert schema["required"] == ["timeframe", "lookback_bars"]


def test_submit_decision_schema_requires_full_field_set():
    schema = tools.SUBMIT_DECISION_TOOL["input_schema"]
    required = set(schema["required"])
    assert {"direction", "conviction_0_100", "time_horizon_days",
            "key_drivers", "exit_conditions", "confidence_caveats",
            "rationale_md"}.issubset(required)
    assert schema["properties"]["direction"]["enum"] == ["LONG", "SHORT", "FLAT"]
    conv = schema["properties"]["conviction_0_100"]
    assert conv["minimum"] == 0 and conv["maximum"] == 100


# ─── Validator ──────────────────────────────────────────────────────────────

def test_validate_decision_payload_clamps_conviction_and_horizon():
    payload = tools._validate_decision_payload({
        "direction": "long",  # lowercase → uppercased
        "conviction_0_100": 250,  # → clamped to 100
        "time_horizon_days": 999,  # → clamped to 30
        "key_drivers": ["a"],
        "exit_conditions": "x",
        "confidence_caveats": "y",
        "rationale_md": "z",
    })
    assert payload["direction"] == "LONG"
    assert payload["conviction_0_100"] == 100
    assert payload["time_horizon_days"] == 30


def test_validate_decision_payload_rejects_unknown_direction():
    with pytest.raises(ValueError, match="invalid direction"):
        tools._validate_decision_payload({"direction": "BUY", "conviction_0_100": 50})


def test_validate_decision_payload_handles_non_string_drivers():
    payload = tools._validate_decision_payload({
        "direction": "FLAT", "conviction_0_100": 0, "time_horizon_days": 1,
        "key_drivers": "single string",  # wrong shape → wrapped
        "exit_conditions": "", "confidence_caveats": "", "rationale_md": "",
    })
    assert payload["key_drivers"] == ["single string"]


def test_validate_decision_payload_truncates_long_drivers_list():
    payload = tools._validate_decision_payload({
        "direction": "LONG", "conviction_0_100": 50, "time_horizon_days": 3,
        "key_drivers": [f"d{i}" for i in range(20)],
        "exit_conditions": "", "confidence_caveats": "", "rationale_md": "",
    })
    assert len(payload["key_drivers"]) == 7


# ─── Dispatcher: render_chart ──────────────────────────────────────────────

@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Minimal trader.db with synthetic BTC 1h bars + a news table."""
    import math
    p = tmp_path / "trader.db"
    con = sqlite3.connect(str(p))
    try:
        con.execute("""
            CREATE TABLE cd_spot_binance (
                timestamp INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL, volume REAL
            )
        """)
        anchor = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
        klines = []
        for i in range(150 * 24):
            ts = anchor - (150 * 24 - i) * 3600
            base = 70000.0 + 5000.0 * math.sin(i / 80.0)
            klines.append((ts, base, base + 200, base - 200, base + 50, 100.0))
        con.executemany("INSERT INTO cd_spot_binance VALUES (?,?,?,?,?,?)", klines)
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    from services import clock
    clock.set_simulated_now(datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))
    yield p


def test_dispatch_render_chart_returns_image_and_text_content(fixture_db):
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC", open_positions=None)
    blocks = dispatch("render_chart", {"timeframe": "1d", "lookback_bars": 30})
    assert isinstance(blocks, list)
    types = [b["type"] for b in blocks]
    assert "image" in types
    img = next(b for b in blocks if b["type"] == "image")
    assert img["source"]["type"] == "base64"
    assert img["source"]["media_type"] == "image/png"
    # Decoded image starts with the PNG magic
    decoded = base64.b64decode(img["source"]["data"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_dispatch_render_chart_passes_open_positions_through(fixture_db):
    dispatch = tools.make_dispatcher(
        variant_id="v", asset="BTC",
        open_positions=[{"direction": "LONG", "entry_price": 70000}],
    )
    blocks = dispatch("render_chart", {"timeframe": "1d", "lookback_bars": 30})
    # Just smoke-check it didn't crash and produced an image
    assert any(b["type"] == "image" for b in blocks)


# ─── Dispatcher: query_news ─────────────────────────────────────────────────

def test_dispatch_query_news_returns_json_string(fixture_db):
    # Seed a couple of fresh headlines via news_fetcher's schema
    con = sqlite3.connect(str(fixture_db))
    try:
        news_fetcher._ensure_schema(con)
        now_ts = int(time.time())
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h1", "cryptopanic:x", now_ts - 600, now_ts,
                     "BTC ETF inflows surge", "https://x/1", "BTC", 1))
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h2", "cryptopanic:y", now_ts - 1200, now_ts,
                     "Range-bound", "https://x/2", "BTC", 0))
        con.commit()
    finally:
        con.close()
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC")
    out = dispatch("query_news", {"asset": "BTC", "hours": 24})
    parsed = json.loads(out)
    assert parsed["count"] >= 2
    assert any("ETF" in h["title"] for h in parsed["headlines"])
    # Hot flag is on the ETF headline
    etf = next(h for h in parsed["headlines"] if "ETF" in h["title"])
    assert etf["hot"] is True


def test_dispatch_query_news_default_limit_and_window(fixture_db):
    """Smoke: missing args don't crash; defaults applied."""
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC")
    out = dispatch("query_news", {})
    parsed = json.loads(out)
    assert "count" in parsed
    assert isinstance(parsed["headlines"], list)


# ─── Dispatcher: submit_decision ───────────────────────────────────────────

def test_dispatch_submit_decision_raises_with_validated_payload(fixture_db):
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC")
    with pytest.raises(tools.DecisionSubmitted) as exc_info:
        dispatch("submit_decision", {
            "direction": "long",
            "conviction_0_100": 65,
            "time_horizon_days": 5,
            "key_drivers": ["funding flipped negative", "F&G fear bucket"],
            "exit_conditions": "close on funding > +0.02% sustained 24h",
            "confidence_caveats": "low news flow",
            "rationale_md": "...",
        })
    payload = exc_info.value.payload
    assert payload["direction"] == "LONG"
    assert payload["conviction_0_100"] == 65
    assert payload["time_horizon_days"] == 5
    assert len(payload["key_drivers"]) == 2


def test_dispatch_submit_decision_clamping_propagates(fixture_db):
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC")
    with pytest.raises(tools.DecisionSubmitted) as exc_info:
        dispatch("submit_decision", {
            "direction": "FLAT", "conviction_0_100": -5,
            "time_horizon_days": 0, "key_drivers": [],
            "exit_conditions": "", "confidence_caveats": "", "rationale_md": "",
        })
    payload = exc_info.value.payload
    assert payload["conviction_0_100"] == 0
    assert payload["time_horizon_days"] == 1


# ─── Dispatcher: unknown tool ──────────────────────────────────────────────

def test_dispatch_unknown_tool_returns_error_string_not_exception(fixture_db):
    dispatch = tools.make_dispatcher(variant_id="v", asset="BTC")
    out = dispatch("not_a_real_tool", {"x": 1})
    assert isinstance(out, str)
    assert "unknown tool" in out
    assert "not_a_real_tool" in out
