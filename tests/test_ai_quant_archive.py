"""Tests for services.ai_quant.archive — the per-decision .md mirror.

Coverage:
- Filename pattern (date-prefixed, sortable, includes row id).
- Markdown rendering shows direction / conviction / drivers / rationale /
  tool calls; ERROR rows render an Error block; missing fields don't
  blow up the renderer.
- ``write_archive_md`` is best-effort: a write failure returns None and
  doesn't raise.
- End-to-end: ``journal.save_decision`` writes both a DB row AND a .md
  file under ``<dashboard.db parent>/ai_quant_archive/``.
- ``journal.save_decision`` survives an archive write failure.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from services import clock
from services.ai_quant import archive, journal
from services.ai_quant.decision import DecisionResult


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    p = tmp_path / "dashboard.db"
    sqlite3.connect(str(p)).close()
    monkeypatch.setattr("services.db.DASH_DB", p)
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, 34, tzinfo=timezone.utc))
    yield p


def _result_with_decision(**overrides) -> DecisionResult:
    payload = {
        "direction": "LONG", "conviction_0_100": 65, "time_horizon_days": 5,
        "key_drivers": ["funding flipped negative", "F&G fear unlocked"],
        "exit_conditions": "close on funding > +0.02% for 2 consecutive 8h windows",
        "confidence_caveats": "low news flow during US holiday",
        "rationale_md": "Setup looks clean. **BTC** holding above EMA50, "
                         "funding negative — squeeze setup.",
    }
    return DecisionResult(
        decision=overrides.get("decision", payload),
        error=overrides.get("error"),
        turns=overrides.get("turns", 2),
        tool_calls=overrides.get("tool_calls",
                                  [{"name": "render_chart",
                                    "input": {"timeframe": "4h", "lookback_bars": 90}},
                                   {"name": "submit_decision", "input": {}}]),
        usage=overrides.get("usage",
                             {"input_tokens": 5000, "output_tokens": 800,
                              "cache_creation_input_tokens": 4000,
                              "cache_read_input_tokens": 0}),
        cost_usd=overrides.get("cost_usd", 0.21),
        model_id=overrides.get("model_id", "claude-opus-4-7"),
    )


# ─── Filename pattern ───────────────────────────────────────────────────────

def test_filename_is_sortable_and_unique_per_row():
    fn = archive._filename_for(
        row_id=42, decision_date="2026-05-08",
        variant_id="p300_aggressive_v2_v1_0", asset="btc", decided="long",
    )
    # Date-prefixed (so `ls` is chronological), uppercased asset/decided,
    # row id suffix to disambiguate same-day retries.
    assert fn.startswith("2026-05-08_")
    assert "_BTC_" in fn
    assert "_LONG_" in fn
    assert fn.endswith("_id42.md")


def test_filename_strips_unsafe_chars():
    fn = archive._filename_for(
        row_id=1, decision_date="2026-05-08",
        variant_id="weird/variant\\name with spaces",
        asset="BTC", decided="LONG",
    )
    # Slashes / backslashes / spaces removed; alphanumerics preserved.
    assert "/" not in fn
    assert "\\" not in fn
    assert " " not in fn
    assert "weirdvariantnamewithspaces" in fn


# ─── Markdown rendering ─────────────────────────────────────────────────────

def _full_row() -> dict:
    return {
        "id": 17,
        "decision_utc": int(datetime(2026, 5, 8, 0, 12, 34,
                                       tzinfo=timezone.utc).timestamp()),
        "decision_date": "2026-05-08",
        "variant_id": "p300_aggressive_v2_v1_0",
        "asset": "BTC",
        "decided": "LONG",
        "conviction": 65,
        "time_horizon_days": 5,
        "key_drivers_json": '["funding flipped negative", "F&G fear unlocked"]',
        "exit_conditions": "close on funding > +0.02%",
        "confidence_caveats": "low news flow",
        "rationale_md": "Setup looks **clean**.",
        "tool_calls_json": '[{"name":"render_chart","input":{"timeframe":"4h"}}]',
        "model_id": "claude-opus-4-7",
        "input_tokens": 5000, "output_tokens": 800,
        "cache_read_tokens": 0, "cache_write_tokens": 4000,
        "cost_usd": 0.2134, "turns": 2,
        "trade_action": "opened:SJ-1234",
        "error": None,
    }


def test_render_includes_direction_and_conviction_in_title_and_table():
    md = archive._render_markdown(_full_row())
    # Title shows decision at a glance
    assert md.splitlines()[0].startswith("# AI_QUANT decision — 2026-05-08 BTC LONG")
    assert "(conviction 65)" in md
    # Field table
    assert "| **Direction** | **LONG** |" in md
    assert "| **Conviction** | **65 / 100** |" in md
    assert "| Trade action | `opened:SJ-1234` |" in md
    assert "| Cost (USD) | $0.2134 |" in md
    # Token line preserves all four counts, in the documented order
    assert "| Tokens (in / out / cache-write / cache-read) | 5000 / 800 / 4000 / 0 |" in md


def test_render_lists_drivers_and_rationale_and_tool_calls():
    md = archive._render_markdown(_full_row())
    assert "## Key drivers" in md
    assert "- funding flipped negative" in md
    assert "- F&G fear unlocked" in md
    assert "## Exit conditions" in md
    assert "close on funding > +0.02%" in md
    assert "## Confidence caveats" in md
    assert "## Rationale" in md
    assert "Setup looks **clean**." in md
    assert "## Tool calls" in md
    assert "`render_chart`" in md


def test_render_error_row_shows_error_block_and_skips_decision_fields():
    row = _full_row() | {
        "decided": "ERROR",
        "conviction": None,
        "time_horizon_days": None,
        "key_drivers_json": None,
        "exit_conditions": None,
        "confidence_caveats": None,
        "rationale_md": None,
        "tool_calls_json": None,
        "trade_action": "error",
        "error": "hit max_turns=10 without a decision",
    }
    md = archive._render_markdown(row)
    assert "ERROR" in md.splitlines()[0]
    assert "(conviction" not in md.splitlines()[0]
    assert "## Error" in md
    assert "hit max_turns=10" in md
    # Optional sections are omitted when their data is missing
    assert "## Key drivers" not in md
    assert "## Rationale" not in md
    assert "## Tool calls" not in md


def test_render_handles_malformed_json_gracefully():
    row = _full_row() | {
        "key_drivers_json": "not a json array",
        "tool_calls_json": "{also broken",
    }
    md = archive._render_markdown(row)
    # Renderer falls back to empty lists, never raises
    assert "## Key drivers" not in md
    assert "## Tool calls" not in md


def test_render_handles_missing_optional_numeric_fields():
    row = _full_row() | {
        "input_tokens": None, "output_tokens": None,
        "cache_read_tokens": None, "cache_write_tokens": None,
        "cost_usd": None, "turns": None, "time_horizon_days": None,
    }
    md = archive._render_markdown(row)
    # Cost defaults to $0.0000, tokens to 0/0/0/0; renderer doesn't crash
    assert "$0.0000" in md
    assert "0 / 0 / 0 / 0" in md
    assert "| Time horizon | — |" in md


# ─── write_archive_md best-effort behavior ─────────────────────────────────

def test_write_archive_md_creates_file_under_dashboard_db_parent(fixture_db, tmp_path):
    row = _full_row()
    path = archive.write_archive_md(row_id=row["id"], row=row)
    assert path is not None
    assert path.exists()
    # Lives under <db parent>/ai_quant_archive/, not somewhere arbitrary
    assert path.parent == tmp_path / "ai_quant_archive"
    contents = path.read_text(encoding="utf-8")
    assert "AI_QUANT decision" in contents
    assert "LONG" in contents


def test_write_archive_md_returns_none_on_failure(fixture_db, monkeypatch):
    """If the directory can't be created (e.g. parent path is a file),
    the function returns None and logs — never raises."""
    # Force _archive_dir() to a path that can't be a directory
    bad_path = fixture_db.parent / "not_a_dir.txt"
    bad_path.write_text("blocking the directory slot", encoding="utf-8")
    monkeypatch.setattr(archive, "_archive_dir",
                         lambda: bad_path / "ai_quant_archive")
    # bad_path is a regular file → creating bad_path/ai_quant_archive will fail
    result = archive.write_archive_md(row_id=1, row=_full_row())
    assert result is None  # graceful failure


# ─── End-to-end: journal.save_decision triggers archive write ──────────────

def test_save_decision_writes_both_db_row_and_md_file(fixture_db, tmp_path):
    res = _result_with_decision()
    rid = journal.save_decision(
        variant_id="p300_aggressive_v2_v1_0", asset="BTC",
        decision_result=res,
        context_bundle={"as_of_utc": "2026-05-08T00:12:34Z"},
        trade_action="opened:SJ-1234",
    )
    # DB row landed
    assert journal.get_today_decision("p300_aggressive_v2_v1_0")["id"] == rid

    # MD file landed in the expected location with the expected name
    archive_dir = tmp_path / "ai_quant_archive"
    files = list(archive_dir.glob("*.md"))
    assert len(files) == 1
    f = files[0]
    assert f.name == (
        f"2026-05-08_p300_aggressive_v2_v1_0_BTC_LONG_id{rid}.md"
    )

    md = f.read_text(encoding="utf-8")
    assert "AI_QUANT decision — 2026-05-08 BTC LONG" in md
    assert "(conviction 65)" in md
    assert "## Rationale" in md
    assert "Setup looks clean." in md
    assert "opened:SJ-1234" in md


def test_save_decision_archives_error_rows_too(fixture_db, tmp_path):
    res = DecisionResult(
        decision=None, error="API 500: gateway timeout",
        turns=10, tool_calls=[],
        usage={"input_tokens": 30000, "output_tokens": 0},
        cost_usd=0.6, model_id="claude-opus-4-7",
    )
    rid = journal.save_decision(
        variant_id="p300_aggressive_v2_v1_0", asset="BTC",
        decision_result=res, context_bundle=None, trade_action="error",
    )
    files = list((tmp_path / "ai_quant_archive").glob("*.md"))
    assert len(files) == 1
    md = files[0].read_text(encoding="utf-8")
    assert "ERROR" in md
    assert "API 500" in md
    assert files[0].name.endswith(f"_id{rid}.md")


def test_save_decision_returns_normally_even_if_archive_write_fails(
    fixture_db, monkeypatch,
):
    """Archive failure must never poison the DB save."""
    def _boom(**kwargs):
        raise RuntimeError("disk full")
    monkeypatch.setattr(archive, "write_archive_md", _boom)

    rid = journal.save_decision(
        variant_id="p300_aggressive_v2_v1_0", asset="BTC",
        decision_result=_result_with_decision(),
        context_bundle=None, trade_action="opened:SJ-X",
    )
    # DB row landed despite archive failure
    assert isinstance(rid, int) and rid > 0
    assert journal.get_today_decision("p300_aggressive_v2_v1_0")["id"] == rid


def test_same_day_retry_does_not_overwrite_first_row_md(fixture_db, tmp_path):
    """ERROR row at 00:06 then a successful retry at 00:12 — both should
    leave their own .md file (row id makes the filename unique)."""
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 6, tzinfo=timezone.utc))
    err = DecisionResult(
        decision=None, error="API 500", turns=1, tool_calls=[],
        usage={}, cost_usd=0.0, model_id="claude-opus-4-7",
    )
    rid_err = journal.save_decision(
        variant_id="p300_aggressive_v2_v1_0", asset="BTC",
        decision_result=err, trade_action="error",
    )
    clock.set_simulated_now(datetime(2026, 5, 8, 0, 12, tzinfo=timezone.utc))
    rid_ok = journal.save_decision(
        variant_id="p300_aggressive_v2_v1_0", asset="BTC",
        decision_result=_result_with_decision(), trade_action="opened:SJ-Y",
    )
    files = sorted((tmp_path / "ai_quant_archive").glob("*.md"))
    assert len(files) == 2
    names = [f.name for f in files]
    assert any(n.endswith(f"_id{rid_err}.md") for n in names)
    assert any(n.endswith(f"_id{rid_ok}.md") for n in names)
