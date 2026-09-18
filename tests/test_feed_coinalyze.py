"""Tests for the Coinalyze liquidation feed (2026-09-18).

Network is never touched — `fetch_coinalyze._api_get` is monkeypatched.
Same shape as tests/test_feed_coinbase.py.

The regression this file exists for: `ca_liquidations` is fed from a rolling
~89-day source window, so the earliest fetchable timestamp advances with the
clock. Treating the span before the earliest stored row as a fillable gap
makes every run discover a fresh sliver it cannot fetch and record it in
known_unfillable.json — a new entry every hour, forever.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

import fetch_coinalyze
from strategies.support import clock
from strategies.support import db as _db_mod
from data.sources import coinalyze


NOW = datetime(2026, 9, 18, 12, 30, tzinfo=timezone.utc)
NOW_S = int(NOW.timestamp())
H = 3600
DAY = 86400


@pytest.fixture
def tmp_prod(tmp_path, monkeypatch):
    path = (tmp_path / "prod.db").resolve()
    for name in ("PROD_DB", "DASH_DB", "TRADER_DB"):
        monkeypatch.setattr(_db_mod, name, path)
    monkeypatch.setattr(fetch_coinalyze, "DB_PATH", path)
    monkeypatch.setattr(fetch_coinalyze, "RATE_LIMIT_S", 0)
    monkeypatch.setattr(fetch_coinalyze, "UNFILLABLE_PATH",
                        tmp_path / "known_unfillable.json")
    monkeypatch.setenv("COINALYZE_API_KEY", "test-key")
    coinalyze._done.clear()
    yield path
    coinalyze._done.clear()
    clock.set_simulated_now(None)


def _bars(start_ts, n, step):
    return [{"t": start_ts + i * step, "l": 1.0 + i, "s": 2.0 + i}
            for i in range(n)]


def _stub(monkeypatch, responder):
    """Record every request and answer it via `responder(params) -> history`."""
    calls: list[dict] = []

    def fake(endpoint, params, _attempt=0):
        calls.append({"endpoint": endpoint, **params})
        hist = responder(params)
        return [{"history": hist}] if hist else []

    monkeypatch.setattr(fetch_coinalyze, "_api_get", fake)
    return calls


def _windowed(step=H):
    """Model the real API: sub-daily requests older than the rolling window
    come back EMPTY, and the true edge sits just *inside* our constant (it was
    measured at 88-90 days and pinned at 89). That last part is what makes the
    treadmill reproducible — a request at exactly `now - 89d` is the one that
    returns nothing. A stub that answers every request hides the bug entirely,
    because the unfillable write is triggered by an empty response.
    """
    servable_days = fetch_coinalyze.LIQ_ROLLING_WINDOW_DAYS - 1

    def responder(p):
        start = int(p["from"])
        floor = int(datetime.now(timezone.utc).timestamp()) - servable_days * DAY
        if p["interval"] != "daily" and start < floor:
            return []
        return _bars(start, 2, DAY if p["interval"] == "daily" else step)

    return responder


def _rows(path, table):
    con = sqlite3.connect(path)
    try:
        return con.execute(
            f"SELECT asset, timestamp FROM {table} ORDER BY asset, timestamp"
        ).fetchall()
    finally:
        con.close()


# ─── the two-table split ─────────────────────────────────────────────────────

def test_daily_and_hourly_go_to_different_tables(tmp_prod, monkeypatch):
    """Their timestamps collide at UTC midnight and neither table carries an
    interval column, so sharing one would overwrite an hour with a whole day."""
    def responder(p):
        step = DAY if p["interval"] == "daily" else H
        return _bars(int(p["from"]), 3, step)

    _stub(monkeypatch, responder)
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="1hour")
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="daily")

    assert _rows(tmp_prod, "ca_liquidations")
    assert _rows(tmp_prod, "ca_liquidations_daily")
    assert fetch_coinalyze._liq_table("1hour") == "ca_liquidations"
    assert fetch_coinalyze._liq_table("daily") == "ca_liquidations_daily"


def test_a_midnight_hour_is_not_overwritten_by_the_daily_bar(tmp_prod, monkeypatch):
    midnight = (NOW_S // DAY) * DAY

    def responder(p):
        if p["interval"] == "daily":
            return [{"t": midnight, "l": 999.0, "s": 999.0}]
        return [{"t": midnight, "l": 1.0, "s": 2.0}]

    _stub(monkeypatch, responder)
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="1hour")
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="daily")

    con = sqlite3.connect(tmp_prod)
    try:
        hourly = con.execute("SELECT long_qty FROM ca_liquidations "
                             "WHERE timestamp=?", (midnight,)).fetchone()
        daily = con.execute("SELECT long_qty FROM ca_liquidations_daily "
                            "WHERE timestamp=?", (midnight,)).fetchone()
    finally:
        con.close()
    assert hourly[0] == 1.0, "the daily bar overwrote the midnight hour"
    assert daily[0] == 999.0


# ─── the rolling window ──────────────────────────────────────────────────────

def test_hourly_never_asks_beyond_the_rolling_window(tmp_prod, monkeypatch):
    calls = _stub(monkeypatch, lambda p: _bars(int(p["from"]), 2, H))
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="1hour")
    floor = (int(datetime.now(timezone.utc).timestamp())
             - fetch_coinalyze.LIQ_ROLLING_WINDOW_DAYS * DAY)
    assert calls, "expected at least one request"
    assert min(int(c["from"]) for c in calls) >= floor - H


def test_daily_reaches_back_to_the_history_floor(tmp_prod, monkeypatch):
    calls = _stub(monkeypatch, lambda p: _bars(int(p["from"]), 2, DAY))
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="daily")
    assert min(int(c["from"]) for c in calls) == fetch_coinalyze.EARLIEST_LIQ_TS


def test_rolling_window_leading_gap_is_not_recorded_unfillable(tmp_prod, monkeypatch):
    """The treadmill regression: a table whose earliest row sits just inside
    the window must not accrue a new unfillable entry on every run."""
    con = sqlite3.connect(tmp_prod)
    fetch_coinalyze._ensure_liq_table(con, "ca_liquidations")
    start = int(datetime.now(timezone.utc).timestamp()) - 80 * DAY
    con.executemany(
        "INSERT INTO ca_liquidations VALUES (?,?,?,?)",
        [("BTC", start + i * H, 1.0, 2.0) for i in range(5)])
    con.commit()
    con.close()

    _stub(monkeypatch, _windowed())
    for _ in range(3):
        fetch_coinalyze.fetch_liquidations(("BTC",), interval="1hour")

    path = fetch_coinalyze.UNFILLABLE_PATH
    if not path.exists():
        return
    import json
    entries = json.loads(path.read_text())["entries"]
    leading = [e for e in entries
               if e["table"] == "ca_liquidations" and e["start_ts"] < start]
    assert not leading, f"recorded a leading gap on a rolling window: {leading}"


def test_expired_trailing_span_is_recorded_once(tmp_prod, monkeypatch):
    """A real loss — rows that aged out while the feed was down — is worth
    recording, and recording exactly once."""
    con = sqlite3.connect(tmp_prod)
    fetch_coinalyze._ensure_liq_table(con, "ca_liquidations")
    stale = int(datetime.now(timezone.utc).timestamp()) - 200 * DAY
    con.execute("INSERT INTO ca_liquidations VALUES (?,?,?,?)",
                ("BTC", stale, 1.0, 2.0))
    con.commit()
    con.close()

    _stub(monkeypatch, _windowed())
    fetch_coinalyze.fetch_liquidations(("BTC",), interval="1hour")

    import json
    entries = json.loads(fetch_coinalyze.UNFILLABLE_PATH.read_text())["entries"]
    expired = [e for e in entries if "rolling window" in e.get("reason", "")]
    assert len(expired) == 1
    assert expired[0]["start_ts"] >= stale


# ─── the feed-facing refresh ─────────────────────────────────────────────────

def test_refresh_throttles_hourly_and_daily_separately(tmp_prod, monkeypatch):
    seen: list[str] = []

    def fake(assets, interval="1hour"):
        seen.append(interval)
        return {a: 1 for a in assets}

    monkeypatch.setattr(fetch_coinalyze, "fetch_liquidations", fake)
    coinalyze.refresh(now=NOW)
    assert sorted(seen) == ["1hour", "daily"]

    coinalyze.refresh(now=NOW)                       # same hour, same day
    assert sorted(seen) == ["1hour", "daily"], "refetched inside the throttle"

    coinalyze.refresh(now=NOW.replace(hour=NOW.hour + 1))   # new hour, same day
    assert sorted(seen) == ["1hour", "1hour", "daily"]

    coinalyze.refresh(now=NOW.replace(day=NOW.day + 1))     # new day
    assert sorted(seen) == ["1hour", "1hour", "1hour", "daily", "daily"]


def test_refresh_isolates_a_failing_interval(tmp_prod, monkeypatch):
    def fake(assets, interval="1hour"):
        if interval == "1hour":
            raise RuntimeError("upstream down")
        return {a: 3 for a in assets}

    monkeypatch.setattr(fetch_coinalyze, "fetch_liquidations", fake)
    out = coinalyze.refresh(now=NOW)
    assert out["1hour"] == -1
    assert out["daily"] == 6


def test_a_failed_interval_retries_on_the_next_tick(tmp_prod, monkeypatch):
    calls: list[str] = []

    def fake(assets, interval="1hour"):
        calls.append(interval)
        raise RuntimeError("still down")

    monkeypatch.setattr(fetch_coinalyze, "fetch_liquidations", fake)
    coinalyze.refresh(now=NOW)
    coinalyze.refresh(now=NOW)
    assert calls.count("1hour") == 2, "a failure must not consume the throttle slot"


def test_refresh_noops_under_simulated_clock(tmp_prod, monkeypatch):
    monkeypatch.setattr(fetch_coinalyze, "fetch_liquidations",
                        lambda *a, **k: pytest.fail("fetched in sim mode"))
    clock.set_simulated_now(NOW)
    assert coinalyze.refresh() == {}


def test_missing_api_key_is_idle_not_an_error(tmp_prod, monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    monkeypatch.setattr(fetch_coinalyze, "fetch_liquidations",
                        lambda *a, **k: pytest.fail("fetched without a key"))
    assert coinalyze.refresh(now=NOW) == {}


# ─── registration ────────────────────────────────────────────────────────────

def test_tables_are_registered_everywhere():
    import bootstrap, botlib
    from data import check_gaps
    from data.sources import binance

    assert botlib.FRESHNESS_CONTRACTS["ca_liquidations"] == ("timestamp", 1.0, 3 * H)
    assert botlib.FRESHNESS_CONTRACTS["ca_liquidations_daily"] == (
        "timestamp", 1.0, 2 * DAY + H)
    # The live writer replaced the one-shot: a frozen table's staleness is
    # deliberately not a failure, which is how the 2026 outage stayed silent.
    assert "ca_liquidations" not in botlib.FROZEN_TABLES
    for table, cadence in (("ca_liquidations", H), ("ca_liquidations_daily", DAY)):
        spec = next(s for s in check_gaps.SPECS if s.table == table)
        assert (spec.time_col, spec.asset_col, spec.cadence_seconds) == (
            "timestamp", "asset", cadence)
        assert table in bootstrap.SCHEMAS
    assert "data.sources.coinalyze" in binance.refresh_all.__code__.co_consts


def test_bootstrap_and_module_ddl_agree():
    """Two copies of the DDL exist (bootstrap for fresh DBs, the module for
    feed.py which never runs bootstrap) — they must not drift."""
    import bootstrap

    def norm(s):
        return " ".join(s.split())

    for table in ("ca_liquidations", "ca_liquidations_daily"):
        con = sqlite3.connect(":memory:")
        try:
            fetch_coinalyze._ensure_liq_table(con, table)
            module_ddl = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()[0]
        finally:
            con.close()
        con = sqlite3.connect(":memory:")
        try:
            con.execute(bootstrap.SCHEMAS[table])
            boot_ddl = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()[0]
        finally:
            con.close()
        assert norm(module_ddl) == norm(boot_ddl), table
