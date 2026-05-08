"""Tests for services.ai_quant.context.

Strategy: seed two synthetic SQLite fixtures (trader.db and dashboard.db)
with just enough rows to exercise each section of build_context, plus
targeted unit tests for the section-level helpers and the _safe wrapper.

We don't reach for real-world numerical accuracy here — the indicator math
is already covered in tests/test_indicators.py — only that the bundler
gathers, shapes, and serialises the data correctly.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import clock
from services.ai_quant import context as ctx_mod


# ─── Fixtures ───────────────────────────────────────────────────────────────

def _seed_trader_db(p: Path, days: int = 200) -> None:
    con = sqlite3.connect(str(p))
    try:
        con.executescript("""
            CREATE TABLE cd_spot_binance (
                timestamp INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL
            );
            CREATE TABLE cd_funding_rate (
                timestamp INTEGER PRIMARY KEY,
                fr_close REAL
            );
            CREATE TABLE ca_long_short_ratio (
                asset TEXT, timestamp INTEGER, ratio REAL,
                PRIMARY KEY (asset, timestamp)
            );
            CREATE TABLE scheduled_events (
                date TEXT, event_type TEXT, description TEXT,
                PRIMARY KEY (date, event_type)
            );
        """)
        anchor = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
        start = anchor - days * 86400
        # Smooth synthetic price walk so EMA/ADX produce real values
        klines = []
        for i in range(days * 24):
            ts = start + i * 3600
            base = 70000.0 + 5000.0 * math.sin(i / 80.0) + i * 0.5
            klines.append((ts, base, base + 200, base - 200, base + 50))
        con.executemany("INSERT INTO cd_spot_binance VALUES (?,?,?,?,?)", klines)
        # Funding: 8h cadence, alternating sign
        funding = []
        for i in range(days * 3):
            ts = start + i * 8 * 3600
            funding.append((ts, 0.0001 * math.sin(i / 6.0)))
        con.executemany("INSERT INTO cd_funding_rate VALUES (?,?)", funding)
        # L/S ratio daily
        lsr = []
        for d in range(days):
            ts = start + d * 86400
            lsr.append(("BTC", ts, 1.0 + 0.3 * math.sin(d / 10.0)))
        con.executemany("INSERT INTO ca_long_short_ratio VALUES (?,?,?)", lsr)
        # Events: a few past + 5 upcoming
        today_iso = datetime(2026, 5, 1, tzinfo=timezone.utc).date().isoformat()
        upcoming = [
            ("2026-05-13", "CPI", "US CPI release"),
            ("2026-05-29", "OPEX_MONTHLY", "Monthly options expiry"),
            ("2026-06-05", "NFP", "US Non-Farm Payrolls"),
            ("2026-06-17", "FOMC", "Fed FOMC decision"),
            ("2026-06-26", "OPEX_QUARTERLY", "Quarterly options expiry"),
        ]
        past = [("2026-04-15", "CPI", "Past CPI"), ("2026-04-30", "FOMC", "Past FOMC")]
        con.executemany(
            "INSERT INTO scheduled_events VALUES (?,?,?)", past + upcoming,
        )
        con.commit()
    finally:
        con.close()


def _seed_dash_db(p: Path) -> None:
    con = sqlite3.connect(str(p))
    try:
        # Minimal trades schema — only the columns the context bundler reads.
        con.execute("""
            CREATE TABLE trades (
                id TEXT PRIMARY KEY,
                series TEXT, asset TEXT, direction TEXT, strategy TEXT,
                regime TEXT, allocation_pct REAL, leverage REAL,
                entry_time TEXT, exit_time TEXT, status TEXT,
                execution_mode TEXT, strategy_variant TEXT,
                actual_entry_time TEXT, entry_price REAL, size_usdt REAL,
                qty REAL, order_ids TEXT, notes TEXT,
                current_qty REAL, current_leverage REAL, current_size_usdt REAL,
                realized_pnl_usdt REAL, avg_entry_price REAL
            )
        """)
        con.commit()
    finally:
        con.close()


def _insert_trade(p: Path, **kw) -> None:
    con = sqlite3.connect(str(p))
    try:
        cols = (
            "id series asset direction strategy regime allocation_pct leverage "
            "entry_time exit_time status execution_mode strategy_variant "
            "actual_entry_time entry_price size_usdt qty order_ids notes "
            "current_qty current_leverage current_size_usdt realized_pnl_usdt "
            "avg_entry_price"
        ).split()
        defaults = {
            "id": "SJ-9001", "series": "SJ", "asset": "BTC", "direction": "LONG",
            "strategy": "AI_QUANT", "regime": "test", "allocation_pct": 5.0,
            "leverage": 3.0, "entry_time": "2026-05-01T00:00:00+00:00",
            "exit_time": "9999-12-31T23:59:59+00:00", "status": "open",
            "execution_mode": "SHADOW",
            "strategy_variant": "p300_test",
            "actual_entry_time": "2026-05-01T00:00:00+00:00",
            "entry_price": 70000.0, "size_usdt": 1500.0, "qty": 0.0214,
            "order_ids": "[]", "notes": "{}",
            "current_qty": 0.0214, "current_leverage": 3.0,
            "current_size_usdt": 1500.0, "realized_pnl_usdt": 0.0,
            "avg_entry_price": 70000.0,
        }
        defaults.update(kw)
        con.execute(
            f"INSERT INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            tuple(defaults[c] for c in cols),
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def fixture_dbs(tmp_path, monkeypatch):
    """Both DBs seeded; clock pinned to 2026-05-08 12:00 UTC."""
    trader = tmp_path / "trader.db"
    dash = tmp_path / "dashboard.db"
    _seed_trader_db(trader)
    _seed_dash_db(dash)
    monkeypatch.setattr("services.db.TRADER_DB", trader)
    monkeypatch.setattr("services.db.DASH_DB", dash)
    clock.set_simulated_now(datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc))
    yield {"trader": trader, "dash": dash}


# ─── Helpers: seed CoinDesk-derived tables ─────────────────────────────────

def _seed_coindesk_tables(p: Path) -> None:
    """Add cd_open_interest / cd_liquidations / cd_dvol to an existing
    trader.db. Caller decides what rows to insert; this just creates the
    schema mirroring what coindesk_fetcher._ensure_schema does."""
    con = sqlite3.connect(str(p))
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS cd_open_interest (
                timestamp INTEGER PRIMARY KEY,
                oi_open REAL, oi_high REAL, oi_low REAL, oi_close REAL,
                oi_value_open REAL, oi_value_high REAL, oi_value_low REAL,
                oi_value_close REAL
            );
            CREATE TABLE IF NOT EXISTS cd_liquidations (
                timestamp INTEGER PRIMARY KEY,
                long_quantity REAL, short_quantity REAL,
                long_quote_quantity REAL, short_quote_quantity REAL,
                long_count INTEGER, short_count INTEGER,
                vwap_long_price REAL, vwap_short_price REAL
            );
            CREATE TABLE IF NOT EXISTS cd_dvol (
                asset TEXT, timestamp INTEGER,
                open REAL, high REAL, low REAL, close REAL,
                PRIMARY KEY (asset, timestamp)
            );
        """)
        con.commit()
    finally:
        con.close()


# ─── Top-level build_context ────────────────────────────────────────────────

def test_build_context_returns_all_expected_sections(fixture_dbs):
    bundle = ctx_mod.build_context("p300_test", "BTC")
    expected_sections = {
        "as_of_utc", "variant_id", "asset", "market", "funding", "lsr",
        "open_interest", "liquidations", "dvol",
        "calendar", "sentiment", "macro", "news", "portfolio", "data_freshness",
    }
    assert set(bundle) == expected_sections
    assert bundle["asset"] == "BTC"
    assert bundle["variant_id"] == "p300_test"


def test_build_context_is_json_serializable(fixture_dbs):
    bundle = ctx_mod.build_context("p300_test", "BTC")
    text = json.dumps(bundle, default=str)
    assert len(text) > 200


def test_build_context_size_stays_under_lookback_budget(fixture_dbs):
    """Sanity-check that the bundle doesn't blow up. Budget: ~32 KB."""
    bundle = ctx_mod.build_context("p300_test", "BTC")
    text = json.dumps(bundle)
    assert len(text) < 32_000


def test_build_context_swallows_section_failure_via_safe_wrapper(monkeypatch, fixture_dbs):
    """If one section raises, the rest still ship and the offending key
    surfaces an ``{"error": ...}`` shape."""
    def boom(*_a, **_k):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(ctx_mod, "_market_section", boom)
    bundle = ctx_mod.build_context("p300_test", "BTC")
    assert "error" in bundle["market"]
    assert "synthetic failure" in bundle["market"]["error"]
    # Other sections are still real dicts, not error stubs
    assert "latest_8h_rate_pct" in bundle["funding"]


# ─── Per-section ────────────────────────────────────────────────────────────

def test_market_section_computes_returns_emas_adx(fixture_dbs):
    section = ctx_mod._market_section("BTC")
    for key in (
        "live_price", "last_daily_close", "pct_change_1d", "pct_change_7d",
        "pct_change_30d", "realized_vol_30d_pct_annualized",
        "ema50", "ema150", "adx14",
    ):
        assert key in section, f"missing {key}"
    # Synthetic data: prices oscillate around 70000 with drift, so EMAs
    # and the close should sit somewhere in the realistic range.
    assert 60_000 < section["last_daily_close"] < 100_000
    assert section["adx14"] is None or 0 <= section["adx14"] <= 100


def test_market_section_errors_when_too_few_candles(tmp_path, monkeypatch):
    """Empty DB → section reports the shortage rather than crashing."""
    p = tmp_path / "tiny.db"
    con = sqlite3.connect(str(p))
    try:
        con.execute("CREATE TABLE cd_spot_binance (timestamp INTEGER PRIMARY KEY, "
                    "open REAL, high REAL, low REAL, close REAL)")
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    clock.set_simulated_now(datetime(2026, 5, 1, tzinfo=timezone.utc))
    section = ctx_mod._market_section("BTC")
    assert "error" in section


def test_funding_section_picks_correct_table_per_asset(fixture_dbs):
    """ETH path uses cd_funding_rate_eth — fixture only seeds BTC's table,
    so ETH section should error cleanly, not return BTC's data."""
    section_btc = ctx_mod._funding_section("BTC")
    assert "latest_8h_rate_pct" in section_btc
    section_eth = ctx_mod._funding_section("ETH")
    assert "error" in section_eth


def test_lsr_section_returns_only_requested_asset(fixture_dbs):
    """Fixture seeds only BTC L/S; ETH should error rather than fall back."""
    btc = ctx_mod._lsr_section("BTC")
    assert btc.get("latest_ratio") is not None
    eth = ctx_mod._lsr_section("ETH")
    assert "error" in eth


def test_calendar_section_only_returns_upcoming_events(fixture_dbs):
    section = ctx_mod._calendar_section()
    assert "next_by_type" in section
    assert "FOMC" in section["next_by_type"]
    fomc = section["next_by_type"]["FOMC"]
    # Fixture FOMC is 2026-06-17, clock is 2026-05-08 → 40 days
    assert fomc["days_to"] == 40
    # No past events should leak in
    for e in section["upcoming_12"]:
        assert e["date"] >= "2026-05-08"


def test_portfolio_section_reports_own_position_and_other_open_trades(fixture_dbs):
    """One AI_QUANT LONG owned by p300_test, one S-003 LONG by the same
    variant (visible-only), and one trade owned by a different variant
    (must NOT show up)."""
    _insert_trade(fixture_dbs["dash"], id="SJ-1001", strategy="AI_QUANT",
                  strategy_variant="p300_test", direction="LONG",
                  entry_price=68000.0, avg_entry_price=68000.0,
                  actual_entry_time="2026-05-06T00:00:00+00:00")
    _insert_trade(fixture_dbs["dash"], id="SJ-1002", strategy="S-003",
                  strategy_variant="p300_test", direction="SHORT",
                  entry_price=72000.0, avg_entry_price=72000.0,
                  actual_entry_time="2026-05-07T00:00:00+00:00")
    _insert_trade(fixture_dbs["dash"], id="SJ-1003", strategy="AI_QUANT",
                  strategy_variant="other_variant",
                  actual_entry_time="2026-05-04T00:00:00+00:00")
    section = ctx_mod._portfolio_section("p300_test", "BTC")
    own = section["ai_quant_open_position"]
    assert own is not None
    assert own["trade_id"] == "SJ-1001"
    assert own["direction"] == "LONG"
    # Age: clock is 2026-05-08 12:00, entry 2026-05-06 00:00 → 60h
    assert own["age_hours"] == pytest.approx(60.0, abs=0.5)
    # Only the same-variant non-AI_QUANT trade shows up
    others = section["other_open_positions"]
    assert len(others) == 1
    assert others[0]["id"] == "SJ-1002"
    assert section["n_other_open"] == 1


def test_portfolio_section_with_no_ai_quant_position_returns_null(fixture_dbs):
    section = ctx_mod._portfolio_section("p300_test", "BTC")
    assert section["ai_quant_open_position"] is None
    assert section["other_open_positions"] == []


def test_freshness_section_handles_missing_news_table(fixture_dbs):
    section = ctx_mod._freshness_section()
    # btc_1h_spot is seeded, news_headlines table doesn't exist → None
    assert section["btc_1h_spot"] is not None
    assert section["btc_1h_spot"]["age_hours"] >= 0
    assert section["news_latest"] is None


def test_news_section_integrates_with_news_fetcher_query(fixture_dbs):
    """Manually insert headlines via news_fetcher's schema and assert
    they show up in the news section, slimmed to (ts_utc, title, source, hot)."""
    from services import news_fetcher
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    try:
        news_fetcher._ensure_schema(con)
        now_ts = int(clock.now_utc().timestamp())
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h1", "cryptopanic:x", now_ts - 600, now_ts,
                     "BTC fresh news", "https://x/1", "BTC", 1))
        con.execute("INSERT INTO news_headlines VALUES (?,?,?,?,?,?,?,?)",
                    ("h2", "cryptopanic:y", now_ts - 1200, now_ts,
                     "Macro news", "https://x/2", None, 0))
        con.commit()
    finally:
        con.close()
    section = ctx_mod._news_section("BTC")
    titles = {h["title"] for h in section["asset_tagged"]}
    assert "BTC fresh news" in titles
    macro_titles = {h["title"] for h in section["macro_untagged"]}
    assert "Macro news" in macro_titles
    assert section["n_hot"] == 1


# ─── _safe wrapper ──────────────────────────────────────────────────────────

def test_safe_returns_dict_on_exception():
    out = ctx_mod._safe("xyz", lambda: (_ for _ in ()).throw(ValueError("nope")))
    assert isinstance(out, dict)
    assert "error" in out
    assert "ValueError" in out["error"]


# ─── Open Interest section ─────────────────────────────────────────────────

def test_open_interest_section_returns_error_when_table_missing(fixture_dbs):
    """Pristine fixture has no cd_open_interest table — section reports
    error rather than crashing."""
    section = ctx_mod._open_interest_section("BTC")
    assert "error" in section


def test_open_interest_section_rejects_non_btc_asset(fixture_dbs):
    """ETH OI requires a parallel table we don't yet populate."""
    section = ctx_mod._open_interest_section("ETH")
    assert "error" in section
    assert "BTC" in section["error"]


def test_open_interest_section_computes_change_and_peak(fixture_dbs):
    """Seeded with 8d of hourly OI; section reports latest + 24h/7d
    delta + 7d peak distance."""
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    rows = []
    # 8 days × 24 hours of synthetic OI rising linearly with a recent dip
    for hours_ago in range(8 * 24, 0, -1):
        ts = now_ts - hours_ago * 3600
        # Linearly grow then dip in the last 12h
        if hours_ago > 12:
            value = 8e9 + (8 * 24 - hours_ago) * 1e7
        else:
            value = 8.5e9 - (12 - hours_ago) * 5e7  # dip
        rows.append((ts, 100, 100, 100, 100, value, value, value, value))
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.executemany("INSERT INTO cd_open_interest VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    section = ctx_mod._open_interest_section("BTC")
    assert "error" not in section
    assert section["latest_btc_perp_usd"] > 0
    assert section["pct_change_24h"] is not None
    assert section["pct_change_7d"] is not None
    assert section["peak_7d_usd"] >= section["latest_btc_perp_usd"]
    assert section["distance_from_7d_peak_pct"] <= 0
    assert "as_of_utc" in section


# ─── Liquidations section ──────────────────────────────────────────────────

def test_liquidations_section_aggregates_24h_and_7d(fixture_dbs):
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    # 30h of rows; 24h ones with longs/shorts and the older ones zero
    rows = []
    for hours_ago in range(30, 0, -1):
        ts = now_ts - hours_ago * 3600
        if hours_ago <= 24:
            longs = 10_000_000.0
            shorts = 2_000_000.0
        else:
            longs = 0.0
            shorts = 0.0
        rows.append((ts, 0, 0, longs, shorts, 5, 1, 80_000, 60_000))
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.executemany("INSERT INTO cd_liquidations VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    section = ctx_mod._liquidations_section("BTC")
    assert "error" not in section
    # 24h × 10M longs = 240M
    assert section["longs_24h_usd"] == pytest.approx(240_000_000.0, abs=1e-3)
    assert section["shorts_24h_usd"] == pytest.approx(48_000_000.0, abs=1e-3)
    assert section["ratio_long_short_24h"] == 5.0
    assert section["n_hours_with_data_24h"] == 24
    assert section["biggest_hour_24h"] is not None


def test_liquidations_section_flags_all_zero_data_quality(fixture_dbs):
    """When upstream returns all zeros (CoinDesk's known sparse history
    period), surface a data_quality_warning instead of letting the LLM
    read it as 'no liquidations'."""
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    rows = [
        (now_ts - h * 3600, 0, 0, 0.0, 0.0, 0, 0, 0, 0)
        for h in range(1, 49)
    ]
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.executemany("INSERT INTO cd_liquidations VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    section = ctx_mod._liquidations_section("BTC")
    assert "data_quality_warning" in section
    assert "missing data" in section["data_quality_warning"].lower()


def test_liquidations_section_returns_error_when_table_missing(fixture_dbs):
    section = ctx_mod._liquidations_section("BTC")
    assert "error" in section


def test_liquidations_section_rejects_non_btc(fixture_dbs):
    section = ctx_mod._liquidations_section("ETH")
    assert "error" in section


# ─── DVOL section ──────────────────────────────────────────────────────────

def test_dvol_section_reports_30d_range_and_percentile(fixture_dbs):
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    # 20 days of synthetic DVOL: 30 → 50 monotone
    rows = []
    for d_ago in range(20, 0, -1):
        ts = now_ts - d_ago * 86400
        close = 30 + (20 - d_ago)
        rows.append(("BTC", ts, close, close, close, close))
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.executemany("INSERT INTO cd_dvol VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    section = ctx_mod._dvol_section("BTC")
    assert "error" not in section
    # Latest is 49 (20-1 = 19, plus 30 = 49)
    assert section["latest"] == 49.0
    assert section["min_30d"] == 30.0
    assert section["max_30d"] == 49.0
    # Latest is the maximum → 100th percentile
    assert section["percentile_30d"] == 100
    assert section["delta_7d_pct"] is not None


def test_dvol_section_isolates_assets(fixture_dbs):
    """ETH DVOL must not return BTC DVOL rows."""
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    rows = []
    for d in range(5):
        ts = now_ts - (5 - d) * 86400
        rows.append(("BTC", ts, 30, 30, 30, 30))
        rows.append(("ETH", ts, 60, 60, 60, 60))
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.executemany("INSERT INTO cd_dvol VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    btc = ctx_mod._dvol_section("BTC")
    eth = ctx_mod._dvol_section("ETH")
    assert btc["latest"] == 30.0
    assert eth["latest"] == 60.0


def test_dvol_section_rejects_unsupported_asset(fixture_dbs):
    section = ctx_mod._dvol_section("DOGE")
    assert "error" in section
    assert "DOGE" in section["error"]


def test_dvol_section_returns_error_when_no_rows(fixture_dbs):
    """Schema present but empty → section reports the gap."""
    _seed_coindesk_tables(fixture_dbs["trader"])
    section = ctx_mod._dvol_section("BTC")
    assert "error" in section


# ─── Freshness section gains derivatives entries ──────────────────────────

def test_freshness_section_includes_derivatives_when_tables_present(fixture_dbs):
    _seed_coindesk_tables(fixture_dbs["trader"])
    now_ts = clock.now_ts()
    con = sqlite3.connect(str(fixture_dbs["trader"]))
    con.execute("INSERT INTO cd_open_interest VALUES (?,?,?,?,?,?,?,?,?)",
                (now_ts - 7200, 100, 100, 100, 100, 8e9, 8e9, 8e9, 8e9))
    con.execute("INSERT INTO cd_liquidations VALUES (?,?,?,?,?,?,?,?,?)",
                (now_ts - 3600, 0, 0, 1000, 500, 1, 1, 80_000, 60_000))
    con.execute("INSERT INTO cd_dvol VALUES (?,?,?,?,?,?)",
                ("BTC", now_ts - 86400, 30, 30, 30, 30))
    con.commit()
    con.close()
    section = ctx_mod._freshness_section()
    assert "open_interest_latest" in section
    assert "liquidations_latest" in section
    assert "dvol_btc_latest" in section
    assert section["open_interest_latest"]["age_hours"] == pytest.approx(2.0, abs=0.1)
    assert section["liquidations_latest"]["age_hours"] == pytest.approx(1.0, abs=0.1)
    assert section["dvol_btc_latest"]["age_hours"] == pytest.approx(24.0, abs=0.1)


def test_freshness_section_tolerates_missing_derivatives_tables(fixture_dbs):
    """Pristine fixture without any cd_* derivatives tables — freshness
    still works for the tables that do exist."""
    section = ctx_mod._freshness_section()
    assert section["open_interest_latest"] is None
    assert section["liquidations_latest"] is None
    assert section["dvol_btc_latest"] is None
    # And the existing tables still report
    assert section["btc_1h_spot"] is not None


def test_safe_passes_dict_through_on_success():
    out = ctx_mod._safe("xyz", lambda: {"a": 1, "b": 2})
    assert out == {"a": 1, "b": 2}
