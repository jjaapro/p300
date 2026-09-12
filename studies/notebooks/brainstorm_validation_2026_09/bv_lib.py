"""bv_lib — shared, READ-ONLY helpers for the brainstorm validation study.

Everything here reads prod.db through ``file:...?mode=ro`` and reads the
``ai_trading`` caches as plain pickles (never through their loader, which
refetches and rewrites stale files).  Nothing writes outside this folder.

Sections
  1. paths / connections / json
  2. loaders (p300 tables, brainstorm caches, Binance funding fetch)
  3. statistics: exact ports of the brainstorm's zroll / event study, plus
     cluster-robust t, block bootstrap, DSR wrappers
  4. signals: momentum, absorption, Bollinger squeeze, RSI (ports)
  5. the cross-margin sizing simulator (port of run_leverage_sim2.simulate,
     with exposure tracking)
  6. ADX helpers: harness / s005 imports, ledger -> daily returns, funding accrual
"""
from __future__ import annotations

import json
import math
import pickle
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RESULTS = HERE / "results"
CACHE = HERE / "cache"
RESULTS.mkdir(exist_ok=True)
CACHE.mkdir(exist_ok=True)
AI_TRADING = Path(r"C:\Source\Repos\ai_trading")
SCALP_CACHE = AI_TRADING / "scalp_lab" / "cache"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.support import db  # noqa: E402
from studies.lib.validation import bootstrap as vboot  # noqa: E402
from studies.lib.validation import dsr_pbo  # noqa: E402

UTC = timezone.utc
BOOT_SEED = 42
BOOT_ITER = 5000
ETF_CUTOFF = "2024-01-11"


# ─── 1. paths / connections / json ───────────────────────────────────────────

def utc(ts) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=UTC)


def iso(ts) -> str:
    return utc(ts).strftime("%Y-%m-%d")


def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)


def q(sql: str, params=()):
    con = ro_connect()
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def env_info() -> dict:
    return dict(python=sys.version.split()[0], numpy=np.__version__,
                pandas=pd.__version__,
                run_at=datetime.now(UTC).isoformat(timespec="seconds"))


def _jsonable(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if not math.isfinite(v) else v
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return [_jsonable(x) for x in o.tolist()]
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(x) for x in o]
    if isinstance(o, (datetime, pd.Timestamp)):
        return o.isoformat()
    return o


def jdump(obj, name: str) -> Path:
    path = RESULTS / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_jsonable(obj), fh, indent=2)
    return path


def jload(name: str):
    with open(RESULTS / name, encoding="utf-8") as fh:
        return json.load(fh)


# ─── 2. loaders ──────────────────────────────────────────────────────────────

def load_hourly(table: str = "cd_spot_binance", start_ts: int | None = None,
                with_taker: bool = True) -> pd.DataFrame:
    """Open-stamped hourly bars: ts, open, high, low, close, volume[, volume_buy]."""
    cols = "timestamp, open, high, low, close, volume" + (", volume_buy" if with_taker else "")
    sql = f"SELECT {cols} FROM {table} WHERE open > 0 AND close > 0"
    params: tuple = ()
    if start_ts is not None:
        sql += " AND timestamp >= ?"
        params = (int(start_ts),)
    sql += " ORDER BY timestamp"
    con = ro_connect()
    try:
        df = pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
    df = df.rename(columns={"timestamp": "ts"})
    df["ts"] = df["ts"].astype(np.int64)
    if with_taker:
        df["volume_buy"] = df["volume_buy"].astype(float).fillna(0.0)
    return df


def daily_from_intraday(df: pd.DataFrame, phase_hour: int = 0, step_s: int = 3600,
                        min_rows: int = 1, require_complete: bool = True,
                        drop_today: bool = True) -> pd.DataFrame:
    """Aggregate open-stamped intraday bars into daily bars whose day starts at
    ``phase_hour`` UTC.  Returns ts (day start), dt (ISO of day start), open, high,
    low, close, volume[, volume_buy], n_rows."""
    ts = df["ts"].to_numpy(np.int64)
    day_start = ((ts - phase_hour * 3600) // 86400) * 86400 + phase_hour * 3600
    g = df.assign(day_start=day_start).groupby("day_start", sort=True)
    spec = dict(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum"),
                n_rows=("ts", "size"), last_ts=("ts", "max"), first_ts=("ts", "min"))
    if "volume_buy" in df.columns:
        spec["volume_buy"] = ("volume_buy", "sum")
    out = g.agg(**spec).reset_index().rename(columns={"day_start": "ts"})
    keep = out["n_rows"] >= min_rows
    if require_complete:
        # the day must start with its first bar (open is the true daily open) and end with its last
        keep &= (out["last_ts"] + step_s >= out["ts"] + 86400) & (out["first_ts"] == out["ts"])
    if drop_today:
        today_start = (int(time.time()) - phase_hour * 3600) // 86400 * 86400 + phase_hour * 3600
        keep &= out["ts"] < today_start
    out = out[keep].drop(columns=["last_ts", "first_ts"]).reset_index(drop=True)
    out["dt"] = [iso(t) for t in out["ts"]]
    return out


def load_daily_btc(phase_hour: int = 0, require_complete: bool = True) -> pd.DataFrame:
    """BTC spot daily from cd_spot_binance hourly (the live ADX signal source)."""
    return daily_from_intraday(load_hourly("cd_spot_binance"), phase_hour, 3600,
                               min_rows=20, require_complete=require_complete)


def daily_from_1m_sql(table: str, phase_hour: int = 0, min_rows: int = 1000) -> pd.DataFrame:
    """Daily OHLCV aggregated in SQL from a 1m table (open_time in ms)."""
    ph = phase_hour * 3600
    con = ro_connect()
    try:
        rows = con.execute(
            f"SELECT ((open_time/1000) - {ph})/86400 AS d, MIN(open_time), MAX(open_time), "
            f"MAX(high), MIN(low), SUM(volume), COUNT(*) FROM {table} "
            f"WHERE open > 0 AND close > 0 GROUP BY d ORDER BY d").fetchall()
        keys = [r[1] for r in rows] + [r[2] for r in rows]
        oc: dict[int, tuple[float, float]] = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            for k, o, c in con.execute(
                    f"SELECT open_time, open, close FROM {table} WHERE open_time IN "
                    f"({','.join('?' * len(chunk))})", chunk):
                oc[int(k)] = (float(o), float(c))
    finally:
        con.close()
    recs = []
    now_day = (int(time.time()) - ph) // 86400
    for d, mn, mx, hi, lo, vol, n in rows:
        day_start = int(d) * 86400 + ph
        if n < min_rows or int(d) >= now_day:
            continue
        if mx // 1000 + 60 < day_start + 86400:
            continue
        recs.append(dict(ts=day_start, dt=iso(day_start), open=oc[int(mn)][0], high=float(hi),
                         low=float(lo), close=oc[int(mx)][1], volume=float(vol), n_rows=int(n)))
    return pd.DataFrame(recs)


def load_15m(table: str = "cd_spot_15m") -> pd.DataFrame:
    con = ro_connect()
    try:
        df = pd.read_sql_query(
            f"SELECT timestamp, open, high, low, close, volume, volume_buy FROM {table} "
            f"WHERE open > 0 AND close > 0 ORDER BY timestamp", con)
    finally:
        con.close()
    df = df.rename(columns={"timestamp": "ts"})
    df["ts"] = df["ts"].astype(np.int64)
    df["volume_buy"] = df["volume_buy"].astype(float).fillna(0.0)
    return df


def load_5m_from_5s(refresh: bool = False) -> pd.DataFrame:
    """cd_spot_5s aggregated to complete 5-minute bars (cached pickle)."""
    path = CACHE / "spot_5m_from_5s.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    con = ro_connect()
    try:
        mn, mx = con.execute("SELECT MIN(timestamp), MAX(timestamp) FROM cd_spot_5s").fetchone()
        parts = []
        t0 = int(mn) - int(mn) % 300
        while t0 <= mx:
            t1 = t0 + 30 * 86400
            df = pd.read_sql_query(
                "SELECT timestamp, open, high, low, close, volume, volume_buy FROM cd_spot_5s "
                "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp", con, params=(t0, t1))
            if len(df):
                b = (df["timestamp"] // 300 * 300).astype(np.int64)
                agg = df.groupby(b, sort=True).agg(
                    open=("open", "first"), high=("high", "max"), low=("low", "min"),
                    close=("close", "last"), volume=("volume", "sum"),
                    volume_buy=("volume_buy", "sum"), n=("timestamp", "size"))
                parts.append(agg)
            t0 = t1
    finally:
        con.close()
    out = pd.concat(parts)
    out.index.name = "ts"
    out = out.reset_index()
    out = out[out["n"] == 60].reset_index(drop=True)
    out.to_pickle(path)
    return out


def load_screener_daily(min_days: int = 800, exclude=("BTCUSDT", "ETHUSDT")) -> dict[str, pd.DataFrame]:
    con = ro_connect()
    try:
        df = pd.read_sql_query(
            "SELECT asset, ts, open, high, low, close, volume FROM screener_klines_daily "
            "WHERE open > 0 AND close > 0 ORDER BY asset, ts", con)
    finally:
        con.close()
    out = {}
    for a, g in df.groupby("asset"):
        if a in exclude or len(g) < min_days:
            continue
        g = g.drop(columns="asset").reset_index(drop=True)
        g["ts"] = g["ts"].astype(np.int64)
        g["dt"] = [iso(t) for t in g["ts"]]
        out[a] = g
    return out


def load_bitstamp_json() -> pd.DataFrame:
    with open(ROOT / "studies" / "material" / "bitstamp_btcusd_daily.json", encoding="utf-8") as fh:
        d = json.load(fh)
    df = pd.DataFrame(d)
    df["ts"] = df["ts"].astype(np.int64)
    return df


def scalp_pickle(name: str):
    with open(SCALP_CACHE / name, "rb") as fh:
        return pickle.load(fh)


def bitstamp_pickle(step: int = 86400) -> tuple[list, str]:
    files = sorted(SCALP_CACHE.glob(f"bitstamp_btcusd_{step}_*.pkl"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"no bitstamp pickle for step {step}")
    with open(files[-1], "rb") as fh:
        return pickle.load(fh), files[-1].name


def bars_to_df(bars) -> pd.DataFrame:
    """scalp_lab bar list [t_ms, o, h, l, c, v, taker_buy] -> DataFrame."""
    a = np.asarray(bars, dtype=float)
    df = pd.DataFrame(a[:, :7], columns=["ts_ms", "open", "high", "low", "close", "volume", "volume_buy"])
    df["ts"] = (df["ts_ms"] // 1000).astype(np.int64)
    df["dt"] = [iso(t) for t in df["ts"]]
    return df.drop(columns="ts_ms")


def df_to_bars(df: pd.DataFrame) -> list:
    """DataFrame (ts s, open..close, volume[, volume_buy]) -> scalp_lab bar list."""
    vb = df["volume_buy"].to_numpy(float) if "volume_buy" in df.columns else np.zeros(len(df))
    return [[int(t) * 1000, float(o), float(h), float(l), float(c), float(v), float(tb)]
            for t, o, h, l, c, v, tb in zip(df["ts"], df["open"], df["high"], df["low"],
                                            df["close"], df["volume"], vb)]


def _http_json(url: str, tries: int = 4):
    req = urllib.request.Request(url, headers={"User-Agent": "p300-brainstorm-validation/1.0"})
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1 + i)


def fetch_binance_funding(symbol: str = "BTCUSDT", max_age_s: int = 12 * 3600) -> list[tuple[int, float]]:
    """Full Binance USDT-M settlement history [(ms, rate)], cached in cache/.
    Timestamps normalised to the exact hour (Binance stamps a few with ms offsets)."""
    path = CACHE / f"binance_funding_{symbol}.json"
    rows: list[tuple[int, float]] = []
    first_perp_ms = 1_567_000_000_000          # 2019-08-28, before the first BTCUSDT settlement
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        rows = [(int(t), float(r)) for t, r in d["rows"]]
        if rows and rows[0][0] > 1_570_000_000_000:    # a truncated cache (the API ignores startTime=0)
            rows = []
        if rows and (time.time() * 1000 - d.get("fetched_ms", 0)) < max_age_s * 1000:
            return rows
    start = rows[-1][0] + 1 if rows else first_perp_ms
    while True:
        b = _http_json(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}"
                       f"&startTime={start}&limit=1000")
        if not b:
            break
        rows += [(int(round(int(x["fundingTime"]) / 3600000.0)) * 3600000, float(x["fundingRate"]))
                 for x in b]
        if len(b) < 1000:
            break
        start = int(b[-1]["fundingTime"]) + 1
        time.sleep(0.25)
    dd: dict[int, float] = {}
    for t, r in rows:
        dd[t] = r
    rows = sorted(dd.items())
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"symbol": symbol, "fetched_ms": int(time.time() * 1000),
                   "source": "https://fapi.binance.com/fapi/v1/fundingRate", "rows": rows}, fh)
    return rows


# ─── 3. statistics ───────────────────────────────────────────────────────────

def zroll_exact(x, w: int) -> np.ndarray:
    """Exact vectorised equivalent of the brainstorm ``zroll``: z-score of x[i]
    against the population mean/sd of the w bars BEFORE i (x[i-w:i])."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    if n <= w:
        return out
    cs = np.cumsum(np.insert(x, 0, 0.0))
    cs2 = np.cumsum(np.insert(x * x, 0, 0.0))
    i = np.arange(w, n)
    m = (cs[i] - cs[i - w]) / w
    v = np.maximum((cs2[i] - cs2[i - w]) / w - m * m, 0.0)
    s = np.sqrt(v)
    ok = s > 0
    out[i[ok]] = (x[i[ok]] - m[ok]) / s[ok]
    return out


def event_study(mask, direction, close, H: int, *, min_n: int = 30, drift: str = "full",
                cluster_mult: int = 5, warm: int = 0, sigma_mode: str = "bar",
                trailing_w: int = 365) -> dict | None:
    """Drift-adjusted, non-overlapping forward-return test.

    Exact port of the brainstorm ``stats``/``evaluate`` conventions:
      fwd[i] = close[i+H]/close[i] - 1;  greedy keep if i - last >= H;
      adj = direction * (fwd - mu);  t on adj (ddof=1);
      episode clusters = runs of kept entries separated by < cluster_mult*H bars.
    drift: "full" (mean fwd over the whole panel, their default), "none",
           "trailing" (causal mean of the fwd values known at i, over trailing_w bars).
    sigma_mode: "bar" -> capture = m / (sd_bar*sqrt(H)) (absorption scripts);
                "fwd" -> capture = m / sd(fwd over the panel) (bands script).
    warm: first bar eligible (bands/oscillator scripts use 60).
    """
    close = np.asarray(close, dtype=float)
    direction = np.asarray(direction, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    n = len(close)
    if n <= H + warm:
        return None
    fwd = np.full(n, np.nan)
    fwd[:n - H] = close[H:] / close[:n - H] - 1.0
    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1.0
    sd_bar = float(ret.std())
    fwd_panel = fwd[warm:n - H]
    if drift == "full":
        mu = np.full(n, float(np.nanmean(fwd_panel)))
    elif drift == "none":
        mu = np.zeros(n)
    elif drift == "trailing":
        mu = pd.Series(fwd).shift(H).rolling(trailing_w, min_periods=max(30, trailing_w // 3)).mean().to_numpy()
    else:
        raise ValueError(drift)
    sigma_H = sd_bar * math.sqrt(H) if sigma_mode == "bar" else float(np.nanstd(fwd_panel, ddof=1))
    idx = np.flatnonzero(mask & np.isfinite(fwd) & np.isfinite(mu))
    idx = idx[idx >= warm]
    keep, last = [], -10 ** 9
    for i in idx:
        if i - last >= H:
            keep.append(int(i))
            last = int(i)
    if len(keep) < min_n:
        return None
    k = np.asarray(keep)
    d = direction[k]
    adj = d * (fwd[k] - mu[k])
    raw = d * fwd[k]
    m = float(adj.mean())
    sd = float(adj.std(ddof=1))
    t = m / (sd / math.sqrt(len(adj))) if sd > 0 else 0.0
    clusters, cur = [], [0]
    for j in range(1, len(k)):
        if k[j] - k[j - 1] < cluster_mult * H:
            cur.append(j)
        else:
            clusters.append(cur)
            cur = [j]
    clusters.append(cur)
    cm = np.array([adj[np.asarray(g)].mean() for g in clusters])
    tc = (float(cm.mean() / (cm.std(ddof=1) / math.sqrt(len(cm))))
          if len(cm) > 2 and cm.std(ddof=1) > 0 else float("nan"))
    long = d > 0
    return dict(n=int(len(k)), mean_bp=m * 1e4, t=t, tc=tc, g=int(len(cm)),
                win=float((adj > 0).mean() * 100), capture=m / sigma_H * 100 if sigma_H > 0 else float("nan"),
                sigma_H_pct=sigma_H * 100, drift_bp=float(np.nanmean(mu[k])) * 1e4,
                raw_mean_bp=float(raw.mean()) * 1e4,
                n_long=int(long.sum()), long_mean_bp=(float(adj[long].mean()) * 1e4 if long.any() else float("nan")),
                n_short=int((~long).sum()), short_mean_bp=(float(adj[~long].mean()) * 1e4 if (~long).any() else float("nan")),
                idx=k, adj=adj, raw=raw, dir=d)


def summary(r: dict | None, keys=("n", "mean_bp", "t", "tc", "g", "win", "capture", "raw_mean_bp",
                                   "n_long", "long_mean_bp", "n_short", "short_mean_bp")) -> dict | None:
    if r is None:
        return None
    return {k: r[k] for k in keys if k in r}


def per_year(r: dict, times) -> pd.DataFrame:
    """Per-calendar-year table of a result dict (times = epoch-s array for all bars)."""
    times = np.asarray(times, dtype=np.int64)
    yrs = np.array([utc(times[i]).year for i in r["idx"]])
    rows = []
    for y in sorted(set(yrs.tolist())):
        a = r["adj"][yrs == y]
        sd = a.std(ddof=1) if len(a) > 1 else 0.0
        rows.append(dict(year=int(y), n=int(len(a)), mean_bp=float(a.mean()) * 1e4,
                         t=(float(a.mean() / (sd / math.sqrt(len(a)))) if sd > 0 else 0.0),
                         win=float((a > 0).mean() * 100)))
    return pd.DataFrame(rows)


def era_split(r: dict, times, cutoff: str = ETF_CUTOFF) -> dict:
    times = np.asarray(times, dtype=np.int64)
    dts = np.array([iso(times[i]) for i in r["idx"]])
    out = {}
    for name, sel in (("pre_etf", dts < cutoff), ("post_etf", dts >= cutoff)):
        a = r["adj"][sel]
        if len(a) < 2:
            out[name] = dict(n=int(len(a)))
            continue
        sd = a.std(ddof=1)
        out[name] = dict(n=int(len(a)), mean_bp=float(a.mean()) * 1e4,
                         t=float(a.mean() / (sd / math.sqrt(len(a)))) if sd > 0 else 0.0,
                         win=float((a > 0).mean() * 100))
    return out


def cluster_t(x, groups) -> tuple[float, int]:
    """Cluster-robust t of the mean of x with clusters ``groups`` (Liang–Zeger)."""
    x = np.asarray(x, dtype=float)
    groups = np.asarray(groups)
    m = x.mean()
    e = x - m
    s = pd.Series(e).groupby(groups).sum().to_numpy()
    g = len(s)
    var = (s ** 2).sum() / (len(x) ** 2) * (g / max(g - 1, 1))
    return (float(m / math.sqrt(var)) if var > 0 else 0.0), int(g)


def dsr(adj, n_trials: int) -> dict | None:
    return dsr_pbo.dsr_from_returns(np.asarray(adj, dtype=float), int(n_trials))


def boot_mean_ci(x, block: int = 1, n_iter: int = BOOT_ITER, seed: int = BOOT_SEED,
                 qs=(0.05, 0.5, 0.95)) -> dict:
    """Circular-block bootstrap of the mean of x."""
    x = np.asarray(x, dtype=float)
    T = len(x)
    if T < 2:
        return dict(mean=float(x.mean()) if T else float("nan"), n=T)
    rng = np.random.default_rng(seed)
    nb = (T + block - 1) // block
    boots = np.empty(n_iter)
    chunk = max(1, min(n_iter, 20_000_000 // max(T, 1)))      # bounded memory
    for s in range(0, n_iter, chunk):
        e = min(n_iter, s + chunk)
        starts = rng.integers(0, T, size=(e - s, nb))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
        idx = idx.reshape(e - s, -1)[:, :T]
        boots[s:e] = x[idx].mean(axis=1)
    qv = np.quantile(boots, qs)
    return dict(mean=float(x.mean()), n=int(T), block=block, n_iter=n_iter,
                ci=[float(v) for v in qv], p_gt_0=float((boots > 0).mean()))


def paired_block_boot(a, b, stat, block: int = 30, n_iter: int = 2000, seed: int = BOOT_SEED,
                      qs=(0.05, 0.5, 0.95)) -> dict:
    """Bootstrap of stat(a_resampled) - stat(b_resampled) with the SAME block indices."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    T = len(a)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_iter)
    for i in range(n_iter):
        idx = vboot.circular_block_indices(T, T, block, rng)
        diffs[i] = stat(a[idx]) - stat(b[idx])
    qv = np.quantile(diffs, qs)
    return dict(point=float(stat(a) - stat(b)), ci=[float(v) for v in qv],
                p_gt_0=float((diffs > 0).mean()), block=block, n_iter=n_iter)


def cagr_of(daily_ret, periods_per_year: float = 365.0) -> float:
    r = np.asarray(daily_ret, dtype=float)
    eq = np.prod(1.0 + r)
    if eq <= 0:
        return -1.0
    return float(eq ** (periods_per_year / len(r)) - 1.0)


def maxdd_of(daily_ret) -> float:
    r = np.asarray(daily_ret, dtype=float)
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.maximum(eq, 1e-12))
    return float((eq / peak - 1.0).min())


def sharpe_of(daily_ret, periods_per_year: float = 365.0) -> float:
    r = np.asarray(daily_ret, dtype=float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(periods_per_year)) if sd > 0 else 0.0


def curve_stats(daily_ret, periods_per_year: float = 365.0) -> dict:
    r = np.asarray(daily_ret, dtype=float)
    c = cagr_of(r, periods_per_year)
    d = maxdd_of(r)
    return dict(cagr=c, maxdd=d, sharpe=sharpe_of(r, periods_per_year),
                mar=(c / abs(d) if d < 0 else float("inf")), total=float(np.prod(1 + r) - 1),
                n_days=int(len(r)))


# ─── 4. signals (ports of the brainstorm definitions) ────────────────────────

def mom_signal(o, h, l, c, W: int = 60, z: float = 1.0):
    """Big-bar momentum: range z-score over the prior W bars > z; direction = sign(close-open)."""
    o, h, l, c = (np.asarray(x, dtype=float) for x in (o, h, l, c))
    rng = np.divide(h - l, c, out=np.zeros(len(c)), where=c > 0)
    hi = zroll_exact(rng, W)
    d = np.sign(c - o)
    d[d == 0] = 1.0
    with np.errstate(invalid="ignore"):
        mask = hi > z
    return mask, d


def abs_signal(c, v, tb, W: int = 288, z: float = 1.0, rz: float = 0.5):
    """Absorption A1: volume z > z, |imbalance| z > z, |return| z < rz; fade the aggressor."""
    c, v, tb = (np.asarray(x, dtype=float) for x in (c, v, tb))
    n = len(c)
    ret = np.zeros(n)
    ret[1:] = c[1:] / c[:-1] - 1.0
    imb = np.divide(2 * tb - v, v, out=np.zeros(n), where=v > 0)
    zv, zi, zr = zroll_exact(v, W), zroll_exact(np.abs(imb), W), zroll_exact(np.abs(ret), W)
    with np.errstate(invalid="ignore"):
        mask = (zv > z) & (zi > z) & (zr < rz) & np.isfinite(zv)
    return mask, -np.sign(imb)


def _sma(x, n):
    return pd.Series(x).rolling(n).mean().to_numpy()


def _rstd(x, n, ddof=0):
    return pd.Series(x).rolling(n).std(ddof=ddof).to_numpy()


def _pctrank(x, n):
    """Fraction of the trailing n-window (inclusive of the current bar) strictly below x[i]."""
    s = pd.Series(x)
    return s.rolling(n).apply(lambda w: float((w[:-1] < w[-1]).mean()), raw=True).to_numpy()


def bb_squeeze_signal(c, thr: float = 0.20, bb_len: int = 20, bb_k: float = 2.0,
                      rank_w: int = 126, std_ddof: int = 0, rank_mode: str = "incl"):
    """BBsqz{thr}_fire_dn / _fire_up as in run_bands_vol.build_signals.
    was[i] = pctrank(bandwidth)[i-1] < thr; exp[i] = bw[i] > bw[i-1]; fire = was & exp.
    Returns (fire_dn, fire_up)."""
    c = np.asarray(c, dtype=float)
    n = len(c)
    mid = _sma(c, bb_len)
    sd = _rstd(c, bb_len, std_ddof)
    ub, lb = mid + bb_k * sd, mid - bb_k * sd
    with np.errstate(invalid="ignore", divide="ignore"):
        bw = (ub - lb) / mid
    if rank_mode == "excl":
        bwp = _pctrank(bw, rank_w)
    else:  # inclusive rank (fraction of window <= current, minus own share)
        bwp = pd.Series(bw).rolling(rank_w).apply(lambda w: float((w <= w[-1]).mean()), raw=True).to_numpy()
    was = np.zeros(n, bool)
    exp_ = np.zeros(n, bool)
    with np.errstate(invalid="ignore"):
        was[1:] = bwp[:-1] < thr
        exp_[1:] = bw[1:] > bw[:-1]
    fire = was & exp_ & ~np.isnan(mid)
    with np.errstate(invalid="ignore"):
        fire_dn = fire & (c < mid)
        fire_up = fire & (c > mid)
    fire_dn[:60] = False
    fire_up[:60] = False
    return fire_dn, fire_up


def rsi_wilder(c, n: int = 14) -> np.ndarray:
    c = np.asarray(c, dtype=float)
    out = np.full(len(c), np.nan)
    if len(c) < n + 1:
        return out
    d = np.diff(c)
    g = np.maximum(d, 0.0)
    l = np.maximum(-d, 0.0)
    ag, al = g[:n].mean(), l[:n].mean()
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(c)):
        ag = (ag * (n - 1) + g[i - 1]) / n
        al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def cross_up(s, lvl: float) -> np.ndarray:
    s = np.asarray(s, dtype=float)
    out = np.zeros(len(s), bool)
    with np.errstate(invalid="ignore"):
        out[1:] = (s[:-1] <= lvl) & (s[1:] > lvl)
    return out


def rsi_enter_ob(c, n: int = 14, lvl: float = 75.0) -> np.ndarray:
    ev = cross_up(rsi_wilder(c, n), lvl)
    ev[:60] = False
    return ev


# ─── 5. sizing simulator (port of run_leverage_sim2.simulate + exposure) ────

def simulate_cross_margin(c, mask, direction, hold: int, funding_charges_per_bar: float,
                          fee_mode: str, frac: float, max_conc: int = 3, eq0: float = 10_000.0,
                          mmr: float = 0.005, funding_8h: float = 0.0001,
                          fee=None) -> dict:
    """Cross-margin, no manual stop, account-level liquidation, pure time exits.

    ``funding_charges_per_bar``: the brainstorm passes bars_per_8h=1 for daily bars,
    which charges 0.01% once per DAY (one third of the 8h reality).  Here the
    charge is funding_8h * funding_charges_per_bar per bar (1/3 reproduces their
    run; 3 is the correct daily charge for a perp).
    Returns final, maxdd, trades, liq, the equity curve, mean/max gross exposure.
    """
    fee = fee or {"taker": 0.0004, "maker": 0.0001}
    c = np.asarray(c, dtype=float)
    n = len(c)
    eq = eq0
    curve = np.full(n, np.nan)
    gross = np.zeros(n)
    conc = np.zeros(n, dtype=int)
    pos: list = []
    liq = trades = 0
    f = fee[fee_mode]
    for i in range(n):
        still = []
        for (px, d, xi, N) in pos:
            if i >= xi:
                eq += d * (c[i] / px - 1) * N - N * f
            else:
                still.append((px, d, xi, N))
        pos = still
        if pos and funding_charges_per_bar > 0:
            eq -= sum(N for (_, _, _, N) in pos) * funding_8h * funding_charges_per_bar
        if mask[i] and len(pos) < max_conc and eq > 0:
            N = frac * eq
            pos.append((c[i], direction[i], min(i + hold, n - 1), N))
            eq -= N * f
            trades += 1
        if pos:
            unreal = sum(d * (c[i] / px - 1) * N for (px, d, _, N) in pos)
            mtm = eq + unreal
            if mtm < mmr * sum(N for (_, _, _, N) in pos):
                eq = max(0.0, mtm)
                pos = []
                liq += 1
        else:
            mtm = eq
        curve[i] = mtm
        gross[i] = (sum(N for (_, _, _, N) in pos) / mtm) if (mtm > 0 and pos) else 0.0
        conc[i] = len(pos)
        if mtm <= 0:
            curve[i:] = 0.0
            break
    valid = curve[np.isfinite(curve)]
    peak = np.maximum.accumulate(valid)
    return dict(final=float(valid[-1]), maxdd=float((valid / np.maximum(peak, 1e-9) - 1).min() * 100),
                trades=trades, liq=liq, curve=curve, gross=gross, conc=conc,
                mean_gross=float(gross.mean()), mean_gross_when_in=float(gross[gross > 0].mean()) if (gross > 0).any() else 0.0,
                max_gross=float(gross.max()), max_conc=int(conc.max()),
                time_in_market=float((conc > 0).mean()))


# ─── 6. ADX helpers ──────────────────────────────────────────────────────────

def import_s005():
    if str(AI_TRADING) not in sys.path:
        sys.path.insert(0, str(AI_TRADING))
    from scalp_lab import s005  # type: ignore
    return s005


def import_adx_harness():
    p = ROOT / "studies" / "notebooks" / "adx_study"
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
    import harness  # type: ignore
    return harness


def df_to_candles(d: pd.DataFrame) -> list[dict]:
    return [dict(ts=int(r.ts), dt=str(r.dt), open=float(r.open), high=float(r.high),
                 low=float(r.low), close=float(r.close)) for r in d.itertuples(index=False)]


def agg_bars(bars, k: int, off: int = 0) -> list:
    """Port of run_s005_freq._agg: aggregate k consecutive bars anchored on multiples
    of k*step, shifted by off*step (the bar-phase construction)."""
    step = (bars[1][0] - bars[0][0]) // 1000
    g = step * k
    out, cur = [], None
    for r in bars:
        ts = r[0] // 1000
        if ts % g == off * step:
            if cur is not None:
                out.append(cur)
            cur = [r[0], r[1], r[2], r[3], r[4], r[5], 0.0]
        elif cur is not None:
            cur[2] = max(cur[2], r[2])
            cur[3] = min(cur[3], r[3])
            cur[4] = r[4]
            cur[5] += r[5]
    if cur is not None:
        out.append(cur)
    return out


def reprice_sl_at_close(trades: list[dict], candles: list[dict], cost_bp_rt: float = 10.0) -> list[dict]:
    """The brainstorm 'study fill': a stop breach detected on the bar range but the
    exit priced at that bar's CLOSE.  Only SL rows change."""
    dt_idx = {c["dt"]: i for i, c in enumerate(candles)}
    out = []
    for t in trades:
        t = dict(t)
        if t.get("reason") == "SL":
            i1 = dt_idx[str(t["exit_dt"]).replace(" (open)", "")]
            px = candles[i1]["close"]
            ep = t["entry_price"]
            g = (px - ep) / ep * 100 if t["dir"] == "long" else (ep - px) / ep * 100
            t["exit_price"] = px
            t["gross_pct"] = g
            t["net_pct"] = g - cost_bp_rt / 100.0
        out.append(t)
    return out


def ledger_to_daily_returns(trades: list[dict], candles: list[dict], cost_bp_rt: float = 10.0,
                            short_weight: float = 1.0, long_weight: float = 1.0,
                            funding_pct: dict | None = None) -> np.ndarray:
    """Daily mark-to-market return series of an ADX-harness ledger.

    A trade entered at the close of entry_dt earns the close-to-close return of
    every following day through exit_dt; an SL / ATR exit day earns
    exit_price/prev_close - 1 instead.  Fees: cost_bp_rt/2 per leg on the entry
    and exit days.  ``funding_pct`` (optional) maps a trade's entry_dt to the
    percent funding earned over the hold; it is spread evenly across the held days.
    """
    closes = np.array([c["close"] for c in candles], dtype=float)
    dt_idx = {c["dt"]: i for i, c in enumerate(candles)}
    r = np.zeros(len(candles))
    leg = cost_bp_rt / 2.0 / 1e4
    for t in trades:
        i0 = dt_idx[t["entry_dt"]]
        i1 = dt_idx[str(t["exit_dt"]).replace(" (open)", "")]
        sgn = 1.0 if t["dir"] == "long" else -1.0
        w = long_weight if t["dir"] == "long" else short_weight
        if w == 0.0:
            continue
        held = max(i1 - i0, 1)
        fund_day = 0.0
        if funding_pct and t["entry_dt"] in funding_pct:
            fund_day = funding_pct[t["entry_dt"]] / 100.0 / held
        for j in range(i0 + 1, i1 + 1):
            if j == i1 and t["reason"] in ("SL", "ATR_trail"):
                px = float(t["exit_price"])
            else:
                px = closes[j]
            r[j] += w * (sgn * (px / closes[j - 1] - 1.0) + fund_day)
        r[i0] -= w * leg
        r[i1] -= w * leg
    return r


def funding_pct_between(fund_rows: list[tuple[int, float]], ts0: int, ts1: int) -> float:
    """Sum of settlement rates with ts0 < t <= ts1 (epoch s), in percent of notional."""
    if not hasattr(funding_pct_between, "_arr") or funding_pct_between._src is not fund_rows:  # type: ignore
        funding_pct_between._src = fund_rows  # type: ignore
        funding_pct_between._arr = (np.array([t for t, _ in fund_rows], dtype=np.int64) // 1000,  # type: ignore
                                    np.array([r for _, r in fund_rows], dtype=float))
    ts, rates = funding_pct_between._arr  # type: ignore
    lo = np.searchsorted(ts, ts0, side="right")
    hi = np.searchsorted(ts, ts1, side="right")
    return float(rates[lo:hi].sum() * 100.0)


def trade_t(trades: list[dict], key: str = "net_pct") -> tuple[float, int]:
    x = np.array([t[key] for t in trades], dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0, len(x)
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))), len(x)
