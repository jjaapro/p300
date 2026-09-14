"""monitor.py — status files, exit codes, read-only prod.db, job checks.

Until 2026-09-14 monitor.py only printed: a crash exited 1 exactly like a
run reporting alerts, nothing recorded that it had run at all, and every
check opened prod.db read-write (a wrong path created an empty database).
These tests pin the replacements: monitor_last.json on every run (plus
monitor_last_deep.json on --deep), exit 2 + result "error" on a crash,
PROD_DB_UNREADABLE without creating the file, the DISK_LOW / BACKUP_STALE /
DEEP_SCAN_STALE checks, and a warnings tier that never touches the exit code.

Everything runs against tmp paths: strategies.support.db.PROD_DB and DATA_DIR
are monkeypatched (house convention), Telegram and the .env loader stubbed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import monitor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _dashboard_fixture import build_fixture_db  # noqa: E402

GB = 1_000_000_000
_Usage = namedtuple("_Usage", "total used free")
_REAL_DB_CHECKS = monitor._db_checks      # the env fixture stubs it

KNOWN_CODES = {
    "STALE_TABLE", "UNCLASSIFIED_TABLE", "GHOST_REGISTRY", "HISTORY_BURN",
    "EVENT_RUNWAY", "ARCHIVE_STALE", "INTERIOR_GAPS", "NO_HEARTBEATS",
    "MISSING_BOT", "DEAD_PROCESS", "DEGRADED", "DUPLICATE", "SILENT_BOT",
    "OVERDUE_TRADE", "PROD_DB_UNREADABLE", "DISK_LOW", "BACKUP_STALE",
    "DEEP_SCAN_STALE",
}


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _ledger(p: Path, closed_pnls=()) -> Path:
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE trades (id TEXT PRIMARY KEY, strategy_variant TEXT,"
        " strategy TEXT, direction TEXT, entry_time TEXT, exit_time TEXT,"
        " actual_entry_time TEXT, actual_exit_time TEXT, pnl_usdt REAL,"
        " status TEXT, execution_mode TEXT)")
    for i, (variant, pnl) in enumerate(closed_pnls):
        t = f"2026-09-1{i}T10:00:00+00:00"
        con.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"SJ-{i}", variant, "S", "LONG", t, t, t, t, pnl,
                     "closed", "paper"))
    con.commit()
    con.close()
    return p


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                    encoding="utf-8")


def _status(data_dir: Path, name: str = monitor.MONITOR_STATUS) -> dict:
    return json.loads((data_dir / "diagnostics" / name).read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tmp DATA_DIR where every file-based check is green: fresh archive
    files, a fresh ok backup, a fresh deep scan, 50 GB free. prod.db is a
    minimal ledger; the prod.db checks are stubbed unless a test restores
    them (they are exercised on the dashboard fixture below)."""
    data = tmp_path / "data"
    for name in monitor.ARCHIVE_FILES:
        _write(data / "archive" / name, "{}")
    _write(data / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "ok", "finished_utc": _iso_ago(3600)})
    _write(data / "diagnostics" / monitor.DEEP_STATUS,
           {"result": "green", "finished_utc": _iso_ago(3600), "alerts": []})
    prod = _ledger(tmp_path / "prod.db")
    monkeypatch.setattr("strategies.support.db.DATA_DIR", data)
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod)
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Usage(500 * GB, 450 * GB, 50 * GB))
    monkeypatch.setattr(monitor, "_db_checks", lambda *a, **k: None)
    pushed: list[str] = []
    monkeypatch.setattr(monitor, "_notify", lambda text: pushed.append(text) or False)
    monkeypatch.setattr("strategies.support.env.load_env_file", lambda *a, **k: None)
    return {"data": data, "prod": prod, "pushed": pushed, "tmp": tmp_path}


# ─── recording every run ──────────────────────────────────────────────────────

def test_green_run_records_itself(env, capsys):
    deep_before = (env["data"] / "diagnostics" / monitor.DEEP_STATUS).read_bytes()
    code = monitor.main(["--quiet"])
    assert code == monitor.EXIT_GREEN == 0
    s = _status(env["data"])
    assert s["schema"] == 1 and s["job"] == "monitor" and s["mode"] == "hourly"
    assert s["result"] == "green" and s["exit_code"] == 0
    assert s["alerts"] == [] and s["error"] is None
    assert s["argv"] == ["--quiet"] and s["pid"] == os.getpid()
    started = datetime.fromisoformat(s["started_utc"])
    finished = datetime.fromisoformat(s["finished_utc"])
    assert started <= finished and s["duration_s"] >= 0
    assert abs((datetime.now(timezone.utc) - finished).total_seconds()) < 60
    for w in s["warnings"]:
        assert set(w) == {"code", "text", "severity"}
    # an hourly run never touches the deep scan's record: DEEP_SCAN_STALE, the
    # dashboard's DEEP_STALE and its INTERIOR_GAPS replay all read that file
    assert (env["data"] / "diagnostics" / monitor.DEEP_STATUS).read_bytes() == deep_before
    assert "all green" in (env["data"] / "diagnostics" / monitor.MONITOR_LOG
                           ).read_text(encoding="utf-8")
    assert list((env["data"] / "diagnostics").glob("*.tmp")) == []
    assert env["pushed"] == []


def test_deep_run_writes_both_files(env):
    (env["data"] / "diagnostics" / monitor.DEEP_STATUS).unlink()
    assert monitor.main(["--deep", "--quiet"]) == 0
    assert _status(env["data"])["mode"] == "deep"
    deep = _status(env["data"], monitor.DEEP_STATUS)
    assert deep["mode"] == "deep" and deep["result"] == "green"


def test_crash_exits_2_and_records_the_traceback(env, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("boom in a check")
    monkeypatch.setattr(monitor, "_file_checks", boom)
    assert monitor.main(["--deep"]) == monitor.EXIT_CRASH == 2
    for name in (monitor.MONITOR_STATUS, monitor.DEEP_STATUS):
        s = _status(env["data"], name)
        assert s["result"] == "error" and s["exit_code"] == 2
        assert "RuntimeError: boom in a check" in s["error"]
        assert "Traceback" in s["error"]
    assert "monitor CRASHED" in (env["data"] / "diagnostics" / monitor.MONITOR_LOG
                                 ).read_text(encoding="utf-8")


def test_an_import_failure_in_a_script_run_exits_2_and_is_recorded(tmp_path):
    """Run the way the task runs it, with botlib and strategies unimportable (a
    copy of monitor.py outside the repo, so it records under tmp/data). Python's
    own exit would be 1 ("alerts") with nothing recorded."""
    script = tmp_path / "monitor.py"
    script.write_bytes(Path(monitor.__file__).read_bytes())
    out = subprocess.run([sys.executable, str(script), "--deep", "--quiet"],
                         cwd=tmp_path, capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert out.returncode == 2, out.stderr
    for name in (monitor.MONITOR_STATUS, monitor.DEEP_STATUS):
        s = _status(tmp_path / "data", name)
        assert s["result"] == "error" and s["exit_code"] == 2 and s["mode"] == "deep"
        assert "ModuleNotFoundError" in s["error"] and "botlib" in s["error"]
    assert "ModuleNotFoundError" in out.stderr
    # pythonw throws stderr away, and the dashboard and OPERATIONS §11 send the
    # operator to monitor.log for a crash
    log_path = tmp_path / "data" / "diagnostics" / monitor.MONITOR_LOG
    assert log_path.exists(), "the crash at import left nothing in monitor.log"
    logged = log_path.read_text(encoding="utf-8")
    assert "monitor CRASHED" in logged and "No module named 'botlib'" in logged


def test_monitor_and_dashboard_import_without_the_results_module():
    """evidence.py is display only. Imported at the top of monitor.py and
    dashboard/queries.py, a broken edit to it made every hourly run crash
    (exit 2, no operational alerts) and kept the dashboard from starting."""
    code = ("import sys\n"
            "sys.modules['strategies.support.evidence'] = None\n"
            "import monitor\n"
            "import dashboard.queries\n"
            "print('imported')\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=Path(monitor.__file__).parent,
                         capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert out.returncode == 0 and "imported" in out.stdout, out.stderr


def test_an_unwritable_status_dir_does_not_change_the_exit_code(env, capsys):
    diag = env["data"] / "diagnostics"
    for f in diag.iterdir():
        f.unlink()
    diag.rmdir()
    diag.write_text("a file where the directory should be", encoding="utf-8")
    # keep the run green without the status files: a fresh copy on disk, and
    # a --deep run (which does not check the deep scan's own record)
    (env["data"] / "backups").mkdir()
    (env["data"] / "backups" / "prod-20260914.db").write_bytes(b"x")
    assert monitor.main(["--deep", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "not written" in out and "monitor.log unavailable" in out


def test_status_write_retries_while_a_reader_holds_the_file(env, monkeypatch):
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(13, "being read")
        return real_replace(src, dst)
    monkeypatch.setattr(monitor.os, "replace", flaky)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    assert monitor.write_status("x.json", {"a": 1}) is True
    assert len(calls) == 3
    assert _status(env["data"], "x.json") == {"a": 1}

    def always_busy(src, dst):
        raise PermissionError(13, "being read")
    monkeypatch.setattr(monitor.os, "replace", always_busy)
    assert monitor.write_status("y.json", {"a": 1}) is False
    assert not (env["data"] / "diagnostics" / "y.json").exists()
    assert list((env["data"] / "diagnostics").glob("*.tmp")) == []


def test_status_read_retries_while_a_writer_replaces_the_file(env, monkeypatch):
    """The dashboard reads every 5 s and an hourly run reads the deep record:
    one PermissionError mid-replace must not read as MONITOR_ERROR or a false
    DEEP_SCAN_STALE."""
    path = env["data"] / "diagnostics" / "x.json"
    _write(path, {"a": 1})
    real_read = Path.read_text
    calls = []

    def flaky(self, *a, **k):
        if self == path:
            calls.append(self)
            if len(calls) < 3:
                raise PermissionError(13, "being replaced")
        return real_read(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", flaky)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    assert monitor.read_status("x.json") == ({"a": 1}, None)
    assert len(calls) == 3

    def always_busy(self, *a, **k):
        if self == path:
            raise PermissionError(13, "being replaced")
        return real_read(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", always_busy)
    payload, err = monitor.read_status("x.json")
    assert payload is None and "x.json unreadable: PermissionError" in err


def test_monitor_log_rotates_at_1_mb_keeping_5(env):
    handler = monitor._open_log()
    try:
        for _ in range(700):                   # ~7 MB: more rollovers than it keeps
            monitor.log.info("x" * 10_000)
    finally:
        monitor.log.removeHandler(handler)
        handler.close()
    logs = list((env["data"] / "diagnostics").glob(f"{monitor.MONITOR_LOG}*"))
    assert sorted(p.name for p in logs) == [monitor.MONITOR_LOG] + [
        f"{monitor.MONITOR_LOG}.{i}" for i in range(1, 6)]
    assert all(p.stat().st_size <= 1_000_000 for p in logs)


# ─── warnings tier ────────────────────────────────────────────────────────────

def test_results_warnings_never_change_the_exit_code(env, capsys):
    _ledger_path = env["prod"]
    con = sqlite3.connect(str(_ledger_path))
    con.execute("INSERT INTO trades VALUES ('SJ-4250','bot_squeeze_bull_v1','SQUEEZE_BULL',"
                "'LONG','2026-09-11T18:00:31+00:00','2026-09-13T17:00:00+00:00',"
                "'2026-09-11T18:00:31+00:00','2026-09-13T17:00:20+00:00',-19.16,"
                "'closed','paper')")
    con.commit()
    con.close()
    assert monitor.main(["--quiet"]) == 0
    out = capsys.readouterr().out
    assert "~~ [red] LOSING MONEY squeeze_bull/bot_squeeze_bull_v1" in out  # printed under --quiet
    s = _status(env["data"])
    assert s["result"] == "green" and s["alerts"] == []
    red = [w for w in s["warnings"] if w["severity"] == "red"]
    assert [w["code"] for w in red] == ["LOSING_MONEY"]
    assert env["pushed"] == []                 # a warning alone is never pushed


def _evidence_unimportable(monkeypatch) -> None:
    """strategies.support.evidence fails to import from here on (a broken edit)."""
    import strategies.support
    monkeypatch.setitem(sys.modules, "strategies.support.evidence", None)
    monkeypatch.delattr(strategies.support, "evidence", raising=False)


@pytest.mark.parametrize("failure, needle", [("raises", "KeyError"),
                                             ("unimportable", "strategies.support.evidence")])
def test_a_broken_results_check_is_a_warning_not_a_crash(env, monkeypatch, capsys,
                                                         failure, needle):
    """A bug in evidence.py, at run time or at import, must not cost the hourly
    run its operational alerts."""
    if failure == "raises":
        def boom(con, *a, **k):
            raise KeyError("values")
        monkeypatch.setattr("strategies.support.evidence.evaluate", boom)
    else:
        _evidence_unimportable(monkeypatch)
    _write(env["data"] / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "ok", "finished_utc": _iso_ago(80 * 3600)})
    assert monitor.main(["--quiet"]) == 1
    s = _status(env["data"])
    assert s["result"] == "alerts" and s["error"] is None
    assert [a["code"] for a in s["alerts"]] == ["BACKUP_STALE"]
    assert [(w["code"], w["severity"]) for w in s["warnings"]] == [
        ("EVIDENCE_UNAVAILABLE", "amber")]
    assert needle in s["warnings"][0]["text"]
    if failure == "raises":       # monitor's fallback spells out evidence.unavailable()
        from strategies.support import evidence
        assert s["warnings"][0]["text"] == evidence.unavailable("evaluate", KeyError("values"))["text"]


# ─── read-only prod.db ────────────────────────────────────────────────────────

def test_missing_prod_db_alerts_and_is_not_created(env, monkeypatch):
    # the directory exists, so a read-write connect WOULD create the file
    missing = env["tmp"] / "gone.db"
    monkeypatch.setattr("strategies.support.db.PROD_DB", missing)
    assert monitor.main(["--quiet"]) == 1
    assert not missing.exists()
    s = _status(env["data"])
    assert [a["code"] for a in s["alerts"]] == ["PROD_DB_UNREADABLE"]
    assert s["warnings"] == []


def test_a_file_that_is_not_a_database_is_unreadable(env, monkeypatch):
    junk = env["tmp"] / "junk.db"
    junk.write_bytes(b"not sqlite" * 100)
    monkeypatch.setattr("strategies.support.db.PROD_DB", junk)
    assert monitor.main(["--quiet"]) == 1
    assert _status(env["data"])["alerts"][0]["code"] == "PROD_DB_UNREADABLE"


def test_wal_database_opens_read_only_after_its_wal_files_are_gone(env):
    """The fleet fully stopped: prod.db in WAL mode with no -wal/-shm. That is
    when the monitor matters most, so a read-only open must still work."""
    p = env["prod"]
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    assert not Path(f"{p}-wal").exists() and not Path(f"{p}-shm").exists()
    ro = monitor._ro_con()
    try:
        assert ro.execute("SELECT COUNT(*) FROM t").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
    finally:
        ro.close()


def test_alerts_carry_explicit_codes_and_every_connect_is_read_only(env, monkeypatch, capsys):
    """The real prod.db checks, --deep included, on the dashboard's synthetic
    prod.db: explicit codes, and not one connection that could write."""
    monkeypatch.setattr(monitor, "_db_checks", _REAL_DB_CHECKS)
    prod = build_fixture_db(env["tmp"] / "fixture.db")
    monkeypatch.setattr("strategies.support.db.PROD_DB", prod)
    before = prod.read_bytes()

    seen = []
    real_connect = sqlite3.connect

    def spy(database, *a, **k):
        seen.append((str(database), k.get("uri", False)))
        return real_connect(database, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)

    assert monitor.main(["--deep"]) == 1
    s = _status(env["data"])
    codes = [a["code"] for a in s["alerts"]]
    assert set(codes) <= KNOWN_CODES, set(codes) - KNOWN_CODES
    assert {"STALE_TABLE", "GHOST_REGISTRY", "INTERIOR_GAPS", "NO_HEARTBEATS",
            "MISSING_BOT"} <= set(codes)
    assert all(a["text"] for a in s["alerts"])
    assert s["result"] == "alerts" and s["exit_code"] == 1
    assert "!! STALE TABLE  btc_1m" in capsys.readouterr().out
    prod_hits = [d for d, _ in seen if str(prod) in d]
    assert prod_hits, "the checks never opened prod.db"
    assert all(d.startswith("file:") and "mode=ro" in d for d in prod_hits), prod_hits
    assert prod.read_bytes() == before
    assert env["pushed"] and "ALERT(S)" in env["pushed"][0]


def test_a_gap_scan_that_errors_is_an_alert_not_a_silent_skip(env, monkeypatch, capsys):
    """Only a table that does not exist is skipped (it is already a STALE
    TABLE alert). Any other error used to drop the table from the deep scan
    while the run still said 'deep gap scan: done'."""
    from data import check_gaps
    monkeypatch.setattr(monitor, "_db_checks", _REAL_DB_CHECKS)
    monkeypatch.setattr("strategies.support.db.PROD_DB",
                        build_fixture_db(env["tmp"] / "fixture.db"))
    real_collect = check_gaps.collect_gaps

    def collect(con, spec, *a, **k):
        if spec.table == "cd_futures_15m":
            raise sqlite3.OperationalError("disk I/O error")
        return real_collect(con, spec, *a, **k)
    monkeypatch.setattr(check_gaps, "collect_gaps", collect)
    assert monitor.main(["--deep", "--quiet"]) == 1
    gaps = [a["text"] for a in _status(env["data"], monitor.DEEP_STATUS)["alerts"]
            if a["code"] == "INTERIOR_GAPS"]
    assert [t for t in gaps if "scan failed" in t] == [
        "INTERIOR GAPS cd_futures_15m: scan failed: OperationalError('disk I/O error')"]


# ─── disk, backups, deep scan ─────────────────────────────────────────────────

@pytest.mark.parametrize("free_gb, expect", [
    (50.0, None), (5.0, None), (4.9, False), (2.0, False), (1.9, True)])
def test_disk_low_thresholds(env, monkeypatch, free_gb, expect):
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Usage(500 * GB, 0, int(free_gb * GB)))
    got = monitor.disk_low()
    if expect is None:
        assert got is None
    else:
        critical, text = got
        assert critical is expect
        assert text.startswith("DISK LOW (CRITICAL)" if expect else "DISK LOW (warn)")
        assert f"{free_gb:.1f} GB free" in text


def test_disk_low_is_an_alert_in_a_run(env, monkeypatch):
    monkeypatch.setattr(monitor.shutil, "disk_usage",
                        lambda p: _Usage(500 * GB, 0, int(1.5 * GB)))
    assert monitor.main(["--quiet"]) == 1
    assert [a["code"] for a in _status(env["data"])["alerts"]] == ["DISK_LOW"]


def _now():
    return datetime.now(timezone.utc)


@pytest.mark.parametrize("age_h, expect", [(2, None), (29, None), (31, False),
                                          (71, False), (73, True)])
def test_backup_stale_from_backup_last_json(env, age_h, expect):
    _write(env["data"] / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "ok", "finished_utc": _iso_ago(age_h * 3600)})
    got = monitor.backup_stale(_now())
    assert got is None if expect is None else got[0] is expect
    if got:
        assert "backup_last.json" in got[1]


def test_a_failed_backup_run_does_not_count_as_a_backup(env):
    _write(env["data"] / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "check_failed", "finished_utc": _iso_ago(600)})
    got = monitor.backup_stale(_now())
    assert got is not None, "a failed run 10 minutes ago read as a fresh backup"
    critical, text = got
    assert critical is True and "no backup found" in text


def test_backup_stale_falls_back_to_the_newest_copy_mtime(env):
    (env["data"] / "diagnostics" / monitor.BACKUP_STATUS).unlink()
    backups = env["data"] / "backups"
    backups.mkdir()
    old = backups / "prod-20260722.db"
    old.write_bytes(b"x")
    t = _now().timestamp() - 54 * 86400
    os.utime(old, (t, t))
    # neither counts as a backup: an unverified snapshot, another file kind
    (backups / "prod-20260914.db.partial").write_bytes(b"x")
    (backups / "minute-repair-20260907.db").write_bytes(b"x")
    critical, text = monitor.backup_stale(_now())
    assert critical is True and "prod-20260722.db" in text and "54.0d" in text

    fresh = backups / "prod-20260913.db"
    fresh.write_bytes(b"x")
    t = _now().timestamp() - 31 * 3600
    os.utime(fresh, (t, t))
    critical, text = monitor.backup_stale(_now())
    assert critical is False and "prod-20260913.db" in text

    t = _now().timestamp() - 10 * 3600
    os.utime(fresh, (t, t))
    assert monitor.backup_stale(_now()) is None


def test_a_copy_pruned_between_glob_and_stat_is_skipped(env, monkeypatch):
    (env["data"] / "diagnostics" / monitor.BACKUP_STATUS).unlink()
    backups = env["data"] / "backups"
    backups.mkdir()
    (backups / "prod-20260913.db").write_bytes(b"x")
    gone = backups / "prod-20260914.db"         # listed, then pruned before its stat
    real_glob = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pattern: [*real_glob(self, pattern), gone])
    critical, text = monitor.backup_stale(_now() + timedelta(hours=40))
    assert critical is False and "prod-20260913.db" in text


def test_a_wrong_typed_backup_record_is_an_alert_not_a_crash(env):
    """Valid JSON with a non-string finished_utc used to raise TypeError in
    _age_s, so every hourly run crashed (exit 2) on one hand-edited file."""
    _write(env["data"] / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "ok", "finished_utc": 123})
    assert monitor.main(["--quiet"]) == 1
    s = _status(env["data"])
    assert s["result"] == "alerts"
    assert [a["code"] for a in s["alerts"]] == ["BACKUP_STALE"]


def test_backup_stale_is_an_alert_in_a_run(env):
    _write(env["data"] / "diagnostics" / monitor.BACKUP_STATUS,
           {"result": "ok", "finished_utc": _iso_ago(80 * 3600)})
    assert monitor.main(["--quiet"]) == 1
    alerts = _status(env["data"])["alerts"]
    assert [a["code"] for a in alerts] == ["BACKUP_STALE"]
    assert alerts[0]["text"].startswith("BACKUP STALE (CRITICAL)")


def test_deep_scan_stale_only_on_hourly_runs(env):
    _write(env["data"] / "diagnostics" / monitor.DEEP_STATUS,
           {"result": "green", "finished_utc": _iso_ago(31 * 3600)})
    assert monitor.main(["--quiet"]) == 1
    assert [a["code"] for a in _status(env["data"])["alerts"]] == ["DEEP_SCAN_STALE"]
    # a --deep run is the deep scan: it does not alert on its own staleness
    assert monitor.main(["--deep", "--quiet"]) == 0
    assert monitor.main(["--quiet"]) == 0          # and it refreshed the record


@pytest.mark.parametrize("payload, needle", [
    (None, "no deep gap scan recorded"),
    ("{broken", "unreadable"),
    ({"result": "error", "finished_utc": _iso_ago(60)}, "crashed"),
    ({"result": "green", "finished_utc": _iso_ago(29 * 3600)}, None),
])
def test_deep_scan_stale_cases(env, payload, needle):
    path = env["data"] / "diagnostics" / monitor.DEEP_STATUS
    path.unlink()
    if payload is not None:
        _write(path, payload)
    got = monitor.deep_scan_stale(_now())
    assert got is None if needle is None else needle in got
