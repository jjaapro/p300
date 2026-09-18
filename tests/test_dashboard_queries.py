"""dashboard/queries.py — fleet-state derivation matrix + data checks.

`_fleet` is pure (beats dict + ScanResult in, rows + alerts out) so the
whole state matrix runs without a database. The data checks run against a
synthetic prod.db via the house monkeypatch point
`strategies.support.db.PROD_DB` (read at call time by design).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
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


def test_held_unit_is_not_dead_or_missing(monkeypatch):
    """A deliberately parked unit reads HELD, in info severity, whether it has
    a stale heartbeat from an earlier run or no heartbeat row at all."""
    import monitor
    monkeypatch.setitem(monitor.HELD_UNITS, "adx", "held for a reason")

    # stale heartbeat, no process: parked, not dead
    beats = {"adx": _beat("adx", tick_age=99999)}
    rows, alerts = queries._fleet(_scanres(), beats, NOW)
    assert _row(rows, "adx")["state"] == "HELD"
    a = next(a for a in alerts if a["code"] == "HELD")
    assert a["severity"] == "info"
    assert "held for a reason" in a["text"]
    assert "DEAD" not in _codes_for(alerts, "adx")

    # never started at all: still parked, not missing
    rows, alerts = queries._fleet(_scanres(), {}, NOW)
    assert _row(rows, "adx")["state"] == "HELD"
    assert "MISSING" not in _codes_for(alerts, "adx")


def test_held_unit_that_is_actually_running_is_checked_normally(monkeypatch):
    """Holding suppresses "it should be running", never "it is misbehaving":
    once a process exists, every normal check applies again."""
    import monitor
    monkeypatch.setitem(monitor.HELD_UNITS, "adx", "held for a reason")
    beats = {"adx": _beat("adx", tick_age=600)}      # ticking too slowly
    rows, alerts = queries._fleet(
        _scanres({"adx": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "adx")["state"] == "DEAD"
    assert "DEAD" in _codes_for(alerts, "adx")


def test_nothing_is_held_and_any_held_unit_would_be_known():
    """The live registry: r4 rejoined the fleet defaults 2026-09-12 (ETH
    windows only), so nothing is held today. Holding a unit again without
    taking it out of the defaults (or vice versa) is caught here together
    with start_fleet.ps1's default list."""
    import re
    import monitor
    import dashboard.procscan as procscan
    assert "r4" not in monitor.HELD_UNITS
    assert "squeeze_bull" not in monitor.HELD_UNITS
    # a held unit must still be a known unit, or nothing would render it
    for unit in monitor.HELD_UNITS:
        assert unit in procscan.UNIT_SCRIPTS
    ps1 = (Path(__file__).resolve().parents[1] / "start_fleet.ps1").read_text(encoding="utf-8")
    m = re.search(r"\[string\[\]\]\$Units = @\((.*?)\)", ps1, re.S)
    defaults = set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))
    assert "r4" in defaults
    assert not (defaults & set(monitor.HELD_UNITS)), "a held unit must not be a default"


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


def test_collector_is_a_fleet_unit():
    assert queries.UNITS[:2] == ("feed", "collector")
    # every scannable unit is rendered, so a second collector can never hide
    assert set(queries.UNITS) == set(procscan.UNIT_SCRIPTS)


def test_collector_tile_states():
    """Heartbeat only, no eval cadence: OK without SILENT, and the collector
    still goes DEGRADED / DEAD / DUPLICATE / MISSING like any unit."""
    beats = {"collector": _beat("collector", eval_age=None, pid=11)}
    rows, alerts = queries._fleet(_scanres({"collector": [_inst(10, 11)]}), beats, NOW)
    r = _row(rows, "collector")
    assert r["state"] == "OK" and r["expectation_s"] is None
    assert _codes_for(alerts, "collector") == []
    beats = {"collector": _beat("collector", eval_age=None, pid=11, status="degraded",
                                note="silent: bybit_liq")}
    rows, alerts = queries._fleet(_scanres({"collector": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "collector")["state"] == "DEGRADED"
    assert "DEGRADED" in _codes_for(alerts, "collector")
    beats = {"collector": _beat("collector", tick_age=600.0, eval_age=None, pid=11)}
    rows, alerts = queries._fleet(_scanres({"collector": [_inst(10, 11)]}), beats, NOW)
    assert _row(rows, "collector")["state"] == "DEAD"
    beats = {"collector": _beat("collector", eval_age=None, pid=11)}
    rows, alerts = queries._fleet(_scanres({"collector": [_inst(10, 11), _inst(20, 21)]}), beats, NOW)
    assert _row(rows, "collector")["state"] == "DUPLICATE"
    assert "DUPLICATE" in _codes_for(alerts, "collector")
    rows, alerts = queries._fleet(_scanres({}), {}, NOW)
    assert _row(rows, "collector")["state"] == "MISSING"


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


# ─── scheduled jobs: monitor / deep scan / backup status files ────────────────

_GB = 1_000_000_000
_Usage = namedtuple("_Usage", "total used free")


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    """Empty tmp DATA_DIR, 50 GB free. Returns the diagnostics dir."""
    data = tmp_path / "data"
    monkeypatch.setattr("strategies.support.db.DATA_DIR", data)
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Usage(500 * _GB, 0, 50 * _GB))
    return data / "diagnostics"


def _put(diag, name, payload):
    diag.mkdir(parents=True, exist_ok=True)
    (diag / name).write_text(payload if isinstance(payload, str)
                             else json.dumps(payload), encoding="utf-8")


def _fresh_all(diag):
    _put(diag, monitor.MONITOR_STATUS, {"result": "green", "finished_utc": _ago(600), "alerts": []})
    _put(diag, monitor.DEEP_STATUS, {"result": "green", "finished_utc": _ago(3600), "alerts": []})
    _put(diag, monitor.BACKUP_STATUS, {"result": "ok", "finished_utc": _ago(3600)})


def _by_code(alerts):
    return {a["code"]: a for a in alerts}


def _job_run():
    return queries._job_alerts(datetime.now(timezone.utc))


def test_job_alerts_nothing_ever_ran(jobs_dir):
    alerts, jobs = _job_run()
    got = _by_code(alerts)
    assert set(got) == {"MONITOR_NEVER_RUN", "DEEP_STALE", "BACKUP_STALE"}
    assert got["MONITOR_NEVER_RUN"]["severity"] == "amber"
    assert got["DEEP_STALE"]["severity"] == "amber"
    assert got["BACKUP_STALE"]["severity"] == "red"          # no backup at all
    assert jobs["monitor"]["finished_utc"] is None and jobs["backup"]["result"] is None


def test_job_alerts_all_fresh_is_silent(jobs_dir):
    _fresh_all(jobs_dir)
    alerts, jobs = _job_run()
    assert alerts == []
    assert jobs["monitor"]["result"] == "green" and jobs["monitor"]["n_alerts"] == 0
    assert 500 < jobs["monitor"]["age_s"] < 700


def test_monitor_stale_uses_the_monitor_threshold(jobs_dir):
    _fresh_all(jobs_dir)
    limit = monitor.MONITOR_STALE_S
    assert limit == 2 * 3600 + 900
    _put(jobs_dir, monitor.MONITOR_STATUS, {"result": "green", "finished_utc": _ago(limit - 60)})
    assert _job_run()[0] == []
    _put(jobs_dir, monitor.MONITOR_STATUS, {"result": "green", "finished_utc": _ago(limit + 60)})
    a = _by_code(_job_run()[0])["MONITOR_STALE"]
    assert a["severity"] == "red" and "monitor-hourly" in a["text"]


def test_monitor_error_result_is_red(jobs_dir):
    _fresh_all(jobs_dir)
    _put(jobs_dir, monitor.MONITOR_STATUS, {"result": "error", "exit_code": 2,
                                            "finished_utc": _ago(60), "error": "Traceback"})
    got = _by_code(_job_run()[0])
    assert set(got) == {"MONITOR_ERROR"}
    assert got["MONITOR_ERROR"]["severity"] == "red"


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", ""])
def test_malformed_status_file_is_monitor_error_not_a_crash(jobs_dir, junk, fixture_db):
    _fresh_all(jobs_dir)
    _put(jobs_dir, monitor.MONITOR_STATUS, junk)
    got = _by_code(_job_run()[0])
    assert "MONITOR_ERROR" in got, sorted(got)
    assert got["MONITOR_ERROR"]["severity"] == "red"
    assert "MONITOR_NEVER_RUN" not in got
    ov = queries.overview(scanres=_scanres())                # and overview() survives it
    assert "MONITOR_ERROR" in {a["code"] for a in ov["alerts"]}


@pytest.mark.parametrize("name, payload, expect", [
    # valid JSON, wrong types: a hand edit or a future schema change
    (monitor.DEEP_STATUS, {"result": "green", "finished_utc": 123, "alerts": []}, "DEEP_STALE"),
    (monitor.DEEP_STATUS, {"result": "green", "finished_utc": ["x"], "alerts": []}, "DEEP_STALE"),
    (monitor.DEEP_STATUS, {"result": "alerts", "finished_utc": _ago(60), "alerts": 5}, None),
    (monitor.BACKUP_STATUS, {"result": "ok", "finished_utc": 123}, "BACKUP_STALE"),
    (monitor.MONITOR_STATUS, {"result": "green", "finished_utc": True}, "MONITOR_STALE"),
])
def test_wrong_typed_status_fields_are_alerts_not_a_crash(jobs_dir, name, payload, expect):
    _fresh_all(jobs_dir)
    _put(jobs_dir, name, payload)
    got = _by_code(_job_run()[0])
    assert set(got) == ({expect} if expect else set())


def test_a_job_check_that_raises_is_shown_and_overview_survives(fixture_db, jobs_dir, monkeypatch):
    """The fleet panel is why the dashboard exists: a failing job check must
    not take /api/overview down with it."""
    _fresh_all(jobs_dir)

    def drive_gone(path):
        raise OSError(2, "drive gone")
    monkeypatch.setattr(monitor.shutil, "disk_usage", drive_gone)
    ov = queries.overview(scanres=_scanres())
    a = next(a for a in ov["alerts"] if a["code"] == "MONITOR_ERROR")
    assert a["severity"] == "red" and "drive gone" in a["text"]
    assert ov["jobs"] == {}
    assert {r["unit"] for r in ov["fleet"]} == set(queries.UNITS)


def test_deep_file_replays_only_interior_gaps_with_as_of(jobs_dir):
    _fresh_all(jobs_dir)
    finished = datetime.now(timezone.utc) - timedelta(hours=3)
    _put(jobs_dir, monitor.DEEP_STATUS, {
        "result": "alerts", "finished_utc": finished.isoformat(),
        "alerts": [{"code": "INTERIOR_GAPS", "text": "INTERIOR GAPS coinbase_spot_1h: 4 gap(s)"},
                   {"code": "STALE_TABLE", "text": "STALE TABLE btc_1m: age 2h"}]})
    alerts, _ = _job_run()
    assert [a["code"] for a in alerts] == ["INTERIOR_GAPS"]
    assert alerts[0]["severity"] == "amber"
    assert alerts[0]["text"] == (f"INTERIOR GAPS coinbase_spot_1h: 4 gap(s) "
                                 f"(as of {finished:%H:%M}Z)")


def test_deep_stale_and_deep_error(jobs_dir):
    _fresh_all(jobs_dir)
    _put(jobs_dir, monitor.DEEP_STATUS, {"result": "green", "alerts": [],
                                         "finished_utc": _ago(monitor.DEEP_STALE_S + 60)})
    got = _by_code(_job_run()[0])
    assert set(got) == {"DEEP_STALE"} and got["DEEP_STALE"]["severity"] == "amber"
    _put(jobs_dir, monitor.DEEP_STATUS, {"result": "error", "finished_utc": _ago(60)})
    got = _by_code(_job_run()[0])
    assert set(got) == {"MONITOR_ERROR"}
    assert got["MONITOR_ERROR"]["severity"] == "red" and "deep scan" in got["MONITOR_ERROR"]["text"]


def test_backup_failed_and_stale(jobs_dir):
    _fresh_all(jobs_dir)
    _put(jobs_dir, monitor.BACKUP_STATUS, {"result": "ok", "finished_utc": _ago(40 * 3600)})
    got = _by_code(_job_run()[0])
    assert set(got) == {"BACKUP_STALE"} and got["BACKUP_STALE"]["severity"] == "amber"
    _put(jobs_dir, monitor.BACKUP_STATUS, {"result": "ok", "finished_utc": _ago(80 * 3600)})
    assert _by_code(_job_run()[0])["BACKUP_STALE"]["severity"] == "red"
    # a failed run today, with yesterday's good copy still on disk
    backups = jobs_dir.parent / "backups"
    backups.mkdir(parents=True)
    (backups / "prod-20260913.db").write_bytes(b"x")
    _put(jobs_dir, monitor.BACKUP_STATUS, {"result": "check_failed", "finished_utc": _ago(60)})
    got = _by_code(_job_run()[0])
    assert set(got) == {"BACKUP_FAILED"} and got["BACKUP_FAILED"]["severity"] == "amber"
    assert "check_failed" in got["BACKUP_FAILED"]["text"]


@pytest.mark.parametrize("free_gb, severity", [(50, None), (3, "amber"), (1, "red")])
def test_disk_low_is_computed_live(jobs_dir, monkeypatch, free_gb, severity):
    _fresh_all(jobs_dir)
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Usage(500 * _GB, 0, free_gb * _GB))
    got = _by_code(_job_run()[0])
    if severity is None:
        assert "DISK_LOW" not in got
    else:
        assert got["DISK_LOW"]["severity"] == severity


# ─── results warnings on the overview ─────────────────────────────────────────

def _add_actual_entry_column(p):
    con = sqlite3.connect(str(p))
    con.execute("ALTER TABLE trades ADD COLUMN actual_entry_time TEXT")
    con.execute("UPDATE trades SET actual_entry_time = entry_time")
    con.commit()
    con.close()


def _insert_closed(p, tid, variant, pnl, strategy="SQUEEZE_BULL"):
    con = sqlite3.connect(str(p))
    con.execute(
        "INSERT INTO trades (id, asset, direction, strategy, strategy_variant,"
        " entry_time, exit_time, actual_entry_time, actual_exit_time,"
        " pnl_usdt, status, execution_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, "BTC", "LONG", strategy, variant, _iso(200000), _iso(100000),
         _iso(200000), _iso(100000), pnl, "closed", "paper"))
    con.commit()
    con.close()


def test_overview_results_badge_and_alert_leave_liveness_alone(fixture_db, jobs_dir):
    _fresh_all(jobs_dir)
    _add_actual_entry_column(fixture_db)
    _insert_closed(fixture_db, "SJ-50", "bot_squeeze_bull_v1", -19.16)
    con = sqlite3.connect(str(fixture_db))
    con.execute("INSERT INTO bot_heartbeats VALUES (?,?,?,?,?,?,?,?,?)",
                ("squeeze_bull", _ago(10), _ago(60), None, 0, 60, "ok", "", 11))
    con.commit()
    con.close()
    units = {u: [_inst(10, 11)] for u in queries.UNITS}
    ov = queries.overview(scanres=_scanres(units))

    sb = _row(ov["fleet"], "squeeze_bull")
    assert sb["state"] == "OK"                        # a badge, not a state
    assert [r["variant"] for r in sb["results"]] == ["bot_squeeze_bull_v1",
                                                     "bot_squeeze_bull_nostop_v1"]
    losing = sb["results"][0]
    assert losing["flags"] == ["LOSING_MONEY"] and losing["n"] == 1
    assert losing["total_usdt"] == -19.16
    assert [c["id"] for c in losing["last_closes"]] == ["SJ-50"]
    assert sb["results"][1]["flags"] == []
    assert _row(ov["fleet"], "adx")["state"] == "MISSING"     # untouched
    assert _row(ov["fleet"], "feed")["results"] == []

    red = [a for a in ov["alerts"] if a["code"] == "LOSING_MONEY"]
    assert len(red) == 1 and red[0]["severity"] == "red"
    assert "squeeze_bull/bot_squeeze_bull_v1" in red[0]["text"]
    info = {a["code"]: a["severity"] for a in ov["alerts"]
            if a["code"] in ("RESEARCH_SAMPLE_SMALL",)}
    assert info == {"RESEARCH_SAMPLE_SMALL": "info"}          # carry, research n=1
    assert set(ov["jobs"]) == {"monitor", "deep", "backup"}
    json.dumps(ov)


def test_results_flags_never_change_a_fleet_state(fixture_db, jobs_dir):
    """Through overview(), the same scan and heartbeats with and without losing
    variants: DUPLICATE, DEAD and OK tiles keep their state, losing or not."""
    _fresh_all(jobs_dir)
    _add_actual_entry_column(fixture_db)
    con = sqlite3.connect(str(fixture_db))
    con.executemany("INSERT INTO bot_heartbeats VALUES (?,?,?,?,?,?,?,?,?)", [
        ("chento_v3", _ago(10), _ago(60), None, 0, 60, "ok", "", 11),
        ("adx", _ago(3600), _ago(3600), None, 0, 60, "ok", "", 31),
        ("squeeze_bull", _ago(10), _ago(60), None, 0, 60, "ok", "", 41)])
    con.commit()
    con.close()
    scan = _scanres({"chento_v3": [_inst(10, 11), _inst(20, 21)],
                     "adx": [_inst(30, 31)], "squeeze_bull": [_inst(40, 41)]})
    watched = ("chento_v3", "adx", "squeeze_bull")

    def states(ov):
        return {r["unit"]: r["state"] for r in ov["fleet"]}

    before = states(queries.overview(scanres=scan))
    assert [before[u] for u in watched] == ["DUPLICATE", "DEAD", "OK"]
    for i, variant in enumerate(("bot_chento_v3_v1", "bot_adx_v1", "bot_squeeze_bull_v1")):
        _insert_closed(fixture_db, f"SJ-7{i}", variant, -5000.0)
    ov = queries.overview(scanres=scan)
    assert states(ov) == before
    for u in watched:
        assert "LOSING_MONEY" in _row(ov["fleet"], u)["results"][0]["flags"], u


@pytest.mark.parametrize("failure, needle", [("raises", "KeyError"),
                                             ("unimportable", "strategies.support.evidence")])
def test_results_failure_is_shown_and_overview_survives(fixture_db, jobs_dir, monkeypatch,
                                                        failure, needle):
    """Results are display only: evidence.py raising, or not importing at all
    (a broken edit), is an amber line and never takes the fleet panel down."""
    if failure == "raises":
        def broken(con, **kw):
            raise KeyError("values")
        monkeypatch.setattr("strategies.support.evidence.evaluate", broken)
    else:
        import strategies.support
        monkeypatch.setitem(sys.modules, "strategies.support.evidence", None)
        monkeypatch.delattr(strategies.support, "evidence", raising=False)
    ov = queries.overview(scanres=_scanres())
    a = next(a for a in ov["alerts"] if a["code"] == "EVIDENCE_UNAVAILABLE")
    assert a["severity"] == "amber" and needle in a["text"]
    assert all(r["results"] == [] for r in ov["fleet"])
    assert {r["unit"] for r in ov["fleet"]} == set(queries.UNITS)


def test_overview_stays_read_only(fixture_db, jobs_dir, monkeypatch):
    _add_actual_entry_column(fixture_db)
    before = fixture_db.read_bytes()
    seen = []
    real = sqlite3.connect

    def spy(database, *a, **k):
        seen.append(str(database))
        return real(database, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)
    queries.overview(scanres=_scanres())
    assert seen and all(d.startswith("file:") and "mode=ro" in d for d in seen), seen
    assert fixture_db.read_bytes() == before
    assert not jobs_dir.parent.exists()           # read the status files, wrote none


def test_r_multiple_derives_from_the_stop_when_the_sleeve_wrote_no_risk(fixture_db):
    """squeeze_bull and short_squeeze write the stop (their no-stop twins only
    the reference stop their size was set from) and never `_risk`, so their R
    multiple was blank on the dashboard. SJ-4250's shape: entry 77493.9, stop
    75944.022, qty 0.0645, pnl -19.16 -> -0.19 R."""
    con = sqlite3.connect(str(fixture_db))
    con.executemany("INSERT INTO variants VALUES (?,?)", [
        ("bot_squeeze_bull_v1", '{"bot": "squeeze_bull"}'),
        ("bot_squeeze_bull_nostop_v1", '{"bot": "squeeze_bull"}')])
    con.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("SJ-5", "BTC", "LONG", "SQUEEZE_BULL", "bot_squeeze_bull_v1",
             _iso(200000), _iso(100000), _iso(100000), 77493.9, 77196.9, 5000.0,
             0.06452120747568531, 0.5, -19.161338139904906, -0.38, "closed", "paper",
             '{"trigger": "squeeze_bull_oi_flush", "_stop_price": 75944.022, '
             '"_target_price": 79818.717, "_time_stop_iso": "2026-09-13T17:00:00+00:00"}'
             "\nSQUEEZE_BULL_EXIT: time_stop"),
            ("SJ-6", "BTC", "LONG", "SQUEEZE_BULL", "bot_squeeze_bull_nostop_v1",
             _iso(200000), _iso(100000), _iso(100000), 77493.9, 79043.778, 5000.0,
             0.0645, 0.5, 100.0, 2.0, "closed", "paper",
             '{"exit_policy": "target_time", "_stop_price": null, '
             '"_reference_stop_price": 75944.022, "_target_price": 79818.717}'),
        ])
    con.commit()
    con.close()
    by_id = {t["id"]: t for t in queries.trades("all")["trades"]}
    assert by_id["SJ-5"]["r_multiple"] == -0.19
    assert by_id["SJ-5"]["plan"]["risk_price"] == pytest.approx(1549.878)
    assert by_id["SJ-6"]["r_multiple"] == 1.0          # 100 / (0.0645 x 1549.878)
    assert by_id["SJ-6"]["plan"]["stop_price"] is None
    assert by_id["SJ-6"]["plan"]["reference_stop_price"] == 75944.022
    assert by_id["SJ-4"]["r_multiple"] == 2.0          # chento's own _risk still wins
    assert by_id["SJ-1"]["r_multiple"] is None         # open trades have none
    assert by_id["SJ-2"]["plan"] is None               # carry: no stop, nothing invented
