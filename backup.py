"""Daily prod.db backup — the database is the project's crown jewel.

Klines are refetchable, but LSR / open-interest / liquidations history
beyond Binance's ~30-day retention is IRREPLACEABLE: lose prod.db and the
inputs several validated edges are built on are gone for good. Until
2026-07-22 no backup routine existed at all.

Uses `VACUUM INTO` — WAL-safe (consistent snapshot while feed/bots keep
writing) and compacting. Retention: last 7 daily + last 4 Sunday copies.

Usage:
  python backup.py              # snapshot + prune + quick verify
  python backup.py --verify-full  # also run full integrity_check on the copy

Schedule daily via Task Scheduler. Exit 0 = backup verified; non-zero =
investigate (monitor.py alerts independently if backups go stale).
Occasionally copy the newest file off-machine — retention here does not
survive a disk failure.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from strategies.support import db  # noqa: E402

log = logging.getLogger("backup")

BACKUP_DIR = REPO / "data" / "backups"
KEEP_DAILY = 7
KEEP_WEEKLY = 4          # Sunday copies


def _prune() -> list[str]:
    """Keep the newest KEEP_DAILY files plus the newest KEEP_WEEKLY Sunday
    files; delete the rest. Returns the deleted names."""
    files = sorted(BACKUP_DIR.glob("prod-*.db"))
    keep: set[Path] = set(files[-KEEP_DAILY:])
    sundays = [f for f in files
               if datetime.strptime(f.stem, "prod-%Y%m%d").weekday() == 6]
    keep.update(sundays[-KEEP_WEEKLY:])
    deleted = []
    for f in files:
        if f not in keep:
            f.unlink()
            deleted.append(f.name)
    return deleted


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prod.db daily backup")
    ap.add_argument("--verify-full", action="store_true",
                    help="Run full PRAGMA integrity_check on the copy "
                         "(minutes on a 1.5GB file; default is quick_check).")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    src = db.PROD_DB
    if not src.exists():
        log.error(f"source missing: {src}")
        return 2
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    src_size = src.stat().st_size
    free = shutil.disk_usage(BACKUP_DIR).free
    if free < 2 * src_size:
        log.error(f"insufficient disk: need ~{2 * src_size / 1e9:.1f} GB free, "
                  f"have {free / 1e9:.1f} GB")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dest = BACKUP_DIR / f"prod-{stamp}.db"
    if dest.exists():
        dest.unlink()                      # same-day rerun replaces

    t0 = datetime.now()
    con = sqlite3.connect(str(src))
    try:
        con.execute("VACUUM INTO ?", (str(dest),))
    finally:
        con.close()
    took = (datetime.now() - t0).total_seconds()
    log.info(f"snapshot {dest.name}: {dest.stat().st_size / 1e9:.2f} GB "
             f"in {took:.0f}s")

    check = "integrity_check" if args.verify_full else "quick_check"
    vcon = sqlite3.connect(str(dest))
    try:
        result = vcon.execute(f"PRAGMA {check}").fetchone()[0]
    finally:
        vcon.close()
    if result != "ok":
        log.error(f"backup FAILED {check}: {result}")
        return 1
    log.info(f"{check}: ok")

    deleted = _prune()
    if deleted:
        log.info(f"pruned: {', '.join(deleted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
