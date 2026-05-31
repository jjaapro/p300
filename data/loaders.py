"""Data loaders for the J+ port, clock-bounded to avoid look-ahead.

All loaders respect `strategies.support.clock.now_utc()`:
  - Live mode (clock == real now) → returns everything through most recent row.
  - Simulated mode (clock == T) → returns rows with timestamp strictly ≤ T.

No loader has any way to read future bars. If you add one, keep this property.

Data sources (p300/data/trader.db):
  cd_spot_binance      BTC spot hourly OHLCV (timestamp in seconds) — primary
  cd_futures_ohlcv     BTC perp hourly OHLCV (kept for reference, not used here)
  btc_1m               BTC spot 1m (not used here)
  eth_1m               ETH spot 1m (aggregated to hourly on demand)
  ca_long_short_ratio  daily long% for BTC

History note: as of 2026-05-01 the BTC hourly load reads cd_spot_binance
instead of cd_futures_ohlcv. Reason: TradingView's BTCUSDT 1D defaults to
spot, so the bot's signal source now matches both the chart traders look
at and the price_feed used for execution (which already reads btc_1m spot).
The R4 BTC windowed-return numbers shift slightly because spot/perp 1h
closes can differ by a few bp, but the strategy logic is unchanged.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from strategies.support import clock
from strategies.support import db

# ─── BTC hourly (perp) ───────────────────────────────────────────────────────

def load_btc_hourly() -> list[tuple[int, float, float, float, float, float]]:
    """Return BTC SPOT hourly bars from cd_spot_binance as a list of
    (timestamp_s, open, high, low, close, volume). Bounded by clock.now_ts().

    cd_spot_binance stores timestamps in seconds; each row is one 1h bar.
    Switched from cd_futures_ohlcv (perp) on 2026-05-01 — see module docstring.
    """
    upper_ts = clock.now_ts()
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, open, high, low, close, volume FROM cd_spot_binance "
        "WHERE timestamp >= strftime('%s','2019-09-01') AND timestamp <= ? "
        "ORDER BY timestamp",
        (upper_ts,),
    ).fetchall()
    con.close()
    return [(int(ts), float(o), float(h), float(l), float(c), float(v or 0))
            for ts, o, h, l, c, v in rows
            if o is not None and c is not None and o > 0 and c > 0]


def btc_hourly_by_bucket() -> dict[tuple[str, int], tuple[float, float]]:
    """Return {(date_iso, hour_int): (open, close)} — the key format used
    by the R4 BTC return computation."""
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for ts, o, h, l, c, v in load_btc_hourly():
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        out[(dt.date().isoformat(), dt.hour)] = (o, c)
    return out


# ─── ETH hourly (aggregated from eth_1m on the fly) ─────────────────────────

_ETH_1M_LOOKBACK_MS = 3 * 365 * 86_400 * 1000   # 3y: J+ regime uses ~2y of daily returns


def load_eth_hourly() -> dict[tuple[str, int], tuple[float, float]]:
    """Aggregate eth_1m into hourly buckets. Returns {(date_iso, hour_int):
    (open, close)}. The first 1m bar's open is the hour's open; the last
    1m bar's close is the hour's close. Bounded by clock.

    eth_1m.open_time is in ms, so we convert to seconds via // 1000 at the
    aggregation step.

    Bounded BELOW by 3 years before sim_now to keep the .fetchall() bounded
    (eth_1m has 3.37M+ rows since 2020, which OOMs Python in long replay
    runs). Downstream J+ regime/vol-target only walks ~2y of daily returns.
    """
    upper_ms = clock.now_ts_ms()
    lower_ms = max(0, upper_ms - _ETH_1M_LOOKBACK_MS)
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT open_time, open, close FROM eth_1m "
        "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
        (lower_ms, upper_ms),
    ).fetchall()
    con.close()
    # Group by (date, hour), preserving order so first/last 1m bar is correct.
    buckets: dict[tuple[str, int], list[tuple[int, float, float]]] = defaultdict(list)
    for ot_ms, o, c in rows:
        if o is None or c is None:
            continue
        dt = datetime.fromtimestamp(int(ot_ms) // 1000, tz=timezone.utc)
        buckets[(dt.date().isoformat(), dt.hour)].append((int(ot_ms), float(o), float(c)))
    out: dict[tuple[str, int], tuple[float, float]] = {}
    for key, bars in buckets.items():
        bars.sort(key=lambda r: r[0])
        out[key] = (bars[0][1], bars[-1][2])  # first-open, last-close
    return out


# ─── Daily aggregates ───────────────────────────────────────────────────────

def _btc_daily_map_from_hourly(
    hourly: list[tuple[int, float, float, float, float, float]] | None = None,
) -> dict[str, dict]:
    """{date_iso: {'o': open_first_hour_of_day, 'c': close_last_hour_of_day,
    'dt': datetime_of_first_hour}}."""
    if hourly is None:
        hourly = load_btc_hourly()
    out: dict[str, dict] = {}
    for ts, o, h, l, c, v in hourly:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        d = dt.date().isoformat()
        if d not in out:
            out[d] = {"o": o, "c": c, "dt": dt}
        else:
            out[d]["c"] = c
    return out


def load_btc_daily() -> dict[str, dict]:
    """BTC daily {date: {o, c, dt}} aggregated from cd_spot_binance hourly."""
    return _btc_daily_map_from_hourly()


def load_eth_daily() -> dict[str, dict]:
    """ETH daily {date: {o, c}} aggregated from eth_1m.

    Same 3y lower bound as load_eth_hourly — see comment there.
    """
    upper_ms = clock.now_ts_ms()
    lower_ms = max(0, upper_ms - _ETH_1M_LOOKBACK_MS)
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT open_time, open, close FROM eth_1m "
        "WHERE open_time >= ? AND open_time <= ? ORDER BY open_time",
        (lower_ms, upper_ms),
    ).fetchall()
    con.close()
    out: dict[str, dict] = {}
    for ot_ms, o, c in rows:
        if o is None or c is None:
            continue
        dt = datetime.fromtimestamp(int(ot_ms) // 1000, tz=timezone.utc)
        d = dt.date().isoformat()
        if d not in out:
            out[d] = {"o": float(o), "c": float(c)}
        else:
            out[d]["c"] = float(c)
    return out


def load_ls_ratio_btc() -> dict[str, float]:
    """{date_iso: long_pct} for BTC from ca_long_short_ratio, bounded by clock."""
    upper_ts = clock.now_ts()
    con = sqlite3.connect(str(db.TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, long_pct FROM ca_long_short_ratio "
        "WHERE asset='BTC' AND timestamp <= ? ORDER BY timestamp",
        (upper_ts,),
    ).fetchall()
    con.close()
    out: dict[str, float] = {}
    for ts, v in rows:
        d = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        out[d] = float(v) if v is not None else 0.0
    return out
