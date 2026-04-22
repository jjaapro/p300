"""BTC regime classifier — used by S-096 Thu Bear V3 gate.

Returns one of four labels per day:
  bull_trend  — 50d MA slope > +chop_band, vol not extreme
  bear_trend  — 50d MA slope < -chop_band, vol not extreme
  chop        — |slope| <= chop_band
  sell_off    — RV percentile >= vol_high AND close < 50d MA AND slope < 0

Inputs (5 params total):
  ma_window           (50)    MA length
  rv_window           (30)    realized-vol window (daily, annualized * sqrt(365))
  vol_pct_window      (365)   trailing window for RV percentile rank
  vol_high_threshold  (0.75)  RV percentile cut for sell_off
  chop_slope_band_pct (0.5)   slope dead-band as % of price (10d-normalized)

Slope = 10-day delta of the 50d SMA, normalized by current close. 10-day step
is hardcoded (standard smoother, not a hyperparameter).

Ported from the trader repo with `load_daily` inlined from crisis_alpha_gate.py
so the only dependency is sqlite3 + math.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

TRADER_DB = Path(__file__).resolve().parent / "data" / "trader.db"

REGIME_LABELS = ("bull_trend", "bear_trend", "chop", "sell_off")
SLOPE_LOOKBACK_DAYS = 10


# ─── Data loader (inlined from crisis_alpha_gate.load_daily) ──────────────────

def load_daily(symbol: str = "BTC") -> list[tuple[str, dict]]:
    """Daily aggregated OHLC from cd_futures_ohlcv (BTC only is supported here).

    Returns [(iso_date, {open, close, dt}), ...] chronological.
    """
    if symbol != "BTC":
        raise NotImplementedError(f"regime_classifier only supports BTC, got {symbol}")
    con = sqlite3.connect(str(TRADER_DB))
    rows = con.execute(
        "SELECT timestamp, open, close FROM cd_futures_ohlcv "
        "WHERE timestamp >= strftime('%s','2020-01-01') ORDER BY timestamp"
    ).fetchall()
    con.close()
    daily: dict[str, dict] = {}
    for ts, o, c in rows:
        if o is None or c is None or o <= 0 or c <= 0:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        d = dt.date().isoformat()
        if d not in daily:
            daily[d] = {"open": o, "close": c, "dt": dt}
        else:
            daily[d]["close"] = c
    return [(k, daily[k]) for k in sorted(daily)]


# ─── Numeric helpers ─────────────────────────────────────────────────────────

def _sma(values, window):
    out = [None] * len(values)
    if len(values) < window:
        return out
    s = sum(values[:window])
    out[window - 1] = s / window
    for i in range(window, len(values)):
        s += values[i] - values[i - window]
        out[i] = s / window
    return out


def _rolling_rv_annualized(log_returns, window):
    out = [None] * len(log_returns)
    sqrt_365 = math.sqrt(365)
    if len(log_returns) <= window:
        return out
    for i in range(window, len(log_returns)):
        chunk = log_returns[i - window + 1: i + 1]
        m = sum(chunk) / window
        var = sum((r - m) ** 2 for r in chunk) / (window - 1)
        out[i] = math.sqrt(var) * sqrt_365
    return out


def _rolling_pct_rank(values, window):
    out = [None] * len(values)
    for i in range(window - 1, len(values)):
        chunk = values[i - window + 1: i + 1]
        if any(v is None for v in chunk):
            continue
        cur = chunk[-1]
        rank = sum(1 for v in chunk if v <= cur)
        out[i] = rank / len(chunk)
    return out


# ─── Classifier ──────────────────────────────────────────────────────────────

def classify_regime(symbol: str = "BTC",
                    ma_window: int = 50,
                    rv_window: int = 30,
                    vol_pct_window: int = 365,
                    vol_high_threshold: float = 0.75,
                    chop_slope_band_pct: float = 0.5) -> list[dict]:
    bars = load_daily(symbol)
    if not bars:
        return []
    dates = [d for d, _ in bars]
    closes = [b["close"] for _, b in bars]

    log_rets = [0.0]
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            log_rets.append(0.0)

    ma = _sma(closes, ma_window)
    rv_ann = _rolling_rv_annualized(log_rets, rv_window)
    rv_pct = _rolling_pct_rank(rv_ann, vol_pct_window)

    slope_pct = [None] * len(closes)
    for i in range(ma_window + SLOPE_LOOKBACK_DAYS - 1, len(closes)):
        ma_now = ma[i]
        ma_then = ma[i - SLOPE_LOOKBACK_DAYS]
        if ma_now is None or ma_then is None or closes[i] <= 0:
            continue
        slope_pct[i] = (ma_now - ma_then) / closes[i] * 100

    out = []
    for i, date in enumerate(dates):
        rec = {
            "date": date, "close": closes[i], "ma": ma[i],
            "slope_pct": slope_pct[i], "rv_ann": rv_ann[i],
            "rv_pct": rv_pct[i], "label": None,
        }
        s = slope_pct[i]; p = rv_pct[i]; m = ma[i]
        if s is None or p is None or m is None:
            out.append(rec); continue
        below_ma = closes[i] < m
        if p >= vol_high_threshold and below_ma and s < 0:
            rec["label"] = "sell_off"
        elif s > chop_slope_band_pct:
            rec["label"] = "bull_trend"
        elif s < -chop_slope_band_pct:
            rec["label"] = "bear_trend"
        else:
            rec["label"] = "chop"
        out.append(rec)
    return out


def regime_map(symbol: str = "BTC", **kwargs) -> dict[str, str]:
    """{date_iso: label} for labeled days only. Drop-in for signal gates."""
    return {r["date"]: r["label"] for r in classify_regime(symbol, **kwargs)
            if r["label"] is not None}
