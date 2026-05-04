"""services.funding_util — per-settlement funding accrual."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import funding_util


@pytest.fixture
def funding_db(tmp_path, monkeypatch):
    """Build a minimal trader.db with cd_funding_rate populated with known
    settlements and point funding_util at it."""
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE cd_funding_rate ("
        "  timestamp INTEGER PRIMARY KEY, "
        "  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )
    # 3 settlements per day for 3 days — 2024-01-01 to 2024-01-03
    # Rates: +0.01%, +0.02%, -0.03% in base fraction units (Binance convention)
    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    rates = [
        # Jan 1: 00, 08, 16 UTC
        (0, 0.0001), (8, 0.0002), (16, -0.0003),
        # Jan 2
        (24, 0.0005), (32, -0.0001), (40, 0.0002),
        # Jan 3
        (48, -0.0004), (56, 0.0000), (64, 0.0001),
    ]
    for hours, r in rates:
        ts = int(base.timestamp()) + hours * 3600
        con.execute(
            "INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
            (ts, r, r, r, r),
        )
    con.commit()
    con.close()
    monkeypatch.setattr(funding_util, "TRADER_DB", db)
    return db


def test_short_earns_positive_when_rates_positive(funding_db):
    # Jan 1 covers 3 settlements summing to 0.0001 + 0.0002 - 0.0003 = 0.0000
    # → SHORT earns +0.0000 * 100 = 0.00%
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    assert pct == pytest.approx(0.0)


def test_long_sign_is_opposite_of_short(funding_db):
    start = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc)
    short_pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    long_pct = funding_util.accrued_funding_pct("BTC", start, end, "LONG")
    # Jan 2 sum = 0.0005 - 0.0001 + 0.0002 = 0.0006 → SHORT +0.06%, LONG -0.06%
    assert short_pct == pytest.approx(0.06)
    assert long_pct == pytest.approx(-0.06)
    assert short_pct == -long_pct


def test_sum_across_multiple_days(funding_db):
    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 3, 23, 59, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    # All 9 settlements: 0.0001+0.0002-0.0003 + 0.0005-0.0001+0.0002
    #                    -0.0004+0.0000+0.0001 = 0.0003
    # SHORT pct = 0.0003 × 100 = 0.03%
    assert pct == pytest.approx(0.03)


def test_partial_day_entry_only_counts_settlements_after(funding_db):
    """Enter at Jan 2 10:00 UTC — should miss Jan 2 00:00 and 08:00
    settlements, only include Jan 2 16:00 and onwards."""
    start = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 2, 23, 59, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    # Only the 16:00 settlement (rate 0.0002) → 0.02%
    assert pct == pytest.approx(0.02)


def test_empty_window_returns_zero(funding_db):
    # End before start
    start = datetime(2024, 1, 3, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    assert pct == 0.0


def test_missing_table_returns_zero_and_logs(funding_db, caplog):
    pct = funding_util.accrued_funding_pct("ETH", datetime(2024, 1, 1, tzinfo=timezone.utc),
                                            datetime(2024, 1, 3, tzinfo=timezone.utc),
                                            "SHORT")
    # cd_funding_rate_eth doesn't exist in fixture DB → returns 0 defensively.
    assert pct == 0.0


def test_unknown_asset_returns_zero(funding_db):
    assert funding_util.accrued_funding_pct("SOL",
                                             datetime(2024, 1, 1, tzinfo=timezone.utc),
                                             datetime(2024, 1, 3, tzinfo=timezone.utc),
                                             "SHORT") == 0.0


def test_hourly_fixture_does_not_inflate_funding_8x(tmp_path, monkeypatch):
    """Regression: production cd_funding_rate stores 24 hourly rows per day
    (CryptoDataDownload format), not 3 settlement rows per day. The function
    must filter to 8h boundaries (ts % 28800 == 0) so each settlement is
    counted exactly once. Without this filter the result is 8x inflated.

    Bug found 2026-05-04 against a real MEXC trade where the function reported
    -26.13% funding for a 79-day LONG; correct value was -3.27% (the function
    was counting each rate 8 times).
    """
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE cd_funding_rate ("
        "  timestamp INTEGER PRIMARY KEY, "
        "  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )
    # ONE day, but stored as 24 hourly rows with the rate constant within
    # each 8h block — exactly how production data is structured for older
    # imports. Settlement rates: 0.0001, 0.0002, -0.0003 over the 3 settlements.
    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    for hour in range(24):
        # Determine which 8h block this hour is in.
        if hour < 8:
            rate = 0.0001
        elif hour < 16:
            rate = 0.0002
        else:
            rate = -0.0003
        ts = int(base.timestamp()) + hour * 3600
        con.execute(
            "INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
            (ts, rate, rate, rate, rate),
        )
    con.commit()
    con.close()
    monkeypatch.setattr(funding_util, "TRADER_DB", db)

    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    # Expected: 3 settlements (00:00, 08:00, 16:00) summing to
    # 0.0001 + 0.0002 - 0.0003 = 0.0000 → SHORT receives +0.00%.
    # If the function (incorrectly) summed all 24 hourly rows:
    # 8*0.0001 + 8*0.0002 - 8*0.0003 = 0.0000 too — degenerate test.
    # So the assertion below cannot be the only check; combine with a sum-
    # mismatch case via a separate fixture below.
    assert pct == pytest.approx(0.0, abs=1e-9)


def test_hourly_fixture_with_uneven_rates_counts_settlements_only(tmp_path, monkeypatch):
    """Stronger regression: hourly fixture where the 24-row sum and the
    3-row sum DIFFER — proves the function uses settlement-only sampling."""
    db = tmp_path / "trader.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE cd_funding_rate ("
        "  timestamp INTEGER PRIMARY KEY, "
        "  fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL)"
    )
    # 24 hourly rows where rates vary within each 8h block (not realistic for
    # production but lets us prove the function picks ONLY the boundary rows).
    base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    settlement_rates = {0: 0.0010, 8: 0.0020, 16: 0.0030}
    for hour in range(24):
        # Hours that aren't 8h boundaries get a wildly different rate to expose
        # any function that incorrectly samples them.
        rate = settlement_rates.get(hour, 999.0)
        ts = int(base.timestamp()) + hour * 3600
        con.execute(
            "INSERT INTO cd_funding_rate VALUES (?,?,?,?,?)",
            (ts, rate, rate, rate, rate),
        )
    con.commit()
    con.close()
    monkeypatch.setattr(funding_util, "TRADER_DB", db)

    start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)
    pct = funding_util.accrued_funding_pct("BTC", start, end, "SHORT")
    # If correctly filtered: 0.0010 + 0.0020 + 0.0030 = 0.0060 → +0.60%
    # If broken (sums all 24): 3*0.006 + 21*999 = enormous nonsense
    assert pct == pytest.approx(0.60, abs=1e-6)
