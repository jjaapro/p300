"""jplus.r4 — R4 BTC (06→18 UTC) and R4 ETH (Tue20→Wed20 UTC) returns.

Fixtures are hand-built (date, hour) → (open, close) dicts covering
  - Mon/Wed wk1-2 firing conditions
  - Skip conditions (wrong day, wrong week, missing exit bar)
  - Sign of return (rise vs fall)
  - Fee deduction
"""
from __future__ import annotations

import pytest

from jplus import r4


# ─── R4 BTC ─────────────────────────────────────────────────────────────────

def test_r4_btc_fires_on_monday_week_one():
    # 2024-01-01 is Mon (day=1, weekday=0). Entry 06, exit 18.
    by_hour = {
        ("2024-01-01", 6): (50_000.0, 50_000.0),
        ("2024-01-01", 18): (51_000.0, 51_000.0),
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-01" in out
    # 2% gross. Fees moved out of r4.py into the trade-emitter as of
    # the Core J+ migration Step 5; r4.py now emits gross window returns.
    assert out["2024-01-01"] == pytest.approx(0.02)


def test_r4_btc_fires_on_wednesday_week_two():
    # 2024-01-10 is Wed, day=10 (week 2). Should fire.
    by_hour = {
        ("2024-01-10", 6): (50_000.0, 50_000.0),
        ("2024-01-10", 18): (49_000.0, 49_000.0),  # fell
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-10" in out
    # Gross only — fees applied at trade-event close, not in r4.py.
    assert out["2024-01-10"] == pytest.approx(-0.02)


def test_r4_btc_skips_day_over_14():
    # 2024-01-15 is Mon, day=15 (week 3) — should NOT fire.
    by_hour = {
        ("2024-01-15", 6): (50_000.0, 50_000.0),
        ("2024-01-15", 18): (51_000.0, 51_000.0),
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-15" not in out


def test_r4_btc_skips_tuesday():
    # 2024-01-02 is Tue, weekday=1 — NOT Mon or Wed.
    by_hour = {
        ("2024-01-02", 6): (50_000.0, 50_000.0),
        ("2024-01-02", 18): (51_000.0, 51_000.0),
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-02" not in out


def test_r4_btc_skips_if_exit_bar_missing():
    # Mon wk1, 06 present but no 18 bar → skip.
    by_hour = {
        ("2024-01-01", 6): (50_000.0, 50_000.0),
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-01" not in out


def test_r4_btc_skips_if_entry_zero_or_negative():
    by_hour = {
        ("2024-01-01", 6): (0.0, 0.0),
        ("2024-01-01", 18): (51_000.0, 51_000.0),
    }
    out = r4.r4_btc_returns(by_hour)
    assert "2024-01-01" not in out


def test_r4_btc_uses_exit_bar_open_not_close():
    """Verifies the exit price is the 18:00 bar's OPEN, matching upstream
    convention (price at exact 18:00 UTC, not 19:00)."""
    by_hour = {
        ("2024-01-01", 6): (50_000.0, 50_500.0),
        ("2024-01-01", 18): (52_000.0, 60_000.0),  # huge spread within hour
    }
    out = r4.r4_btc_returns(by_hour)
    # Uses exit_open (52_000), not exit_close (60_000). Gross only.
    assert out["2024-01-01"] == pytest.approx((52_000 - 50_000) / 50_000)


# ─── R4 ETH ─────────────────────────────────────────────────────────────────

def test_r4_eth_fires_tue_to_wed_week_two():
    # Tue 2024-01-09 20:00 → Wed 2024-01-10 20:00. Wed.day=10 → wk2, fires.
    eth_by_hour = {
        ("2024-01-09", 20): (3_000.0, 3_000.0),
        ("2024-01-10", 20): (3_100.0, 3_100.0),
    }
    out = r4.r4_eth_returns(eth_by_hour)
    # Keyed by Wed date
    assert "2024-01-10" in out
    assert out["2024-01-10"] == pytest.approx((3_100 - 3_000) / 3_000)


def test_r4_eth_keyed_by_wed_not_tue():
    eth_by_hour = {
        ("2024-01-02", 20): (3_000.0, 3_000.0),  # Tue
        ("2024-01-03", 20): (3_050.0, 3_050.0),  # Wed, day=3 → wk1
    }
    out = r4.r4_eth_returns(eth_by_hour)
    assert "2024-01-02" not in out
    assert "2024-01-03" in out


def test_r4_eth_skips_if_wed_in_week_three():
    # Tue Jan 16 2024 → Wed Jan 17 (day=17, week 3) — skip
    eth_by_hour = {
        ("2024-01-16", 20): (3_000.0, 3_000.0),
        ("2024-01-17", 20): (3_100.0, 3_100.0),
    }
    out = r4.r4_eth_returns(eth_by_hour)
    assert "2024-01-17" not in out


def test_r4_eth_skips_if_tue_hour_20_missing():
    eth_by_hour = {
        ("2024-01-10", 20): (3_100.0, 3_100.0),  # only Wed, no Tue
    }
    out = r4.r4_eth_returns(eth_by_hour)
    assert out == {}


def test_r4_eth_ignores_non_tue_20_bars():
    # A Mon 20:00 bar isn't an entry candidate
    eth_by_hour = {
        ("2024-01-08", 20): (3_000.0, 3_000.0),  # Mon
        ("2024-01-09", 20): (3_100.0, 3_100.0),  # Tue (not trade day)
    }
    out = r4.r4_eth_returns(eth_by_hour)
    assert out == {}
