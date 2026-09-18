"""Re-stamp cd_open_interest's Binance-era rows to the close-of-hour convention.

BACKLOG item 30. From 2026-06-10 09:00 UTC the Binance fetcher stored each
snapshot under the boundary it was taken at (T), which is the open interest at
the START of hour T. CoinDesk's rows, and everything the squeeze bots were
researched on, hold the open interest at the END of hour H under stamp H. So
every Binance-era row is one bar stale against the price bar it is joined to.
The corrected fetcher (data/sources/binance.py, same commit) writes a snapshot
at T under T-3600; this script moves the rows already written to match, in
one transaction, and verifies the result against Binance Vision's 5-minute
`metrics` archive — the truth series — when given one.

Run it with the feed STOPPED. The running feed re-inserts its 500-row window
every minute with INSERT OR IGNORE and would fill the vacated newest stamp
with a start-of-hour value inside a minute of the shift. The script refuses
if the feed heartbeat is under two minutes old unless --allow-live-feed.

    python data/migrations/2026_09_19_oi_close_of_hour.py --dry-run
    python data/migrations/2026_09_19_oi_close_of_hour.py --archive <snapshots.json>
    python data/migrations/2026_09_19_oi_close_of_hour.py --revert

Backup: the affected block is copied to cd_open_interest_bak_20260919 before
anything moves, and --revert restores from it. The backup table stays until
the operator drops it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db  # noqa: E402

TABLE = "cd_open_interest"
BACKUP = "cd_open_interest_bak_20260919"
H = 3600
# First Binance-era row: the last CoinDesk (OHLC) row is 2026-06-10 08:00.
SEAM = int(datetime(2026, 6, 10, 9, tzinfo=timezone.utc).timestamp())
# Verification is threshold-free (see verify); kept only for the seam printout.
TOL = 1e-3


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def feed_is_live(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT last_tick_utc FROM bot_heartbeats WHERE name='feed'").fetchone()
    except sqlite3.OperationalError:
        return False
    if not row or not row[0]:
        return False
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
    return age < 120


def block(con: sqlite3.Connection) -> list[tuple]:
    return con.execute(f"SELECT * FROM {TABLE} WHERE timestamp >= ? ORDER BY timestamp",
                       (SEAM,)).fetchall()


def verify(con: sqlite3.Connection, snaps: dict[int, float]) -> dict:
    """Score every stored stamp from the seam with the same closer-to rule the
    monitor uses daily (binance.score_oi_alignment): is each value nearer the
    archive's snapshot at H+1h (close of hour, correct) or at H (the defect)?
    No tolerance — the two Binance series never agree exactly."""
    from data.sources import binance
    rows = con.execute(f"SELECT timestamp, oi_close FROM {TABLE} WHERE timestamp >= ? "
                       f"ORDER BY timestamp", (SEAM - H,)).fetchall()
    return binance.score_oi_alignment(rows, snaps)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--archive", type=Path, default=None,
                    help="snapshots.json: {epoch_s: sum_open_interest} from the 5-minute archive.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--revert", action="store_true", help="Restore the block from the backup table.")
    ap.add_argument("--allow-live-feed", action="store_true")
    args = ap.parse_args(argv)

    snaps: dict[int, float] = {}
    if args.archive:
        snaps = {int(k): float(v) for k, v in json.loads(args.archive.read_text()).items()}
        print(f"archive: {len(snaps):,} snapshots, {_utc(min(snaps))} -> {_utc(max(snaps))}")

    con = sqlite3.connect(str(args.db or db.PROD_DB), timeout=30)
    try:
        if feed_is_live(con) and not args.allow_live_feed and not args.dry_run:
            print("REFUSED: the feed heartbeat is under two minutes old. Stop the feed first "
                  "(its per-minute INSERT OR IGNORE would refill the vacated stamp), or pass "
                  "--allow-live-feed if you know why that is fine.")
            return 2

        if args.revert:
            n_bak = con.execute(f"SELECT COUNT(*) FROM {BACKUP}").fetchone()[0]
            print(f"revert: restoring {n_bak} rows from {BACKUP}")
            if args.dry_run:
                return 0
            con.execute("BEGIN")
            con.execute(f"DELETE FROM {TABLE} WHERE timestamp >= ?", (SEAM - H,))
            con.execute(f"INSERT OR REPLACE INTO {TABLE} SELECT * FROM {BACKUP}")
            con.execute("COMMIT")
            print("reverted")
            return 0

        rows = block(con)
        if not rows:
            print("nothing to do: no rows at or after the seam")
            return 0
        flat = sum(1 for r in rows if r[1] == r[2] == r[3] == r[4])
        print(f"block: {len(rows)} rows from {_utc(rows[0][0])} to {_utc(rows[-1][0])}, "
              f"{flat} point-snapshot rows")
        if flat != len(rows):
            print("REFUSED: the block holds rows that are not point snapshots; the seam is wrong.")
            return 2
        stamps = [r[0] for r in rows]
        if any((b - a) != H for a, b in zip(stamps, stamps[1:])):
            print("REFUSED: the block is not one contiguous hourly run; inspect before shifting.")
            return 2
        seam_prev = con.execute(f"SELECT oi_close FROM {TABLE} WHERE timestamp=?",
                                (SEAM - H,)).fetchone()
        print(f"seam: CoinDesk close at {_utc(SEAM - H)} = {seam_prev[0] if seam_prev else None:,.0f}; "
              f"first Binance snapshot at {_utc(SEAM)} = {rows[0][4]:,.0f} "
              f"(these are the same instant; the CoinDesk row is kept)")

        if snaps:
            print("before:", verify(con, snaps))
        if args.dry_run:
            print(f"dry run: would move {len(rows)} rows back one hour, dropping the one that "
                  f"lands on the CoinDesk seam row; newest stamp {_utc(stamps[-1])} becomes "
                  f"{_utc(stamps[-1] - H)} and {_utc(stamps[-1])} stays empty until the feed's next pull.")
            return 0

        con.execute("BEGIN")
        con.execute(f"DROP TABLE IF EXISTS {BACKUP}")
        con.execute(f"CREATE TABLE {BACKUP} AS SELECT * FROM {TABLE} WHERE timestamp >= ?",
                    (SEAM - H,))
        con.execute(f"DELETE FROM {TABLE} WHERE timestamp >= ?", (SEAM,))
        moved = dropped = 0
        for r in rows:
            cur = con.execute(
                f"INSERT OR IGNORE INTO {TABLE} VALUES (?,?,?,?,?,?,?,?,?)",
                (r[0] - H, *r[1:]))
            if cur.rowcount:
                moved += 1
            else:
                dropped += 1        # the seam row: CoinDesk already holds that instant
        con.execute("COMMIT")
        print(f"moved {moved} rows back one hour, {dropped} dropped onto the CoinDesk seam row; "
              f"backup in {BACKUP}")
        if snaps:
            print("after: ", verify(con, snaps))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
