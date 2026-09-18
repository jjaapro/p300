"""Exit-policy study, microstructure stage 1: populations, the 1-minute walker, events, matched placebos, statistics.

PREREGISTRATION_MICROSTRUCTURE.md freezes every constant here (section numbers in the comments). The feature
functions take their windows as arguments so the fixtures can use small ones; the frozen values are the defaults and
tests/test_micro_events.py pins them.

Continuation values need exit prices; nothing that runs before F0 (`micro_run.py checks`) computes one at an event.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NB = ROOT / "studies" / "notebooks"
ORB_CACHE = NB / "orb_study" / "cache"
BOOK_CACHE = HERE / "cache"
BOOK_MANIFEST = HERE / "data" / "raw" / "bookDepth" / "manifest.json"
RESULTS = HERE / "results" / "microstructure"

CHENTO_FEATURES = {a: NB / "okx_gate_revalidation" / "results" / f"features_{a}.csv" for a in ("BTC", "ETH")}
CHENTO_FEATURES_SHA256 = {"BTC": "c1762d577495b9b75a9fce3c97c227e1524e33b5cfdd5f1a43f55e946ba7f81c",
                          "ETH": "83663d81fca7c309b4eb8656c315fb1e5812f704d658d402a7c35f2e66dcc646"}
SQB_LEDGER = NB / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv"
SQB_LEDGER_SHA256 = "d3793c4e419af462709aa25a5c930f8b7b94a73dd489af53cd7cb68100521b14"
SS_REPLAY = NB / "execution_2026_09" / "results" / "e0_short_squeeze_notebook_replay.csv"
CHENTO_A0_WALKS = HERE / "results" / "chento" / "walks.csv.gz"
SQB_WALKS = HERE / "results" / "squeeze_bull" / "walks.csv.gz"
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

# --- frozen constants -----------------------------------------------------------------------------
MIN_S = 60
POPULATIONS = ("chento", "squeeze_bull", "short_squeeze")
HORIZON_H = {"chento": 72, "squeeze_bull": 48, "short_squeeze": 6}          # section 3
CHENTO_TARGET_R = 6.0
SQB_STOP, SQB_TARGET = 0.02, 0.03
CENSOR_H = 720                                                              # 3.1 secondary walk
FLOW_WINDOW = 5                                                             # section 5
NORM_WINDOW = 10_080
NORM_MIN_PRESENT = 5_040
Z_EXTREME = 3.0
BOOK_WINDOW_MIN_PRESENT = 3
LEVEL_LOOKBACK = 1_440
LEVEL_MIN_PRESENT = 1_000
LEVEL_MIN_DISTANCE = 0.001
BREAK_MINUTES = 15
AT_LEVEL_BAND = 0.0025
BOOK_ELIGIBLE_FROM = int(datetime(2023, 1, 8, tzinfo=timezone.utc).timestamp())
BIN_R = 0.25                                                                # section 6
WINDOW_SHARE = 0.05
MIN_CONTROLS = 3
BLOCK_DAYS, N_BOOT, SEED = 30, 10_000, 42
MIN_EVENT_TRADES = 30                                                       # sections 7-8
SIGN_CONTROL_MIN = 10
EQUIVALENCE_R = 0.10
ALPHA = 0.05
BOOK_COVERAGE_MIN = 0.90                                                    # section 9, M6
POWER_Z = 2.49                                                              # M8: z(0.95) + z(0.80)

PRIMARY = ("E1_against", "E2_rejection", "E3_against", "E4_at_level")
SIGN_CONTROL = {"E1_against": "E1_supportive", "E2_rejection": "E2_acceptance", "E3_against": "E3_supportive"}
KINDS = PRIMARY + tuple(SIGN_CONTROL.values())
NO_EVENT = np.iinfo(np.int64).max


def window_minutes(pop: str) -> int:
    return int(round(WINDOW_SHARE * HORIZON_H[pop] * 60))


# --- small helpers -----------------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_day(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                   encoding="utf-8")
    tmp.replace(path)


def first_true(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else -1


# --- features (section 5) ----------------------------------------------------------------------------

def trailing_z(x: np.ndarray, lag: int = FLOW_WINDOW, window: int = NORM_WINDOW,
               min_present: int = NORM_MIN_PRESENT) -> np.ndarray:
    """(x_b - mean) / sd with mean and sd (ddof 0) over x at b - lag - window + 1 ... b - lag."""
    ref = pd.Series(x).shift(lag).rolling(window, min_periods=min_present)
    mean, sd = ref.mean().to_numpy(), ref.std(ddof=0).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (x - mean) / sd
    z[~(sd > 0)] = np.nan
    return z


def _pad_front(values: np.ndarray, k: int) -> np.ndarray:
    return np.concatenate([np.full(k, np.nan), values])


def flow_features(open_: np.ndarray, close: np.ndarray, volume: np.ndarray, taker_buy: np.ndarray,
                  window: int = FLOW_WINDOW, norm_window: int = NORM_WINDOW,
                  min_present: int = NORM_MIN_PRESENT) -> tuple[np.ndarray, np.ndarray]:
    """E1 inputs: z of the 5-minute taker delta against its trailing 7 days, and close_b - open_{b-4}."""
    d = np.where(volume > 0, 2.0 * taker_buy - volume, np.nan)
    D = _pad_front(sliding_window_view(d, window).sum(axis=1), window - 1)        # missing if any minute is
    z = trailing_z(D, lag=window, window=norm_window, min_present=min_present)
    dp = close - _pad_front(open_[: len(open_) - (window - 1)], window - 1)
    return z, dp


def book_features(bid: np.ndarray, ask: np.ndarray, window: int = FLOW_WINDOW,
                  min_in_window: int = BOOK_WINDOW_MIN_PRESENT, norm_window: int = NORM_WINDOW,
                  min_present: int = NORM_MIN_PRESENT) -> np.ndarray:
    """E3 input: z of the 5-minute mean +-1 % imbalance against its trailing 7 days."""
    with np.errstate(invalid="ignore", divide="ignore"):
        q = (bid - ask) / (bid + ask)
    view = sliding_window_view(q, window)
    count = np.isfinite(view).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        Q = np.where(count >= min_in_window, np.nansum(view, axis=1) / count, np.nan)
    return trailing_z(_pad_front(Q, window - 1), lag=window, window=norm_window, min_present=min_present)


@dataclass
class Series:
    asset: str
    t0_s: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    z_flow: np.ndarray
    dp5: np.ndarray
    zq: np.ndarray
    book_present: np.ndarray

    def index(self, ts_s: int) -> int:
        return (int(ts_s) - self.t0_s) // MIN_S

    def time_s(self, i: int) -> int:
        return self.t0_s + int(i) * MIN_S


def load_raw(asset: str) -> dict:
    with np.load(ORB_CACHE / f"{SYMBOL[asset]}_perp_1m.npz") as z:
        raw = {k: z[k].astype(float) for k in ("open", "high", "low", "close", "volume", "taker_buy_volume")}
        raw["t0_s"] = int(z["t0_ms"][0]) // 1000
    with np.load(BOOK_CACHE / f"{SYMBOL[asset]}_book1pct_1m.npz") as b:
        raw["book_t0_s"] = int(b["t0_s"][0])
        raw["bid"] = b["bid_notional_1pct"].astype(float)
        raw["ask"] = b["ask_notional_1pct"].astype(float)
    return raw


def build_series(asset: str, raw: dict, n_minutes: int | None = None) -> Series:
    """Features from the raw arrays, optionally using only the first n_minutes of the panel (causality check M7)."""
    n = len(raw["close"]) if n_minutes is None else int(n_minutes)
    dead = ~(raw["volume"][:n] > 0)
    px = {k: np.where(dead, np.nan, raw[k][:n]) for k in ("open", "high", "low", "close")}
    z_flow, dp5 = flow_features(px["open"], px["close"], raw["volume"][:n], raw["taker_buy_volume"][:n])
    off = (raw["book_t0_s"] - raw["t0_s"]) // MIN_S
    zq = np.full(n, np.nan)
    present = np.zeros(n, dtype=bool)
    m = max(0, n - off)
    if m > 0:
        bid, ask = raw["bid"][:m], raw["ask"][:m]
        zq[off:off + m] = book_features(bid, ask)
        present[off:off + m] = np.isfinite(bid) & np.isfinite(ask)
    return Series(asset, raw["t0_s"], px["open"], px["high"], px["low"], px["close"], z_flow, dp5, zq, present)


def load_series(asset: str) -> Series:
    return build_series(asset, load_raw(asset))


# --- populations (section 3) -------------------------------------------------------------------------

def load_chento() -> pd.DataFrame:
    rows = []
    for asset in ("BTC", "ETH"):
        f = pd.read_csv(CHENTO_FEATURES[asset])
        f = f[f["in_off"].astype(str) == "True"]
        for r in f.itertuples(index=False):
            t = int(pd.Timestamp(r.t).timestamp())
            s = 1 if r.direction == "long" else -1
            entry, risk = float(r.entry), float(r.risk)
            rows.append({"pop": "chento", "tid": f"{asset}:{t}:{r.direction}", "asset": asset, "direction": r.direction,
                         "s": s, "signal_ts": t, "entry_ts": t + 900, "entry": entry, "risk": risk,
                         "stop": entry - s * risk, "target": entry + s * CHENTO_TARGET_R * risk})
    return _finish(rows)


def load_squeeze_bull() -> pd.DataFrame:
    d = pd.read_csv(SQB_LEDGER)
    rows = []
    for r in d[d["regime_backonly"] == "bull_30d"].itertuples(index=False):
        bar = int(pd.Timestamp(r.ts).timestamp())
        entry = float(r.entry)
        rows.append({"pop": "squeeze_bull", "tid": f"BTC:{bar}", "asset": "BTC", "direction": "long", "s": 1,
                     "signal_ts": bar, "entry_ts": bar + 3600, "entry": entry, "risk": SQB_STOP * entry,
                     "stop": entry * (1 - SQB_STOP), "target": entry * (1 + SQB_TARGET)})
    return _finish(rows)


def load_short_squeeze() -> pd.DataFrame:
    d = pd.read_csv(SS_REPLAY)
    rows = []
    for r in d.itertuples(index=False):
        trig = int(pd.Timestamp(r.trigger_ts).timestamp())
        entry, stop = float(r.entry), float(r.stop)
        rows.append({"pop": "short_squeeze", "tid": f"BTC:{trig}", "asset": "BTC", "direction": "long", "s": 1,
                     "signal_ts": trig, "entry_ts": trig + 900, "entry": entry, "risk": entry - stop,
                     "stop": stop, "target": float(r.target), "replay_exit_reason": r.exit_reason})
    return _finish(rows)


def _finish(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).sort_values(["entry_ts", "tid"], kind="mergesort").reset_index(drop=True)
    df["entry_day"] = [utc_day(t) for t in df["entry_ts"]]
    return df


def load_population(pop: str) -> pd.DataFrame:
    return {"chento": load_chento, "squeeze_bull": load_squeeze_bull, "short_squeeze": load_short_squeeze}[pop]()


# --- walker (section 3.1) -----------------------------------------------------------------------------

def _first_valid_at_or_after(close: np.ndarray, i: int) -> int:
    n = len(close)
    while i < n and np.isnan(close[i]):
        i += 1
    return i if i < n else -1


def walk(ser: Series, i0: int, s: int, stop: float, target: float, horizon_h: int) -> tuple[int, str, float]:
    """(x, kind, exit price). The trade is open after the close of minute b exactly when b < x."""
    n = len(ser.close)
    i_h = i0 + horizon_h * 60
    i_end = min(i_h, n)
    O, H, L = ser.open[i0:i_end], ser.high[i0:i_end], ser.low[i0:i_end]
    with np.errstate(invalid="ignore"):
        if s > 0:
            gap, touch, hit = O <= stop, L <= stop, H >= target
        else:
            gap, touch, hit = O >= stop, H >= stop, L <= target
    candidates = []
    j = first_true(gap | touch)
    if j >= 0:
        candidates.append((j, 0, "stop", float(O[j]) if gap[j] else float(stop)))
    j = first_true(hit)
    if j >= 0:
        candidates.append((j, 1, "target", float(target)))
    if candidates:
        j, _, kind, px = min(candidates, key=lambda c: (c[0], c[1]))
        return i0 + j, kind, px
    k = _first_valid_at_or_after(ser.close, i_h) if i_h < n else -1
    if k >= 0:
        return k, ("time" if horizon_h != CENSOR_H else "censored_horizon"), float(ser.open[k])
    last = n - 1
    while np.isnan(ser.close[last]):
        last -= 1
    return last + 1, "censored_data_end", float(ser.close[last])


def walk_population(trades: pd.DataFrame, series: dict[str, Series]) -> pd.DataFrame:
    """Shipped walk and the no-time-exit walk for every trade, plus the 24 h level."""
    out = []
    for tr in trades.itertuples(index=False):
        ser = series[tr.asset]
        i0 = ser.index(tr.entry_ts)
        x, kind, px = walk(ser, i0, tr.s, tr.stop, tr.target, HORIZON_H[tr.pop])
        x2, kind2, px2 = walk(ser, i0, tr.s, tr.stop, tr.target, CENSOR_H)
        L = level_of(ser, i0, tr.s)
        out.append({"tid": tr.tid, "i0": i0, "x": x, "kind": kind, "exit_price": px, "x_notime": x2,
                    "kind_notime": kind2, "exit_price_notime": px2, "level": L,
                    "level_valid": level_valid(L, tr.entry, tr.s)})
    return trades.merge(pd.DataFrame(out), on="tid", how="left", validate="one_to_one")


# --- events (section 5) -----------------------------------------------------------------------------

def level_of(ser: Series, i0: int, s: int, lookback: int = LEVEL_LOOKBACK, min_present: int = LEVEL_MIN_PRESENT) -> float:
    lo = i0 - lookback
    if lo < 0:
        return float("nan")
    seg = ser.high[lo:i0] if s > 0 else ser.low[lo:i0]
    if np.isfinite(seg).sum() < min_present:
        return float("nan")
    return float(np.nanmax(seg) if s > 0 else np.nanmin(seg))


def level_valid(L: float, entry: float, s: int) -> bool:
    return bool(np.isfinite(L) and s * (L - entry) / entry >= LEVEL_MIN_DISTANCE)


def window_masks(ser: Series, i0: int, x: int, s: int, level: float | None) -> dict[str, np.ndarray]:
    """Per-minute masks over minutes i0 ... x-1 (index 0 = i0) for the window events E1, E3, E4."""
    c, z, dp, zq = ser.close[i0:x], ser.z_flow[i0:x], ser.dp5[i0:x], ser.zq[i0:x]
    ok = (np.arange(len(c)) >= FLOW_WINDOW - 1) & np.isfinite(c)
    with np.errstate(invalid="ignore"):
        m = {"E1_against": ok & (s * z >= Z_EXTREME) & (s * dp <= 0),
             "E1_supportive": ok & (s * z <= -Z_EXTREME) & (s * dp >= 0),
             "E3_against": ok & (s * zq <= -Z_EXTREME),
             "E3_supportive": ok & (s * zq >= Z_EXTREME)}
        if level is None:
            m["E4_at_level"] = np.zeros(len(c), dtype=bool)
        else:
            m["E4_at_level"] = m["E1_against"] & (np.abs(c - level) <= AT_LEVEL_BAND * level)
    return m


def break_events(ser: Series, i0: int, x: int, s: int, level: float) -> tuple[int, int]:
    """(rejection minute, acceptance minute) of the first break of the level; -1 where absent."""
    seg = ser.high[i0:x] if s > 0 else ser.low[i0:x]
    with np.errstate(invalid="ignore"):
        j = first_true(seg > level if s > 0 else seg < level)
    if j < 0:
        return -1, -1
    b0 = i0 + j
    for b in range(b0, b0 + BREAK_MINUTES):
        if b >= x:
            return -1, -1
        cb = ser.close[b]
        if np.isnan(cb):
            continue
        if s * (cb - level) < 0:
            return b, -1
    last = b0 + BREAK_MINUTES - 1
    return (-1, last) if np.isfinite(ser.close[last]) else (-1, -1)


def trade_events(ser: Series, tr) -> dict:
    """First event minute (absolute index, -1 if none) and event-minute count of every kind, for one walked trade."""
    level = tr.level if tr.level_valid else None
    masks = window_masks(ser, tr.i0, tr.x, tr.s, level)
    out = {}
    for kind, m in masks.items():
        j = first_true(m)
        out[f"{kind}_first"] = tr.i0 + j if j >= 0 else -1
        out[f"{kind}_minutes"] = int(m.sum())
    rej, acc = break_events(ser, tr.i0, tr.x, tr.s, tr.level) if tr.level_valid else (-1, -1)
    out["E2_rejection_first"], out["E2_rejection_minutes"] = rej, int(rej >= 0)
    out["E2_acceptance_first"], out["E2_acceptance_minutes"] = acc, int(acc >= 0)
    return out


def eligible(trades: pd.DataFrame, kind: str) -> np.ndarray:
    if kind.startswith(("E2", "E4")):
        return trades["level_valid"].to_numpy(bool)
    if kind.startswith("E3"):
        return (trades["entry_ts"] >= BOOK_ELIGIBLE_FROM).to_numpy()
    return np.ones(len(trades), dtype=bool)


def events_population(trades: pd.DataFrame, series: dict[str, Series]) -> pd.DataFrame:
    rows = [trade_events(series[tr.asset], tr) for tr in trades.itertuples(index=False)]
    ev = pd.concat([trades.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    for kind in KINDS:                                 # an ineligible trade has no event of that kind
        el = eligible(ev, kind)
        ev.loc[~el, f"{kind}_first"] = -1
        ev.loc[~el, f"{kind}_minutes"] = 0
    return ev


# --- continuation value and the matched placebo (section 6) ------------------------------------------

@dataclass
class Grids:
    """Per-trade in-trade arrays, padded to the longest trade: rows = trades, columns = elapsed minutes."""
    open_: np.ndarray      # bool: open after the minute's close and its close present
    bin_: np.ndarray       # int64 profit bin (garbage where not open)
    mark: np.ndarray
    cv: np.ndarray | None
    cv_notime: np.ndarray | None


def build_grids(trades: pd.DataFrame, series: dict[str, Series], with_cv: bool) -> Grids:
    lengths = (trades["x"] - trades["i0"]).to_numpy(np.int64)
    T, E = len(trades), int(lengths.max())
    open_ = np.zeros((T, E), dtype=bool)
    mark = np.full((T, E), np.nan)
    cv = np.full((T, E), np.nan) if with_cv else None
    cv2 = np.full((T, E), np.nan) if with_cv else None
    for r, tr in enumerate(trades.itertuples(index=False)):
        c = series[tr.asset].close[tr.i0:tr.x]
        open_[r, :len(c)] = np.isfinite(c)
        mark[r, :len(c)] = tr.s * (c - tr.entry) / tr.risk
        if with_cv:
            cv[r, :len(c)] = tr.s * (tr.exit_price - c) / tr.risk
            cv2[r, :len(c)] = tr.s * (tr.exit_price_notime - c) / tr.risk
    with np.errstate(invalid="ignore"):
        bin_ = np.where(open_, np.floor(mark / BIN_R), -(10 ** 9)).astype(np.int64)
    return Grids(open_, bin_, mark, cv, cv2)


def match(trades: pd.DataFrame, grids: Grids, kind: str, window: int) -> pd.DataFrame:
    """One row per eligible trade with a `kind` event: elapsed, bin, controls, and (with CV grids) the placebo values."""
    el = eligible(trades, kind)
    first_abs = trades[f"{kind}_first"].to_numpy(np.int64)
    i0 = trades["i0"].to_numpy(np.int64)
    x = trades["x"].to_numpy(np.int64)
    s = trades["s"].to_numpy(np.int64)
    first_e = np.where(first_abs >= 0, first_abs - i0, NO_EVENT)
    E = grids.open_.shape[1]
    rows = []
    for i in np.flatnonzero(el & (first_abs >= 0)):
        e = int(first_e[i])
        k = int(grids.bin_[i, e])
        cand = el & (np.arange(len(trades)) != i) & (s == s[i]) & ~((i0 <= x[i]) & (i0[i] <= x))
        cand = np.flatnonzero(cand)
        lo, hi = max(0, e - window), min(E, e + window + 1)
        elapsed = np.arange(lo, hi)[None, :]
        at_risk = grids.open_[cand, lo:hi] & (elapsed < first_e[cand][:, None])
        valid = at_risk & (grids.bin_[cand, lo:hi] == k)
        count, count_t = valid.sum(axis=1), at_risk.sum(axis=1)
        keep, keep_t = count > 0, count_t > 0
        row = {"tid": trades["tid"].iat[i], "entry_ts": int(trades["entry_ts"].iat[i]),
               "entry_day": trades["entry_day"].iat[i], "asset": trades["asset"].iat[i],
               "direction": trades["direction"].iat[i], "elapsed_min": e, "bin": k,
               "mark_R": float(grids.mark[i, e]), "controls": int(keep.sum()), "controls_time_only": int(keep_t.sum())}
        if grids.cv is not None:
            row["cv"] = float(grids.cv[i, e])
            row["cv_notime"] = float(grids.cv_notime[i, e])
            row["placebo"] = _placebo(grids.cv[cand, lo:hi], valid, count, keep)
            row["placebo_notime"] = _placebo(grids.cv_notime[cand, lo:hi], valid, count, keep)
            row["placebo_time_only"] = _placebo(grids.cv[cand, lo:hi], at_risk, count_t, keep_t)
        rows.append(row)
    cols = ["tid", "entry_ts", "entry_day", "asset", "direction", "elapsed_min", "bin", "mark_R", "controls",
            "controls_time_only"]
    return pd.DataFrame(rows, columns=cols + (["cv", "cv_notime", "placebo", "placebo_notime", "placebo_time_only"]
                                              if grids.cv is not None else []))


def _placebo(values: np.ndarray, valid: np.ndarray, count: np.ndarray, keep: np.ndarray) -> float:
    """Mean over controls of each control's mean over its contributing minutes; NaN below MIN_CONTROLS."""
    if keep.sum() < MIN_CONTROLS:
        return float("nan")
    per_control = np.where(valid, values, 0.0).sum(axis=1)[keep] / count[keep]
    return float(per_control.mean())


# --- statistics (sections 6-8) -----------------------------------------------------------------------

def day_axis(days: pd.Series) -> list[str]:
    d0, d1 = date.fromisoformat(min(days)), date.fromisoformat(max(days))
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def block_indices(T: int, block: int = BLOCK_DAYS, n_boot: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, T, size=(n_boot, -(-T // block)))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    return idx.reshape(n_boot, -1)[:, :T].astype(np.int32)


def boot_means(days, values: np.ndarray, axis: list[str], idx: np.ndarray) -> np.ndarray:
    pos = {d: i for i, d in enumerate(axis)}
    sums, counts = np.zeros(len(axis)), np.zeros(len(axis))
    for d, v in zip(days, values):
        sums[pos[d]] += v
        counts[pos[d]] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return boot[np.isfinite(boot)]


def p_less(point: float, boot: np.ndarray) -> float:
    """One-sided p for mean < 0 from the centred bootstrap."""
    return float((1 + np.sum(boot - point <= point)) / (len(boot) + 1))


def holm_adjust(p: dict[str, float]) -> dict[str, float]:
    order = sorted(p, key=p.get)
    m, adj, running = len(order), {}, 0.0
    for i, key in enumerate(order):
        running = max(running, min(1.0, (m - i) * p[key]))
        adj[key] = running
    return adj


def summarize(rows: pd.DataFrame, value: str, axis: list[str], idx: np.ndarray) -> dict:
    """Mean, 95 % interval, one-sided p, halves and years of `value` (a Δ column) over rows with a placebo."""
    r = rows[np.isfinite(rows[value])].sort_values("entry_ts", kind="mergesort")
    n = len(r)
    if n == 0:
        return {"n": 0}
    v = r[value].to_numpy(float)
    boot = boot_means(r["entry_day"], v, axis, idx)
    half = -(-n // 2)
    point = float(v.mean())
    return {"n": n, "mean": point, "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
            "p_one_sided_less": p_less(point, boot), "first_half": float(v[:half].mean()),
            "second_half": float(v[half:].mean()) if n > half else float("nan"),
            "first_half_n": half, "second_half_n": n - half,
            "by_year": {k: {"n": int(len(g)), "mean": float(g.mean())}
                        for k, g in pd.Series(v).groupby(r["entry_day"].str[:4].to_numpy())}}


def classify(test: dict, in_family: bool, holm_p: float | None, control: dict | None, kind: str) -> str:
    if not in_family:
        return "DESCRIPTIVE"
    lo, hi = test["ci95"]
    halves_negative = test["first_half"] < 0 and test["second_half"] < 0
    if holm_p is not None and holm_p < ALPHA and halves_negative:
        if kind not in SIGN_CONTROL:
            return "INFORMATIVE"
        if control is None or control.get("n", 0) < SIGN_CONTROL_MIN:
            return "INFORMATIVE, sign control unavailable"
        if test["mean"] < control["mean"]:
            return "INFORMATIVE"
    if -EQUIVALENCE_R < lo and hi < EQUIVALENCE_R:
        return "NO INFORMATION >= 0.10 R"
    if lo > 0:
        return "CONTRARY"
    return "UNDETERMINED"
