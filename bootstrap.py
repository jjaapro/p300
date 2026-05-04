"""One-shot bootstrap: build a fresh data/trader.db from scratch.

What it does, in order:
  1. Create data/trader.db with all required schemas if missing.
  2. Rebuild scheduled_events from fetch_events.py (FOMC/CPI/NFP/OPEX —
     pure Python, no external data).
  3. Fetch ca_long_short_ratio history from Coinalyze (~5 years from
     2021-01-01). Requires COINALYZE_API_KEY in the environment.
  4. Backfill funding rate history (BTC + ETH) from Binance.
  5. Backfill klines (btc_1m, eth_1m, cd_futures_ohlcv, cd_spot_binance)
     from Binance. Slow — ~30-60 minutes for 5 years.

Idempotent: re-running picks up where the last run left off.

Examples:
  export COINALYZE_API_KEY=...              # https://coinalyze.net/
  python bootstrap.py                       # full bootstrap, since 2020-01-01
  python bootstrap.py --since 2024-01-01    # shorter history (faster)
  python bootstrap.py --skip-klines         # everything except the slow part
  python bootstrap.py --skip-coinalyze      # if you don't have a key yet
                                            # (CPR will be dormant ~6 months
                                            # while binance_feed accumulates)

After this:
  python register_p300.py    # register the variant in dashboard.db
  python health.py           # confirm everything is wired up
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
DB_PATH = REPO / "data" / "trader.db"

# Schemas for tables this repo owns end-to-end. Each fetcher (fetch_events,
# fetch_coinalyze, binance_feed) creates the tables it writes to via
# CREATE TABLE IF NOT EXISTS, so this central list is for the kline +
# funding tables that don't have a single owning fetcher script.
SCHEMAS: dict[str, str] = {
    "cd_futures_ohlcv": """
        CREATE TABLE IF NOT EXISTS cd_futures_ohlcv (
            timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL,
            volume REAL, quote_volume REAL,
            volume_buy REAL, quote_volume_buy REAL,
            volume_sell REAL, quote_volume_sell REAL,
            total_trades INTEGER, trades_buy INTEGER, trades_sell INTEGER
        )
    """,
    "cd_spot_binance": """
        CREATE TABLE IF NOT EXISTS cd_spot_binance (
            timestamp INTEGER PRIMARY KEY, open REAL, high REAL, low REAL, close REAL,
            volume REAL, quote_volume REAL,
            volume_buy REAL, quote_volume_buy REAL,
            volume_sell REAL, quote_volume_sell REAL,
            total_trades INTEGER, trades_buy INTEGER, trades_sell INTEGER
        )
    """,
    "btc_1m": """
        CREATE TABLE IF NOT EXISTS btc_1m (
            open_time INTEGER PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, num_trades INTEGER
        )
    """,
    "eth_1m": """
        CREATE TABLE IF NOT EXISTS eth_1m (
            open_time INTEGER PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, num_trades INTEGER
        )
    """,
    "cd_funding_rate": """
        CREATE TABLE IF NOT EXISTS cd_funding_rate (
            timestamp INTEGER PRIMARY KEY,
            fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL
        )
    """,
    "cd_funding_rate_eth": """
        CREATE TABLE IF NOT EXISTS cd_funding_rate_eth (
            timestamp INTEGER PRIMARY KEY,
            fr_open REAL, fr_high REAL, fr_low REAL, fr_close REAL
        )
    """,
}


def ensure_db_and_schemas() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        for ddl in SCHEMAS.values():
            con.execute(ddl)
        con.commit()
    finally:
        con.close()


def rebuild_calendar() -> None:
    print("\n=== Rebuilding scheduled_events ===")
    import fetch_events
    counts = fetch_events.rebuild()
    for k, n in counts.items():
        print(f"  {k:<16} {n:>4} rows")


def fetch_lsr_history(skip: bool) -> None:
    print("\n=== Fetching ca_long_short_ratio history (Coinalyze) ===")
    if skip:
        print("  skipped (--skip-coinalyze)")
        print("  WARNING: CPR sleeve and regime LS circuit breaker need ≥210d "
              "of LS history. Without this fetch, you'll be dormant for ~6 "
              "months while binance_feed accumulates the rolling 30d window.")
        return
    import fetch_coinalyze
    try:
        fetch_coinalyze.fetch_lsr()
    except fetch_coinalyze.MissingApiKey as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        print("  Re-run with --skip-coinalyze if you want to proceed without it.",
              file=sys.stderr)
        sys.exit(2)


def backfill_from_binance(since: str, skip_klines: bool) -> None:
    import binance_feed

    print(f"\n=== Backfilling funding (since {since}) ===")
    for sym, tbl in [("BTCUSDT", "cd_funding_rate"),
                     ("ETHUSDT", "cd_funding_rate_eth")]:
        n = binance_feed.backfill_funding_rate(sym, tbl, since=since)
        print(f"  {tbl}: {n:,} rows")

    if skip_klines:
        print("\n=== Skipping kline backfill (--skip-klines) ===")
        return
    print(f"\n=== Backfilling klines (since {since}) — this is the slow part ===")
    res = binance_feed.backfill_all_klines(since=since)
    for tbl, n in res.items():
        print(f"  {tbl}: {n:,} rows")


def fetch_fomc_sleeve_inputs() -> None:
    """Daily-cadence external feeds the FOMC observer sleeve needs:
      - Crypto Fear & Greed Index (alternative.me, free, no auth)
      - NY Fed Reference Rates XML (Fed Funds target band)
      - Polymarket "How many Fed rate cuts in 2026" market

    Each is best-effort; failure is logged but doesn't abort bootstrap. The
    binance_feed loop also refreshes them daily, so a transient miss here
    self-heals on the next live tick."""
    print("\n=== Fetching FOMC sleeve inputs ===")
    from services import sentiment_index_service, fed_funds_service, polymarket_service, fomc_service
    fomc_service.init_schema()
    print("  fomc_observer table ensured")
    print(f"  fear_greed:    {'ok' if sentiment_index_service.refresh() else 'FAILED'}")
    print(f"  fed_funds:     {'ok' if fed_funds_service.refresh_xml() else 'FAILED'}")
    print(f"  polymarket:    {'ok' if polymarket_service.refresh() else 'FAILED'}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2020-01-01",
                    help="Backfill start date for klines/funding (UTC, "
                         "YYYY-MM-DD). Default 2020-01-01.")
    ap.add_argument("--skip-klines", action="store_true",
                    help="Skip the slow kline backfill (run "
                         "`binance_feed.py --backfill-klines` later if needed).")
    ap.add_argument("--skip-coinalyze", action="store_true",
                    help="Skip Coinalyze LSR fetch (no API key required, "
                         "but CPR will be dormant until binance_feed accumulates "
                         "≥210 days of rolling 30d data).")
    ap.add_argument("--skip-binance", action="store_true",
                    help="Skip ALL Binance backfills (calendar + LSR only).")
    args = ap.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

    print(f"Bootstrap target: {DB_PATH}")
    ensure_db_and_schemas()
    print("  schemas ensured")
    rebuild_calendar()
    fetch_lsr_history(skip=args.skip_coinalyze)
    if not args.skip_binance:
        backfill_from_binance(since=args.since, skip_klines=args.skip_klines)
    fetch_fomc_sleeve_inputs()
    print("\nBootstrap complete. Next:")
    print("  python register_p300.py    # register the variant in dashboard.db")
    print("  python health.py           # confirm everything is wired up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
