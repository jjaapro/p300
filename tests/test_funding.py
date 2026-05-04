"""services.funding — single source of truth for perp funding-rate access."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from services import funding


# ─── Shared fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def funding_db(tmp_path, monkeypatch):
    """trader.db with cd_funding_rate populated at idealized 8h-only cadence
    (one row per settlement). Used by accrued_pct tests."""
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE cd_funding_rate ("
        "  timestamp INTEGER PRIMARY KEY, "
        "  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )
    # 3 settlements per day for 3 days — 2024-01-01 to 2024-01-03.
    # Rates in decimal-fraction (Binance convention; 0.0001 = 0.01% per 8h).
    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    rates = [
        (0, 0.0001), (8, 0.0002), (16, -0.0003),    # Jan 1
        (24, 0.0005), (32, -0.0001), (40, 0.0002),  # Jan 2
        (48, -0.0004), (56, 0.0000), (64, 0.0001),  # Jan 3
    ]
    for hours, r in rates:
        ts = int(base.timestamp()) + hours * 3600
        con.execute("INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
                    (ts, r, r, r, r))
    con.commit()
    con.close()
    from services import db as _db_mod; monkeypatch.setattr(_db_mod, "TRADER_DB", db)
    return db


def _build_hourly_db(tmp_path, monkeypatch, day_count: int,
                     settlement_rates_per_day: list[tuple[float, float, float]],
                     non_boundary_rate: float = 999.0):
    """Build a trader.db with HOURLY cadence (24 rows/day) where rates AT 8h
    boundaries are the supplied settlement rates and non-boundary hours are a
    sentinel value (default 999.0). Used to prove funding queries sample only
    at boundary timestamps regardless of stored cadence."""
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE cd_funding_rate ("
        "  timestamp INTEGER PRIMARY KEY, "
        "  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )
    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    for day in range(day_count):
        rates = settlement_rates_per_day[day]
        for hour in range(24):
            if hour == 0:
                rate = rates[0]
            elif hour == 8:
                rate = rates[1]
            elif hour == 16:
                rate = rates[2]
            else:
                rate = non_boundary_rate
            ts = int(base.timestamp()) + (day * 24 + hour) * 3600
            con.execute("INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
                        (ts, rate, rate, rate, rate))
    con.commit()
    con.close()
    from services import db as _db_mod; monkeypatch.setattr(_db_mod, "TRADER_DB", db)
    return db


# ─── accrued_pct ─────────────────────────────────────────────────────────────

def test_short_earns_positive_when_rates_positive(funding_db):
    # Jan 1: 0.0001 + 0.0002 - 0.0003 = 0.0000 → SHORT pct = 0.00%.
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding.accrued_pct("BTC", start, end, "SHORT")
    assert pct == pytest.approx(0.0)


def test_long_sign_is_opposite_of_short(funding_db):
    start = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc)
    short_pct = funding.accrued_pct("BTC", start, end, "SHORT")
    long_pct = funding.accrued_pct("BTC", start, end, "LONG")
    # Jan 2 sum = 0.0005 - 0.0001 + 0.0002 = 0.0006 → SHORT +0.06%, LONG -0.06%.
    assert short_pct == pytest.approx(0.06)
    assert long_pct == pytest.approx(-0.06)
    assert short_pct == -long_pct


def test_sum_across_multiple_days(funding_db):
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, 23, 59, tzinfo=timezone.utc)
    pct = funding.accrued_pct("BTC", start, end, "SHORT")
    # All 9 settlements sum to 0.0003 → SHORT +0.03%.
    assert pct == pytest.approx(0.03)


def test_partial_day_entry_only_counts_settlements_after(funding_db):
    """Enter Jan 2 10:00 UTC — should miss the 00:00 and 08:00 settlements."""
    start = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc)
    pct = funding.accrued_pct("BTC", start, end, "SHORT")
    # Only the 16:00 settlement (0.0002) → +0.02%.
    assert pct == pytest.approx(0.02)


def test_empty_window_returns_zero(funding_db):
    # End before start.
    start = datetime(2024, 1, 3, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert funding.accrued_pct("BTC", start, end, "SHORT") == 0.0


def test_missing_table_returns_zero_and_logs(funding_db, caplog):
    # cd_funding_rate_eth doesn't exist in fixture DB.
    pct = funding.accrued_pct("ETH",
                               datetime(2024, 1, 1, tzinfo=timezone.utc),
                               datetime(2024, 1, 3, tzinfo=timezone.utc),
                               "SHORT")
    assert pct == 0.0


def test_unknown_asset_returns_zero(funding_db):
    assert funding.accrued_pct("SOL",
                                datetime(2024, 1, 1, tzinfo=timezone.utc),
                                datetime(2024, 1, 3, tzinfo=timezone.utc),
                                "SHORT") == 0.0


def test_hourly_fixture_does_not_inflate_funding_8x(tmp_path, monkeypatch):
    """Regression: production tables store 24 hourly rows per day for older
    imports, not 3 settlement rows. The function must filter to 8h boundaries
    so each settlement is counted exactly once. Bug found 2026-05-04 (the
    function was reporting 8x funding for any window touching legacy rows)."""
    _build_hourly_db(tmp_path, monkeypatch, day_count=1,
                     settlement_rates_per_day=[(0.0001, 0.0002, -0.0003)])
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding.accrued_pct("BTC", start, end, "SHORT")
    # 3 settlements sum to 0.0000 → 0.00%. (24-hour sum would also be ~0 here
    # because the rates cancel — kept as a sanity check; the next test uses
    # uneven sentinel rates and is the discriminating regression.)
    assert pct == pytest.approx(0.0, abs=1e-9)


def test_hourly_fixture_with_sentinel_non_boundaries(tmp_path, monkeypatch):
    """Stronger regression: non-boundary hours have rate=999 (absurd). If the
    function sampled them, the result would be enormous. With the boundary
    filter, the result is exactly the sum of the 3 real settlement rates."""
    _build_hourly_db(tmp_path, monkeypatch, day_count=1,
                     settlement_rates_per_day=[(0.0010, 0.0020, 0.0030)],
                     non_boundary_rate=999.0)
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding.accrued_pct("BTC", start, end, "SHORT")
    # Filtered: 0.0010 + 0.0020 + 0.0030 = 0.0060 → +0.60%.
    # Unfiltered: 3*0.006 + 21*999 = ~21000 (test fails loudly).
    assert pct == pytest.approx(0.60, abs=1e-6)


# ─── daily_sums_pct ──────────────────────────────────────────────────────────

def test_daily_sums_pct_returns_three_settlements_summed(funding_db):
    """Each day's value is the sum of its 3 settlements * 100 (PERCENT)."""
    since_ts = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    until_ts = int(datetime(2024, 1, 4, tzinfo=timezone.utc).timestamp())
    sums = funding.daily_sums_pct("BTC", since_ts, until_ts)
    assert sums == {
        "2024-01-01": pytest.approx(0.00, abs=1e-9),  # 0.0001+0.0002-0.0003 = 0
        "2024-01-02": pytest.approx(0.06),            # 0.0006 * 100
        "2024-01-03": pytest.approx(-0.03),           # -0.0003 * 100
    }


def test_daily_sums_pct_drops_incomplete_days_by_default(tmp_path, monkeypatch):
    """A day with <3 settlements is dropped unless complete_only=False."""
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE cd_funding_rate (timestamp INTEGER PRIMARY KEY, "
                "fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)")
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = [(0, 0.0001), (8, 0.0002),                # Jan 1: only 2 settlements
            (24, 0.0005), (32, 0.0001), (40, 0.0001)]  # Jan 2: complete
    for h, r in rows:
        ts = int(base.timestamp()) + h * 3600
        con.execute("INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
                    (ts, r, r, r, r))
    con.commit(); con.close()
    from services import db as _db_mod; monkeypatch.setattr(_db_mod, "TRADER_DB", db)

    since = int(base.timestamp())
    until = since + 3 * 86400
    complete = funding.daily_sums_pct("BTC", since, until, complete_only=True)
    incomplete = funding.daily_sums_pct("BTC", since, until, complete_only=False)
    assert "2024-01-01" not in complete
    assert "2024-01-02" in complete
    assert "2024-01-01" in incomplete
    assert incomplete["2024-01-01"] == pytest.approx(0.03)  # 0.0003*100


def test_daily_sums_pct_uses_only_8h_boundaries(tmp_path, monkeypatch):
    """Same hourly-cadence regression as accrued_pct but for daily_sums_pct."""
    _build_hourly_db(tmp_path, monkeypatch, day_count=2,
                     settlement_rates_per_day=[
                         (0.0010, 0.0020, 0.0030),
                         (0.0040, 0.0050, 0.0060),
                     ],
                     non_boundary_rate=999.0)
    since = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    until = since + 2 * 86400
    sums = funding.daily_sums_pct("BTC", since, until)
    assert sums == {
        "2024-01-01": pytest.approx(0.60),  # 0.006 * 100
        "2024-01-02": pytest.approx(1.50),  # 0.015 * 100
    }


def test_daily_sums_pct_unknown_asset_returns_empty(funding_db):
    assert funding.daily_sums_pct("SOL", 0, 9_999_999_999) == {}


# ─── daily_means_rate ────────────────────────────────────────────────────────

def test_daily_means_rate_returns_avg_of_3_settlements(funding_db):
    until_ts = int(datetime(2024, 1, 4, tzinfo=timezone.utc).timestamp())
    means = funding.daily_means_rate("BTC", until_ts)
    # Means of each day's 3 settlement rates.
    assert means["2024-01-01"] == pytest.approx(0.0, abs=1e-9)
    assert means["2024-01-02"] == pytest.approx(0.0002)
    assert means["2024-01-03"] == pytest.approx(-0.0001)


def test_daily_means_rate_uses_only_8h_boundaries(tmp_path, monkeypatch):
    """Same hourly-cadence regression for daily_means_rate."""
    _build_hourly_db(tmp_path, monkeypatch, day_count=1,
                     settlement_rates_per_day=[(0.0010, 0.0020, 0.0030)],
                     non_boundary_rate=999.0)
    until = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp())
    means = funding.daily_means_rate("BTC", until)
    # Mean of (0.001, 0.002, 0.003) = 0.002.
    # Unfiltered mean of 24 rows would be ~874 (most non-boundary).
    assert means == {"2024-01-01": pytest.approx(0.002, abs=1e-9)}


def test_daily_means_rate_unknown_asset_returns_empty(funding_db):
    assert funding.daily_means_rate("SOL", 9_999_999_999) == {}
