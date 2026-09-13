"""Shared read-only loaders and vectorised PDO-sleeve features.

Every function here is a vectorised transcription of a function that lives in
``strategies/sleeves/timing_anomalies/internal/pdo/signal.py``.  The
transcription is *verified*, not trusted: ``parity_check.py`` drives the
sleeve's own functions under a frozen clock and asserts exact float equality
against the arrays produced here.

prod.db is opened READ-ONLY.  Nothing in this study writes to it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.support import db

HOUR = 3600
DAY = 86400
ETF_DATE = "2024-01-11"
OOS_START = "2026-04-01"
STUDY_START = "2020-01-01"
COST_BP_RT = 18.0          # study convention
COST_BP_RT_LIVE = 10.0     # what the live sleeve charges (reported only)

# frozen sleeve constants (mirrors of pdo/config.py -- asserted equal at import)
GAP_THRESHOLD_PCT = 2.0
TOUCH_TOL_PCT = 0.10
HOLD_BARS_BY_ASSET = {"BTC": 24, "ETH": 4}


def ro_conn() -> sqlite3.Connection:
    """Read-only connection to prod.db."""
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def assert_config_parity() -> dict:
    """The study's frozen constants must equal the live sleeve's."""
    from studies.material.archive.pdo import config as cfg
    assert cfg.GAP_THRESHOLD_PCT == GAP_THRESHOLD_PCT, cfg.GAP_THRESHOLD_PCT
    assert cfg.TOUCH_TOL_PCT == TOUCH_TOL_PCT, cfg.TOUCH_TOL_PCT
    assert cfg.HOLD_BARS_BY_ASSET == HOLD_BARS_BY_ASSET, cfg.HOLD_BARS_BY_ASSET
    return {
        "gap_threshold_pct": cfg.GAP_THRESHOLD_PCT,
        "touch_tol_pct": cfg.TOUCH_TOL_PCT,
        "hold_bars_by_asset": dict(cfg.HOLD_BARS_BY_ASSET),
        "live_regime_threshold_pct": cfg.REGIME_THRESHOLD_PCT,
        "live_cost_bp_rt": cfg.COST_BP_RT,
    }


# --- raw minute data ---------------------------------------------------------

def load_minutes(asset: str) -> pd.DataFrame:
    """1-minute bars for BTC/ETH, ascending by open_time (ms)."""
    table = f"{asset.lower()}_1m"
    with ro_conn() as con:
        df = pd.read_sql_query(
            f"SELECT open_time, open, high, low, close FROM {table} "
            f"ORDER BY open_time", con)
    df["ts"] = (df["open_time"] // 1000).astype(np.int64)
    return df


class AssetBars:
    """Hourly bars + daily opens for one asset, built exactly as the sleeve
    builds them from the 1-minute table."""

    def __init__(self, asset: str):
        self.asset = asset
        m = load_minutes(asset)
        self.minutes = m

        # --- hourly aggregation: MIN(low), MAX(high), close of last 1m bar ---
        hidx = (m["ts"].to_numpy() // HOUR).astype(np.int64)
        g = pd.DataFrame({
            "h": hidx,
            "low": m["low"].to_numpy(),
            "high": m["high"].to_numpy(),
            "close": m["close"].to_numpy(),
        })
        agg = g.groupby("h", sort=True).agg(low=("low", "min"),
                                            high=("high", "max"),
                                            close=("close", "last"))
        self.h_index = agg.index.to_numpy().astype(np.int64)   # hour ordinal of bar OPEN
        self.h_low = agg["low"].to_numpy()
        self.h_high = agg["high"].to_numpy()
        self.h_close = agg["close"].to_numpy()
        self._h_pos = {int(h): i for i, h in enumerate(self.h_index)}

        # --- daily first bar: open + its open_time ---------------------------
        didx = (m["ts"].to_numpy() // DAY).astype(np.int64)
        gd = pd.DataFrame({"d": didx, "open": m["open"].to_numpy(),
                           "ts": m["ts"].to_numpy()})
        aggd = gd.groupby("d", sort=True).agg(open=("open", "first"),
                                              ts=("ts", "first"))
        self.d_index = aggd.index.to_numpy().astype(np.int64)   # day ordinal
        self.d_open = aggd["open"].to_numpy()
        self.d_open_ts = aggd["ts"].to_numpy().astype(np.int64)
        self._d_pos = {int(d): i for i, d in enumerate(self.d_index)}

    # -- hourly bar lookups ---------------------------------------------------
    def hour_bar_closing_at(self, t: int):
        """The bar covering [t-1h, t) -- what ``_get_hourly_bar_for_today``
        returns when the clock reads ``t`` (t on the hour). None if absent."""
        i = self._h_pos.get((t - HOUR) // HOUR)
        if i is None:
            return None
        return (float(self.h_low[i]), float(self.h_high[i]), float(self.h_close[i]))

    def hour_close_at(self, t: int):
        """Close of the bar covering [t-1h, t)."""
        i = self._h_pos.get((t - HOUR) // HOUR)
        return None if i is None else float(self.h_close[i])

    # -- daily open lookups ---------------------------------------------------
    def day_first(self, day_ord: int):
        """(open, open_time_seconds) of the first 1m bar of that UTC day."""
        i = self._d_pos.get(int(day_ord))
        if i is None:
            return None
        return float(self.d_open[i]), int(self.d_open_ts[i])

    def pdo_cdo_at(self, t: int):
        """Vectorised ``_load_today_open_and_pdo`` evaluated with clock == t.

        Bar day = UTC date of (t - 1h).  PDO = first 1m open of bar_day-1
        (no upper bound).  today_open = first 1m open of bar_day, but only if
        that bar's open_time <= t (the sleeve bounds it by ``now_ts_ms()``).
        """
        bar_day = (t - HOUR) // DAY
        prev = self.day_first(bar_day - 1)
        cur = self.day_first(bar_day)
        if prev is None or cur is None:
            return None
        if cur[1] > t:                      # first bar of the day is later than now
            return None
        pdo = prev[0]
        today_open = cur[0]
        gap = (today_open - pdo) / pdo * 100 if pdo > 0 else 0
        return {"bar_day": int(bar_day), "pdo": pdo, "today_open": today_open,
                "gap_pct": gap}


class BtcRegime:
    """Vectorised ``_btc_30d_return_pct`` over cd_spot_binance.

    The live function queries a +/-1h window and takes ORDER BY timestamp DESC
    LIMIT 1, so the effective picks are:
        prev = latest existing stamp in {t-1h, t-2h, t-3h}
        old  = latest existing stamp in {t-721h, t-722h, t-723h}
    i.e. a 720-hour return whose near end is the bar that just closed at t.
    (Pine's close[1]/close[721] is the same 720h span shifted one hour back;
    the study reproduces the *sleeve*, which is what production runs.)
    """

    def __init__(self):
        with ro_conn() as con:
            df = pd.read_sql_query(
                "SELECT timestamp, close FROM cd_spot_binance ORDER BY timestamp",
                con)
        self.close_by_ts = dict(zip(df["timestamp"].astype(np.int64).tolist(),
                                    df["close"].astype(float).tolist()))

    def _pick(self, cands) -> float | None:
        for ts in cands:
            v = self.close_by_ts.get(int(ts))
            if v is not None:
                return v
        return None

    def at(self, t: int) -> float | None:
        prev = self._pick([t - HOUR, t - 2 * HOUR, t - 3 * HOUR])
        old = self._pick([t - 721 * HOUR, t - 722 * HOUR, t - 723 * HOUR])
        if prev is None or old is None or old <= 0:
            return None
        return (prev - old) / old * 100


# --- helpers -----------------------------------------------------------------

def hour_range(start_iso: str, end_ts: int) -> np.ndarray:
    """Every hour boundary in [start, end_ts], as unix seconds."""
    s = int(datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc).timestamp())
    s = s - (s % HOUR) + HOUR
    return np.arange(s, end_ts + 1, HOUR, dtype=np.int64)


def iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dstr(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def summarize(vals) -> dict:
    """Descriptive stats used all over the findings tables."""
    a = np.asarray([v for v in vals if np.isfinite(v)], dtype=float)
    n = int(a.size)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "median": float("nan"),
                "sd": float("nan"), "win_rate": float("nan"),
                "sum": 0.0, "sharpe": float("nan"), "min": float("nan"),
                "max": float("nan")}
    sd = float(a.std(ddof=1)) if n > 1 else float("nan")
    return {
        "n": n,
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "sd": sd,
        "win_rate": float((a > 0).mean()),
        "sum": float(a.sum()),
        "sharpe": float(a.mean() / sd) if (n > 1 and sd and sd > 0) else float("nan"),
        "min": float(a.min()),
        "max": float(a.max()),
    }
