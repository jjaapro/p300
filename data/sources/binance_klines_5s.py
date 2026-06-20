"""Build 5-second BTC SPOT OHLCV from Binance Vision 1s klines.

Binance USDⓈ-M *futures* publishes no 1s klines (1s is a spot-only interval),
so the finest perp candle available is 15m (cd_futures_15m). For sub-minute
work we use SPOT 1s klines — which is also the instrument the TradingView
chart defaults to (and what the discretionary traders we study actually look
at; see data/loaders.py). We download the daily 1s-kline archive and resample
to 5s, writing the SAME schema as cd_futures_15m so the chento research can
load cd_spot_5s with the identical column contract.

Source:
  https://data.binance.vision/data/spot/daily/klines/<SYM>/1s/<SYM>-1s-YYYY-MM-DD.zip

Each daily ZIP holds one CSV with the 12 standard kline columns:
  open_time, open, high, low, close, volume, close_time, quote_volume,
  count, taker_buy_volume, taker_buy_quote_volume, ignore

Timestamp unit gotcha: Binance switched Vision CSV timestamps from
milliseconds to MICROSECONDS on 2025-01-01. Older files may still carry
seconds. We normalise any of s / ms / µs / ns to seconds by magnitude so the
same parser works across the cutover.

5s aggregation (lossless for OHLCV):
  open   = first 1s open in the bucket      high = max 1s high
  close  = last  1s close in the bucket     low  = min 1s low
  volume / quote_volume / taker-buy / count = summed
  *_sell = total − taker-buy        trades_buy/_sell = NULL (not in klines,
  exactly as cd_futures_15m leaves them)

Idempotent: INSERT OR REPLACE on the timestamp PK. Skip-existing per day, so
re-runs only fetch missing days. 404 (archive not yet published) is tolerated
and skipped, matching binance_agg_trades.py.

Usage:
    python data/sources/binance_klines_5s.py --days 365
    python data/sources/binance_klines_5s.py --start 2026-06-04 --end 2026-06-05
"""
from __future__ import annotations

import argparse
import csv
import io
import itertools
import logging
import sqlite3
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, UTC
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from strategies.support import db as _db  # noqa: E402

DB_PATH = _db.PROD_DB
BASE_URL = "https://data.binance.vision/data/spot/daily/klines"
TABLE = "cd_spot_5s"
BAR_SECONDS = 5
BINS_PER_DAY = 86_400 // BAR_SECONDS  # 17_280

log = logging.getLogger("p300.binance_klines_5s")


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    timestamp         INTEGER PRIMARY KEY,
    open              REAL,
    high              REAL,
    low               REAL,
    close             REAL,
    volume            REAL,
    quote_volume      REAL,
    volume_buy        REAL,
    quote_volume_buy  REAL,
    volume_sell       REAL,
    quote_volume_sell REAL,
    total_trades      INTEGER,
    trades_buy        INTEGER,
    trades_sell       INTEGER
);
"""

COLS = [
    "timestamp", "open", "high", "low", "close", "volume", "quote_volume",
    "volume_buy", "quote_volume_buy", "volume_sell", "quote_volume_sell",
    "total_trades", "trades_buy", "trades_sell",
]


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.commit()


def to_seconds(t: int) -> int:
    """Normalise an epoch in s / ms / µs / ns to whole seconds by magnitude.

    Binance Vision timestamps were ms before 2025-01-01 and µs after. A BTC
    epoch is ~1.7e9 s / 1.7e12 ms / 1.7e15 µs / 1.7e18 ns, so the decade
    thresholds below disambiguate cleanly for any plausible date."""
    if t >= 10 ** 17:        # nanoseconds
        return t // 1_000_000_000
    if t >= 10 ** 14:        # microseconds
        return t // 1_000_000
    if t >= 10 ** 11:        # milliseconds
        return t // 1_000
    return t                 # already seconds


def fetch_day_zip(asset: str, day: date, timeout: int = 60) -> bytes:
    """Download one day's 1s-kline ZIP. Raises FileNotFoundError on 404."""
    url = f"{BASE_URL}/{asset}/1s/{asset}-1s-{day.isoformat()}.zip"
    r = requests.get(url, timeout=timeout)
    if r.status_code == 404:
        raise FileNotFoundError(f"No archive for {asset} 1s on {day}: {url}")
    r.raise_for_status()
    return r.content


def parse_and_resample(zip_bytes: bytes, bar_seconds: int = BAR_SECONDS) -> dict[int, dict]:
    """Stream the 1s-kline CSV once and aggregate into `bar_seconds` buckets.

    Returns {bin_unix_seconds: row_dict}. Handles an optional header row and
    files sorted (or not) by open_time."""
    out: dict[int, dict] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        csv_name = z.namelist()[0]
        with z.open(csv_name) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                return out
            try:
                int(first[0])
                rows = itertools.chain([first], reader)  # no header
            except (ValueError, IndexError):
                rows = reader                            # first row was header

            for row in rows:
                if len(row) < 11:
                    continue
                try:
                    ot = to_seconds(int(row[0]))
                    o, h, l, c = (float(row[1]), float(row[2]),
                                  float(row[3]), float(row[4]))
                    vol = float(row[5])
                    qvol = float(row[7])
                    count = int(row[8])
                    tbv = float(row[9])
                    tbqv = float(row[10])
                except (ValueError, IndexError):
                    continue

                b = (ot // bar_seconds) * bar_seconds
                r = out.get(b)
                if r is None:
                    out[b] = {
                        "timestamp": b,
                        "open_ts": ot, "open": o,
                        "high": h, "low": l,
                        "close_ts": ot, "close": c,
                        "volume": vol, "quote_volume": qvol,
                        "volume_buy": tbv, "quote_volume_buy": tbqv,
                        "total_trades": count,
                    }
                else:
                    if ot <= r["open_ts"]:
                        r["open_ts"], r["open"] = ot, o
                    if ot >= r["close_ts"]:
                        r["close_ts"], r["close"] = ot, c
                    if h > r["high"]:
                        r["high"] = h
                    if l < r["low"]:
                        r["low"] = l
                    r["volume"] += vol
                    r["quote_volume"] += qvol
                    r["volume_buy"] += tbv
                    r["quote_volume_buy"] += tbqv
                    r["total_trades"] += count
    return out


def upsert_bins(con: sqlite3.Connection, bins: dict[int, dict]) -> int:
    if not bins:
        return 0
    placeholders = ",".join(["?"] * len(COLS))
    data = [
        (r["timestamp"], r["open"], r["high"], r["low"], r["close"],
         r["volume"], r["quote_volume"],
         r["volume_buy"], r["quote_volume_buy"],
         r["volume"] - r["volume_buy"], r["quote_volume"] - r["quote_volume_buy"],
         r["total_trades"], None, None)
        for r in bins.values()
    ]
    con.executemany(
        f"INSERT OR REPLACE INTO {TABLE} ({','.join(COLS)}) "
        f"VALUES ({placeholders})", data)
    con.commit()
    return len(data)


def _day_is_covered(con: sqlite3.Connection, day: date) -> bool:
    day_start = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp())
    have = con.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE timestamp >= ? AND timestamp < ?",
        (day_start, day_start + 86_400)).fetchone()[0]
    return have >= 0.9 * BINS_PER_DAY


def backfill(asset: str, start: date, end: date, *,
             sleep_between: float = 0.5, skip_existing: bool = True) -> dict:
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(con)
        stats: dict[str, int] = {}
        day = start
        while day <= end:
            day_str = day.isoformat()
            if skip_existing and _day_is_covered(con, day):
                log.info(f"  {day_str}: already covered, skip")
                day += timedelta(days=1)
                continue
            try:
                t0 = time.time()
                zb = fetch_day_zip(asset, day)
                t1 = time.time()
                bins = parse_and_resample(zb)
                n = upsert_bins(con, bins)
                t2 = time.time()
                log.info(f"  {day_str}: {len(zb)/1048576:.1f}MB zip -> {n} 5s bars "
                         f"(fetch {t1-t0:.1f}s, parse+write {t2-t1:.1f}s)")
                stats[day_str] = n
            except FileNotFoundError as e:
                log.warning(f"  {day_str}: archive missing ({e})")
                stats[day_str] = 0
            except Exception as e:  # noqa: BLE001 — log and continue the backfill
                log.error(f"  {day_str}: ERROR {e}")
                stats[day_str] = 0
            if sleep_between > 0:
                time.sleep(sleep_between)
            day += timedelta(days=1)

        total = con.execute(
            f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {TABLE}").fetchone()
        if total[0]:
            log.info(f"\n{TABLE}: {total[0]:,} bars  "
                     f"{datetime.fromtimestamp(total[1], UTC)} -> "
                     f"{datetime.fromtimestamp(total[2], UTC)}")
    finally:
        con.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--asset", default="BTCUSDT", help="Spot symbol (default BTCUSDT)")
    p.add_argument("--start", help="Start date YYYY-MM-DD (overrides --days)")
    p.add_argument("--end", help="End date YYYY-MM-DD inclusive (default: today UTC)")
    p.add_argument("--days", type=int, default=365,
                   help="Days back from --end if --start omitted (default 365)")
    p.add_argument("--sleep", type=float, default=0.5,
                   help="Seconds between requests (default 0.5)")
    p.add_argument("--force", action="store_true", help="Re-fetch covered days")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    end = date.fromisoformat(args.end) if args.end else datetime.now(UTC).date()
    start = (date.fromisoformat(args.start) if args.start
             else end - timedelta(days=args.days))
    log.info(f"backfill {TABLE} {args.asset} {start} -> {end} "
             f"({(end - start).days + 1} days)")
    backfill(args.asset, start, end,
             sleep_between=args.sleep, skip_existing=not args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
