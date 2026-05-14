"""Smoke tests for strategies.sleeves.ai_quant.chart.render_chart.

Builds a synthetic trader.db fixture and asserts the renderer produces a
valid non-empty PNG. Doesn't snapshot the bytes (mplfinance/matplotlib
output is fragile across versions) — instead asserts on the PNG magic
header, byte-count plausibility, and parameter validation paths.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from services import clock
from strategies.sleeves.ai_quant.chart import render_chart

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _seed_fixture_db(db_path, days: int = 200) -> None:
    """Populate a sqlite file with the three tables render_chart reads.

    Daily-style synthetic data: one 1h bar per hour for ``days`` days, one
    funding-rate row per 8h, and one long/short row per day.
    """
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE cd_spot_binance (
                timestamp INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, quote_volume REAL,
                volume_buy REAL, volume_sell REAL,
                total_trades INTEGER, trades_buy INTEGER, trades_sell INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE cd_funding_rate (
                timestamp INTEGER PRIMARY KEY,
                fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL
            )
        """)
        con.execute("""
            CREATE TABLE ca_long_short_ratio (
                asset TEXT, timestamp INTEGER, ratio REAL,
                long_pct REAL, short_pct REAL,
                PRIMARY KEY (asset, timestamp)
            )
        """)
        end = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())
        start = end - days * 86400
        # Synthetic price walk: smooth oscillation around 70000 + small drift
        import math
        klines = []
        for i in range(days * 24):
            ts = start + i * 3600
            base = 70000.0 + 5000.0 * math.sin(i / 80.0) + i * 0.5
            o = base
            h = base + 200
            l = base - 200
            c = base + 50
            klines.append((ts, o, h, l, c, 100.0, 7e6, 50.0, 50.0, 1000, 500, 500))
        con.executemany(
            "INSERT INTO cd_spot_binance VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", klines,
        )
        # Funding every 8h, near zero
        funding = []
        for i in range(days * 3):
            ts = start + i * 8 * 3600
            fr = 0.0001 * math.sin(i / 10.0)
            funding.append((ts, fr, fr, fr, fr))
        con.executemany("INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)", funding)
        # L/S ratio daily
        lsr = []
        for d in range(days):
            ts = start + d * 86400
            ratio = 1.0 + 0.5 * math.sin(d / 20.0)
            lsr.append(("BTC", ts, ratio, 50.0, 50.0))
        con.executemany("INSERT INTO ca_long_short_ratio VALUES (?,?,?,?,?)", lsr)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Synthetic trader.db with 200 days of fake BTC data; clock pinned to 2026-05-01."""
    p = tmp_path / "trader.db"
    _seed_fixture_db(p)
    monkeypatch.setattr("services.db.TRADER_DB", p)
    clock.set_simulated_now(datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc))
    yield p


def test_render_chart_default_args_returns_valid_png(fixture_db):
    png = render_chart(asset="BTC", timeframe="1d", lookback_bars=60)
    assert png[:8] == PNG_MAGIC
    # Plausible size for a 13x6.5" chart at 100 dpi: 30 KB - 300 KB.
    assert 20_000 < len(png) < 500_000


def test_render_chart_writes_to_disk_when_out_path_given(fixture_db, tmp_path):
    out = tmp_path / "out.png"
    png = render_chart(timeframe="1d", lookback_bars=30, out_path=out)
    assert out.exists()
    assert out.read_bytes() == png
    assert out.read_bytes()[:8] == PNG_MAGIC


def test_render_chart_4h_and_1h_timeframes_succeed(fixture_db):
    for tf in ("1h", "4h", "1d"):
        png = render_chart(timeframe=tf, lookback_bars=24)
        assert png[:8] == PNG_MAGIC, f"timeframe {tf} produced non-PNG output"


def test_render_chart_open_position_overlay_does_not_crash(fixture_db):
    png = render_chart(
        timeframe="1d", lookback_bars=30,
        open_positions=[
            {"direction": "LONG", "entry_price": 70000, "entry_dt": "2026-04-15"},
            {"direction": "SHORT", "entry_price": 72000, "entry_dt": "2026-04-20"},
        ],
    )
    assert png[:8] == PNG_MAGIC


def test_render_chart_minimal_indicators_produces_price_only_panel(fixture_db):
    png = render_chart(timeframe="1d", lookback_bars=30, indicators=["ema50"])
    assert png[:8] == PNG_MAGIC
    # Without funding/lsr panels the figure is shorter, so PNG is smaller.
    assert len(png) < 200_000


def test_render_chart_rejects_non_btc_asset(fixture_db):
    with pytest.raises(ValueError, match="BTC"):
        render_chart(asset="ETH", timeframe="1d", lookback_bars=30)


def test_render_chart_rejects_unknown_timeframe(fixture_db):
    with pytest.raises(ValueError, match="timeframe"):
        render_chart(timeframe="2h", lookback_bars=30)


def test_render_chart_rejects_unknown_indicator_name(fixture_db):
    with pytest.raises(ValueError, match="unknown indicators"):
        render_chart(timeframe="1d", lookback_bars=30, indicators=["bollinger"])


def test_render_chart_rejects_too_small_lookback(fixture_db):
    with pytest.raises(ValueError, match="lookback_bars"):
        render_chart(timeframe="1d", lookback_bars=2)


def test_render_chart_new_indicators_are_accepted(fixture_db):
    """ema20, volume, rsi14 must be valid indicator names and render OK
    both individually and as a set."""
    for ind in ("ema20", "volume", "rsi14"):
        png = render_chart(timeframe="1d", lookback_bars=60, indicators=[ind])
        assert png[:8] == PNG_MAGIC, f"{ind} alone produced non-PNG"

    png = render_chart(
        timeframe="1d", lookback_bars=60,
        indicators=["ema20", "ema50", "ema150", "volume", "rsi14",
                    "funding", "lsr"],
    )
    assert png[:8] == PNG_MAGIC


def test_render_chart_default_includes_volume_and_rsi(fixture_db):
    """Default-args chart should now include volume + RSI panels — the PNG
    is materially larger than the price-only minimal chart."""
    full = render_chart(asset="BTC", timeframe="1d", lookback_bars=60)
    minimal = render_chart(timeframe="1d", lookback_bars=60, indicators=["ema50"])
    assert full[:8] == PNG_MAGIC
    assert len(full) > len(minimal), \
        "default chart should be larger than ema50-only after adding panels"


def test_render_chart_raises_when_no_candles_available(tmp_path, monkeypatch):
    """Empty DB → clear error rather than silent empty PNG."""
    p = tmp_path / "empty.db"
    con = sqlite3.connect(str(p))
    try:
        con.execute("CREATE TABLE cd_spot_binance (timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL)")
        con.commit()
    finally:
        con.close()
    monkeypatch.setattr("services.db.TRADER_DB", p)
    clock.set_simulated_now(datetime(2026, 5, 1, tzinfo=timezone.utc))
    with pytest.raises(RuntimeError, match="no candles"):
        render_chart(timeframe="1d", lookback_bars=30)
