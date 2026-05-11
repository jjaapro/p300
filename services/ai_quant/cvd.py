"""CVD (Cumulative Volume Delta) helpers for the AI_QUANT sleeve.

Reads taker-buy / taker-sell volumes from cd_futures_ohlcv (the perp,
where most BTC orderflow lives — daily perp volume is typically 5-10×
the spot volume on Binance) and produces:

- hourly bar deltas (for chart rendering)
- daily aggregation + rolling z-score (for the text context)
- a context-ready summary the LLM can reason over

All queries bound the upper edge with ``clock.now_ts()`` so the sleeve
stays replay-safe — backtest mode won't peek past the simulated clock.

Mirrors the canonical CVD computation from
trader/research/probe_skew_cvd.py (S-077 skew/CVD divergence probe). The
sleeve doesn't depend on research code so the logic is duplicated here
rather than imported across repos.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from services import clock, db


def load_hourly_cvd(since_ts: int, until_ts: int
                     ) -> list[tuple[int, float, float, float]]:
    """Hourly rows from cd_futures_ohlcv between [since_ts, until_ts] inclusive.

    Returns a list of ``(timestamp, buy_btc, sell_btc, close)``. Rows where
    ``volume_buy IS NULL`` are filtered out — legacy partial-bar writes
    would otherwise contribute zero buy and full sell, biasing the delta.
    """
    con = sqlite3.connect(str(db.TRADER_DB))
    try:
        rows = con.execute(
            "SELECT timestamp, volume_buy, volume_sell, close "
            "FROM cd_futures_ohlcv "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "  AND volume_buy IS NOT NULL "
            "ORDER BY timestamp",
            (since_ts, until_ts),
        ).fetchall()
    finally:
        con.close()
    return [(int(t), float(b), float(s), float(c)) for t, b, s, c in rows]


def daily_cvd_series(since_ts: int, until_ts: int) -> list[dict]:
    """Aggregate hourly perp CVD into UTC-day buckets in [since_ts, until_ts].

    The bucket containing ``clock.now_ts()`` is included even if partial —
    the daily-decision sleeve reads this just before its decision call and
    expects a snapshot of today so far. Returns a list of dicts sorted by
    date, each carrying ``cvd_btc``, ``buy_btc``, ``sell_btc``,
    ``volume_btc``, and ``close`` (last hourly close in that day).
    """
    rows = load_hourly_cvd(since_ts, until_ts)
    by_day: dict[str, dict] = {}
    for ts, buy, sell, close in rows:
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        slot = by_day.setdefault(d, {"buy": 0.0, "sell": 0.0,
                                       "close": close, "last_ts": ts})
        slot["buy"] += buy
        slot["sell"] += sell
        if ts >= slot["last_ts"]:
            slot["close"] = close
            slot["last_ts"] = ts
    out: list[dict] = []
    for d in sorted(by_day):
        s = by_day[d]
        out.append({
            "date": d,
            "cvd_btc": s["buy"] - s["sell"],
            "buy_btc": s["buy"],
            "sell_btc": s["sell"],
            "volume_btc": s["buy"] + s["sell"],
            "close": s["close"],
        })
    return out


def add_cvd_zscore(series: list[dict], window: int = 30,
                    min_obs: int = 15) -> None:
    """In-place: annotate each day with ``cvd_z`` — rolling z-score of
    ``cvd_btc`` against the prior ``window`` days. Same formula as the
    trader probe; ``cvd_z`` is None until ``min_obs`` prior days exist.
    """
    cvds = [s["cvd_btc"] for s in series]
    for i, s in enumerate(series):
        prior = cvds[max(0, i - window):i]
        if len(prior) < min_obs:
            s["cvd_z"] = None
            continue
        m = sum(prior) / len(prior)
        sd = (sum((x - m) ** 2 for x in prior) / len(prior)) ** 0.5
        s["cvd_z"] = (s["cvd_btc"] - m) / sd if sd > 0 else 0.0


def cvd_summary(lookback_days: int = 60) -> dict:
    """Context-section payload for the AI_QUANT prompt.

    Returns latest daily CVD + its rolling z-score, plus 24h and 7d
    rolling-sum totals computed from the hourly stream so the LLM can see
    both "today vs history" (z-score) and "recent flow direction"
    (24h / 7d aggregates) at a glance.

    ``lookback_days`` needs to comfortably exceed the z-score window
    (30 + 15 min-obs); 60 is the default.
    """
    until = clock.now_ts()
    since = until - lookback_days * 86400
    hourly = load_hourly_cvd(since, until)
    if not hourly:
        return {"error": "no CVD rows in cd_futures_ohlcv"}

    cutoff_24h = until - 24 * 3600
    cutoff_7d = until - 7 * 86400
    cvd_24h = sum(b - s for t, b, s, _c in hourly if t >= cutoff_24h)
    cvd_7d = sum(b - s for t, b, s, _c in hourly if t >= cutoff_7d)
    vol_24h = sum(b + s for t, b, s, _c in hourly if t >= cutoff_24h)
    vol_7d = sum(b + s for t, b, s, _c in hourly if t >= cutoff_7d)

    series = daily_cvd_series(since, until)
    add_cvd_zscore(series)
    latest = series[-1] if series else None

    return {
        "source": "binance_perp_btc_usdt_1h",
        "cvd_24h_btc": round(cvd_24h, 2),
        "cvd_7d_btc": round(cvd_7d, 2),
        "ratio_24h_buy_pressure_pct": (
            round((cvd_24h / vol_24h + 1) * 50.0, 1) if vol_24h > 0 else None
        ),
        "ratio_7d_buy_pressure_pct": (
            round((cvd_7d / vol_7d + 1) * 50.0, 1) if vol_7d > 0 else None
        ),
        "latest_daily_cvd_btc": round(latest["cvd_btc"], 2) if latest else None,
        "latest_daily_cvd_z": (
            round(latest["cvd_z"], 2)
            if latest and latest["cvd_z"] is not None else None
        ),
        "latest_date_utc": latest["date"] if latest else None,
        "n_days_in_zscore_window": (
            sum(1 for s in series if s.get("cvd_z") is not None)
        ),
    }
