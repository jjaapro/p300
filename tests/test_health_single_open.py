"""health.py's single-open invariant (BACKLOG 22, 2026-09-19): it covers the bot variants the fleet trades, holds
the two stacking bots to their own rule, and still checks the legacy p300_% rows."""
from __future__ import annotations

import sqlite3

import pytest

import health
from strategies.support import db as _db_mod


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "prod.db"
    monkeypatch.setattr(_db_mod, "DASH_DB", path)
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE trades (id TEXT PRIMARY KEY, strategy_variant TEXT, strategy TEXT, asset TEXT, "
                "status TEXT, entry_time TEXT, notes TEXT)")
    con.commit()
    con.close()
    return path


def _open(path, tid, variant, strategy, asset="BTC", entry_time="2026-09-19T00:00:00+00:00", bar_ts=None):
    notes = f'{{"trigger": "x", "bar_ts": "{bar_ts}", "_stop_price": 1.0}}' if bar_ts else "{}"
    con = sqlite3.connect(str(path))
    con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", (tid, variant, strategy, asset, "open", entry_time, notes))
    con.commit()
    con.close()


def test_two_open_adx_trades_on_one_bot_variant_fail(ledger):
    _open(ledger, "A1", "bot_adx_v1", "ADX")
    _open(ledger, "A2", "bot_adx_v1", "ADX")
    with pytest.raises(health.HealthError) as e:
        health.check_single_open_invariant()
    assert e.value.code == 6 and "2 open ADX trades for bot_adx_v1" in str(e.value)


def test_legacy_variants_are_still_checked(ledger):
    _open(ledger, "L1", "p300_aggressive_v2_v1_0", "CPR")
    _open(ledger, "L2", "p300_aggressive_v2_v1_0", "CPR")
    with pytest.raises(health.HealthError):
        health.check_single_open_invariant()


def test_one_open_trade_per_variant_passes(ledger, capsys):
    _open(ledger, "A1", "bot_adx_v1", "ADX")
    _open(ledger, "C1", "bot_carry_v1", "CARRY")
    _open(ledger, "S1", "bot_squeeze_bull_v1", "SQUEEZE_BULL")
    _open(ledger, "S2", "bot_squeeze_bull_nostop_v1", "SQUEEZE_BULL")     # the twin is another variant
    health.check_single_open_invariant()
    assert "[PASS] single-open" in capsys.readouterr().out


def test_chento_stacks_by_design_but_not_on_the_same_bar(ledger, capsys):
    for k, bar in enumerate(("2026-09-10T08:45:00+00:00", "2026-09-10T15:00:00+00:00", "2026-09-11T02:15:00+00:00")):
        _open(ledger, f"T{k}", "bot_chento_v3_v1", "CHENTO_TRIPLE_V3", bar_ts=bar)
    health.check_single_open_invariant()
    out = capsys.readouterr().out
    assert "3 open CHENTO_TRIPLE_V3 trades stack by design" in out and "[PASS] single-open" in out
    _open(ledger, "T9", "bot_chento_v3_v1", "CHENTO_TRIPLE_V3", bar_ts="2026-09-10T15:00:00+00:00")   # the double start
    with pytest.raises(health.HealthError) as e:
        health.check_single_open_invariant()
    assert "same bar 2026-09-10T15:00:00+00:00" in str(e.value)


def test_chento_closed_rows_with_exit_lines_do_not_confuse_the_bar_key(ledger):
    """A closed chento row carries a plain-text exit line after its JSON; only open rows are read, and the bar
    key is sliced from the string, so an open row's notes need no JSON parse."""
    _open(ledger, "T1", "bot_chento_v3_v1", "CHENTO_TRIPLE_V3", bar_ts="2026-09-10T08:45:00+00:00")
    con = sqlite3.connect(str(ledger))
    con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)",
                ("T0", "bot_chento_v3_v1", "CHENTO_TRIPLE_V3", "BTC", "closed", "2026-09-01T00:00:00+00:00",
                 '{"bar_ts": "2026-09-10T08:45:00+00:00"}\nCHENTO_TRIPLE_V3_EXIT: stop_hit'))
    con.commit()
    con.close()
    health.check_single_open_invariant()


def test_r4_windows_overlap_by_design_but_one_window_opened_twice_fails(ledger, capsys):
    _open(ledger, "R1", "bot_r4_v1", "R4_ETH", asset="ETH", entry_time="2026-10-02T04:00:00+00:00")
    _open(ledger, "R2", "bot_r4_v1", "R4_ETH", asset="ETH", entry_time="2026-10-06T20:00:00+00:00")
    _open(ledger, "R3", "bot_r4_v1", "R4_ETH_V2", asset="ETH", entry_time="2026-10-06T20:00:00+00:00")
    health.check_single_open_invariant()
    assert "2 open R4_ETH trades stack by design" in capsys.readouterr().out
    _open(ledger, "R4", "bot_r4_v1", "R4_ETH", asset="ETH", entry_time="2026-10-06T20:00:00+00:00")
    with pytest.raises(health.HealthError) as e:
        health.check_single_open_invariant()
    assert "same window 2026-10-06T20:00:00+00:00" in str(e.value)
