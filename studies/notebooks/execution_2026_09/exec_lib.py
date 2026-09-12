"""Shared, read-only library for the execution study (README.md is the pre-registration).

Paths (1 m spot, 5 s spot) are cached as npz; events come from the sleeves' own validated ledgers;
`walk()` / `limit_fill()` are the fill simulator; statistics are day-block bootstraps.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass, field
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
BV_DIR = ROOT / "studies" / "notebooks" / "brainstorm_validation_2026_09"
for p in (str(ROOT), str(BV_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
import bv_lib as bv  # noqa: E402  (read-only helpers: ro_connect, boot_mean_ci, cluster_t, paired_block_boot)

PROD_DB = ROOT / "data" / "databases" / "prod.db"
SEED = 42
BOOT_ITER = 5000

# ── pre-registered assumptions (README) ─────────────────────────────────────
FEES_BP = dict(perp_maker=2.0, perp_taker=5.0, spot_maker=10.0, spot_taker=10.0,
               perp_maker_bnb=1.8, perp_taker_bnb=4.5)
AS_BP = dict(base=1.0, stressed=2.7, lo=0.2, hi=3.0)
TICK = dict(perp=0.1, spot=0.01)
CODED_COST_BP = {  # strategies/trades.py + sleeve configs (fee + slippage, round trip)
    "CHENTO_BTC": dict(sleeve=18.0, research=18.0),
    "CHENTO_ETH": dict(sleeve=18.0, research=18.0),
    "SHORT_SQUEEZE": dict(sleeve=25.0, research=4.0),   # sleeve 10 + 15; notebook 2 bp per leg
    "SQUEEZE_BULL": dict(sleeve=18.0, research=18.0),
    "ADX": dict(sleeve=15.0, research=10.0),            # sleeve 10 + 5 default slip; harness 10
}
PATIENCE_S = (60, 300, 900, 3600)
MARKOUT_S = (60, 300, 900, 3600)


# ── small utils ──────────────────────────────────────────────────────────────
def utc(ts: int | float) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def iso(ts) -> str:
    return utc(ts).strftime("%Y-%m-%d %H:%M")


def ro_connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)


def sql_df(sql: str, params=()) -> pd.DataFrame:
    con = ro_connect()
    try:
        return pd.read_sql(sql, con, params=params)
    finally:
        con.close()


def jdump(obj, name: str) -> Path:
    p = RESULTS / name
    p.write_text(json.dumps(bv._jsonable(obj), indent=1, default=str), encoding="utf-8")
    return p


def jload(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def env_info() -> dict:
    return dict(python=sys.version.split()[0], numpy=np.__version__, pandas=pd.__version__,
                run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


# ── price paths ──────────────────────────────────────────────────────────────
@dataclass
class PricePath:
    name: str
    step: int
    ts: np.ndarray   # int64 open time, seconds
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    vb: np.ndarray | None = None

    def idx_ge(self, t: int) -> int:
        """First bar whose open time is >= t."""
        return int(np.searchsorted(self.ts, int(t), side="left"))

    def idx_gt(self, t: int) -> int:
        """First bar whose open time is > t (== one past the last bar opening <= t)."""
        return int(np.searchsorted(self.ts, int(t), side="right"))

    def covers(self, t0: int, t1: int) -> bool:
        return self.ts[0] <= t0 and t1 <= self.ts[-1]

    def last_close_before(self, t: int) -> float:
        """Close of the last bar that CLOSED at or before t (what the paper bot books at a tick at t)."""
        i = self.idx_gt(int(t) - self.step)
        return float(self.c[i - 1]) if i > 0 else float("nan")

    @property
    def span(self) -> tuple[str, str]:
        return iso(self.ts[0]), iso(self.ts[-1] + self.step)


_SQL = {
    "btc_1m": ("SELECT open_time, open, high, low, close, volume FROM btc_1m ORDER BY open_time", 1000, 60),
    "eth_1m": ("SELECT open_time, open, high, low, close, volume FROM eth_1m ORDER BY open_time", 1000, 60),
    "spot_5s": ("SELECT timestamp, open, high, low, close, volume, volume_buy FROM cd_spot_5s ORDER BY timestamp", 1, 5),
}
_PATHS: dict[str, PricePath] = {}


def build_cache(force: bool = False) -> dict:
    """Verbatim copy of the path tables into npz (open time normalised to seconds)."""
    info = {}
    con = ro_connect()
    try:
        for name, (sql, div, step) in _SQL.items():
            f = CACHE / f"{name}.npz"
            if f.exists() and not force:
                continue
            t0 = time.time()
            a = np.array(con.execute(sql).fetchall(), dtype=np.float64)
            a[:, 0] = a[:, 0] // div
            ts = a[:, 0].astype(np.int64)
            keep = np.concatenate([[True], np.diff(ts) > 0])      # drop exact duplicates, keep first
            a, ts = a[keep], ts[keep]
            extra = {"vb": a[:, 6]} if a.shape[1] > 6 else {}
            np.savez(f, ts=ts, o=a[:, 1], h=a[:, 2], l=a[:, 3], c=a[:, 4], v=a[:, 5], **extra)
            info[name] = dict(rows=int(len(ts)), seconds=round(time.time() - t0, 1))
    finally:
        con.close()
    return info


def load_path(name: str) -> PricePath:
    if name not in _PATHS:
        f = CACHE / f"{name}.npz"
        if not f.exists():
            build_cache()
        z = np.load(f)
        _PATHS[name] = PricePath(name, _SQL[name][2], z["ts"], z["o"], z["h"], z["l"], z["c"], z["v"],
                                 z["vb"] if "vb" in z.files else None)
    return _PATHS[name]


def path_for(asset: str, res: str) -> PricePath:
    if res == "5s":
        if asset != "BTC":
            raise KeyError("5 s path exists for BTC spot only")
        return load_path("spot_5s")
    return load_path("btc_1m" if asset == "BTC" else "eth_1m")


# ── events ───────────────────────────────────────────────────────────────────
@dataclass
class Event:
    sleeve: str
    asset: str
    direction: int            # +1 long, -1 short
    signal_ts: int            # the instant the sleeve can act (close of the trigger bar)
    signal_price: float       # the price the sleeve books (trigger-bar close, venue terms)
    stop: float               # level in venue terms, at the signal price
    target: float
    tif_end: int              # last bar OPEN time (inclusive) the trade may still be open
    level_mode: str           # "relative" (stop distance + target R follow the fill), "fixed_stop", "pct"
    target_r: float
    stop_pct: float = 0.0     # for "pct"
    target_pct: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def risk(self) -> float:
        return abs(self.signal_price - self.stop)

    def levels_for_fill(self, fill: float, scale: float = 1.0) -> tuple[float, float, float]:
        """(stop, target, risk) for a trade filled at `fill` on a venue whose price is `scale` x the
        signal venue (spot/perp re-basing; 1.0 when walking the signal venue itself)."""
        d = self.direction
        if self.level_mode == "relative":
            dist = self.risk * scale
            stop = fill - d * dist
            target = fill + d * self.target_r * dist
            return stop, target, dist
        if self.level_mode == "fixed_stop":
            stop = self.stop * scale
            risk = (fill - stop) * d
            target = fill + d * self.target_r * risk
            return stop, target, risk
        if self.level_mode == "pct":
            stop = fill * (1 - d * self.stop_pct)
            target = fill * (1 + d * self.target_pct)
            return stop, target, abs(fill - stop)
        raise ValueError(self.level_mode)


def _ts_of(x) -> int:
    return int(pd.Timestamp(x).timestamp())


def load_chento(asset: str) -> list[Event]:
    f = ROOT / "studies" / "notebooks" / "overlay_study" / "results_backonly" / f"trades_{asset}.csv"
    t = pd.read_csv(f)
    aligned = (((t.direction == "long") & (t.okx_delta_z >= 0)) | ((t.direction == "short") & (t.okx_delta_z <= 0)))
    t = t[aligned].reset_index(drop=True)
    out = []
    for r in t.itertuples():
        ts_open = _ts_of(r.ts)
        d = 1 if r.direction == "long" else -1
        out.append(Event(sleeve=f"CHENTO_{asset}", asset=asset, direction=d, signal_ts=ts_open + 900,
                         signal_price=float(r.entry), stop=float(r.stop), target=float(r.target),
                         tif_end=ts_open + 72 * 3600 + 840, level_mode="relative", target_r=6.0,
                         extra=dict(ts_open=ts_open, r_csv=float(r.r_outcome))))
    return out


def load_squeeze_bull(bull_only: bool = True) -> list[Event]:
    f = ROOT / "studies" / "notebooks" / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv"
    t = pd.read_csv(f)
    if bull_only:
        t = t[t.regime_backonly == "bull_30d"]
    t = t[t.resolved.astype(bool)].reset_index(drop=True)
    out = []
    for r in t.itertuples():
        ts_open = _ts_of(r.ts)
        e = float(r.entry)
        out.append(Event(sleeve="SQUEEZE_BULL", asset="BTC", direction=1, signal_ts=ts_open + 3600,
                         signal_price=e, stop=e * 0.98, target=e * 1.03,
                         tif_end=ts_open + 48 * 3600 + 3540, level_mode="pct", target_r=1.5,
                         stop_pct=0.02, target_pct=0.03,
                         extra=dict(ts_open=ts_open, r_csv=float(r.r_outcome), exit_csv=str(r.exit_kind))))
    return out


def load_adx() -> list[dict]:
    f = BV_DIR / "results" / "c4_ledger_p300.csv"
    t = pd.read_csv(f)
    t = t[t.still_open.isna() | (t.still_open == False)]  # noqa: E712
    out = []
    for r in t.itertuples():
        e_ts = _ts_of(pd.Timestamp(r.entry_dt, tz="UTC")) + 86400
        x_ts = _ts_of(pd.Timestamp(r.exit_dt, tz="UTC")) + 86400
        out.append(dict(sleeve="ADX", asset="BTC", direction=1 if r.dir == "long" else -1, signal_ts=e_ts,
                        signal_price=float(r.entry_price), exit_ts=x_ts, exit_price=float(r.exit_price),
                        reason=str(r.reason), net_pct=float(r.net_pct), gross_pct=float(r.gross_pct),
                        sl_price=float(r.entry_price) * (1 - 0.10 * (1 if r.dir == "long" else -1))))
    return out


# ── short_squeeze: verbatim port of strategy_backtest.ipynb (cells 1-13) ────
SS_SESSIONS = {"asia": (0, 7), "london": (7, 14), "ny": (14, 21)}
SS_LOOKBACK_BARS = 24
SS_COOLDOWN_BARS = 16
SS_TIME_STOP_HOURS = 6
SS_SLIPPAGE_BP = 2
SS_WINDOW_BARS = 90 * 56
SS_PARAMS = dict(perp_cvd_pct_max=0.15, divergence_pct_min=0.70, close_in_range_min=0.10)


def _load_ts_table(table: str) -> pd.DataFrame:
    df = sql_df(f"SELECT * FROM {table} ORDER BY timestamp")
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts")
    return df[~df.index.duplicated(keep="last")]


def _rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    out = np.full(len(series), np.nan)
    arr = series.values
    for i in range(window, len(arr)):
        out[i] = (arr[i - window: i] <= arr[i]).mean()
    return pd.Series(out, index=series.index)


def short_squeeze_frame() -> tuple[pd.DataFrame, pd.Series]:
    """Returns (b15 enriched frame, long_trigger mask) exactly as the notebook builds them."""
    perp15 = _load_ts_table("cd_futures_15m")
    spot15 = _load_ts_table("cd_spot_15m")
    perp_h = _load_ts_table("cd_futures_ohlcv")
    oi_h = _load_ts_table("cd_open_interest")
    fund_h = _load_ts_table("cd_funding_rate")
    start = oi_h.index.min()
    perp15 = perp15.loc[start:]
    spot15 = spot15.loc[start:]
    perp_h = perp_h.loc[start:]

    b15 = pd.DataFrame(index=perp15.index)
    b15["o"] = perp15["open"]; b15["h"] = perp15["high"]
    b15["l"] = perp15["low"]; b15["c"] = perp15["close"]
    b15["perp_cvd"] = perp15["volume_buy"] - perp15["volume_sell"]
    b15["spot_cvd"] = (spot15["volume_buy"] - spot15["volume_sell"]).reindex(b15.index)
    b15 = b15.dropna(subset=["c", "perp_cvd", "spot_cvd"]).copy()

    def _session_of(h):
        for name, (lo, hi) in SS_SESSIONS.items():
            if lo <= h < hi:
                return name
        return None

    b15["session"] = b15.index.hour.map(_session_of)
    b15 = b15.dropna(subset=["session"]).copy()
    b15["date"] = b15.index.date
    b15["prior_low_24"] = b15["l"].rolling(SS_LOOKBACK_BARS + 1).min().shift(1)
    bar_range = (b15["h"] - b15["l"]).clip(lower=1e-9)
    b15["close_in_range"] = (b15["c"] - b15["l"]) / bar_range
    b15["divergence"] = b15["spot_cvd"] - b15["perp_cvd"]
    univ = b15[b15["session"].isin(["london", "ny"])].copy()
    univ["perp_cvd_pct"] = _rolling_percentile(univ["perp_cvd"], SS_WINDOW_BARS)
    univ["divergence_pct"] = _rolling_percentile(univ["divergence"], SS_WINDOW_BARS)
    b15["perp_cvd_pct"] = univ["perp_cvd_pct"].reindex(b15.index)
    b15["divergence_pct"] = univ["divergence_pct"].reindex(b15.index)

    def _agg_asia(g):
        asia = g[g.index.hour < 7]
        if len(asia) < 2:
            return pd.Series({"close_lt_open": False, "oi_pct": 0.0, "fund_mean": 0.0})
        oi_series = oi_h["oi_close"].reindex(asia.index).ffill(limit=3)
        oi_o, oi_c = oi_series.iloc[0], oi_series.iloc[-1]
        f = fund_h["fr_close"].reindex(asia.index).ffill(limit=8).mean()
        oi_pct = 0.0 if (pd.isna(oi_o) or pd.isna(oi_c)) else (oi_c - oi_o) / max(oi_o, 1e-9)
        return pd.Series({"close_lt_open": asia["close"].iloc[-1] < asia["open"].iloc[0],
                          "oi_pct": oi_pct, "fund_mean": f if pd.notna(f) else 0.0})

    perp_h_d = perp_h.copy()
    perp_h_d["date"] = perp_h_d.index.date
    day_ctx = perp_h_d.groupby("date").apply(_agg_asia, include_groups=False)
    if isinstance(day_ctx.index, pd.MultiIndex):
        day_ctx = day_ctx.unstack(-1)
    day_ctx["short_macro"] = day_ctx["close_lt_open"].astype(bool) & (day_ctx["oi_pct"] > 0.005) & (day_ctx["fund_mean"] < 0)
    b15["short_macro"] = b15["date"].map(day_ctx["short_macro"].to_dict()).fillna(False).astype(bool)

    in_window = b15["session"].isin(["london", "ny"])
    long_trigger = (in_window & b15["short_macro"] & (b15["l"] < b15["prior_low_24"])
                    & (b15["perp_cvd_pct"] < SS_PARAMS["perp_cvd_pct_max"])
                    & (b15["divergence_pct"] > SS_PARAMS["divergence_pct_min"])
                    & (b15["close_in_range"] >= SS_PARAMS["close_in_range_min"]))
    out = pd.Series(False, index=long_trigger.index)
    last = None
    for ts, v in long_trigger.items():
        if not v:
            continue
        if last is None or (ts - last) >= pd.Timedelta(minutes=15 * SS_COOLDOWN_BARS):
            out.loc[ts] = True
            last = ts
    return b15, out


def short_squeeze_notebook_simulate(b15: pd.DataFrame, trig: pd.Series, m1: PricePath, tp_R: float = 3.0) -> pd.DataFrame:
    """The notebook's `simulate()` on btc_1m spot with 2 bp per leg (parity gate)."""
    rows = []
    for ts in trig.index[trig]:
        bar = b15.loc[ts]
        entry = float(bar["c"]); stop = float(bar["l"]) * (1 - 0.001); risk = entry - stop
        if risk <= 0:
            continue
        target = entry + tp_R * risk
        t0 = int(ts.timestamp()) + 900
        i0, i1 = m1.idx_ge(t0), m1.idx_gt(t0 + SS_TIME_STOP_HOURS * 3600)
        if i1 <= i0:
            continue
        w = walk(m1, i0, i1, 1, stop, target)
        exit_price = w["price"]
        slip = SS_SLIPPAGE_BP / 1e4
        eff_e, eff_x = entry * (1 + slip), exit_price * (1 - slip)
        rows.append(dict(trigger_ts=ts, entry=entry, stop=stop, target=target, exit_price=exit_price,
                         exit_reason={"stop": "stop", "target": "target", "tif": "time"}[w["kind"]],
                         pnl_R=(eff_x - eff_e) / risk, risk_pct=risk / entry))
    return pd.DataFrame(rows)


def load_short_squeeze() -> tuple[list[Event], pd.DataFrame, pd.Series]:
    b15, trig = short_squeeze_frame()
    out = []
    for ts in trig.index[trig]:
        bar = b15.loc[ts]
        ts_open = int(ts.timestamp())
        entry = float(bar["c"]); stop = float(bar["l"]) * (1 - 0.001)
        if entry - stop <= 0:
            continue
        out.append(Event(sleeve="SHORT_SQUEEZE", asset="BTC", direction=1, signal_ts=ts_open + 900,
                         signal_price=entry, stop=stop, target=entry + 3.0 * (entry - stop),
                         tif_end=ts_open + 900 + SS_TIME_STOP_HOURS * 3600, level_mode="fixed_stop", target_r=3.0,
                         extra=dict(ts_open=ts_open)))
    return out, b15, trig


# ── fill simulator ───────────────────────────────────────────────────────────
def _first(cond: np.ndarray) -> int:
    idx = np.flatnonzero(cond)
    return int(idx[0]) if idx.size else -1


def walk(path: PricePath, i0: int, i1: int, direction: int, stop: float, target: float) -> dict:
    """Walk bars [i0, i1). Stop before target inside a bar (the sleeves' convention).
    Returns kind, idx, price (level fill), price_pathsem (stop: min/max(open, stop) = gap-through at the open)."""
    lo, hi = path.l[i0:i1], path.h[i0:i1]
    if lo.size == 0:
        return dict(kind="none", idx=-1, price=float("nan"), price_pathsem=float("nan"))
    if direction > 0:
        s, t = lo <= stop, hi >= target
    else:
        s, t = hi >= stop, lo <= target
    si, ti = _first(s), _first(t)
    if si < 0 and ti < 0:
        j = i1 - 1
        return dict(kind="tif", idx=j, price=float(path.c[j]), price_pathsem=float(path.c[j]))
    if si >= 0 and (ti < 0 or si <= ti):
        j = i0 + si
        o = float(path.o[j])
        ps = min(o, stop) if direction > 0 else max(o, stop)
        return dict(kind="stop", idx=j, price=float(stop), price_pathsem=ps)
    j = i0 + ti
    return dict(kind="target", idx=j, price=float(target), price_pathsem=float(target))


def limit_fill(path: PricePath, i0: int, i1: int, direction: int, limit: float, rule: str = "touch") -> tuple[int, float]:
    """Resting limit posted before bar i0, alive over bars [i0, i1). Returns (fill bar index or -1, fill price).
    A bar opening beyond the limit fills at its open (the order was already marketable)."""
    lo, hi, o = path.l[i0:i1], path.h[i0:i1], path.o[i0:i1]
    if lo.size == 0:
        return -1, float("nan")
    if direction > 0:
        cond = (lo <= limit) if rule == "touch" else (lo < limit)
    else:
        cond = (hi >= limit) if rule == "touch" else (hi > limit)
    k = _first(cond)
    if k < 0:
        return -1, float("nan")
    j = i0 + k
    oj = float(path.o[j])
    fill = min(limit, oj) if direction > 0 else max(limit, oj)
    return j, fill


def trade_r(direction: int, fill: float, exit_price: float, risk: float) -> float:
    return direction * (exit_price - fill) / risk


WALK_SLEEVES = ("CHENTO_BTC", "CHENTO_ETH", "SHORT_SQUEEZE", "SQUEEZE_BULL")
_EVENTS: dict[str, list[Event]] = {}


def load_events(sleeve: str) -> list[Event]:
    if sleeve not in _EVENTS:
        if sleeve.startswith("CHENTO_"):
            _EVENTS[sleeve] = load_chento(sleeve.split("_")[1])
        elif sleeve == "SHORT_SQUEEZE":
            _EVENTS[sleeve] = load_short_squeeze()[0]
        elif sleeve == "SQUEEZE_BULL":
            _EVENTS[sleeve] = load_squeeze_bull(bull_only=True)
        elif sleeve == "SQUEEZE_BULL_ALL":
            _EVENTS[sleeve] = load_squeeze_bull(bull_only=False)
        else:
            raise KeyError(sleeve)
    return _EVENTS[sleeve]


def context(ev: Event, path: PricePath, need_tif: bool = True) -> dict | None:
    """Bar indices for an event on a path: i0 = first bar opening at the signal instant (must exist),
    i1 = one past the last bar allowed by the TIF; paper = the close the paper bot books (spot terms);
    scale = spot/perp re-basing factor for the sleeve's levels. None when the path has a gap or no cover."""
    i0 = path.idx_ge(ev.signal_ts)
    if i0 <= 0 or i0 >= len(path.ts) or path.ts[i0] != ev.signal_ts:
        return None
    if need_tif and ev.tif_end > path.ts[-1]:
        return None
    i1 = path.idx_gt(ev.tif_end) if need_tif else min(i0 + 1, len(path.ts))
    paper = float(path.c[i0 - 1])
    # Convention (= the sleeves' own research notebooks and the live short_squeeze bot): the sleeve's VENUE
    # levels are tested against the SPOT path unscaled; R is measured in venue terms. `paper` is the spot
    # close at the signal; a spot fill f maps to venue terms as signal_price * f / paper.
    return dict(i0=i0, i1=i1, paper=paper, venue=ev.signal_price, scale=1.0)


def market_trade(ev: Event, path: PricePath, cx: dict, fill: float | None = None, start: int | None = None) -> dict:
    """Trade outcome (gross R) from a SPOT fill at `fill` (default: the paper close) walking bars [start, i1).
    Levels and R are in venue terms (see context())."""
    f_spot = cx["paper"] if fill is None else float(fill)
    ratio = cx["venue"] / cx["paper"]
    f = f_spot * ratio
    stop, target, risk = ev.levels_for_fill(f, 1.0)
    w = walk(path, cx["i0"] if start is None else start, cx["i1"], ev.direction, stop, target)
    if w["kind"] == "none":
        r = float("nan")
    else:
        exit_venue = w["price"] * ratio if w["kind"] == "tif" else w["price"]
        r = trade_r(ev.direction, f, exit_venue, risk)
    return dict(fill=f, fill_spot=f_spot, stop=stop, target=target, risk=risk, r=r, **w)


# ── statistics ───────────────────────────────────────────────────────────────
def day_groups(ts: np.ndarray) -> np.ndarray:
    return (np.asarray(ts, dtype=np.int64) // 86400)


def block_boot_mean(x, groups, n_iter: int = BOOT_ITER, seed: int = SEED) -> dict:
    """Bootstrap the mean by resampling whole groups (days) with replacement."""
    x = np.asarray(x, dtype=float); g = np.asarray(groups)
    if x.size == 0:
        return dict(n=0, mean=float("nan"), ci90=[float("nan")] * 2, n_groups=0)
    uniq, inv = np.unique(g, return_inverse=True)
    sums = np.bincount(inv, weights=x, minlength=len(uniq))
    cnts = np.bincount(inv, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(seed)
    k = len(uniq)
    means = np.empty(n_iter)
    for b in range(n_iter):
        pick = rng.integers(0, k, k)
        means[b] = sums[pick].sum() / cnts[pick].sum()
    return dict(n=int(x.size), mean=float(x.mean()), ci90=[float(np.quantile(means, 0.05)), float(np.quantile(means, 0.95))],
                n_groups=int(k), sd=float(x.std(ddof=1)) if x.size > 1 else float("nan"))


def halves(ts: np.ndarray) -> np.ndarray:
    """Boolean mask: True for the second half (split at the median event time)."""
    ts = np.asarray(ts); return ts > np.median(ts)


def roll_halfspread_bp(closes: np.ndarray) -> float:
    """Roll (1984): spread = 2 sqrt(-cov(r_t, r_{t-1})); returns the HALF spread in bp, NaN if cov >= 0."""
    r = np.diff(np.log(closes))
    if r.size < 20:
        return float("nan")
    cov = float(np.mean((r[1:] - r[1:].mean()) * (r[:-1] - r[:-1].mean())))
    return float(math.sqrt(-cov) * 1e4) if cov < 0 else float("nan")


def corwin_schultz_halfspread_bp(h: np.ndarray, l: np.ndarray) -> float:
    """Corwin-Schultz (2012) on consecutive bars; negative estimates set to 0; returns HALF spread in bp."""
    with np.errstate(divide="ignore", invalid="ignore"):
        lh = np.log(h / l)
        beta = lh[:-1] ** 2 + lh[1:] ** 2
        h2 = np.maximum(h[:-1], h[1:]); l2 = np.minimum(l[:-1], l[1:])
        gamma = np.log(h2 / l2) ** 2
        k = 3 - 2 * math.sqrt(2)
        alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
        s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s = s[np.isfinite(s)]
    if s.size == 0:
        return float("nan")
    s = np.clip(s, 0, None)
    return float(s.mean() / 2 * 1e4)


def summarize_r(r: np.ndarray, ts: np.ndarray) -> dict:
    r = np.asarray(r, float); ts = np.asarray(ts)
    if r.size == 0:
        return dict(n=0)
    cum = np.cumsum(r); dd = cum - np.maximum.accumulate(cum)
    span_y = max((ts.max() - ts.min()) / (365.25 * 86400), 0.05)
    b = block_boot_mean(r, day_groups(ts))
    h2 = halves(ts)
    return dict(n=int(r.size), mean_r=float(r.mean()), ci90=b["ci90"], win=float((r > 0).mean()),
                sum_r=float(r.sum()), maxdd_r=float(dd.min()), mar=float(r.sum() / span_y / max(-dd.min(), 1e-9)),
                first_half_mean=float(r[~h2].mean()) if (~h2).any() else float("nan"),
                second_half_mean=float(r[h2].mean()) if h2.any() else float("nan"))
