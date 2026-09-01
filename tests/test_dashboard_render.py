"""dashboard/render.py — entry-context PNG rendering + cache."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dashboard import render

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dashboard_fixture import build_fixture_db  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    p = build_fixture_db(tmp_path / "prod.db")
    monkeypatch.setattr("strategies.support.db.PROD_DB", p)
    monkeypatch.setattr(render, "CACHE_DIR", tmp_path / "cache")
    return p


def test_chento_entry_chart_renders(fixture_db):
    png = render.cached_entry_chart("SJ-3")
    assert png is not None
    assert png[:8] == PNG_MAGIC
    assert len(png) > 10_000            # a real chart, not an empty figure


def test_carry_renders_without_levels(fixture_db):
    # SJ-2 (CARRY) has no stop/target/timed stop — must still render
    png = render.cached_entry_chart("SJ-2")
    assert png is not None and png[:8] == PNG_MAGIC


def test_cache_reused_for_open_trade(fixture_db):
    first = render.cached_entry_chart("SJ-3")
    cache_file = render.CACHE_DIR / "SJ-3.png"
    assert cache_file.exists()
    stamp = cache_file.stat().st_mtime_ns
    second = render.cached_entry_chart("SJ-3")
    assert second == first
    assert cache_file.stat().st_mtime_ns == stamp   # not re-rendered


def test_unknown_trade_returns_none(fixture_db):
    assert render.cached_entry_chart("SJ-99999") is None
