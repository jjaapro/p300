"""One-shot: rebuild prod.db tables that were created without PRIMARY KEY
constraints.

Background — every table-creation site in the codebase (bootstrap.py,
data/sources/binance.py, data/sources/coindesk.py, data/sources/news.py,
strategies/support/trade_db.py, fetch_coinalyze.py, fetch_events.py,
strategies/sleeves/fomc/signal.py, strategies/support/variant_registry.py)
defines the table with `PRIMARY KEY` or `UNIQUE` constraints. But these all
use `CREATE TABLE IF NOT EXISTS`, which is a no-op once the table exists.
prod.db's tables were created before those CREATE statements gained the
constraints, so they have no PK. That makes `INSERT OR REPLACE` degrade to
plain INSERT, and repeated backfills accumulate duplicates.

The chart-rendering crash (ai_quant 2026-05-18 night, ValueError "cannot
reindex on an axis with duplicate labels") was the visible symptom; the
silent damage is double-counted volume / mis-weighted indicators across
every consumer that aggregates these tables.

This migration:
  1. Backs up prod.db.
  2. For each table, rebuilds it inside a transaction:
       CREATE __new with the canonical PK from the codebase,
       INSERT … SELECT MAX(rowid) GROUP BY <natural-key> (keep-latest),
       DROP old, RENAME __new -> old.
  3. VACUUMs.

Run with bot.py stopped; opens the DB exclusively.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from strategies.support import db


@dataclass(frozen=True)
class Rebuild:
    table: str
    natural_key: tuple[str, ...]
    create_sql: str          # the new table body — name will be __new_<table>
    post_indexes: tuple[str, ...] = ()


# Schemas mirror the source-of-truth CREATE statements in the codebase.
# The name of the new table is parameterised below ({new}).
PLAN: list[Rebuild] = [
    Rebuild(
        table="btc_1m",
        natural_key=("open_time",),
        create_sql="""
            CREATE TABLE {new} (
                open_time INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, num_trades INTEGER
            )
        """,
    ),
    Rebuild(
        table="eth_1m",
        natural_key=("open_time",),
        create_sql="""
            CREATE TABLE {new} (
                open_time INTEGER PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL,
                volume REAL, num_trades INTEGER
            )
        """,
    ),
    Rebuild(
        table="cd_funding_rate",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY,
                fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL
            )
        """,
    ),
    Rebuild(
        table="cd_funding_rate_eth",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY,
                fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL
            )
        """,
    ),
    Rebuild(
        table="cd_futures_ohlcv",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL,
                volume REAL, quote_volume REAL,
                volume_buy REAL, quote_volume_buy REAL,
                volume_sell REAL, quote_volume_sell REAL,
                total_trades INTEGER, trades_buy INTEGER, trades_sell INTEGER
            )
        """,
    ),
    Rebuild(
        table="cd_spot_binance",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL,
                volume REAL, quote_volume REAL,
                volume_buy REAL, quote_volume_buy REAL,
                volume_sell REAL, quote_volume_sell REAL,
                total_trades INTEGER, trades_buy INTEGER, trades_sell INTEGER
            )
        """,
    ),
    Rebuild(
        table="cd_open_interest",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY,
                oi_open REAL, oi_high REAL, oi_low REAL, oi_close REAL,
                oi_value_open REAL, oi_value_high REAL, oi_value_low REAL, oi_value_close REAL
            )
        """,
    ),
    Rebuild(
        table="cd_liquidations",
        natural_key=("timestamp",),
        create_sql="""
            CREATE TABLE {new} (
                timestamp INTEGER PRIMARY KEY,
                long_quantity REAL,
                short_quantity REAL,
                long_quote_quantity REAL,
                short_quote_quantity REAL,
                long_count INTEGER,
                short_count INTEGER,
                vwap_long_price REAL,
                vwap_short_price REAL
            )
        """,
    ),
    Rebuild(
        table="cd_dvol",
        natural_key=("asset", "timestamp"),
        create_sql="""
            CREATE TABLE {new} (
                asset TEXT, timestamp INTEGER,
                open REAL, high REAL, low REAL, close REAL,
                PRIMARY KEY (asset, timestamp)
            )
        """,
    ),
    Rebuild(
        table="ca_long_short_ratio",
        natural_key=("asset", "timestamp"),
        create_sql="""
            CREATE TABLE {new} (
                asset TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                ratio REAL,
                long_pct REAL,
                short_pct REAL,
                UNIQUE (asset, timestamp)
            )
        """,
    ),
    Rebuild(
        table="news_headlines",
        natural_key=("url_hash",),
        create_sql="""
            CREATE TABLE {new} (
                url_hash TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                published_utc INTEGER NOT NULL,
                fetched_utc INTEGER NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                asset_tag TEXT,
                importance INTEGER NOT NULL DEFAULT 0
            )
        """,
        post_indexes=(
            "CREATE INDEX idx_news_asset ON news_headlines(asset_tag, published_utc)",
            "CREATE INDEX idx_news_published ON news_headlines(published_utc)",
        ),
    ),
    Rebuild(
        table="config",
        natural_key=("key",),
        create_sql="""
            CREATE TABLE {new} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """,
    ),
    Rebuild(
        table="scheduled_events",
        natural_key=("date", "event_type"),
        create_sql="""
            CREATE TABLE {new} (
                date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                description TEXT,
                PRIMARY KEY (date, event_type)
            )
        """,
    ),
    Rebuild(
        table="fomc_observer",
        natural_key=("fomc_date",),
        create_sql="""
            CREATE TABLE {new} (
                fomc_date TEXT PRIMARY KEY,
                announcement_utc TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                target_rate_pct REAL,
                phase TEXT,
                expected_action TEXT,
                expected_action_meta TEXT,
                fear_greed INTEGER,
                fear_greed_bucket TEXT,
                entry_planned_utc TEXT,
                exit_planned_utc TEXT,
                entry_price REAL,
                exit_price REAL,
                return_pct REAL,
                recorded_at TEXT
            )
        """,
    ),
    Rebuild(
        table="trades",
        natural_key=("id",),
        create_sql="""
            CREATE TABLE {new} (
                id TEXT PRIMARY KEY,
                series TEXT, asset TEXT, direction TEXT,
                strategy TEXT, regime TEXT, allocation_pct REAL, leverage REAL,
                entry_time TEXT, exit_time TEXT, actual_entry_time TEXT,
                actual_exit_time TEXT, entry_price REAL, exit_price REAL,
                size_usdt REAL, qty REAL, pnl_usdt REAL, pnl_pct REAL,
                status TEXT, venue TEXT, order_ids TEXT, execution_mode TEXT,
                strategy_variant TEXT, resolution TEXT, notes TEXT, created_at TEXT,
                parent_position_id TEXT, current_qty REAL, current_leverage REAL,
                current_size_usdt REAL, realized_pnl_usdt REAL, avg_entry_price REAL,
                ai_quant_decision_id INTEGER
            )
        """,
        post_indexes=(
            "CREATE INDEX idx_trades_variant_strategy "
            "ON trades(strategy_variant, strategy, status)",
        ),
    ),
    Rebuild(
        table="trade_adjustments",
        natural_key=("id",),
        create_sql="""
            CREATE TABLE {new} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_time TEXT NOT NULL,
                event_date TEXT NOT NULL,
                qty_delta REAL,
                qty_after REAL,
                leverage_before REAL,
                leverage_after REAL,
                margin_delta_usdt REAL,
                size_usdt_after REAL,
                price REAL,
                fee_usdt REAL,
                realized_pnl_delta_usdt REAL,
                notes_json TEXT,
                UNIQUE (trade_id, seq)
            )
        """,
        post_indexes=(
            "CREATE INDEX idx_adj_date ON trade_adjustments(event_date, event_type)",
            "CREATE INDEX idx_adj_trade ON trade_adjustments(trade_id, seq)",
        ),
    ),
    Rebuild(
        table="ai_quant_decisions",
        natural_key=("id",),
        create_sql="""
            CREATE TABLE {new} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_utc INTEGER NOT NULL,
                decision_date TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                decided TEXT NOT NULL,
                conviction INTEGER,
                time_horizon_days INTEGER,
                key_drivers_json TEXT,
                exit_conditions TEXT,
                confidence_caveats TEXT,
                rationale_md TEXT,
                context_json TEXT,
                tool_calls_json TEXT,
                model_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                cost_usd REAL,
                turns INTEGER,
                trade_action TEXT,
                error TEXT,
                created_at TEXT,
                defer_until_utc INTEGER
            )
        """,
        post_indexes=(
            "CREATE INDEX idx_ai_quant_variant_date "
            "ON ai_quant_decisions(variant_id, decision_date)",
        ),
    ),
    Rebuild(
        table="variant_events",
        natural_key=("id",),
        create_sql="""
            CREATE TABLE {new} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT,
                summary TEXT
            )
        """,
        post_indexes=(
            "CREATE INDEX idx_variant_events_variant "
            "ON variant_events(variant_id, timestamp)",
            "CREATE INDEX idx_variant_events_ts ON variant_events(timestamp)",
        ),
    ),
    Rebuild(
        table="variant_daily_returns",
        natural_key=("variant_id", "date"),
        create_sql="""
            CREATE TABLE {new} (
                variant_id TEXT NOT NULL,
                date TEXT NOT NULL,
                return_1x_pct REAL NOT NULL,
                source TEXT NOT NULL,
                regime TEXT,
                created_at TEXT,
                PRIMARY KEY (variant_id, date)
            )
        """,
    ),
]


def _rebuild_one(con: sqlite3.Connection, r: Rebuild) -> tuple[int, int, int]:
    """Rebuild a single table. Returns (rows_before, rows_after, dropped)."""
    tmp = f"__new_{r.table}"
    nk = ", ".join(f'"{c}"' for c in r.natural_key)
    rows_before = con.execute(f'SELECT COUNT(*) FROM "{r.table}"').fetchone()[0]
    columns_old = [c[1] for c in con.execute(f'PRAGMA table_info("{r.table}")').fetchall()]

    con.execute("BEGIN")
    try:
        con.execute(r.create_sql.format(new=tmp))
        columns_new = [c[1] for c in con.execute(f'PRAGMA table_info("{tmp}")').fetchall()]
        # Use the intersection so we don't fail on column-set drift; new-only
        # columns will pick up their DEFAULTs.
        shared = [c for c in columns_new if c in columns_old]
        cols_sql = ", ".join(f'"{c}"' for c in shared)
        con.execute(
            f'INSERT INTO "{tmp}" ({cols_sql}) '
            f'SELECT {cols_sql} FROM "{r.table}" '
            f'WHERE rowid IN (SELECT MAX(rowid) FROM "{r.table}" GROUP BY {nk})'
        )
        rows_after = con.execute(f'SELECT COUNT(*) FROM "{tmp}"').fetchone()[0]
        con.execute(f'DROP TABLE "{r.table}"')
        con.execute(f'ALTER TABLE "{tmp}" RENAME TO "{r.table}"')
        for idx_sql in r.post_indexes:
            con.execute(idx_sql)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return rows_before, rows_after, rows_before - rows_after


def main() -> int:
    src = Path(db.PROD_DB)
    backup = src.with_suffix(".db.bak-2026-05-18-pk-rebuild")
    if backup.exists():
        print(f"refusing to overwrite existing backup: {backup}", file=sys.stderr)
        return 2
    print(f"backing up {src} -> {backup}")
    shutil.copy2(src, backup)

    con = sqlite3.connect(str(src))
    try:
        print(f"{'TABLE':<26} {'BEFORE':>10} {'AFTER':>10} {'DROPPED':>10}")
        print("-" * 60)
        totals_before = totals_after = 0
        for r in PLAN:
            try:
                b, a, d = _rebuild_one(con, r)
            except sqlite3.IntegrityError as e:
                print(f"\nFAILED on {r.table}: {e}", file=sys.stderr)
                print(f"backup remains at {backup}", file=sys.stderr)
                return 3
            print(f"{r.table:<26} {b:>10} {a:>10} {d:>10}")
            totals_before += b
            totals_after += a

        print("-" * 60)
        print(f"{'TOTAL':<26} {totals_before:>10} {totals_after:>10} "
              f"{totals_before - totals_after:>10}")

        print("\nVACUUM...")
        con.isolation_level = None  # VACUUM cannot run inside a transaction
        con.execute("VACUUM")
    finally:
        con.close()

    print(f"\nDone. Backup retained at {backup} - delete after smoke-test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
