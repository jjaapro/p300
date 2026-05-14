"""Read the last-close price AT THE CURRENT CLOCK for the assets P-300 touches.

Live mode: clock.now_utc() == real now, so this returns whatever was most
recently written to trader.db for (BTC, ETH).

Simulated mode: clock.now_utc() == replay T, so this returns the last 1m bar
with open_time strictly less than T (i.e. the most recently CLOSED bar).
The previous implementation used cd_futures_ohlcv (1h) with `<=` which gave
an effective 1h look-ahead — close-of-bar at sim T = price at T+1h. That
distorted any short-window (event) strategy's backtest by 30-60 min on
entries and exits. Switching to btc_1m + strict-less-than reduces look-ahead
to ≤1 minute (negligible).

Data provenance (both 1m, both spot):
  BTC: btc_1m  (Binance BTC/USDT spot, 1m, open_time ms)
  ETH: eth_1m  (Binance ETH/USDT spot, 1m, open_time ms)

Stale-data guard: if the most recent bar < clock is older than the per-asset
freshness threshold, return None and warn. In simulated mode the threshold is
still checked against the clock (not wall-clock), so gaps in historical data
still get flagged — a carry-over from live behaviour that serves as a data-
integrity check during replay.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from strategies.support import clock
from strategies.support import db

log = logging.getLogger("dashboard.price_feed")

# Both BTC and ETH use 1m bars; allow up to 10 min of staleness before
# rejecting. (Previous code allowed 75 min for BTC because it was on 1h
# bars from cd_futures_ohlcv — no longer applicable.)
_MAX_STALE_SECS = {"BTC": 10 * 60, "ETH": 10 * 60}

_last_stale_warn_ts: dict[str, float] = {}


def _warn_stale_once(asset: str, age_s: float, clock_ts: int) -> None:
    """Rate-limit stale-data warnings to once/5min per asset so stalled
    feeds don't flood the log. Uses clock time so replay warnings also
    rate-limit sensibly."""
    prev = _last_stale_warn_ts.get(asset, 0)
    if clock_ts - prev > 300:
        if clock.is_simulated():
            log.warning(f"[price_feed] SIM: {asset} bar at clock is {age_s:.0f}s old "
                         f"(> {_MAX_STALE_SECS[asset]}s) — data gap in trader.db.")
        else:
            log.warning(f"[price_feed] {asset} latest price is {age_s:.0f}s old "
                         f"(> {_MAX_STALE_SECS[asset]}s) — returning None. "
                         f"Check binance_feed is running.")
        _last_stale_warn_ts[asset] = clock_ts


def get_current_price(asset: str) -> float | None:
    """Latest CLOSED 1m bar's close, strictly before sim/wall clock.

    Strict less-than (open_time < clock_ms, not <=) avoids look-ahead: at
    sim T, the bar that opened at T is still in progress and its close is
    a future observation. We want the bar that just CLOSED — the one that
    opened at T-1m. Look-ahead exposure is therefore at most 1 minute.

    Live mode: behaves the same way. The latest 1m bar in trader.db is the
    just-closed one (binance_feed writes it ~1s after close). Older bars
    only mean binance_feed is lagging — the staleness guard catches that.
    """
    asset = asset.upper()
    if asset not in _MAX_STALE_SECS:
        return None
    clock_ts = clock.now_ts()
    clock_ts_ms = clock.now_ts_ms()
    table = {"BTC": "btc_1m", "ETH": "eth_1m"}[asset]
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        row = con.execute(
            f"SELECT open_time, close FROM {table} "
            f"WHERE open_time < ? "
            f"ORDER BY open_time DESC LIMIT 1",
            (clock_ts_ms,),
        ).fetchone()
        if row is None or row[1] is None:
            return None
        row_ts_s = int(row[0]) // 1000
        price = float(row[1])
    finally:
        con.close()
    age_s = clock_ts - row_ts_s
    if age_s > _MAX_STALE_SECS[asset]:
        _warn_stale_once(asset, age_s, clock_ts)
        return None
    return price


# Backward-compatible alias so ported services can keep calling _get_current_price
_get_current_price = get_current_price
