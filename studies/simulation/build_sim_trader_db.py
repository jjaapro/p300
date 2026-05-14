"""Build a sim trader.db from a date-range slice of a real trader.db.

Usage:
    python studies/simulation/build_sim_trader_db.py \\
        --start 2024-01-01 --end 2024-12-31 \\
        --output data/trader_sim_2024.db

The result is a self-contained SQLite that `python run.py --mode sim`
reads via --trader-db <path>. No external API is touched at sim time —
all market data the bot needs comes from this file.

Time-column formats (verified, not assumed):
  - btc_1m / eth_1m use `open_time` in Unix MILLISECONDS.
  - cd_funding_rate, cd_funding_rate_eth, cd_spot_binance,
    cd_futures_ohlcv, ca_long_short_ratio, cd_dvol, cd_liquidations,
    cd_open_interest use `timestamp` in Unix SECONDS.
  - scheduled_events uses `date` as ISO 'YYYY-MM-DD' — copied in full
    so the FOMC sleeve can see upcoming events.
  - news_headlines uses `published_utc` in Unix SECONDS; opt-in via
    --with-news (AI_QUANT-specific, can be large).
  - fomc_observer is the bot's own audit log of FOMC decisions, not a
    data input; SKIPPED.

The window for date-filtered tables is
``[start - warmup_days, end + 1 day]`` so simulator warmup
(regime classifier 365-day vol percentile, EMA50 weekly, etc.) has
enough lookback. Tiny tables and the calendar copy in full.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Per-table copy mode:
#   "unix_ms"  : filter `open_time` in Unix milliseconds
#   "unix_s"   : filter `timestamp` in Unix seconds
#   "news"     : filter `published_utc` in Unix seconds; opt-in
#   "all"      : full copy (no filter)
#   "skip"     : never copy
TABLE_PLAN: list[tuple[str, str]] = [
    # Big market-data tables — date filter required.
    ("btc_1m",                "unix_ms"),
    ("eth_1m",                "unix_ms"),
    ("cd_spot_binance",       "unix_s"),
    ("cd_funding_rate",       "unix_s"),
    ("cd_funding_rate_eth",   "unix_s"),
    ("cd_futures_ohlcv",      "unix_s"),
    ("ca_long_short_ratio",   "unix_s"),
    # Small or calendar tables — no filter.
    ("cd_dvol",               "all"),
    ("cd_liquidations",       "all"),
    ("cd_open_interest",      "all"),
    ("scheduled_events",      "all"),
    # AI_QUANT context — opt-in.
    ("news_headlines",        "news"),
    # Audit log — never copy.
    ("fomc_observer",         "skip"),
]


def _parse_iso_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _row_count(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _copy_one(dest: sqlite3.Connection, name: str, mode: str,
              lo_s: int, hi_s: int) -> int:
    """Run the INSERT for one table. Returns rowcount."""
    if mode == "all":
        sql, params = f'INSERT INTO "{name}" SELECT * FROM src."{name}"', ()
    elif mode == "unix_ms":
        sql = (f'INSERT INTO "{name}" SELECT * FROM src."{name}" '
               f'WHERE open_time >= ? AND open_time <= ?')
        params = (lo_s * 1000, hi_s * 1000)
    elif mode == "unix_s":
        sql = (f'INSERT INTO "{name}" SELECT * FROM src."{name}" '
               f'WHERE timestamp >= ? AND timestamp <= ?')
        params = (lo_s, hi_s)
    elif mode == "news":
        sql = (f'INSERT INTO "{name}" SELECT * FROM src."{name}" '
               f'WHERE published_utc >= ? AND published_utc <= ?')
        params = (lo_s, hi_s)
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    return int(dest.execute(sql, params).rowcount)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=_parse_iso_date, required=True,
                    help="Sim window start (UTC). Format YYYY-MM-DD.")
    p.add_argument("--end", type=_parse_iso_date, required=True,
                    help="Sim window end (UTC, inclusive). Format YYYY-MM-DD.")
    p.add_argument("--source", default="data/trader.db",
                    help="Source trader.db (read-only). Default data/trader.db.")
    p.add_argument("--output", required=True,
                    help="Destination sim trader.db. Overwritten if exists.")
    p.add_argument("--warmup-days", type=int, default=400,
                    help="Lookback days before --start for simulator warmup "
                         "(default 400; covers the 365-day vol percentile gate "
                         "plus EMA50-weekly buffer).")
    p.add_argument("--with-news", action="store_true",
                    help="Include news_headlines (AI_QUANT context). "
                         "Off by default; the table can be large.")
    args = p.parse_args(argv)

    src = Path(args.source).resolve()
    out = Path(args.output).resolve()
    if not src.exists():
        print(f"error: source DB not found: {src}", file=sys.stderr)
        return 2
    if args.start >= args.end:
        print("error: --start must be before --end", file=sys.stderr)
        return 2

    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    lo_s = int((args.start - timedelta(days=args.warmup_days)).timestamp())
    hi_s = int((args.end + timedelta(days=1)).timestamp())

    print(f"source : {src}")
    print(f"output : {out}")
    print(f"window : "
          f"{datetime.fromtimestamp(lo_s, tz=timezone.utc).date()} -> "
          f"{datetime.fromtimestamp(hi_s, tz=timezone.utc).date()}  "
          f"(sim {args.start.date()} -> {args.end.date()}, "
          f"warmup {args.warmup_days}d)")
    print()

    src_uri = f"file:{src.as_posix()}?mode=ro"

    # Capture source schema (CREATE TABLE + CREATE INDEX statements) once.
    with sqlite3.connect(src_uri, uri=True) as src_con:
        table_schemas = {
            name: ddl for (name, ddl) in src_con.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "  AND sql IS NOT NULL"
            ).fetchall()
        }
        index_ddls = [
            ddl for (ddl,) in src_con.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL "
                "ORDER BY tbl_name, name"
            ).fetchall()
        ]

    # URI mode on the dest connection so the subsequent ATTACH can accept
    # the read-only `file:...?mode=ro` URI for the source.
    dest = sqlite3.connect(f"file:{out.as_posix()}", uri=True)
    try:
        # Re-create tables and indexes using the source DDL verbatim.
        for ddl in table_schemas.values():
            dest.execute(ddl)
        for ddl in index_ddls:
            dest.execute(ddl)
        dest.commit()

        # Attach source read-only and copy filtered rows.
        dest.execute(f"ATTACH DATABASE '{src_uri}' AS src")
        total_src, total_dst = 0, 0
        for name, mode in TABLE_PLAN:
            if name not in table_schemas:
                print(f"  {name:<28}  not in source — skipped")
                continue
            if mode == "skip":
                print(f"  {name:<28}  (skip — never copied)")
                continue
            if mode == "news" and not args.with_news:
                print(f"  {name:<28}  (skip — pass --with-news to include)")
                continue
            src_n = int(dest.execute(
                f'SELECT COUNT(*) FROM src."{name}"').fetchone()[0])
            dst_n = _copy_one(dest, name, mode, lo_s, hi_s)
            dest.commit()
            print(f"  {name:<28}  source={src_n:>10,}  copied={dst_n:>10,}  ({mode})")
            total_src += src_n
            total_dst += dst_n
        dest.execute("DETACH DATABASE src")
        dest.commit()
    finally:
        dest.close()

    print()
    print(f"done — {total_dst:,} of {total_src:,} rows copied to {out}")
    sz_mb = out.stat().st_size / (1024 * 1024)
    print(f"       output size: {sz_mb:,.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
