"""Exit-policy study, squeeze_bull arm (REPORT-ONLY): inputs, the 1-minute walker and the report statistics.

PREREGISTRATION_SQUEEZE_BULL.md freezes every constant here. The walker is array-based: for each fire and arm it
finds the first minute at which each exit condition holds, then takes the earliest in the frozen priority (exits at
a minute's open before stop, stop before target within a minute). tests/test_sqb_walk.py pins each rule.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LEDGER = ROOT / "studies" / "notebooks" / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv"
ORB_CACHE = ROOT / "studies" / "notebooks" / "orb_study" / "cache"
HOURLY = ROOT.parent / "p300-study-snapshots" / "exit_policy_2026_09" / "squeeze_bull_hourly.npz"
RESULTS = HERE / "results" / "squeeze_bull"

COST_BP = 7.0
RISK_OF_ENTRY = 0.02
CENSOR_H = 720
HALF_SPLIT_DAY = "2024-06-14"
BLOCK_DAYS, N_BOOT, SEED = 30, 10_000, 42
FORWARD_H = (1, 2, 4, 8, 12, 24, 48, 72, 96, 168)
TARGET_LADDER = (0.03, 0.04, 0.05, 0.06)
CAPITAL, RISK_USD = 10_000.0, 100.0          # 1 % risk over the 2 % reference stop = 0.5x notional
MIN_MS = 60_000


def sleeve_math():
    """The sleeve's own math.py, loaded from its file under a stub parent package.

    Its only import is `from .config import ...`; a stub package whose __path__ is the strategy folder lets that
    resolve to the real config.py without running the package __init__, which imports the live signal module.
    """
    name = "sqb_strategy.math"
    if name not in sys.modules:
        base = ROOT / "bots" / "squeeze_bull" / "strategy"
        pkg = types.ModuleType("sqb_strategy")
        pkg.__path__ = [str(base)]
        sys.modules["sqb_strategy"] = pkg
        spec = importlib.util.spec_from_file_location(name, base / "math.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


@dataclass(frozen=True)
class Arm:
    id: str
    family: str                       # incumbent | twin
    stop: float | None                # fraction below entry
    target: float | None              # fraction above entry
    tif_h: int | None                 # None: censored at CENSOR_H
    catastrophe: float | None = None
    reflush: bool = False


ARMS = (
    Arm("S0", "incumbent", 0.02, 0.03, 48),
    Arm("S_T12", "incumbent", 0.02, 0.03, 12), Arm("S_T24", "incumbent", 0.02, 0.03, 24),
    Arm("S_T72", "incumbent", 0.02, 0.03, 72), Arm("S_NOTIME", "incumbent", 0.02, 0.03, None),
    Arm("S_TGT4", "incumbent", 0.02, 0.04, 48), Arm("S_TGT5", "incumbent", 0.02, 0.05, 48),
    Arm("S_TGT6", "incumbent", 0.02, 0.06, 48), Arm("S_NOTGT", "incumbent", 0.02, None, 48),
    Arm("C_REFLUSH", "incumbent", 0.02, 0.03, None, reflush=True),
    Arm("N0", "twin", None, 0.03, 48), Arm("N_NOTIME", "twin", None, 0.03, None, catastrophe=0.10),
    Arm("N_NOTGT", "twin", None, None, 48),
)
BY_ID = {a.id: a for a in ARMS}


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
    tmp.write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    tmp.replace(path)


# --- inputs ------------------------------------------------------------------------------------------

def load_fires() -> pd.DataFrame:
    d = pd.read_csv(LEDGER)
    b = d[d["regime_backonly"] == "bull_30d"].copy()
    b["bar_ts"] = [int(pd.Timestamp(x).timestamp()) for x in b["ts"]]
    b["entry_ts"] = b["bar_ts"] + 3600
    b["entry_day"] = [utc_day(x) for x in b["entry_ts"]]
    return b.sort_values("bar_ts", kind="mergesort").reset_index(drop=True)[
        ["bar_ts", "entry_ts", "entry_day", "entry", "r_outcome", "exit_kind"]]


@dataclass
class Path1m:
    t0_ms: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    funding_s: np.ndarray
    funding_rate: np.ndarray

    def index(self, ts: int) -> int:
        return (int(ts) * 1000 - self.t0_ms) // MIN_MS

    def time_s(self, i: int) -> int:
        return (self.t0_ms + int(i) * MIN_MS) // 1000


def load_path() -> Path1m:
    with np.load(ORB_CACHE / "BTCUSDT_perp_1m.npz") as z:
        dead = ~(z["volume"] > 0)
        cols = {k: np.where(dead, np.nan, z[k]).astype(float) for k in ("open", "high", "low", "close")}
        t0 = int(z["t0_ms"][0])
    with np.load(ORB_CACHE / "BTCUSDT_funding.npz") as f:
        fs = (f["time_ms"] // 1000).astype(np.int64)
        fr = f["rate"].astype(float)
    return Path1m(t0, cols["open"], cols["high"], cols["low"], cols["close"], fs, fr)


def load_hourly_flush_bars() -> dict:
    """Hourly bar opens where the sleeve's is_flush holds, on the snapshot's joined frame (positional 4-bar changes)."""
    m = sleeve_math()
    with np.load(HOURLY) as z:
        ts, close, high, low, oi = (z[k] for k in ("ts", "close", "high", "low", "oi_close"))
    closes, ois = close.tolist(), oi.tolist()
    flush = [int(ts[k]) for k in range(len(ts))
             if m.is_flush(m.pct_change_n(ois, 4, k), m.pct_change_n(closes, 4, k))]
    return {"ts": ts.astype(np.int64), "high": high, "low": low, "close": close, "flush_bar_ts": np.array(flush, dtype=np.int64)}


# --- walker (section 3) --------------------------------------------------------------------------

def _first(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else -1


def _first_valid_at_or_after(path: Path1m, i: int) -> int:
    n = len(path.close)
    while i < n and np.isnan(path.close[i]):
        i += 1
    return i if i < n else -1


def funding_R(path: Path1m, entry_ts: int, exit_s: int, inclusive: bool, entry: float) -> float:
    lo = int(np.searchsorted(path.funding_s, entry_ts, side="right"))
    hi = int(np.searchsorted(path.funding_s, exit_s, side="right" if inclusive else "left"))
    risk = RISK_OF_ENTRY * entry
    total = 0.0
    for s, rate in zip(path.funding_s[lo:hi], path.funding_rate[lo:hi]):
        i = path.index(int(s))
        mark = path.open[i] if not np.isnan(path.open[i]) else np.nan
        j = i
        while np.isnan(mark) and j > 0:
            j -= 1
            mark = path.close[j]
        total += -rate * float(mark) / risk
    return total


def walk(path: Path1m, fire, arm: Arm, flush_bar_ts: np.ndarray, cost_bp: float = COST_BP) -> dict:
    entry, entry_ts, bar_ts = float(fire.entry), int(fire.entry_ts), int(fire.bar_ts)
    risk = RISK_OF_ENTRY * entry
    cost_R = cost_bp / 10000.0 * entry / risk
    n = len(path.close)
    i0 = path.index(entry_ts)
    horizon_h = arm.tif_h if arm.tif_h is not None else CENSOR_H
    i_h = i0 + horizon_h * 60
    i_end = min(i_h, n)
    O, Hh, Ll = path.open[i0:i_end], path.high[i0:i_end], path.low[i0:i_end]
    candidates = []                                       # (minute index, priority, kind, price)
    if arm.reflush:
        after = flush_bar_ts[flush_bar_ts >= bar_ts + 4 * 3600]
        if len(after):
            i_r = _first_valid_at_or_after(path, path.index(int(after[0]) + 3600))
            if 0 <= i_r < i_end:
                candidates.append((i_r, 0, "event", float(path.open[i_r])))
    level = None
    if arm.stop is not None:
        level = entry * (1 - arm.stop)
    elif arm.catastrophe is not None:
        level = entry * (1 - arm.catastrophe)
    if level is not None:
        with np.errstate(invalid="ignore"):
            gap = O <= level
            touch = Ll <= level
        j = _first(gap | touch)
        if j >= 0:
            kind = "stop" if arm.stop is not None else "catastrophe"
            candidates.append((i0 + j, 1, kind, float(O[j]) if gap[j] else level))
    if arm.target is not None:
        tgt = entry * (1 + arm.target)
        with np.errstate(invalid="ignore"):
            j = _first(Hh >= tgt)
        if j >= 0:
            candidates.append((i0 + j, 2, "target", tgt))
    if candidates:
        i_x, _, kind, px = min(candidates, key=lambda c: (c[0], c[1]))
    elif i_h < n and _first_valid_at_or_after(path, i_h) >= 0:
        i_x = _first_valid_at_or_after(path, i_h)
        kind, px = ("time" if arm.tif_h is not None else "censored_horizon"), float(path.open[i_x])
    else:
        last = n - 1
        while np.isnan(path.close[last]):
            last -= 1
        i_x, kind, px = last, "censored_data_end", float(path.close[last])
    at_open = kind in ("event", "time", "censored_horizon")
    exit_s = path.time_s(i_x) + (60 if kind == "censored_data_end" else 0)
    f = funding_R(path, entry_ts, exit_s, inclusive=not at_open, entry=entry)
    r_price = (px - entry) / risk - cost_R
    return {"bar_ts": bar_ts, "entry_day": fire.entry_day, "arm": arm.id, "kind": kind, "exit_s": exit_s,
            "exit_price": px, "hours_held": (exit_s - entry_ts) / 3600, "R_price": r_price, "funding_R": f,
            "net_R": r_price + f, "missing_minutes": int(np.isnan(path.close[i0:i_x + 1]).sum())}


def walk_all(path: Path1m, fires: pd.DataFrame, flush_bar_ts: np.ndarray, arms=ARMS) -> pd.DataFrame:
    return pd.DataFrame([walk(path, f, a, flush_bar_ts) for f in fires.itertuples(index=False) for a in arms])


# --- report statistics (section 5) ---------------------------------------------------------------

def day_axis(fires: pd.DataFrame) -> list[str]:
    d0, d1 = date.fromisoformat(fires["entry_day"].min()), date.fromisoformat(fires["entry_day"].max())
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def block_indices(T: int, block: int = BLOCK_DAYS, n_boot: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, T, size=(n_boot, -(-T // block)))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % T
    return idx.reshape(n_boot, -1)[:, :T].astype(np.int32)


def paired(days: pd.Series, d: np.ndarray, axis: list[str], idx: np.ndarray) -> dict:
    pos = {x: i for i, x in enumerate(axis)}
    s, c = np.zeros(len(axis)), np.zeros(len(axis))
    for day, v in zip(days, d):
        s[pos[day]] += v
        c[pos[day]] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    b = boot[np.isfinite(boot)]
    first = (days < HALF_SPLIT_DAY).to_numpy()
    years = pd.Series(d).groupby(days.str[:4].to_numpy()).mean()
    return {"mean_d": float(d.mean()), "ci95": [float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))],
            "share_draws_above_0": float((b > 0).mean()), "first_half": float(d[first].mean()),
            "second_half": float(d[~first].mean()), "by_year": {k: float(v) for k, v in years.items()},
            "fires_changed": int((np.abs(d) > 1e-12).sum())}


def excursions(path: Path1m, fires: pd.DataFrame) -> dict:
    rows = []
    for f in fires.itertuples(index=False):
        entry, i0 = float(f.entry), path.index(int(f.entry_ts))
        row = {"bar_ts": int(f.bar_ts)}
        for label, hours in (("48h", 48), ("7d", 168)):
            H, L = path.high[i0:i0 + hours * 60], path.low[i0:i0 + hours * 60]
            row[f"mfe_pct_{label}"] = float((np.nanmax(H) / entry - 1) * 100)
            row[f"mae_pct_{label}"] = float((np.nanmin(L) / entry - 1) * 100)
            row[f"hours_to_mfe_{label}"] = float(np.nanargmax(H) / 60)
        H7, L7 = path.high[i0:i0 + 168 * 60], path.low[i0:i0 + 168 * 60]
        with np.errstate(invalid="ignore"):
            stop_i = _first(L7 <= entry * 0.98)
            for g in TARGET_LADDER:
                tgt_i = _first(H7 >= entry * (1 + g))
                row[f"reach_{int(g * 100)}pct_before_stop_7d"] = bool(tgt_i >= 0 and (stop_i < 0 or tgt_i < stop_i))
        rows.append(row)
    ex = pd.DataFrame(rows)
    summary = {c: {"mean": float(ex[c].mean()), "median": float(ex[c].median()),
                   "p25": float(ex[c].quantile(0.25)), "p75": float(ex[c].quantile(0.75))}
               for c in ex.columns if c.startswith(("mfe", "mae", "hours"))}
    summary.update({c: float(ex[c].mean()) for c in ex.columns if c.startswith("reach")})
    return {"summary": summary, "per_fire": ex}


def forward_moves(path: Path1m, fires: pd.DataFrame, axis: list[str], idx: np.ndarray) -> dict:
    out = {}
    for h in FORWARD_H:
        moves, days = [], []
        for f in fires.itertuples(index=False):
            i = _first_valid_at_or_after(path, path.index(int(f.entry_ts) + h * 3600))
            if i < 0:
                continue
            moves.append((float(path.open[i]) / float(f.entry) - 1) * 100)
            days.append(f.entry_day)
        r = paired(pd.Series(days), np.asarray(moves), axis, idx)
        out[str(h)] = {"mean_pct": r["mean_d"], "ci95": r["ci95"], "fires": len(moves)}
    return out


def single_open_sequence(path: Path1m, fires: pd.DataFrame, walks: pd.DataFrame, arm: str) -> dict:
    """The live guard: a fire is skipped while this arm's previous trade is open (entry at the prior exit is kept).
    Fixed $10,000, $100 risk per trade; drawdown against the fixed capital with day-end and exit marks."""
    entry_px = dict(zip(fires["bar_ts"].astype(int), fires["entry"].astype(float)))
    taken, last_exit = [], -1
    for r in walks[walks["arm"] == arm].sort_values("bar_ts").itertuples(index=False):
        entry_ts = int(r.bar_ts) + 3600
        if entry_ts < last_exit:
            continue
        taken.append({"entry_ts": entry_ts, "exit_s": int(r.exit_s), "net_R": float(r.net_R), "entry": entry_px[int(r.bar_ts)]})
        last_exit = int(r.exit_s)
    marks = sorted({t["exit_s"] for t in taken}
                   | {d for t in taken for d in range((t["entry_ts"] // 86400 + 1) * 86400, t["exit_s"], 86400)})
    peak = worst = 0.0
    for m in marks:
        pnl = sum(t["net_R"] * RISK_USD for t in taken if t["exit_s"] <= m)
        for t in taken:
            if t["entry_ts"] <= m < t["exit_s"]:
                j = path.index(m) - 1
                while j > 0 and np.isnan(path.close[j]):
                    j -= 1
                pnl += (float(path.close[j]) - t["entry"]) / (RISK_OF_ENTRY * t["entry"]) * RISK_USD
        peak = max(peak, pnl)
        worst = max(worst, peak - pnl)
    return {"trades": len(taken), "total_return_pct": sum(t["net_R"] for t in taken) * RISK_USD / CAPITAL * 100,
            "max_drawdown_pct": worst / CAPITAL * 100}
