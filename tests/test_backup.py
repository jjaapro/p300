"""backup.py — verified snapshots, safe same-day reruns, bounded retention.

Before 2026-09-14 backup.py wrote the final prod-YYYYMMDD.db directly and
deleted today's copy before re-snapshotting, so a run killed by its time
limit or a failed check left a truncated file that retention counted as a
backup (or lost the good morning copy). `--keep-weekly 0` kept every Sunday
([-0:] is the whole list), and a name that did not parse crashed the prune.

Everything runs in tmp: PROD_DB, DATA_DIR and backup.BACKUP_DIR are
monkeypatched. The source is a real WAL database with a writer holding it
open, the way the fleet does.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import backup

_Usage = namedtuple("_Usage", "total used free")


def _today_name() -> str:
    return f"prod-{datetime.now(timezone.utc):%Y%m%d}.db"


def _sqlite_file(p: Path, marker: str) -> Path:
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE marker (v TEXT)")
    con.execute("INSERT INTO marker VALUES (?)", (marker,))
    con.commit()
    con.close()
    return p


def _marker(p: Path) -> str:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        return con.execute("SELECT v FROM marker").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    backups = data / "backups"
    src = tmp_path / "prod.db"
    writer = sqlite3.connect(str(src))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE marker (v TEXT)")
    writer.execute("INSERT INTO marker VALUES ('live')")
    writer.commit()                     # stays in the -wal: the writer keeps it open
    monkeypatch.setattr("strategies.support.db.PROD_DB", src)
    monkeypatch.setattr("strategies.support.db.DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_DIR", backups)
    yield {"src": src, "data": data, "backups": backups, "tmp": tmp_path}
    writer.close()


def _status(env) -> dict:
    return json.loads((env["data"] / "diagnostics" / backup.STATUS_FILE)
                      .read_text(encoding="utf-8"))


# ─── snapshot -> check -> rename ──────────────────────────────────────────────

def test_snapshot_is_written_as_partial_and_renamed_only_after_the_check(env, monkeypatch):
    seen = {}
    real_verify = backup._verify

    def spy(path, check):
        seen["checked"] = path.name
        seen["final_existed"] = (env["backups"] / _today_name()).exists()
        return real_verify(path, check)
    monkeypatch.setattr(backup, "_verify", spy)

    assert backup.main([]) == 0
    final = env["backups"] / _today_name()
    assert seen == {"checked": _today_name() + ".partial", "final_existed": False}
    assert final.exists() and not Path(f"{final}.partial").exists()
    assert _marker(final) == "live"      # the WAL content made it into the copy
    s = _status(env)
    assert set(s) == {"started_utc", "finished_utc", "duration_s", "result",
                      "dest", "size_bytes", "check", "pruned", "free_bytes_after"}
    assert s["result"] == "ok" and s["check"] == "quick_check"
    assert s["dest"] == str(final) and s["size_bytes"] == final.stat().st_size
    assert s["pruned"] == [] and s["free_bytes_after"] > 0
    assert datetime.fromisoformat(s["finished_utc"]) >= datetime.fromisoformat(s["started_utc"])
    assert "quick_check: ok" in (env["data"] / "diagnostics" / backup.LOG_FILE
                                 ).read_text(encoding="utf-8")


def test_a_failed_check_keeps_the_morning_copy(env, monkeypatch):
    env["backups"].mkdir(parents=True)
    morning = _sqlite_file(env["backups"] / _today_name(), "morning")
    monkeypatch.setattr(backup, "_verify", lambda path, check: "*** page 7 corrupt")
    assert backup.main([]) == 1
    assert morning.exists(), "the failed rerun destroyed the morning copy"
    assert _marker(morning) == "morning"
    assert sorted(p.name for p in env["backups"].iterdir()) == [_today_name()]
    assert _status(env)["result"] == "check_failed"


def test_a_failed_snapshot_keeps_existing_copies_and_leaves_no_partial(env, monkeypatch):
    env["backups"].mkdir(parents=True)
    yday = f"prod-{datetime.now(timezone.utc) - timedelta(days=1):%Y%m%d}.db"
    _sqlite_file(env["backups"] / yday, "yesterday")

    def dies_half_way(src, dest):
        dest.write_bytes(b"half a database")
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(backup, "_snapshot", dies_half_way)
    assert backup.main([]) == 1
    assert sorted(p.name for p in env["backups"].iterdir()) == [yday]
    assert _status(env)["result"] == "snapshot_failed"


def test_a_same_day_rerun_replaces_the_copy_once_the_new_one_verifies(env):
    env["backups"].mkdir(parents=True)
    _sqlite_file(env["backups"] / _today_name(), "morning")
    assert backup.main([]) == 0
    assert _marker(env["backups"] / _today_name()) == "live"


def test_a_partial_left_by_a_killed_run_does_not_block_the_next(env, monkeypatch):
    """The leftover is deleted BEFORE free space is measured: on a nearly full
    C: a copy-sized partial would otherwise fail the guard on every rerun."""
    env["backups"].mkdir(parents=True)
    partial = env["backups"] / (_today_name() + ".partial")
    partial.write_bytes(b"killed mid-write")
    monkeypatch.setattr(backup.shutil, "disk_usage",
                        lambda p: _Usage(0, 0, -1 if partial.exists() else 10 ** 12))
    assert backup.main([]) == 0
    assert sorted(p.name for p in env["backups"].iterdir()) == [_today_name()]


def test_partials_from_earlier_days_are_cleared_too(env):
    """prod-*.db.partial matches neither retention's nor monitor's glob, so a
    partial from an earlier day's killed run would otherwise stay forever."""
    env["backups"].mkdir(parents=True)
    (env["backups"] / "prod-20260913.db.partial").write_bytes(b"killed on another day")
    assert backup.main([]) == 0
    assert sorted(p.name for p in env["backups"].iterdir()) == [_today_name()]


def test_a_partial_still_held_open_stops_the_run_and_keeps_copies(env, monkeypatch):
    env["backups"].mkdir(parents=True)
    yday = f"prod-{datetime.now(timezone.utc) - timedelta(days=1):%Y%m%d}.db"
    _sqlite_file(env["backups"] / yday, "yesterday")
    held = env["backups"] / (_today_name() + ".partial")
    held.write_bytes(b"another run is writing this")
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self == held:                 # Windows: open in another process
            raise PermissionError(13, "in use by another process")
        return real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    snapshots = []
    monkeypatch.setattr(backup, "_snapshot", lambda src, dest: snapshots.append(dest))
    assert backup.main([]) == 1
    assert snapshots == []
    assert _status(env)["result"] == "partial_locked"
    assert sorted(p.name for p in env["backups"].iterdir()) == sorted([yday, held.name])


def test_the_source_is_opened_read_only(env, monkeypatch):
    seen = []
    real = sqlite3.connect

    def spy(database, *a, **k):
        seen.append(str(database))
        return real(database, *a, **k)
    monkeypatch.setattr(sqlite3, "connect", spy)
    assert backup.main([]) == 0
    src_opens = [d for d in seen if str(env["src"]) in d and ".partial" not in d]
    assert src_opens and all("mode=ro" in d for d in src_opens), src_opens


# ─── preconditions ────────────────────────────────────────────────────────────

def test_free_space_guard(env, monkeypatch):
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: _Usage(10, 10, 1))
    assert backup.main([]) == 2
    assert list(env["backups"].iterdir()) == []
    s = _status(env)
    assert s["result"] == "low_disk" and s["dest"] is None


@pytest.mark.parametrize("spare, code, result", [(-1, 2, "low_disk"), (0, 0, "ok")])
def test_free_space_guard_needs_twice_the_source(env, monkeypatch, spare, code, result):
    """2x, not 1x: on a nearly full C: the factor alone decides whether the run
    refuses or fills the drive the feed writes to (2026-09-09)."""
    need = 2 * env["src"].stat().st_size
    assert need > 0
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda p: _Usage(0, 0, need + spare))
    assert backup.main([]) == code
    assert _status(env)["result"] == result


def test_an_unexpected_error_is_recorded_and_exits_1(env, monkeypatch):
    """Not a sqlite3.Error: the run still records itself, keeps the copies, and
    puts the traceback in backup.log (pythonw throws stderr away)."""
    env["backups"].mkdir(parents=True)
    yday = f"prod-{datetime.now(timezone.utc) - timedelta(days=1):%Y%m%d}.db"
    _sqlite_file(env["backups"] / yday, "yesterday")

    def bug(src, dest):
        dest.write_bytes(b"half a database")
        raise RuntimeError("a bug, not a sqlite error")
    monkeypatch.setattr(backup, "_snapshot", bug)
    assert backup.main([]) == 1
    assert _status(env)["result"] == "error"
    assert sorted(p.name for p in env["backups"].iterdir()) == [yday]
    logged = (env["data"] / "diagnostics" / backup.LOG_FILE).read_text(encoding="utf-8")
    assert "backup crashed" in logged and "RuntimeError: a bug, not a sqlite error" in logged


def test_status_write_retries_while_a_reader_holds_the_file(env, monkeypatch):
    real_replace = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError(13, "being read")
        return real_replace(src, dst)
    monkeypatch.setattr(backup.os, "replace", flaky)
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    backup._write_status({"result": "ok"})
    assert len(calls) == 3 and _status(env) == {"result": "ok"}

    def always_busy(src, dst):
        raise PermissionError(13, "being read")
    monkeypatch.setattr(backup.os, "replace", always_busy)
    backup._write_status({"result": "error"})          # never raises
    assert _status(env) == {"result": "ok"}
    assert list((env["data"] / "diagnostics").glob("*.tmp")) == []


def test_backup_log_rotates_at_1_mb_keeping_5(env, monkeypatch):
    def chatty(args, status):
        for _ in range(700):                   # ~7 MB: more rollovers than it keeps
            backup.log.info("x" * 10_000)
        status["result"] = "ok"
        return 0
    monkeypatch.setattr(backup, "_backup", chatty)
    assert backup.main([]) == 0
    logs = list((env["data"] / "diagnostics").glob(f"{backup.LOG_FILE}*"))
    assert sorted(p.name for p in logs) == [backup.LOG_FILE] + [
        f"{backup.LOG_FILE}.{i}" for i in range(1, 6)]
    assert all(p.stat().st_size <= 1_000_000 for p in logs)


def test_missing_source(env, monkeypatch):
    missing = env["tmp"] / "gone" / "prod.db"
    monkeypatch.setattr("strategies.support.db.PROD_DB", missing)
    assert backup.main([]) == 2
    assert not missing.exists()
    assert _status(env)["result"] == "source_missing"


def test_keep_daily_below_one_is_refused(env):
    with pytest.raises(SystemExit) as e:
        backup.main(["--keep-daily", "0"])
    assert e.value.code == 2


# ─── retention ────────────────────────────────────────────────────────────────

def _copies(backups: Path, first: date, last: date) -> None:
    backups.mkdir(parents=True, exist_ok=True)
    d = first
    while d <= last:
        (backups / f"prod-{d:%Y%m%d}.db").write_bytes(b"x")
        d += timedelta(days=1)


def _names(backups: Path) -> list[str]:
    return sorted(p.name for p in backups.iterdir())


def test_prune_honours_keep_daily_and_keep_weekly(env):
    # 2026-09-14 (PRUNE_FROM, a Monday) .. 2026-10-05; Sundays 09-20, 09-27, 10-04
    _copies(env["backups"], date(2026, 9, 14), date(2026, 10, 5))
    backup._prune(keep_daily=2, keep_weekly=0)
    assert _names(env["backups"]) == ["prod-20261004.db", "prod-20261005.db"]

    _copies(env["backups"], date(2026, 9, 14), date(2026, 10, 5))
    backup._prune(keep_daily=2, keep_weekly=2)
    assert _names(env["backups"]) == ["prod-20260927.db", "prod-20261004.db",
                                      "prod-20261005.db"]

    _copies(env["backups"], date(2026, 9, 14), date(2026, 10, 5))
    deleted = backup._prune()                                  # defaults 7 + 4
    kept = _names(env["backups"])
    assert len(kept) == 7 + 2          # 09-20 and 09-27 fall outside the newest 7
    assert "prod-20260920.db" in kept and "prod-20260927.db" in kept
    assert len(deleted) == 22 - len(kept)


def test_prune_never_touches_old_copies_or_names_it_cannot_parse(env):
    b = env["backups"]
    b.mkdir(parents=True)
    keep_always = ["prod-20260722.db", "prod-20260913.db", "prod-backup.db",
                   "prod-20260914.db.partial", "minute-repair-20260907.db"]
    for n in keep_always + ["prod-20260914.db", "prod-20261001.db"]:
        (b / n).write_bytes(b"x")
    assert backup.PRUNE_FROM == "2026-09-14"
    assert backup._prune(keep_daily=1, keep_weekly=0) == ["prod-20260914.db"]
    assert _names(b) == sorted(keep_always + ["prod-20261001.db"])


def test_a_run_prunes_with_its_flags_and_records_it(env, monkeypatch):
    # moved back so this test does not depend on today's date vs 2026-09-14
    monkeypatch.setattr(backup, "PRUNE_FROM", "2000-01-01")
    today = datetime.now(timezone.utc).date()
    _copies(env["backups"], today - timedelta(days=4), today - timedelta(days=1))
    assert backup.main(["--keep-daily", "2", "--keep-weekly", "0"]) == 0
    assert _names(env["backups"]) == sorted(
        [f"prod-{today - timedelta(days=1):%Y%m%d}.db", _today_name()])
    assert sorted(_status(env)["pruned"]) == sorted(
        f"prod-{today - timedelta(days=k):%Y%m%d}.db" for k in (2, 3, 4))


def test_a_prune_failure_is_recorded_and_keeps_the_new_copy(env, monkeypatch):
    """Explorer or AV holding an old copy: exit 1 and BACKUP_FAILED on the
    dashboard, not ok while copies pile up past the 2 the task keeps."""
    def locked(keep_daily, keep_weekly):
        raise PermissionError(13, "in use by another process")
    monkeypatch.setattr(backup, "_prune", locked)
    assert backup.main(["--keep-daily", "2", "--keep-weekly", "0"]) == 1
    s = _status(env)
    assert s["result"] == "prune_failed" and s["size_bytes"] > 0
    assert _names(env["backups"]) == [_today_name()]
    assert "prune FAILED" in (env["data"] / "diagnostics" / backup.LOG_FILE
                              ).read_text(encoding="utf-8")
