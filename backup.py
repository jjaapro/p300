"""Daily prod.db backup — the database is the project's crown jewel.

Klines are refetchable, but LSR / open-interest / liquidations history
beyond Binance's ~30-day retention is IRREPLACEABLE: lose prod.db and the
inputs several validated edges are built on are gone for good. Until
2026-07-22 no backup routine existed at all.

Uses `VACUUM INTO` over a read-only connection — WAL-safe (consistent
snapshot while feed/bots keep writing) and compacting. The snapshot goes to
prod-YYYYMMDD.db.partial, is checked, and only then renamed to
prod-YYYYMMDD.db. So a run killed by its time limit, or a failed check, never
leaves a truncated file that retention would count as a backup (the next run
deletes any leftover partial before it measures free space), and a same-day
rerun replaces the morning copy only once the new one verifies.

Retention: the newest --keep-daily copies plus the newest --keep-weekly
Sunday copies (defaults 7 + 4). The scheduled task runs --keep-daily 2
--keep-weekly 0: a 1.65 GB prod.db on a nearly full C: cannot hold more.
Copies dated before PRUNE_FROM are never pruned automatically, and names
that do not parse as prod-YYYYMMDD.db are never touched.

Usage:
  python backup.py                                  # snapshot + check + prune
  python backup.py --keep-daily 2 --keep-weekly 0   # what the task runs
  python backup.py --verify-full    # full integrity_check instead of quick_check

Scheduled by ops/register_tasks.ps1 (\\p300\\backup-daily, 04:40 local).
Every run writes data/diagnostics/backup_last.json and appends to
data/diagnostics/backup.log. Exit 0 = backup verified; 1 = snapshot, check
or prune failed, or a leftover partial is still held open (existing copies
kept); 2 = source missing or not enough free disk. monitor.py alerts BACKUP_STALE when the newest good copy is older
than 30h (critical past 72h), and the dashboard shows it. Occasionally copy
the newest file off-machine — retention here does not survive a disk failure.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from strategies.support import db  # noqa: E402

log = logging.getLogger("backup")

BACKUP_DIR = REPO / "data" / "backups"
KEEP_DAILY = 7
KEEP_WEEKLY = 4          # Sunday copies

# Never auto-prune a copy dated before this day. Older copies are the
# operator's to delete; the operator chose to keep them (2026-09-14).
PRUNE_FROM = "2026-09-14"

STATUS_FILE = "backup_last.json"     # under db.DATA_DIR/diagnostics; monitor reads it
LOG_FILE = "backup.log"


def _copy_date(f: Path) -> datetime | None:
    try:
        return datetime.strptime(f.name, "prod-%Y%m%d.db")
    except ValueError:
        return None


def _prune(keep_daily: int = KEEP_DAILY,
           keep_weekly: int = KEEP_WEEKLY) -> list[str]:
    """Keep the newest `keep_daily` copies plus the newest `keep_weekly`
    Sunday copies; delete the rest, except copies dated before PRUNE_FROM.
    Names that do not parse are ignored. Returns the deleted names."""
    dated = sorted((d, f) for f in BACKUP_DIR.glob("prod-*.db")
                   if (d := _copy_date(f)) is not None)
    files = [f for _, f in dated]
    sundays = [f for d, f in dated if d.weekday() == 6]
    # max(0, len - n), not [-n:]: a keep of 0 must keep none, and [-0:] is all
    keep = set(files[max(0, len(files) - keep_daily):])
    keep.update(sundays[max(0, len(sundays) - keep_weekly):])
    protected_before = datetime.strptime(PRUNE_FROM, "%Y-%m-%d")
    deleted = []
    for d, f in dated:
        if f in keep or d < protected_before:
            continue
        f.unlink()
        deleted.append(f.name)
    return deleted


def _snapshot(src: Path, dest: Path) -> None:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        con.execute("VACUUM INTO ?", (str(dest),))
    finally:
        con.close()


def _verify(path: Path, check: str) -> str:
    """PRAGMA quick_check / integrity_check result: 'ok' or the problem."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return str(con.execute(f"PRAGMA {check}").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error as e:
        return repr(e)


def _write_status(payload: dict) -> None:
    """data/diagnostics/backup_last.json, replaced atomically; retried while a
    reader (the dashboard) holds it open. Never raises."""
    path = db.DATA_DIR / "diagnostics" / STATUS_FILE
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except Exception as e:  # noqa: BLE001
        log.warning(f"status file {path} not written: {e!r}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _backup(args: argparse.Namespace, status: dict) -> int:
    src = db.PROD_DB
    if not src.exists():
        log.error(f"source missing: {src}")
        status["result"] = "source_missing"
        return 2
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # A run killed by its time limit or a logoff leaves prod-YYYYMMDD.db.partial,
    # whatever day it ran. None is ever a valid copy and neither retention nor
    # monitor sees them, so clear them all, and before free space is measured.
    for stale in sorted(BACKUP_DIR.glob("prod-*.db.partial")):
        try:
            stale.unlink()
        except OSError as e:          # held open: another backup run is writing it
            log.error(f"cannot remove {stale.name}: {e!r} — is another backup "
                      f"running? existing copies kept")
            status["result"] = "partial_locked"
            return 1
        log.warning(f"removed unverified partial {stale.name}")

    src_size = src.stat().st_size
    free = shutil.disk_usage(BACKUP_DIR).free
    if free < 2 * src_size:
        log.error(f"insufficient disk: need ~{2 * src_size / 1e9:.1f} GB free, "
                  f"have {free / 1e9:.1f} GB")
        status["result"] = "low_disk"
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = BACKUP_DIR / f"prod-{stamp}.db"
    # Not prod-*.db, so neither retention nor monitor counts it until verified.
    partial = dest.with_name(dest.name + ".partial")
    status["dest"] = str(dest)
    try:
        t0 = time.monotonic()
        try:
            _snapshot(src, partial)
        except sqlite3.Error as e:
            log.error(f"snapshot FAILED: {e!r} — existing copies kept")
            status["result"] = "snapshot_failed"
            return 1
        log.info(f"snapshot {partial.name}: {partial.stat().st_size / 1e9:.2f} GB "
                 f"in {time.monotonic() - t0:.0f}s")

        check = status["check"]
        result = _verify(partial, check)
        if result != "ok":
            log.error(f"backup FAILED {check}: {result} — existing copies kept")
            status["result"] = "check_failed"
            return 1
        log.info(f"{check}: ok")

        os.replace(partial, dest)          # a same-day copy is replaced only now
        status.update(result="ok", size_bytes=dest.stat().st_size)
    finally:
        partial.unlink(missing_ok=True)    # no-op once renamed

    try:
        deleted = _prune(args.keep_daily, args.keep_weekly)
    except OSError as e:
        log.error(f"prune FAILED: {e!r} — the new copy {dest.name} is good")
        status["result"] = "prune_failed"
        return 1
    status["pruned"] = deleted
    if deleted:
        log.info(f"pruned: {', '.join(deleted)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prod.db daily backup")
    ap.add_argument("--verify-full", action="store_true",
                    help="Run full PRAGMA integrity_check on the copy "
                         "(minutes on a 1.5GB file; default is quick_check).")
    ap.add_argument("--keep-daily", type=int, default=KEEP_DAILY,
                    help=f"Newest copies to keep (default {KEEP_DAILY}, "
                         f"at least 1).")
    ap.add_argument("--keep-weekly", type=int, default=KEEP_WEEKLY,
                    help=f"Newest Sunday copies to keep on top "
                         f"(default {KEEP_WEEKLY}).")
    args = ap.parse_args(argv)
    if args.keep_daily < 1:
        ap.error("--keep-daily must be at least 1 (0 would delete the copy "
                 "just taken)")
    if args.keep_weekly < 0:
        ap.error("--keep-weekly must not be negative")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    handler = None
    log_path = db.DATA_DIR / "diagnostics" / LOG_FILE
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000,
                                      backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError as e:
        log.warning(f"{log_path} unavailable: {e!r}")

    started = datetime.now(timezone.utc)
    status = {"started_utc": started.isoformat(timespec="seconds"),
              "finished_utc": None, "duration_s": None, "result": "error",
              "dest": None, "size_bytes": None,
              "check": "integrity_check" if args.verify_full else "quick_check",
              "pruned": [], "free_bytes_after": None}
    code = 1
    try:
        code = _backup(args, status)
    except Exception as e:  # noqa: BLE001 — recorded, then exit 1
        log.exception(f"backup crashed: {e!r}")
        status["result"] = "error"
    finally:
        finished = datetime.now(timezone.utc)
        status["finished_utc"] = finished.isoformat(timespec="seconds")
        status["duration_s"] = round((finished - started).total_seconds(), 2)
        try:
            status["free_bytes_after"] = shutil.disk_usage(
                BACKUP_DIR if BACKUP_DIR.exists() else REPO).free
        except OSError:
            pass
        _write_status(status)
        if handler is not None:
            log.removeHandler(handler)
            handler.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
