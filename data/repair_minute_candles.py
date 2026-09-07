"""Audit and repair fifteen-minute candles incorrectly seeded into btc_1m.

Default: read-only audit. --prepare downloads checksum-verified Binance spot
1m archives and saves originals + replacements in a separate recovery database.
--apply-stage applies that database atomically, refusing changed originals.
The recovery database and downloaded archives are retained under data/backups.

    python -m data.repair_minute_candles
    python -m data.repair_minute_candles --prepare data/backups/minute-repair.db
    python -m data.repair_minute_candles --apply-stage data/backups/minute-repair.db

Archive contract: https://github.com/binance/binance-public-data
Spot timestamps are microseconds from 2025-01-01; earlier archives use ms.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import re
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from strategies.support import db

BASE_URL = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m"
COLUMNS = "open_time, open, high, low, close, volume, num_trades"
ROW_SCHEMA = """(open_time INTEGER PRIMARY KEY, open REAL, high REAL,
                 low REAL, close REAL, volume REAL, num_trades INTEGER)"""
MATCH_SQL = """
    FROM cd_spot_15m s JOIN btc_1m m ON m.open_time = s.timestamp * 1000
    WHERE m.open = s.open AND m.high = s.high AND m.low = s.low
      AND m.close = s.close AND m.volume = s.volume
      AND m.num_trades = s.total_trades
      AND EXISTS (
          SELECT 1 FROM btc_1m later
          WHERE later.open_time > m.open_time
            AND later.open_time < m.open_time + 900000
            AND later.volume > 0)
"""


class ComparisonUnavailable(ValueError):
    """The reference table is absent; this is not a clean audit result."""


def connect_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def find_mislabeled_minutes(con: sqlite3.Connection) -> list[tuple]:
    """Proven clones: all candle fields match AND later trading occurred.

    The later-volume condition avoids flagging a legitimate quarter-hour
    whose entire trading happened in its first minute.
    """
    required = {"btc_1m", "cd_spot_15m"}
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= tables:
        raise ComparisonUnavailable("missing " + ", ".join(sorted(required - tables)))
    for table, columns in [("btc_1m", set(COLUMNS.replace(" ", "").split(","))),
                           ("cd_spot_15m", {"timestamp", "open", "high", "low", "close", "volume", "total_trades"})]:
        present = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if not columns <= present:
            raise ComparisonUnavailable(f"{table} missing columns: {', '.join(sorted(columns - present))}")
    if con.execute("SELECT 1 FROM cd_spot_15m s JOIN btc_1m m ON m.open_time=s.timestamp*1000 LIMIT 1").fetchone() is None:
        raise ComparisonUnavailable("no overlapping reference candles")
    cols = ", ".join("m." + col.strip() for col in COLUMNS.split(","))
    return con.execute(f"SELECT {cols} {MATCH_SQL} ORDER BY m.open_time").fetchall()


def _validate_values(row: tuple) -> None:
    ts, op, hi, lo, cl, volume, trades = row
    if not isinstance(ts, int) or ts % 60000:
        raise ValueError(f"unaligned minute timestamp: {ts}")
    if not all(math.isfinite(x) for x in (op, hi, lo, cl, volume)):
        raise ValueError(f"nonfinite candle at {ts}")
    if not (0 < lo <= min(op, cl) <= max(op, cl) <= hi) or volume < 0:
        raise ValueError(f"invalid OHLCV at {ts}")
    if not isinstance(trades, int) or trades < 0:
        raise ValueError(f"invalid trade count at {ts}")


def parse_archive(payload: bytes, month: str, targets: set[int]) -> dict[int, tuple]:
    """Validate archive cadence/content, returning every requested minute."""
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("invalid archive month")
    first = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    last = first.replace(year=first.year + 1, month=1) if first.month == 12 else first.replace(month=first.month + 1)
    start_ms, end_ms = int(first.timestamp()) * 1000, int(last.timestamp()) * 1000
    if any(not start_ms <= ts < end_ms for ts in targets):
        raise ValueError("target outside requested archive month")
    expected = f"BTCUSDT-1m-{month}.csv"
    result: dict[int, tuple] = {}
    previous = -1
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.namelist() != [expected]:
            raise ValueError(f"unexpected archive members: {archive.namelist()}")
        with archive.open(expected) as raw:
            for values in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                if len(values) != 12:
                    raise ValueError("expected twelve Binance kline columns")
                opened = int(values[0])
                scale = 1000 if opened >= 10**15 else 1
                ts = opened // scale
                if not start_ms <= ts < end_ms:
                    raise ValueError(f"candle outside archive month: {ts}")
                if ts <= previous:
                    raise ValueError(f"duplicate or unordered candle: {ts}")
                previous = ts
                if ts not in targets:
                    # Only selected rows will be written. Archive 2020-12
                    # contains an unrelated zero-volume row with a stale
                    # close_time; it must not prevent repairing valid targets.
                    continue
                closed = int(values[6])
                # Published archives occasionally end a halt's final candle
                # early (e.g. 2020-02-19 10:15:32.286). Its entire interval
                # must still be inside this minute; never accept a 15m span.
                if opened % (60000 * scale) or not 0 <= closed - opened < 60000 * scale:
                    raise ValueError(f"not a one-minute candle: {opened}")
                row = (ts, *(float(v) for v in values[1:6]), int(values[8]))
                _validate_values(row)
                if ts in targets:
                    result[ts] = row
    missing = targets - result.keys()
    if missing:
        raise ValueError(f"archive {month} missing {len(missing)} requested minutes")
    return result


def download_archive(month: str, cache_dir: Path) -> tuple[bytes, str, str]:
    """Fetch public archive + published SHA-256; cached payload is rechecked."""
    filename = f"BTCUSDT-1m-{month}.zip"
    url = f"{BASE_URL}/{filename}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / filename
    with urlopen(url + ".CHECKSUM", timeout=30) as response:
        parts = response.read().decode("ascii").strip().split()
    if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) or parts[1].lstrip("*") != filename:
        raise ValueError(f"invalid published checksum for {filename}")
    expected = parts[0].lower()
    if cached.exists():
        payload = cached.read_bytes()
    else:
        with urlopen(url, timeout=60) as response:
            payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected:
        raise ValueError(f"SHA-256 mismatch for {filename}; remove the invalid cache and retry")
    if not cached.exists():
        cached.write_bytes(payload)
    return payload, url, digest


def prepare_repair(database: Path, stage: Path, cache_dir: Path,
                   *, downloader=download_archive) -> int:
    """Prepare a complete repair without writing anything to the source DB."""
    database, stage = database.resolve(), stage.resolve()
    if stage.exists() or stage == database:
        raise ValueError("recovery database must be a new, separate file")
    con = connect_readonly(database)
    try:
        originals = find_mislabeled_minutes(con)
    finally:
        con.close()
    if not originals:
        return 0
    by_month: dict[str, set[int]] = defaultdict(set)
    for row in originals:
        by_month[datetime.fromtimestamp(row[0] / 1000, timezone.utc).strftime("%Y-%m")].add(row[0])
    replacements = {}
    sources = []
    for month, targets in sorted(by_month.items()):
        payload, url, digest = downloader(month, cache_dir)
        parsed = parse_archive(payload, month, targets)
        replacements.update(parsed)
        sources.append((month, url, digest, len(parsed)))
        print(f"{month}: verified {len(parsed):,} replacement minutes", flush=True)
    if len(replacements) != len(originals):
        raise ValueError("replacement coverage differs from original rows")
    if any(replacements[row[0]] == row for row in originals):
        raise ValueError("archive replacement still equals a proven fifteen-minute clone")
    stage.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never overwrite a recovery artifact or any database.
    with stage.open("xb"):
        pass
    recovery = sqlite3.connect(str(stage))
    try:
        with recovery:
            recovery.execute(f"CREATE TABLE originals {ROW_SCHEMA}")
            recovery.execute(f"CREATE TABLE replacements {ROW_SCHEMA}")
            recovery.execute("CREATE TABLE sources (month TEXT PRIMARY KEY, url TEXT, sha256 TEXT, n INTEGER)")
            recovery.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            recovery.executemany("INSERT INTO originals VALUES (?,?,?,?,?,?,?)", originals)
            recovery.executemany("INSERT INTO replacements VALUES (?,?,?,?,?,?,?)", replacements.values())
            recovery.executemany("INSERT INTO sources VALUES (?,?,?,?)", sources)
            recovery.executemany("INSERT INTO metadata VALUES (?,?)", [
                ("database", str(database)), ("count", str(len(originals))),
                ("created_utc", datetime.now(timezone.utc).isoformat()),
            ])
    finally:
        recovery.close()
    return len(originals)


def apply_repair(database: Path, stage: Path) -> int:
    """Atomically update unchanged originals; the stage is the durable backup.

    An already-applied stage is a no-op. A mixture of originals/replacements
    or other edits is refused instead of overwriting a concurrent correction.
    """
    database, stage = database.resolve(), stage.resolve()
    recovery = connect_readonly(stage)
    try:
        metadata = dict(recovery.execute("SELECT key,value FROM metadata"))
        if Path(metadata["database"]).resolve() != database:
            raise ValueError("recovery database belongs to a different source")
        originals = recovery.execute(f"SELECT {COLUMNS} FROM originals ORDER BY open_time").fetchall()
        replacements = recovery.execute(f"SELECT {COLUMNS} FROM replacements ORDER BY open_time").fetchall()
    finally:
        recovery.close()
    if len(originals) != int(metadata["count"]) or len(originals) != len(replacements) or not originals:
        raise ValueError("incomplete recovery database")
    for original, replacement in zip(originals, replacements):
        _validate_values(replacement)
        if original[0] != replacement[0] or original == replacement:
            raise ValueError("invalid replacement keys or unchanged clone")
    con = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=30)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute(f"CREATE TEMP TABLE repair_originals {ROW_SCHEMA}")
        con.executemany("INSERT INTO repair_originals VALUES (?,?,?,?,?,?,?)", originals)
        cols = ", ".join("m." + c.strip() for c in COLUMNS.split(","))
        current = con.execute(f"SELECT {cols} FROM repair_originals r JOIN btc_1m m USING(open_time) ORDER BY m.open_time").fetchall()
        if current == replacements:
            con.rollback()
            return 0
        if current != originals:
            raise ValueError("source rows changed since preparation; no rows updated")
        before = con.execute("SELECT COUNT(*) FROM btc_1m").fetchone()[0]
        cursor = con.executemany(
            "UPDATE btc_1m SET open=?,high=?,low=?,close=?,volume=?,num_trades=? WHERE open_time=?",
            [(*row[1:], row[0]) for row in replacements])
        if cursor.rowcount != len(originals):
            raise ValueError("unexpected number of updated rows")
        after = con.execute("SELECT COUNT(*) FROM btc_1m").fetchone()[0]
        updated = con.execute(f"SELECT {cols} FROM repair_originals r JOIN btc_1m m USING(open_time) ORDER BY m.open_time").fetchall()
        if before != after or updated != replacements or find_mislabeled_minutes(con):
            raise ValueError("post-repair validation failed; all changes rolled back")
        con.commit()
        return len(originals)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=db.PROD_DB)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare", type=Path, metavar="RECOVERY_DB")
    actions.add_argument("--apply-stage", type=Path, metavar="RECOVERY_DB")
    parser.add_argument("--cache-dir", type=Path, default=db.DATA_DIR / "backups" / "minute-archives")
    args = parser.parse_args(argv)
    if args.prepare:
        count = prepare_repair(args.db, args.prepare, args.cache_dir)
        print(f"Prepared {count:,} replacements; source database unchanged. Recovery: {args.prepare}")
    elif args.apply_stage:
        count = apply_repair(args.db, args.apply_stage)
        print(f"Repaired {count:,} rows. Originals retained in {args.apply_stage}")
    else:
        con = connect_readonly(args.db)
        try:
            rows = find_mislabeled_minutes(con)
        finally:
            con.close()
        print(f"Proven fifteen-minute candles in btc_1m: {len(rows):,}")
        if rows:
            print("Run --prepare RECOVERY_DB, then --apply-stage RECOVERY_DB.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
