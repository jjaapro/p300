"""Market-flow panels for the dashboard: CVD / OI / funding / basis panes,
the daily positioning tile and the delta-by-price profile. STRICTLY
READ-ONLY — every connection comes from queries._ro_con() (mode=ro +
PRAGMA query_only).

What this shows, and what it does not
-------------------------------------
Descriptive context, not a signal. Taker buy/sell splits give the
AGGRESSOR side of the tape (CVD = taker buy − taker sell); the change in
open interest says whether contracts were created or destroyed. Crossing
the two labels each bar:

                          OI up              OI down
    CVD up   (buyers)     longs_opening      short_covering
    CVD down (sellers)    shorts_opening     longs_closing

The passive side is invisible in this data, and every attempt to mine new
predictive edge from these inputs has died (Chento Rule 1, whale
absorption, footprint, LVN, FVG — see studies/). The panes exist so the
operator can see what the bots see: short_squeeze's percentile gauges,
CPR's positioning gate and the regime J+ circuit breaker are replicated
here with the sleeves' own SQL and constants and pinned to them by
tests/test_dashboard_market.py.

Conventions
-----------
- CVD in BASE units (BTC / ETH), not quote: price-invariant over the
  rolling windows, and the unit short_squeeze's gauges use.
- /api/flow bars align 1:1 with /api/candles: same window rule
  (queries._candle_window), same bucketing fold, same in-progress-bucket
  asymmetry (dropped only when bucketing). Enforced by
  test_flow_times_equal_candle_times.
- ETH has no OI table and no spot taker table, so OI / spot / basis /
  labels are null for ETH.
- Sleeve loader functions are NOT imported: they open read-write
  connections on TRADER_DB. Only config constants are imported, so the
  gauges cannot drift from the bots.
"""
from __future__ import annotations

import bisect
from datetime import date, datetime, timedelta, timezone

import numpy as np

import botlib
from dashboard import queries
from strategies.sleeves.short_squeeze import config as ssq_cfg
from strategies.sleeves.timing_anomalies.internal.cpr import config as cpr_cfg
from strategies.support import db
from strategies.support.funding import SETTLEMENT_PERIOD_SECONDS

# ─── Sources ─────────────────────────────────────────────────────────────────
# Spot venue paired with each chart source: 15m BTC (cd_futures_15m) ↔
# cd_spot_15m; hourly BTC (cd_futures_ohlcv) ↔ cd_spot_binance. No ETH spot
# taker table exists.
_SPOT_SOURCES: dict[tuple[str, str], str] = {
    ("BTC", "15m"): "cd_spot_15m",
    ("BTC", "1h"): "cd_spot_binance",
    ("BTC", "4h"): "cd_spot_binance",
    ("BTC", "1d"): "cd_spot_binance",
}
_OI_TABLES = {"BTC": "cd_open_interest"}              # hourly; BTC only
_FUNDING_TABLES = {"BTC": "cd_funding_rate", "ETH": "cd_funding_rate_eth"}
_PERP_15M = {"BTC": "cd_futures_15m", "ETH": "cd_futures_eth_15m"}
_SPOT_15M = {"BTC": "cd_spot_15m"}
_DAILY_CLOSE_TABLES = {"BTC": "cd_spot_binance", "ETH": "cd_futures_eth_15m"}
_LSR_TABLE = "ca_long_short_ratio"

# 2×2 labels only when BOTH moves beat their rolling 60th percentile of
# absolute moves at this timeframe (365d pool for daily bars, 90d otherwise).
_LABEL_POOL_DAYS = {"15m": 90, "1h": 90, "4h": 90, "1d": 365}
_LABEL_PCT = 60
_LABEL_MIN_POOL = 60
_LABEL_TEXT = {
    "longs_opening": "longs opening",
    "short_covering": "short covering",
    "shorts_opening": "shorts opening",
    "longs_closing": "longs closing",
}

# Positioning tile: rank today's ratio against the prior 365 daily rows and
# score each decile by the 20-day forward return (close[T+21d]/close[T+1d]
# − 1 — skip-one-day, the convention of the 2026-09-01 quick check).
_LSR_RANK_WINDOW = 365
_FWD_DAYS = 20
# strategies/support/regime_jplus.py::classify_day — the LS circuit breaker:
# long_pct[T-1] − long_pct[T-8] < −15 forces 'uncertain' for 7 days.
_CB_SHIFT = -15.0
_CB_DAYS = 7

# Per-UTC-day caches. Keyed by the db path as well so per-test fixture
# databases never see each other's pools.
_thr_cache: dict[tuple, dict] = {}
_ssq_cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _oi_carry(secs: int) -> int:
    """OI is hourly: carry the last value ≤ 1h of bars (limit=4 on 15m,
    mirrors chento_limit_bid/signal.py's ffill limit)."""
    return max(1, 3600 // secs)


def _fund_carry(secs: int) -> int:
    """Funding settles every 8h: carry ≤ one settlement period of bars
    (limit=32 on 15m, mirrors chento_limit_bid/signal.py)."""
    return max(1, SETTLEMENT_PERIOD_SECONDS // secs)


def _r(v, nd: int):
    return None if v is None else round(float(v), nd)


# ─── Row loaders (all read-only) ─────────────────────────────────────────────

def _perp_rows(con, table: str, start: int) -> list:
    return con.execute(
        f"SELECT timestamp, open, high, low, close, volume_buy, volume_sell "
        f"FROM {table} WHERE timestamp >= ? ORDER BY timestamp",
        (start,)).fetchall()


def _spot_map(con, table: str, start: int) -> dict[int, tuple]:
    rows = con.execute(
        f"SELECT timestamp, close, volume_buy, volume_sell FROM {table} "
        f"WHERE timestamp >= ? ORDER BY timestamp", (start,)).fetchall()
    return {int(r["timestamp"]): (r["close"], r["volume_buy"], r["volume_sell"])
            for r in rows}


def _series(con, table: str, col: str, start: int) -> tuple[list[int], list[float]]:
    rows = con.execute(
        f"SELECT timestamp, {col} FROM {table} WHERE timestamp >= ? "
        f"AND {col} IS NOT NULL ORDER BY timestamp", (start,)).fetchall()
    return [int(r[0]) for r in rows], [float(r[1]) for r in rows]


def _funding_series(con, table: str, start: int) -> tuple[list[int], list[float]]:
    """Settlement rows only (timestamp % 8h == 0) — the pre-2026-04-25 rows
    are hourly predicted rates; strategies/support/funding.py applies the
    same filter."""
    rows = con.execute(
        f"SELECT timestamp, fr_close FROM {table} WHERE timestamp >= ? "
        f"AND timestamp % ? = 0 AND fr_close IS NOT NULL ORDER BY timestamp",
        (start, SETTLEMENT_PERIOD_SECONDS)).fetchall()
    return [int(r[0]) for r in rows], [float(r[1]) for r in rows]


def _latest_funding(con, table: str, now_s: int) -> float | None:
    row = con.execute(
        f"SELECT fr_close FROM {table} WHERE timestamp <= ? AND timestamp % ? = 0 "
        f"AND fr_close IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
        (now_s, SETTLEMENT_PERIOD_SECONDS)).fetchone()
    return None if row is None else float(row[0])


# ─── Bucketing (mirrors queries.candles) ─────────────────────────────────────

def _fold(rows: list, spot: dict | None, secs: int, native: int,
          now_s: int) -> list[dict]:
    """Group perp rows into `secs` buckets exactly like queries.candles():
    native tf keeps every row (bar time = row time), coarser tfs use
    (ts // secs) * secs and drop the in-progress bucket. Sums CVD, keeps
    the last closes, and counts perp vs spot rows so an incomplete spot
    bucket can be nulled instead of mis-summed."""
    out: list[dict] = []
    cur: dict | None = None
    for r in rows:
        ts = int(r["timestamp"])
        b = ts if secs == native else (ts // secs) * secs
        if cur is None or cur["time"] != b:
            if cur is not None:
                out.append(cur)
            cur = {"time": b, "n": 0, "n_spot": 0, "perp_cvd": 0.0,
                   "perp_ok": True, "spot_cvd": 0.0, "perp_close": None,
                   "spot_close": None}
        cur["n"] += 1
        vb, vs = r["volume_buy"], r["volume_sell"]
        if vb is None or vs is None:
            cur["perp_ok"] = False
        else:
            cur["perp_cvd"] += float(vb) - float(vs)
        cur["perp_close"] = r["close"]
        if spot is not None:
            s = spot.get(ts)
            if s is not None and s[1] is not None and s[2] is not None:
                cur["n_spot"] += 1
                cur["spot_cvd"] += float(s[1]) - float(s[2])
                cur["spot_close"] = s[0]
    if cur is not None:
        out.append(cur)
    if secs != native:
        while out and out[-1]["time"] + secs > now_s:   # in-progress bucket
            out.pop()
    return out


def _attach(bars: list[dict], ts_list: list[int], vals: list[float],
            secs: int, carry: int) -> list[float | None]:
    """Per bar: the last series value stamped inside [time, time+secs);
    otherwise carry the previous value for at most `carry` bars, else
    None. Seeds from the newest series row before the first bar so a
    window starting between two hourly / 8h rows is not blank."""
    out: list[float | None] = []
    last: float | None = None
    left = 0
    if bars and ts_list:
        i = bisect.bisect_left(ts_list, bars[0]["time"]) - 1
        if i >= 0:
            elapsed = (bars[0]["time"] - (ts_list[i] // secs) * secs) // secs
            seed_left = carry - (elapsed - 1)
            if seed_left > 0:
                last, left = vals[i], seed_left
    for b in bars:
        t = b["time"]
        j = bisect.bisect_right(ts_list, t + secs - 1) - 1
        if j >= 0 and ts_list[j] >= t:
            last, left = vals[j], carry
            out.append(last)
        elif last is not None and left > 0:
            left -= 1
            out.append(last)
        else:
            last = None
            out.append(None)
    return out


def _label(cvd: float | None, doi: float | None, cvd_thr: float | None,
           oi_thr: float | None) -> str | None:
    """The CVD × ΔOI quadrant, or None when either input is missing, zero
    or below its threshold (pass 0.0 thresholds for a sign-only read)."""
    if cvd is None or doi is None or cvd == 0 or doi == 0:
        return None
    if cvd_thr is None or oi_thr is None:
        return None
    if abs(cvd) < cvd_thr or abs(doi) < oi_thr:
        return None
    if cvd > 0:
        return "longs_opening" if doi > 0 else "short_covering"
    return "shorts_opening" if doi > 0 else "longs_closing"


def _thresholds(con, asset: str, tf: str, now_s: int) -> dict:
    """Rolling pool of |CVD| and |ΔOI%| per completed bar at this tf; the
    60th percentile of each is the label gate. Cached per UTC day."""
    key = (str(db.PROD_DB), asset, tf, _utc_date(now_s))
    hit = _thr_cache.get(key)
    if hit is not None:
        return hit
    table, native = queries._CANDLE_SOURCES[(asset, tf)]
    secs = queries._TF_SECONDS[tf]
    pool_days = _LABEL_POOL_DAYS[tf]
    pool_start = now_s - pool_days * 86400
    bars = _fold(_perp_rows(con, table, pool_start), None, secs, native, now_s)
    cvd_pool = [abs(b["perp_cvd"]) for b in bars if b["perp_ok"]]
    oi_pool: list[float] = []
    if asset in _OI_TABLES:
        ts, vals = _series(con, _OI_TABLES[asset], "oi_close", pool_start - 86400)
        oi = _attach(bars, ts, vals, secs, _oi_carry(secs))
        for prev, cur in zip(oi, oi[1:]):
            if prev is not None and cur is not None and cur != prev:
                oi_pool.append(abs((cur - prev) / prev * 100))
    thr = {
        "cvd_abs_p60": (float(np.percentile(cvd_pool, _LABEL_PCT))
                        if len(cvd_pool) >= _LABEL_MIN_POOL else None),
        "oi_abs_p60": (float(np.percentile(oi_pool, _LABEL_PCT))
                       if len(oi_pool) >= _LABEL_MIN_POOL else None),
        "pool_days": pool_days,
        "pool_n": len(cvd_pool),
        "oi_pool_n": len(oi_pool),
    }
    if len(_thr_cache) > 32:
        _thr_cache.clear()
    _thr_cache[key] = thr
    return thr


# ─── short_squeeze gauges (replica of the sleeve's loaders) ──────────────────

_SSQ_SQL = """
    SELECT
        p.timestamp,
        COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0)
          AS perp_cvd,
        (COALESCE(s.volume_buy, 0) - COALESCE(s.volume_sell, 0))
          - (COALESCE(p.volume_buy, 0) - COALESCE(p.volume_sell, 0))
          AS divergence
    FROM cd_futures_15m p
    LEFT JOIN cd_spot_15m s ON s.timestamp = p.timestamp
    WHERE {where}
    ORDER BY p.timestamp
"""


def _ssq_session_ok(hour: int) -> bool:
    """short_squeeze.math.session_of_hour(hour) in ('london', 'ny')."""
    return any(lo <= hour < hi for name, (lo, hi) in ssq_cfg.SESSIONS.items()
               if name in ("london", "ny"))


def _pct_rank_incl(value: float, dist: np.ndarray) -> float:
    """short_squeeze.math.percentile_rank: fraction of dist <= value."""
    if len(dist) == 0:
        return 0.0
    return float((dist <= value).mean())


def _ssq_pool(con, now: datetime) -> tuple[np.ndarray, np.ndarray]:
    """Replica of short_squeeze.signal._load_recent_15m_bars(now,
    WINDOW_DAYS): every London/NY 15m bar of the trailing window, with the
    sleeve's COALESCE semantics (a missing spot row reads as −perp)."""
    cutoff_s = int((now - timedelta(days=ssq_cfg.WINDOW_DAYS)).timestamp())
    now_s = int(now.timestamp())
    rows = con.execute(
        _SSQ_SQL.format(where="p.timestamp >= ? AND p.timestamp <= ?"),
        (cutoff_s, now_s)).fetchall()
    perp, div = [], []
    for ts_s, pc, dv in rows:
        hour = datetime.fromtimestamp(int(ts_s), tz=timezone.utc).hour
        if not _ssq_session_ok(hour):
            continue
        perp.append(float(pc or 0.0))
        div.append(float(dv or 0.0))
    return np.array(perp, dtype=float), np.array(div, dtype=float)


def _ssq_latest(con, now: datetime) -> dict | None:
    """Replica of short_squeeze.signal._load_latest_15m_bar(now)."""
    floored = now.replace(second=0, microsecond=0)
    floored = floored - timedelta(minutes=floored.minute % 15)
    ts_s = int((floored - timedelta(minutes=15)).timestamp())
    row = con.execute(_SSQ_SQL.format(where="p.timestamp = ?"),
                      (ts_s,)).fetchone()
    if row is None:
        return None
    ts_s, pc, dv = row
    return {"ts": int(ts_s), "perp_cvd": float(pc or 0.0),
            "divergence": float(dv or 0.0)}


def _ssq_gauge(con, now: datetime) -> dict | None:
    key = (str(db.PROD_DB), _utc_date(int(now.timestamp())))
    pool = _ssq_cache.get(key)
    if pool is None:
        pool = _ssq_pool(con, now)
        _ssq_cache.clear()
        _ssq_cache[key] = pool
    perp_d, div_d = pool
    bar = _ssq_latest(con, now)
    if bar is None or len(perp_d) == 0:
        return None
    perp_pct = _pct_rank_incl(bar["perp_cvd"], perp_d)
    div_pct = _pct_rank_incl(bar["divergence"], div_d)
    return {
        "bar_ts": bar["ts"],
        "perp_cvd": _r(bar["perp_cvd"], 4),
        "perp_cvd_pct": round(perp_pct, 3),
        "divergence": _r(bar["divergence"], 4),
        "divergence_pct": round(div_pct, 3),
        # the sleeve REJECTS perp_pct >= MAX and div_pct <= MIN
        "perp_ok": bool(perp_pct < ssq_cfg.PERP_CVD_PCT_MAX),
        "div_ok": bool(div_pct > ssq_cfg.DIVERGENCE_PCT_MIN),
        "pool_n": int(len(perp_d)),
        "pool_days": ssq_cfg.WINDOW_DAYS,
        "perp_cvd_pct_max": ssq_cfg.PERP_CVD_PCT_MAX,
        "divergence_pct_min": ssq_cfg.DIVERGENCE_PCT_MIN,
    }


# ─── Tape read (last 4h / 24h from the 15m tables, independent of tf) ────────

def _tape_text(label: str, d: dict) -> str:
    parts = []
    if d["price_pct"] is not None:
        parts.append(f"price {d['price_pct']:+.1f}%")
    pc = d["perp_cvd"]
    if pc is not None:
        side = "net buying" if pc > 0 else "net selling" if pc < 0 else "flat"
        parts.append(f"perp CVD {pc:+,.0f} ({side})")
    if d["oi_pct"] is not None:
        s = f"OI {d['oi_pct']:+.1f}%"
        if d["label"]:
            s += f" → {_LABEL_TEXT[d['label']]}"
        parts.append(s)
    sc = d["spot_cvd"]
    if sc is not None and pc is not None:
        agree = (sc > 0) == (pc > 0)
        parts.append(f"spot CVD {sc:+,.0f} ({'agrees' if agree else 'diverges'})")
    if d["funding"] is not None:
        parts.append(f"funding {d['funding'] * 100:+.4f}%/8h")
    return f"{label}: " + " · ".join(parts) if parts else f"{label}: no data"


def _tape(con, asset: str, now_s: int) -> dict:
    out = {}
    fund = _latest_funding(con, _FUNDING_TABLES[asset], now_s)
    for label, hours in (("4h", 4), ("24h", 24)):
        start = now_s - hours * 3600
        rows = con.execute(
            f"SELECT timestamp, open, close, volume_buy, volume_sell "
            f"FROM {_PERP_15M[asset]} WHERE timestamp >= ? AND timestamp <= ? "
            f"ORDER BY timestamp", (start, now_s)).fetchall()
        d = {"window_h": hours, "price_pct": None, "perp_cvd": None,
             "spot_cvd": None, "oi_pct": None, "label": None,
             "funding": fund, "text": ""}
        if len(rows) >= 2:
            if rows[0]["open"]:
                d["price_pct"] = _r((rows[-1]["close"] / rows[0]["open"] - 1) * 100, 2)
            vals = [float(r["volume_buy"]) - float(r["volume_sell"]) for r in rows
                    if r["volume_buy"] is not None and r["volume_sell"] is not None]
            d["perp_cvd"] = _r(sum(vals), 4) if vals else None
        if asset in _SPOT_15M:
            srows = con.execute(
                f"SELECT volume_buy, volume_sell FROM {_SPOT_15M[asset]} "
                f"WHERE timestamp >= ? AND timestamp <= ?", (start, now_s)).fetchall()
            svals = [float(r[0]) - float(r[1]) for r in srows
                     if r[0] is not None and r[1] is not None]
            d["spot_cvd"] = _r(sum(svals), 4) if svals else None
        if asset in _OI_TABLES:
            orows = con.execute(
                f"SELECT oi_close FROM {_OI_TABLES[asset]} WHERE timestamp >= ? "
                f"AND timestamp <= ? AND oi_close IS NOT NULL ORDER BY timestamp",
                (start, now_s)).fetchall()
            if len(orows) >= 2 and orows[0][0]:
                d["oi_pct"] = _r((orows[-1][0] / orows[0][0] - 1) * 100, 3)
        d["label"] = _label(d["perp_cvd"], d["oi_pct"], 0.0, 0.0)   # sign-only
        d["text"] = _tape_text(label, d)
        out[label] = d
    return out


# ─── /api/flow ───────────────────────────────────────────────────────────────

def flow(asset: str = "BTC", tf: str = "1h", bars: int = 0,
         after: int = 0) -> dict:
    asset = (asset or "BTC").upper()
    if (asset, tf) not in queries._CANDLE_SOURCES:
        raise ValueError(f"unsupported asset/tf: {asset}/{tf}")
    table, native = queries._CANDLE_SOURCES[(asset, tf)]
    secs = queries._TF_SECONDS[tf]
    bars = bars or queries._DEFAULT_BARS[tf]
    now = queries._now()
    now_s = int(now.timestamp())
    spot_table = _SPOT_SOURCES.get((asset, tf))
    oi_table = _OI_TABLES.get(asset)
    fund_table = _FUNDING_TABLES[asset]
    oi_carry, fund_carry = _oi_carry(secs), _fund_carry(secs)

    con = queries._ro_con()
    try:
        start = queries._candle_window(con, asset, tf, bars, after, now_s)
        # Incremental refresh: fetch a few earlier bars so the first returned
        # bar's OI delta and carried values are computed, then trim to start.
        fetch_start = start - secs * (oi_carry + 1) if after else start
        rows = _perp_rows(con, table, fetch_start)
        spot = _spot_map(con, spot_table, fetch_start) if spot_table else None
        oi_ts, oi_vals = (_series(con, oi_table, "oi_close", fetch_start - 86400)
                          if oi_table else ([], []))
        f_ts, f_vals = _funding_series(con, fund_table, fetch_start - 86400)
        thr = _thresholds(con, asset, tf, now_s)
        ssq = _ssq_gauge(con, now) if asset == "BTC" else None
        tape = _tape(con, asset, now_s)
    finally:
        con.close()

    folded = _fold(rows, spot, secs, native, now_s)
    oi = (_attach(folded, oi_ts, oi_vals, secs, oi_carry) if oi_table
          else [None] * len(folded))
    fund = _attach(folded, f_ts, f_vals, secs, fund_carry)

    out: list[dict] = []
    prev_oi: float | None = None
    for b, o, f in zip(folded, oi, fund):
        perp_cvd = b["perp_cvd"] if b["perp_ok"] else None
        spot_ok = spot is not None and b["n"] > 0 and b["n_spot"] == b["n"]
        spot_cvd = b["spot_cvd"] if spot_ok else None
        div = (spot_cvd - perp_cvd
               if spot_ok and perp_cvd is not None else None)
        basis = ((b["perp_close"] - b["spot_close"]) / b["spot_close"] * 1e4
                 if spot_ok and b["spot_close"] and b["perp_close"] is not None
                 else None)
        doi = ((o - prev_oi) / prev_oi * 100
               if o is not None and prev_oi else None)
        prev_oi = o
        out.append({
            "time": b["time"],
            "perp_cvd": _r(perp_cvd, 4),
            "spot_cvd": _r(spot_cvd, 4),
            "divergence": _r(div, 4),
            "oi_close": _r(o, 3),
            "oi_delta_pct": _r(doi, 4),
            "funding": _r(f, 8),
            "basis_bp": _r(basis, 2),
            "label": _label(perp_cvd, doi, thr["cvd_abs_p60"], thr["oi_abs_p60"]),
        })
    if after:
        out = [b for b in out if b["time"] >= start]

    return {
        "asset": asset, "tf": tf, "source": table,
        "spot_source": spot_table, "oi_source": oi_table,
        "funding_source": fund_table,
        "units": {"cvd": "base asset (taker buy − taker sell)",
                  "oi": "contracts (base asset)",
                  "funding": "decimal per 8h settlement",
                  "basis": "bp, (perp − spot) / spot"},
        "bars": out,
        "last_time": out[-1]["time"] if out else None,
        "server_time": now_s,
        "thresholds": thr,
        "tape": tape,
        "ssq": ssq,
    }


# ─── /api/positioning ────────────────────────────────────────────────────────

def _daily_closes(con, table: str, since_ts: int) -> dict[int, float]:
    """{day_number: close at 00:00 UTC} — daily rows are the 00:00-stamped
    rows of the hourly / 15m table."""
    rows = con.execute(
        f"SELECT timestamp, close FROM {table} WHERE timestamp >= ? "
        f"AND timestamp % 86400 = 0 AND close IS NOT NULL ORDER BY timestamp",
        (since_ts,)).fetchall()
    return {int(r[0]) // 86400: float(r[1]) for r in rows}


def _funding_daily_means(con, asset: str, until_ts: int) -> dict[str, float]:
    """Replica of strategies.support.funding.daily_means_rate (same SQL) on
    the read-only connection: {date_iso: AVG(fr_close)} over settlements."""
    rows = con.execute(
        f"SELECT date(timestamp,'unixepoch'), AVG(fr_close) "
        f"FROM {_FUNDING_TABLES[asset]} "
        f"WHERE timestamp <= ? AND fr_close IS NOT NULL AND timestamp % ? = 0 "
        f"GROUP BY 1 ORDER BY 1", (until_ts, SETTLEMENT_PERIOD_SECONDS)).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def _rank_prior(vals: np.ndarray, i: int, window: int) -> float | None:
    """Fraction of the `window` rows before i strictly below vals[i]."""
    if i < window:
        return None
    return float((vals[i - window:i] < vals[i]).mean())


def _decile(rank: float) -> int:
    return min(10, int(rank * 10) + 1)


def _cpr_gate(lsr_by_date: dict[str, float], fund_daily: dict[str, float],
              panel_date: date) -> dict:
    """The two positioning conditions of CPR (timing_anomalies/internal/
    cpr/signal.py::_evaluate_today), same conventions: percentile LEVEL
    over the PCTILE_WINDOW dates before the panel date (np.percentile,
    linear), today <= level; 3-day funding mean over three consecutive
    keys of the sorted daily-means map. Trend conditions (EMA20/50) are
    not replicated — this is the positioning view only."""
    window, thr = cpr_cfg.PCTILE_WINDOW, cpr_cfg.PCTILE_THRESHOLD
    pd_iso = panel_date.isoformat()
    out = {"date": pd_iso, "window": window, "threshold": thr,
           "ls_ratio": None, "ls_p20": None, "ls_ok": None,
           "fund_3d": None, "fund_p20": None, "fund_ok": None,
           "funding_asset": "BTC", "reason": None}
    fkeys = sorted(fund_daily)
    fidx = {d: i for i, d in enumerate(fkeys)}

    def fund3(d: str) -> float | None:
        i = fidx.get(d)
        if i is None or i < 2:
            return None
        return float(np.mean([fund_daily[k] for k in fkeys[i - 2:i + 1]]))

    ls_today = lsr_by_date.get(pd_iso)
    f_today = fund3(pd_iso)
    if ls_today is None or f_today is None:
        out["reason"] = "missing_data"
        return out
    dates = [(panel_date - timedelta(days=k)).isoformat()
             for k in range(window, 0, -1)]
    ls_win = [lsr_by_date[d] for d in dates if d in lsr_by_date]
    f_win = [v for v in (fund3(d) for d in dates) if v is not None]
    if len(ls_win) < 30 or len(f_win) < 30:
        out["reason"] = "pctile_window_too_thin"
        return out
    ls_p = float(np.percentile(ls_win, thr * 100))
    f_p = float(np.percentile(f_win, thr * 100))
    out.update(ls_ratio=_r(ls_today, 4), ls_p20=_r(ls_p, 4),
               ls_ok=bool(ls_today <= ls_p),
               fund_3d=_r(f_today, 8), fund_p20=_r(f_p, 8),
               fund_ok=bool(f_today <= f_p))
    return out


def _ls_circuit_breaker(long_pct_by_date: dict[str, float],
                        today_iso: str) -> dict:
    """regime_jplus.classify_day's LS circuit breaker, evaluated for
    `today`: for each classification day T in the last 8 days, shift =
    long_pct[T-1] − long_pct[T-8]; shift < −15 arms 'uncertain' until
    T + 7. Active if today <= the latest until."""
    today = date.fromisoformat(today_iso)
    until: date | None = None
    latest_shift: float | None = None
    for k in range(_CB_DAYS, -1, -1):
        t = today - timedelta(days=k)
        det = (t - timedelta(days=1)).isoformat()
        d7 = (t - timedelta(days=8)).isoformat()
        if det in long_pct_by_date and d7 in long_pct_by_date:
            shift = long_pct_by_date[det] - long_pct_by_date[d7]
            if k == 0:
                latest_shift = shift
            if shift < _CB_SHIFT:
                cand = t + timedelta(days=_CB_DAYS)
                if until is None or cand > until:
                    until = cand
    active = until is not None and today <= until
    return {"shift": _r(latest_shift, 2), "active": bool(active),
            "until": until.isoformat() if active else None,
            "threshold": _CB_SHIFT, "days": _CB_DAYS}


def positioning(asset: str = "BTC") -> dict:
    asset = (asset or "BTC").upper()
    if asset not in _FUNDING_TABLES:
        raise ValueError(f"unsupported asset: {asset}")
    now = queries._now()
    now_s = int(now.timestamp())
    today_iso = _utc_date(now_s)
    limit_s = botlib.FRESHNESS_CONTRACTS[_LSR_TABLE][2]

    con = queries._ro_con()
    try:
        lsr = con.execute(
            f"SELECT timestamp, ratio, long_pct, short_pct FROM {_LSR_TABLE} "
            f"WHERE asset=? AND ratio IS NOT NULL ORDER BY timestamp",
            (asset,)).fetchall()
        first_ts = int(lsr[0]["timestamp"]) if lsr else now_s
        closes = _daily_closes(con, _DAILY_CLOSE_TABLES[asset], first_ts - 86400)
        fund_daily = _funding_daily_means(con, "BTC", now_s)   # CPR uses BTC funding
    finally:
        con.close()

    base = {"asset": asset, "generated_utc": now.isoformat(timespec="seconds"),
            "rank_window": _LSR_RANK_WINDOW, "fwd_days": _FWD_DAYS}
    if not lsr:
        return {**base, "latest": None, "pct_rank_365": None, "decile": None,
                "decile_label": None, "decile_stats": [], "uncond": None,
                "cpr": None, "regime_cb": None}

    days = np.array([int(r["timestamp"]) // 86400 for r in lsr])
    ratios = np.array([float(r["ratio"]) for r in lsr])
    last = lsr[-1]
    age_s = now_s - int(last["timestamp"])

    # decile scoreboard over the whole history
    by_dec: dict[int, list[float]] = {d: [] for d in range(1, 11)}
    for i in range(_LSR_RANK_WINDOW, len(ratios)):
        rk = _rank_prior(ratios, i, _LSR_RANK_WINDOW)
        c1 = closes.get(int(days[i]) + 1)
        c21 = closes.get(int(days[i]) + 1 + _FWD_DAYS)
        if rk is None or c1 is None or c21 is None or c1 <= 0:
            continue
        by_dec[_decile(rk)].append(c21 / c1 - 1)
    all_rets = [x for v in by_dec.values() for x in v]

    def stats(rets: list[float]) -> dict:
        return {"n": len(rets),
                "mean_ret_pct": _r(np.mean(rets) * 100, 2) if rets else None,
                "hit_pct": _r(np.mean([x > 0 for x in rets]) * 100, 1) if rets else None}

    rank = _rank_prior(ratios, len(ratios) - 1, _LSR_RANK_WINDOW)
    dec = None if rank is None else _decile(rank)
    lsr_by_date = {_utc_date(int(r["timestamp"])): float(r["ratio"]) for r in lsr}
    lp_by_date = {_utc_date(int(r["timestamp"])): float(r["long_pct"]) for r in lsr
                  if r["long_pct"] is not None}
    panel_date = (now - timedelta(days=1)).date()

    return {
        **base,
        "latest": {
            "date": _utc_date(int(last["timestamp"])),
            "ts": int(last["timestamp"]),
            "ratio": _r(last["ratio"], 4),
            "long_pct": _r(last["long_pct"], 2),
            "short_pct": _r(last["short_pct"], 2),
            "age_s": age_s,
            "stale": bool(age_s > limit_s),
        },
        "pct_rank_365": _r(rank, 4),
        "decile": dec,
        "decile_label": None if dec is None else f"D{dec}",
        "decile_stats": [{"decile": d, **stats(by_dec[d])} for d in range(1, 11)],
        "uncond": stats(all_rets),
        "cpr": _cpr_gate(lsr_by_date, fund_daily, panel_date),
        "regime_cb": (_ls_circuit_breaker(lp_by_date, today_iso)
                      if asset == "BTC" else None),
    }


# ─── /api/profile (delta by price, from 15m bars) ────────────────────────────

def profile(asset: str = "BTC", hours: int = 24, buckets: int = 24) -> dict:
    """Each 15m bar's taker delta spread uniformly over the part of its
    low..high range that falls in each price bucket (a zero-range bar goes
    to the bucket holding its close). Bar-range resolution — honest but
    coarse; there is no live footprint table."""
    asset = (asset or "BTC").upper()
    if asset not in _PERP_15M:
        raise ValueError(f"unsupported asset: {asset}")
    hours, buckets = int(hours), int(buckets)
    if not (1 <= hours <= 168) or not (4 <= buckets <= 100):
        raise ValueError(f"bad profile window: hours={hours} buckets={buckets}")
    now_s = int(queries._now().timestamp())
    start = now_s - hours * 3600
    con = queries._ro_con()
    try:
        rows = con.execute(
            f"SELECT timestamp, high, low, close, volume_buy, volume_sell "
            f"FROM {_PERP_15M[asset]} WHERE timestamp >= ? AND timestamp <= ? "
            f"ORDER BY timestamp", (start, now_s)).fetchall()
    finally:
        con.close()
    rows = [r for r in rows if r["volume_buy"] is not None
            and r["volume_sell"] is not None and r["high"] is not None
            and r["low"] is not None]
    base = {"asset": asset, "hours": hours, "from": start, "to": now_s,
            "n_bars": len(rows)}
    if not rows:
        return {**base, "price_lo": None, "price_hi": None, "bucket_width": None,
                "last_price": None, "total_delta": 0.0, "max_abs": 0.0,
                "buckets": []}

    lo = min(float(r["low"]) for r in rows)
    hi = max(float(r["high"]) for r in rows)
    width = (hi - lo) / buckets
    acc = [0.0] * buckets

    def kidx(p: float) -> int:
        if width <= 0:
            return 0
        return min(buckets - 1, max(0, int((p - lo) / width)))

    total = 0.0
    for r in rows:
        delta = float(r["volume_buy"]) - float(r["volume_sell"])
        total += delta
        h, l = float(r["high"]), float(r["low"])
        span = h - l
        if width <= 0 or span <= 0:
            acc[kidx(float(r["close"]))] += delta
            continue
        for k in range(kidx(l), kidx(h) + 1):
            blo = lo + k * width
            ov = max(0.0, min(blo + width, h) - max(blo, l))
            acc[k] += delta * ov / span
    max_abs = max(abs(v) for v in acc)
    return {
        **base,
        "price_lo": lo, "price_hi": hi, "bucket_width": width,
        "last_price": float(rows[-1]["close"]),
        "total_delta": _r(total, 4), "max_abs": _r(max_abs, 4),
        "buckets": [{"lo": _r(lo + k * width, 2), "hi": _r(lo + (k + 1) * width, 2),
                     "delta": _r(acc[k], 4)} for k in reversed(range(buckets))],
    }
