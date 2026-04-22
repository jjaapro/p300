"""One-shot seed: copy only the tables P-300 reads from the upstream trader.db
into the local data/trader.db.

Upstream DB path is the sibling trader repo by default. Override with
--source=/path/to/trader.db. Run once at repo init; re-run periodically if you
want fresh ca_long_short_ratio / cd_spot_binance (those are not kept fresh by
binance_feed.py).

Notes on upstream schema drift:
  - The upstream repo stores BTC minute-level data as `btc_15m` (15-min bars)
    and our services expect `btc_1m`. Same columns; we rename on copy. The
    initial seed is therefore 15-min granularity; `binance_feed.py` will
    backfill proper 1-min bars as it runs.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

DEFAULT_SOURCE = Path(r"c:\Source\Repos\trader\data\trader.db")
LOCAL_DB = Path(__file__).resolve().parent / "data" / "trader.db"

TABLES: list[tuple[str, str]] = [
    ("btc_15m", "btc_1m"),
    ("eth_1m", "eth_1m"),
    ("cd_futures_ohlcv", "cd_futures_ohlcv"),
    ("cd_funding_rate", "cd_funding_rate"),
    ("cd_spot_binance", "cd_spot_binance"),
    ("ca_long_short_ratio", "ca_long_short_ratio"),
    ("scheduled_events", "scheduled_events"),
]


def seed(source: Path, dest: Path) -> None:
    if not source.exists():
        print(f"ERROR: source DB not found: {source}", file=sys.stderr)
        sys.exit(1)
    dest.parent.mkdir(parents=True, exist_ok=True)

    print(f"Source: {source}")
    print(f"Dest:   {dest}")

    if dest.exists():
        backup = dest.with_suffix(".db.bak")
        print(f"Backing up existing {dest} -> {backup}")
        shutil.copy2(dest, backup)
        dest.unlink()

    con_src = sqlite3.connect(str(source))
    con_src.row_factory = None  # plain tuples, faster
    con_dest = sqlite3.connect(str(dest))
    con_dest.execute("PRAGMA journal_mode=WAL")
    try:
        for src_table, dest_table in TABLES:
            schema_row = con_src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (src_table,),
            ).fetchone()
            if schema_row is None:
                print(f"  {src_table} -> {dest_table}: NOT FOUND in source — skipping")
                continue
            ddl = schema_row[0]
            if src_table != dest_table:
                ddl = ddl.replace(f"TABLE {src_table}", f"TABLE {dest_table}", 1)
            con_dest.execute(f"DROP TABLE IF EXISTS {dest_table}")
            con_dest.execute(ddl)

            # Stream rows in chunks to avoid memory spikes on big tables
            cur = con_src.execute(f"SELECT * FROM {src_table}")
            cols = [d[0] for d in cur.description]
            qmarks = ",".join("?" * len(cols))
            col_list = ",".join(cols)
            insert_sql = f"INSERT INTO {dest_table} ({col_list}) VALUES ({qmarks})"

            total = 0
            while True:
                batch = cur.fetchmany(50_000)
                if not batch:
                    break
                con_dest.executemany(insert_sql, batch)
                total += len(batch)
            con_dest.commit()

            note = f" (renamed from {src_table})" if src_table != dest_table else ""
            print(f"  {dest_table}: {total:,} rows copied{note}")
    finally:
        con_src.close()
        con_dest.close()
    print("Done.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help=f"upstream trader.db (default: {DEFAULT_SOURCE})")
    args = ap.parse_args(argv)
    seed(args.source, LOCAL_DB)
    return 0


if __name__ == "__main__":
    sys.exit(main())
