"""dashboard/queries.py — fleet-state derivation matrix + data checks.

`_fleet` is pure (beats dict + ScanResult in, rows + alerts out) so the
whole state matrix runs without a database. The data checks run against a
synthetic prod.db via the house monkeypatch point
`strategies.support.db.PROD_DB` (read at call time by design).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import monitor
from dashboard import procscan, queries

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dashboard_fixture import NOW, build_fixture_db  # noqa: E402
from _dashboard_fixture import iso_ago as _iso  # noqa: E402


def _beat(name, tick_age=30.0, eval_age=600.0, status="ok", note="",
          pid=500, interval_s=60, open_trades=0):
    return {"name": name, "last_tick_utc": _iso(tick_age),
            "last_eval_utc": None if eval_age is None else _iso(eval_age),
            "last_signal_utc": None, "open_trades": open_trades,
            "interval_s": interval_s, "status": status, "note": note,
            "pid": pid}


def _inst(*pids, age_s=3600.0, cmdline="python runner.py"):
    return procscan.Instance(pids=tuple(pids), rep_pid=pids[-1],
                             create_time=0.0, age_s=age_s, cmdline=cmdline,
                             username="u")


def _scanres(instances=None, **kw):
    return procscan.ScanResult(instances=instances or {}, **kw)


def _row(rows, unit):
    return next(r for r in rows if r["unit"] == unit)


def _codes_for(alerts, unit):
    return [a["code"] for a in alerts if unit in a["text"]]


# ─── state matrix ─────────────────────────────────────────────────────────────

def test_ok_state():
    beats = {"adx": _beat("adx", pid=11)}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    r = _row(rows, "adx")
    assert r["state"] == "OK"
    assert r["hb_pid_seen"] is True
    assert _codes_for(alerts, "adx") == []


def test_duplicate_by_instance_count_wins_over_everything():
    beats = {"adx": _beat("adx", status="error", note="whatever")}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11), _inst(20, 21)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "DUPLICATE"
    assert "DUPLICATE" in _codes_for(alerts, "adx")


def test_duplicate_alert_mentions_force_start_and_both_pids():
    insts = [_inst(10, 11, cmdline="python feed.py --force-start"),
             _inst(20, 21, cmdline="python feed.py --force-start")]
    _, alerts = queries._fleet(
        _scanres({"feed": insts}), {"feed": _beat("feed", pid=11)}, NOW)
    dup = next(a for a in alerts if a["code"] == "DUPLICATE")
    assert "10->11" in dup["text"] and "20->21" in dup["text"]
    assert "--force-start" in dup["text"]


def test_duplicate_by_heartbeat_note():
    beats = {"adx": _beat("adx", status="error",
                          note="DUPLICATE INSTANCE: pid 7 also writing "
                               "'adx' (I am 11). Kill one.")}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "DUPLICATE"
    assert "DUPLICATE" in _codes_for(alerts, "adx")


def test_missing_heartbeat_row():
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), {}, NOW)
    r = _row(rows, "adx")
    assert r["state"] == "MISSING"
    txt = next(a["text"] for a in alerts
               if a["code"] == "MISSING" and "adx" in a["text"])
    assert "visible" in txt


def test_dead_process_even_with_process_present():
    beats = {"adx": _beat("adx", tick_age=600)}     # 10m > 3x60s
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "DEAD"
    assert "DEAD" in _codes_for(alerts, "adx")


def test_writer_unseen_when_fresh_but_no_process():
    beats = {"adx": _beat("adx")}
    rows, alerts = queries._fleet(_scanres(), beats, NOW)
    assert _row(rows, "adx")["state"] == "WRITER_UNSEEN"
    a = next(a for a in alerts if a["code"] == "WRITER_UNSEEN")
    assert a["severity"] == "amber"


def test_degraded():
    beats = {"adx": _beat("adx", status="degraded",
                          note="mgmt tables stale: ['btc_1m']")}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "DEGRADED"


def test_silent_bot_uses_monitor_expectation():
    limit = monitor.BOT_EXPECTATIONS["chento_v3"]
    beats = {"chento_v3": _beat("chento_v3", eval_age=limit + 60)}
    rows, alerts = queries._fleet(
        _scanres({"chento_v3": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "chento_v3")["state"] == "SILENT"


def test_feed_has_no_silence_expectation():
    beats = {"feed": _beat("feed", eval_age=None, pid=11)}
    rows, _ = queries._fleet(_scanres({"feed": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "feed")["state"] == "OK"


def test_pid_mismatch_corroboration():
    beats = {"adx": _beat("adx", pid=999)}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "OK"       # state unchanged
    assert "PID_MISMATCH" in _codes_for(alerts, "adx")


def test_null_pid_is_muted_not_alarming():
    beats = {"adx": _beat("adx", pid=None)}
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    r = _row(rows, "adx")
    assert r["state"] == "OK"
    assert r["heartbeat"]["pid_known"] is False
    assert "PID_MISMATCH" not in _codes_for(alerts, "adx")


def test_scan_level_alerts():
    _, alerts = queries._fleet(
        _scanres(legacy_bot_py=[77], access_denied=2),
        {u: _beat(u, pid=11) for u in queries.UNITS}, NOW)
    codes = [a["code"] for a in alerts]
    assert "LEGACY_BOT_PY" in codes
    assert "SCAN_BLIND" in codes


# ─── data checks against a synthetic prod.db ──────────────────────────────────

@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    p = build_fixture_db(tmp_path / "prod.db")
    monkeypatch.setattr("strategies.support.db.PROD_DB", p)
    return p


def test_data_alerts(fixture_db):
    con = queries._ro_con()
    try:
        alerts = queries._data_alerts(con, NOW)
    finally:
        con.close()
    texts = [a["text"] for a in alerts]
    # fresh table clean, stale table alerted
    assert not any("STALE TABLE cd_futures_15m:" in t for t in texts)
    assert any("STALE TABLE btc_1m:" in t for t in texts)
    # overdue: no variants.enabled filter, sentinel + grace + closed excluded
    assert any("OVERDUE TRADE SJ-1" in t for t in texts)
    for tid in ("SJ-2", "SJ-3", "SJ-4"):
        assert not any(tid in t for t in texts), tid
    # runway 200d out -> no alert
    assert not any("EVENT RUNWAY" in t for t in texts)


def test_ro_connection_cannot_write(fixture_db):
    con = queries._ro_con()
    try:
        with pytest.raises(sqlite3.OperationalError,
                           match="readonly|attempt to write"):
            con.execute("DELETE FROM trades")
    finally:
        con.close()


def test_overview_end_to_end_on_fixture(fixture_db):
    scanres = _scanres({u: [_inst(10, 11)] for u in queries.UNITS})
    ov = queries.overview(scanres=scanres)
    # no heartbeat rows in fixture -> every unit MISSING
    assert all(r["state"] == "MISSING" for r in ov["fleet"])
    assert ov["all_green"] is False
    assert {r["unit"] for r in ov["fleet"]} == set(queries.UNITS)


def test_feeds_grid_on_fixture(fixture_db):
    fd = queries.feeds()
    by_name = {t["table"]: t for t in fd["tables"]}
    assert by_name["cd_futures_15m"]["state"] == "fresh"
    assert by_name["btc_1m"]["state"] == "stale"
    assert by_name["okx_perp_1h"]["state"] == "missing"
    assert set(by_name) == set(__import__("botlib").FRESHNESS_CONTRACTS)


# ─── trades + candles ─────────────────────────────────────────────────────────

def test_trades_parsing_and_derivations(fixture_db):
    td = queries.trades("all")
    by_id = {t["id"]: t for t in td["trades"]}
    assert set(by_id) == {"SJ-1", "SJ-2", "SJ-3", "SJ-4"}
    assert by_id["SJ-1"]["bot"] == "adx"          # from variants.spec_json
    assert by_id["SJ-2"]["timed_stop"] is None    # 2099 sentinel
    # chento: timed stop prefers the plan's _time_stop_iso
    assert by_id["SJ-3"]["timed_stop"] == by_id["SJ-3"]["plan"]["time_stop"]
    # closed R multiple = pnl / (qty × risk) = 2000 / (0.5 × 2000)
    assert by_id["SJ-4"]["r_multiple"] == 2.0
    assert by_id["SJ-4"]["exit_lines"] == [
        "CHENTO_TRIPLE_V3_EXIT: stop_hit; fees=18bp RT"]
    assert by_id["SJ-2"]["unrealized_usdt"] is None   # CARRY: delta-neutral
    assert by_id["SJ-1"]["unrealized_usdt"] is not None
    assert {t["id"] for t in queries.trades("open")["trades"]} == \
        {"SJ-1", "SJ-2", "SJ-3"}
    assert "p300_aggressive_v2_v1_0" not in {
        t["variant"] for t in td["trades"]}


def test_trades_bad_scope(fixture_db):
    with pytest.raises(ValueError):
        queries.trades("bogus")


def test_candles_native_and_bucketed(fixture_db):
    import time as _t
    native = queries.candles("BTC", "1h", bars=10)
    assert native["source"] == "cd_futures_ohlcv"
    # window auto-extends past the oldest open entry, so all 30 rows return
    assert len(native["bars"]) == 30
    times = [b["time"] for b in native["bars"]]
    assert times == sorted(times)
    assert native["last_time"] == times[-1]

    b4 = queries.candles("BTC", "4h", bars=5)
    assert b4["bars"], "bucketed result empty"
    for b in b4["bars"]:
        assert b["time"] % 14400 == 0
        assert b["high"] >= max(b["open"], b["close"])
        assert b["low"] <= min(b["open"], b["close"])
    # in-progress 4h bucket dropped
    assert b4["bars"][-1]["time"] + 14400 <= _t.time()


def test_candles_bad_params(fixture_db):
    with pytest.raises(ValueError):
        queries.candles("BTC", "5m")
    with pytest.raises(ValueError):
        queries.candles("DOGE", "1h")
