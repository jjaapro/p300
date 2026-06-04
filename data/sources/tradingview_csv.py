"""Import TradingView manual CSV exports of BTC perp with chento-relevant
chart studies (liquidations, funding, OI, top-trader LSR).

Source data: User exports from TradingView Premium with these chart studies
attached:
  - BINANCE:BTCUSDT.P 15m OHLCV
  - Long / Short Liquidations
  - Long/Short Ratio (Accounts)
  - Funding Rate
  - Crypto Open Interest OHLC
  - Top Traders Long/Short Accounts %
  - Top Traders Long/Short Positions %

Expected file naming: `BINANCE_BTCUSDT.P, 15_<hash>.csv` (TV's default).

Schema (single table for now since all files are BTC BINANCE perp):
    tv_btc_perp_15m(
        timestamp INTEGER PRIMARY KEY,  -- unix seconds (bar open time)
        open, high, low, close, volume REAL,
        long_liq, short_liq REAL,                   -- BTC notional per bar
        ls_ratio_accounts REAL,
        funding_rate REAL,
        oi_open, oi_high, oi_low, oi_close REAL,
        long_accounts_pct, short_accounts_pct REAL,
        top_long_accounts_pct, top_short_accounts_pct, top_ls_ratio_accounts REAL,
        top_long_positions_pct, top_short_positions_pct, top_ls_ratio_positions REAL,
        source_file TEXT                            -- which CSV provided the row
    )

Idempotent via INSERT OR REPLACE on timestamp. Last-write-wins between
overlapping files (so the file with the longest history is processed first).

Usage:
    python data/sources/tradingview_csv.py            # ingest all CSVs in default dir
    python data/sources/tradingview_csv.py --file path/to/single.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "archive" / "tradingview"

log = logging.getLogger("p300.tv_csv")


# Map TV CSV filename token (the number after the comma, e.g. "15" / "60")
# to the destination table. Same column schema; differ only in bar period.
RESOLUTION_TABLES = {
    "15": "tv_btc_perp_15m",
    "60": "tv_btc_perp_1h",
}

_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS {table} (
    timestamp                  INTEGER PRIMARY KEY,
    open                       REAL,
    high                       REAL,
    low                        REAL,
    close                      REAL,
    volume                     REAL,
    long_liq                   REAL,
    short_liq                  REAL,
    ls_ratio_accounts          REAL,
    funding_rate               REAL,
    oi_open                    REAL,
    oi_high                    REAL,
    oi_low                     REAL,
    oi_close                   REAL,
    long_accounts_pct          REAL,
    short_accounts_pct         REAL,
    top_long_accounts_pct      REAL,
    top_short_accounts_pct     REAL,
    top_ls_ratio_accounts      REAL,
    top_long_positions_pct     REAL,
    top_short_positions_pct    REAL,
    top_ls_ratio_positions     REAL,
    source_file                TEXT
);
CREATE INDEX IF NOT EXISTS ix_{table}_ts ON {table}(timestamp);
"""


def ensure_schema(con: sqlite3.Connection, table: str) -> None:
    con.executescript(_SCHEMA_TEMPLATE.format(table=table))
    con.commit()


def detect_table(path: Path) -> str:
    """Infer the destination table from the TV CSV filename.

    TV exports files like 'BINANCE_BTCUSDT.P, <resolution>_<hash>.csv', where
    resolution is the number of minutes per bar (15, 60, 240, ...). We split
    on the comma and pull the integer before the underscore.
    """
    stem = path.stem
    if "," in stem:
        right = stem.split(",", 1)[1].strip()
        token = right.split("_", 1)[0].strip()
        if token in RESOLUTION_TABLES:
            return RESOLUTION_TABLES[token]
    raise ValueError(
        f"Cannot detect resolution from filename {path.name!r}. "
        f"Expected '<symbol>, <resolution>_<hash>.csv' with resolution in "
        f"{list(RESOLUTION_TABLES)}."
    )


# Mapping from TV CSV column header (normalized) to DB column.
# TV exports the column headers exactly as the indicator pane labels them,
# including the typo "Crypto Open Interest (High" (missing closing paren).
HEADER_MAP = {
    "time": "timestamp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "long liquidations": "long_liq",
    "short liquidations": "short_liq",
    "long/short ratio accounts": "ls_ratio_accounts",
    "funding rate": "funding_rate",
    "crypto open interest (open)": "oi_open",
    "crypto open interest (high": "oi_high",   # typo preserved
    "crypto open interest (high)": "oi_high",  # in case TV fixes it
    "crypto open interest (low)": "oi_low",
    "crypto open interest (close)": "oi_close",
    "long accounts, %": "long_accounts_pct",
    "short accounts, %": "short_accounts_pct",
    "top traders long accounts %": "top_long_accounts_pct",
    "top traders short accounts %": "top_short_accounts_pct",
    "top traders long/short ratio accounts": "top_ls_ratio_accounts",
    "top traders long positions %": "top_long_positions_pct",
    "top traders short positions %": "top_short_positions_pct",
    "top traders long/short ratio positions": "top_ls_ratio_positions",
}


def _norm(h: str) -> str:
    return h.strip().lower().replace('"', '').strip()


def _to_float_or_none(s: str):
    s = s.strip()
    if s == "" or s.upper() in ("NAN", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def import_csv(path: Path, con: sqlite3.Connection,
                table: str | None = None) -> tuple[int, int, str]:
    """Import a single TV CSV. Returns (rows_seen, rows_upserted, table)."""
    if table is None:
        table = detect_table(path)
    ensure_schema(con, table)
    log.info(f"Reading {path.name} -> {table}")
    with path.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        normalized = [_norm(h) for h in header]
        # Build map: csv-column-index -> db-column-name (None if unknown)
        col_to_db = []
        unknown_cols = []
        for i, h in enumerate(normalized):
            db_col = HEADER_MAP.get(h)
            col_to_db.append(db_col)
            if db_col is None:
                unknown_cols.append((i, header[i]))
        if unknown_cols:
            for i, h in unknown_cols:
                log.warning(f"  unmapped CSV col {i} '{h}' — skipping")

        # Active DB columns + always include source_file
        seen = 0; upserted = 0
        cur = con.cursor()
        for row in reader:
            seen += 1
            db_row = {"source_file": path.name}
            for i, db_col in enumerate(col_to_db):
                if db_col is None:
                    continue
                v = row[i] if i < len(row) else ""
                if db_col == "timestamp":
                    db_row["timestamp"] = int(v)
                else:
                    db_row[db_col] = _to_float_or_none(v)
            if "timestamp" not in db_row:
                continue
            cols = list(db_row.keys())
            placeholders = ",".join(["?"] * len(cols))
            cur.execute(
                f"INSERT OR REPLACE INTO {table} "
                f"({','.join(cols)}) VALUES ({placeholders})",
                [db_row[c] for c in cols],
            )
            upserted += 1
        con.commit()
    return seen, upserted, table


def import_all(directory: Path) -> dict[str, tuple[int, int, str]]:
    """Import all CSVs in `directory`, routing per-file to the table implied
    by the CSV's resolution (15m -> tv_btc_perp_15m, 1h -> tv_btc_perp_1h).
    Within each table, processes largest files first so overlap is dominated
    by the longest history; shorter/newer exports overwrite trailing bars."""
    files = sorted(directory.glob("*.csv"))
    if not files:
        log.warning(f"No CSVs found in {directory}")
        return {}
    # Sort largest-first for last-write-wins on the trailing edges
    files.sort(key=lambda p: p.stat().st_size, reverse=True)

    con = sqlite3.connect(str(DB_PATH))
    try:
        out: dict[str, tuple[int, int, str]] = {}
        for f in files:
            try:
                table = detect_table(f)
            except ValueError as e:
                log.warning(f"  skipping {f.name}: {e}")
                continue
            seen, ups, _ = import_csv(f, con, table=table)
            log.info(f"  {f.name}: {seen:,} read, {ups:,} upserted -> {table}")
            out[f.name] = (seen, ups, table)
        # Final summary per table
        import datetime as dt
        for table in sorted(set(RESOLUTION_TABLES.values())):
            row = con.execute(
                f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}"
            ).fetchone()
            if row and row[0]:
                total, mn, mx = row
                log.info(
                    f"\n{table} total: {total:,} unique bars  "
                    f"({dt.datetime.fromtimestamp(mn, dt.UTC).isoformat()[:16]}"
                    f" -> {dt.datetime.fromtimestamp(mx, dt.UTC).isoformat()[:16]})"
                )
    finally:
        con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dir", default=str(DEFAULT_DIR),
                   help=f"Directory of TV CSVs (default: {DEFAULT_DIR})")
    p.add_argument("--file", default=None,
                   help="Import a single CSV instead of the whole directory")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if args.file:
        con = sqlite3.connect(str(DB_PATH))
        try:
            seen, ups, table = import_csv(Path(args.file), con)
            log.info(f"{args.file}: {seen:,} read, {ups:,} upserted -> {table}")
        finally:
            con.close()
    else:
        import_all(Path(args.dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
