"""Build a sim trader.db from a date-range slice of the live prod.db.

Usage:
    python studies/simulation/build_sim_trader_db.py \\
        --start 2024-01-01 --end 2024-12-31 \\
        --output data/trader_sim_2024.db

The result is a self-contained SQLite that
`python studies/simulation/sim.py` reads via --trader-db <path>. No
external API is touched at sim time — all market data the bot needs
comes from this file.

Every source table is recreated with its own DDL, then rows are copied
according to TABLE_PLAN below. The plan must name every source table:
meeting an unplanned table is an error, so a new feed table can never be
silently left empty in a slice (an empty table looks like "no data in the
window", not "never copied"). REQUIRED_TABLES lists what the production
sleeves read: those must exist in the source, and a loud warning is
printed when one copies zero rows for the window.

Time-column formats (verified, not assumed):
  - btc_1m / eth_1m / op_perp_1m use `open_time` in Unix MILLISECONDS.
  - the cd_* / okx_* / bybit_* / binance_agg_trades_* / tv_* bar tables,
    cd_funding_rate*, ca_long_short_ratio, ca_liquidations, cd_dvol,
    cd_liquidations and cd_open_interest use `timestamp` in Unix SECONDS.
  - scheduled_events / fear_greed_index / cm_daily_metrics use ISO
    'YYYY-MM-DD' dates — small, copied in full.
  - news_headlines uses `published_utc` in Unix SECONDS; opt-in via
    --with-news (AI_QUANT-specific, can be large).
  - bot state (trades, variants, config, ...) and audit logs are never
    copied: the sim ledger starts empty.

The window for date-filtered tables is
``[start - warmup_days, end + 1 day]`` so simulator warmup
(regime classifier 365-day vol percentile, EMA50 weekly, etc.) has
enough lookback. Tiny tables and the calendars copy in full.

Safety: the slice is built in a temporary file next to the destination
and moved into place only after every table copied, so a failed build
never leaves a half-written output — and --output may never name the
source file or the live prod.db (an earlier version deleted whatever
--output pointed at before opening the source).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategies.support import db as _db_mod  # noqa: E402


# Per-table copy mode:
#   "unix_ms"  : filter `open_time` in Unix milliseconds
#   "unix_s"   : filter `timestamp` in Unix seconds
#   "news"     : filter `published_utc` in Unix seconds; opt-in
#   "all"      : full copy (no filter) — small or calendar tables
#   "skip"     : never copy — bot state, audit logs, study-only bulk data
TABLE_PLAN: list[tuple[str, str]] = [
    # 1m klines (open_time in ms) — date filter required.
    ("btc_1m",                 "unix_ms"),
    ("eth_1m",                 "unix_ms"),
    ("op_perp_1m",             "unix_ms"),
    # 15m / 1h bars (timestamp in s) — date filter required.
    ("cd_futures_15m",         "unix_s"),
    ("cd_futures_eth_15m",     "unix_s"),
    ("cd_futures_op_15m",      "unix_s"),
    ("cd_spot_15m",            "unix_s"),
    ("cd_futures_ohlcv",       "unix_s"),
    ("cd_spot_binance",        "unix_s"),
    ("coinbase_spot_1h",       "unix_s"),   # Track D4: (asset, timestamp) in s
    ("paxg_spot_1h",           "unix_s"),   # Track D2: tokenised gold, kline shape
    ("binance_quarterly_1h",   "unix_s"),   # Track D5: (pair, contract_type, timestamp) in s
    ("okx_perp_1h",            "unix_s"),
    ("okx_perp_eth_1h",        "unix_s"),
    ("okx_perp_op_1h",         "unix_s"),
    ("bybit_perp_1h",          "unix_s"),
    ("bybit_perp_eth_1h",      "unix_s"),
    ("bybit_perp_op_1h",       "unix_s"),
    ("binance_agg_trades_1m",  "unix_s"),
    ("binance_agg_trades_5m",  "unix_s"),
    ("binance_agg_trades_15m", "unix_s"),
    ("cm_reference_rate_1h",   "unix_s"),
    ("tv_btc_perp_15m",        "unix_s"),
    ("tv_btc_perp_1h",         "unix_s"),
    # Derivatives / positioning series (timestamp in s).
    ("cd_funding_rate",        "unix_s"),
    ("cd_funding_rate_eth",    "unix_s"),
    ("okx_funding",            "unix_s"),   # Track D6: (inst_id, timestamp) in s
    ("bybit_funding",          "unix_s"),   # Track D6: (symbol,  timestamp) in s
    ("ca_long_short_ratio",    "unix_s"),
    ("ca_liquidations",        "unix_s"),
    # Options chain (Track D1). Marks are per (instrument, timestamp) in s and
    # bulky, so they are windowed; the instrument dictionary has no `timestamp`
    # column at all (expiry_ts / last_seen_ts), so it copies whole.
    ("deribit_options_daily",  "unix_s"),
    ("deribit_options_instruments", "all"),
    # Small or calendar tables — no filter.
    ("cd_dvol",                "all"),
    ("deribit_dvol_daily",     "all"),      # Track D1: 2 assets × daily
    ("macro_daily",            "all"),      # Track D3: TEXT `date`, no unix filter
    ("binance_quarterly_contracts", "all"),  # Track D5: contract dictionary
    ("cd_liquidations",        "all"),
    ("cd_open_interest",       "all"),
    ("scheduled_events",       "all"),
    ("fear_greed_index",       "all"),
    ("cm_daily_metrics",       "all"),
    ("screener_universe",      "all"),
    # AI_QUANT context — opt-in.
    ("news_headlines",         "news"),
    # Study-only bulk data — never copied (cd_spot_5s alone is 6M+ rows).
    ("cd_spot_5s",             "skip"),
    ("screener_klines_1m",     "skip"),
    ("screener_klines_5m",     "skip"),
    ("screener_klines_1h",     "skip"),
    ("screener_klines_daily",  "skip"),
    # Bot state + audit logs — the sim ledger starts empty.
    ("trades",                 "skip"),
    ("trade_adjustments",      "skip"),
    ("variants",               "skip"),
    ("variant_daily_returns",  "skip"),
    ("variant_events",         "skip"),
    ("ai_quant_decisions",     "skip"),
    ("config",                 "skip"),
    ("bot_heartbeats",         "skip"),
    ("fomc_observer",          "skip"),
]

# Tables the production sleeves read at decision time. Missing from the
# source is an error; empty inside the window is a loud warning (a slice
# from before a feed existed is legitimate for sleeves that don't need it).
REQUIRED_TABLES: tuple[str, ...] = (
    "btc_1m", "eth_1m",                        # price_feed, J+ sub-sleeves
    "cd_futures_ohlcv", "cd_spot_binance",     # adx, carry, regime
    "cd_funding_rate", "cd_funding_rate_eth",  # funding accrual
    "ca_long_short_ratio",                     # cpr, chento B5
    "cd_futures_15m", "cd_spot_15m",           # short_squeeze, chento_v3, chento_limit_bid
    "cd_futures_eth_15m",                      # chento_v3 ETH
    "okx_perp_1h", "okx_perp_eth_1h",          # chento_v3 OKX gate
    "cd_open_interest",                        # chento_limit_bid
    "scheduled_events", "fear_greed_index",    # thu_bear V4, fomc
)


def _parse_iso_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _same_file(a: Path, b: Path) -> bool:
    if a == b:
        return True
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False


def _output_problem(src: Path, out: Path) -> str | None:
    """Why `out` must not be written, or None. Checked before anything is
    deleted or opened."""
    if _same_file(out, src):
        return f"--output is the source DB ({src}); refusing to overwrite it"
    prod = Path(_db_mod.PROD_DB).resolve()
    if _same_file(out, prod):
        return f"--output is the live prod.db ({prod}); refusing"
    return None


def _column_list(con: sqlite3.Connection, name: str) -> str:
    """`"a", "b", ...` for the columns that can actually be written.

    `SELECT *` includes GENERATED columns but INSERT rejects them, so a table
    with one (binance_quarterly_1h.series) fails with "N columns but N+1
    values". PRAGMA table_xinfo's last field is 0 for a real column and 2/3
    for a VIRTUAL/STORED generated one; table_info would not list them at all.
    """
    cols = [r[1] for r in con.execute(f'PRAGMA table_xinfo("{name}")') if not r[6]]
    return ", ".join(f'"{c}"' for c in cols)


def _copy_one(dest: sqlite3.Connection, name: str, mode: str,
              lo_s: int, hi_s: int) -> int:
    """Run the INSERT for one table. Returns rowcount."""
    cols = _column_list(dest, name)
    head = f'INSERT INTO "{name}" ({cols}) SELECT {cols} FROM src."{name}"'
    if mode == "all":
        sql, params = head, ()
    elif mode == "unix_ms":
        sql = f'{head} WHERE open_time >= ? AND open_time <= ?'
        params = (lo_s * 1000, hi_s * 1000)
    elif mode == "unix_s":
        sql = f'{head} WHERE timestamp >= ? AND timestamp <= ?'
        params = (lo_s, hi_s)
    elif mode == "news":
        sql = f'{head} WHERE published_utc >= ? AND published_utc <= ?'
        params = (lo_s, hi_s)
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    return int(dest.execute(sql, params).rowcount)


def _read_schema(src_uri: str) -> tuple[dict[str, str], list[str]]:
    """(table name -> CREATE TABLE ddl, [CREATE INDEX ddl, ...])."""
    con = sqlite3.connect(src_uri, uri=True)
    try:
        table_schemas = {
            name: ddl for (name, ddl) in con.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "  AND sql IS NOT NULL"
            ).fetchall()
        }
        index_ddls = [
            ddl for (ddl,) in con.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND sql IS NOT NULL "
                "ORDER BY tbl_name, name"
            ).fetchall()
        ]
    finally:
        con.close()
    return table_schemas, index_ddls


def _build(dest_path: Path, src_uri: str, table_schemas: dict[str, str],
           index_ddls: list[str], lo_s: int, hi_s: int,
           with_news: bool) -> tuple[int, int, list[str]]:
    """Create every table + index in `dest_path` and copy rows per
    TABLE_PLAN. Returns (rows in source, rows copied, required tables that
    copied nothing)."""
    # URI mode on the dest connection so the subsequent ATTACH can accept
    # the read-only `file:...?mode=ro` URI for the source.
    dest = sqlite3.connect(f"file:{dest_path.as_posix()}", uri=True)
    total_src = total_dst = 0
    empty_required: list[str] = []
    try:
        for ddl in table_schemas.values():
            dest.execute(ddl)
        for ddl in index_ddls:
            dest.execute(ddl)
        dest.commit()

        dest.execute(f"ATTACH DATABASE '{src_uri}' AS src")
        for name, mode in TABLE_PLAN:
            if name not in table_schemas:
                print(f"  {name:<28}  not in source — skipped")
                continue
            if mode == "skip":
                print(f"  {name:<28}  (skip — never copied)")
                continue
            if mode == "news" and not with_news:
                print(f"  {name:<28}  (skip — pass --with-news to include)")
                continue
            src_n = int(dest.execute(
                f'SELECT COUNT(*) FROM src."{name}"').fetchone()[0])
            dst_n = _copy_one(dest, name, mode, lo_s, hi_s)
            dest.commit()
            print(f"  {name:<28}  source={src_n:>10,}  copied={dst_n:>10,}  ({mode})")
            if dst_n == 0 and name in REQUIRED_TABLES:
                empty_required.append(name)
            total_src += src_n
            total_dst += dst_n
        dest.execute("DETACH DATABASE src")
        dest.commit()
    finally:
        dest.close()
    return total_src, total_dst, empty_required


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=_parse_iso_date, required=True,
                    help="Sim window start (UTC). Format YYYY-MM-DD.")
    p.add_argument("--end", type=_parse_iso_date, required=True,
                    help="Sim window end (UTC, inclusive). Format YYYY-MM-DD.")
    p.add_argument("--source", default="data/databases/prod.db",
                    help="Source DB (read-only). Default "
                         "data/databases/prod.db (the consolidated DB after "
                         "the 2026-05-16 reorg; pre-P2.6 callers may still "
                         "pass data/trader.db, the schema is unchanged for "
                         "the market-data tables this script reads).")
    p.add_argument("--output", required=True,
                    help="Destination sim trader.db. Overwritten if exists "
                         "(never the source or the live prod.db).")
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
    problem = _output_problem(src, out)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

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
    table_schemas, index_ddls = _read_schema(src_uri)

    planned = {name for name, _ in TABLE_PLAN}
    unknown = sorted(set(table_schemas) - planned)
    if unknown:
        print("error: source tables with no TABLE_PLAN entry (add a copy mode "
              "for each so the slice stays complete): " + ", ".join(unknown),
              file=sys.stderr)
        return 2
    missing = [t for t in REQUIRED_TABLES if t not in table_schemas]
    if missing:
        print("error: source is missing tables the production sleeves read: "
              + ", ".join(missing), file=sys.stderr)
        return 2

    # Build into a sibling temp file; swap it in only after a complete copy.
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".building")
    tmp.unlink(missing_ok=True)
    try:
        total_src, total_dst, empty_required = _build(
            tmp, src_uri, table_schemas, index_ddls, lo_s, hi_s, args.with_news)
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    print()
    for name in empty_required:
        print(f"WARNING: required table {name} has no rows in the window — "
              f"sleeves that read it will see no data in this sim")
    print(f"done — {total_dst:,} of {total_src:,} rows copied to {out}")
    sz_mb = out.stat().st_size / (1024 * 1024)
    print(f"       output size: {sz_mb:,.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
