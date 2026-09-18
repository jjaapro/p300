"""squeeze_bull top anatomy (EXPLORATORY stage A): bounce shapes, causal features, false tops, repair, state map.

PROTOCOL_TOP_ANATOMY.md defines every quantity here. Features are causal: the value at minute m uses only data known
by that minute's close (archive metrics one 5-minute period later). Labels (spike, top, false tops) look ahead by design;
features never do. tests/test_anatomy.py pins the rules on synthetic paths.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import micro_lib as ML

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ORB_CACHE = ROOT / "studies" / "notebooks" / "orb_study" / "cache"
CACHE = HERE / "cache"
RESULTS = HERE / "results" / "top_anatomy"
LEDGER = ROOT / "studies" / "notebooks" / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv"
HOURLY = ROOT.parent / "p300-study-snapshots" / "exit_policy_2026_09" / "squeeze_bull_hourly.npz"
DRIFT = HERE / "results" / "squeeze_bull" / "exploratory_regime_drift.json"

SPIKE = 0.02                    # section 4: +1 R
REVERSAL = 0.02                 # a 1 R fall from the peak
HORIZON_MIN = 7 * 1440
PAUSE_MIN = 30
PROFILE_MIN = 720
METRIC_LAG_S = 300              # section 3: a snapshot stamped T is used from T + 5 min
METRIC_MAX_SLOTS_BACK = 6       # and not when older than 30 minutes
FLOW_MIN = 60
FLOW_MIN_PRESENT = 50
VOL_BASE_MIN = 10_080
VOL_BASE_MIN_PRESENT = 5_040
STATE_STEP_MIN = 60
FORWARD_MIN = 1440
N_BOOT, SEED = 10_000, 42
PR_EDGES = (-np.inf, 0.0, 0.5, 1.0, 1.5, np.inf)
OR_EDGES = (-np.inf, 0.0, 0.5, 1.0, 1.5, np.inf)
STALL_EDGES = (0, 60, 360, 1440, np.inf)
FEATURES = ("PR", "OR", "premium_bp", "top_position_lsr_chg_pct", "account_lsr_chg_pct", "taker_buy_share_60",
            "volume_ratio_60", "book_z", "stall_min", "hours_since_entry")
CASE_ID = "SJ-4250"
CASE_BAR_TS = int(datetime(2026, 9, 11, 17, tzinfo=timezone.utc).timestamp())
CASE_ENTRY = 77493.9
CASE_EXIT_TS = int(datetime(2026, 9, 13, 17, tzinfo=timezone.utc).timestamp())


def first_true(mask: np.ndarray) -> int:
    return int(np.argmax(mask)) if mask.any() else -1


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    tmp.replace(path)


# --- market arrays ---------------------------------------------------------------------------------

def taker_share(volume: np.ndarray, taker_buy: np.ndarray, window: int = FLOW_MIN, min_present: int = FLOW_MIN_PRESENT) -> np.ndarray:
    """Taker buy volume / volume over the last `window` minutes (present minutes only, at least `min_present`)."""
    ok = volume > 0
    v, tb = np.where(ok, volume, 0.0), np.where(ok, taker_buy, 0.0)
    cv, ct, cn = (np.concatenate([[0.0], np.cumsum(a)]) for a in (v, tb, ok.astype(float)))
    n = len(volume)
    lo = np.maximum(np.arange(n) + 1 - window, 0)
    hi = np.arange(n) + 1
    vs, ts_, cnt = cv[hi] - cv[lo], ct[hi] - ct[lo], cn[hi] - cn[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where((cnt >= min_present) & (vs > 0), ts_ / vs, np.nan)


def volume_ratio(volume: np.ndarray, window: int = FLOW_MIN, base: int = VOL_BASE_MIN,
                 base_min_present: int = VOL_BASE_MIN_PRESENT, min_present: int = FLOW_MIN_PRESENT) -> np.ndarray:
    """Volume over the last `window` minutes / (window x mean 1-minute volume over the `base` minutes before them)."""
    v = pd.Series(np.where(volume > 0, volume, np.nan))
    recent = v.rolling(window, min_periods=min_present).mean()
    baseline = v.shift(window).rolling(base, min_periods=base_min_present).mean()
    with np.errstate(invalid="ignore", divide="ignore"):
        return (recent / baseline).to_numpy()


@dataclass
class Market:
    t0_s: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    premium: np.ndarray
    book_z: np.ndarray
    taker_buy_share_60: np.ndarray
    volume_ratio_60: np.ndarray
    m_t0_s: int
    metrics: dict                     # name -> 5-minute grid, NaN where absent

    def index(self, ts_s: int) -> int:
        return (int(ts_s) - self.t0_s) // 60

    def time_s(self, i: int) -> int:
        return self.t0_s + int(i) * 60

    def metric_stamped(self, name: str, ts_s: int) -> float:
        """The snapshot stamped exactly `ts_s`, else the latest earlier one within 30 minutes (reference levels)."""
        a = self.metrics[name]
        k = (int(ts_s) - self.m_t0_s) // 300
        for j in range(k, max(-1, k - METRIC_MAX_SLOTS_BACK - 1), -1):
            if 0 <= j < len(a) and np.isfinite(a[j]):
                return float(a[j])
        return float("nan")

    def metric_minutes(self, name: str, i_from: int, i_to: int) -> np.ndarray:
        """Per minute in [i_from, i_to): the latest snapshot usable at the minute's close (stamp <= close - 5 min)."""
        a = self.metrics[name]
        close_s = self.t0_s + 60 * (np.arange(i_from, i_to) + 1)
        k = (close_s - METRIC_LAG_S - self.m_t0_s) // 300
        out = np.full(len(k), np.nan)
        for back in range(METRIC_MAX_SLOTS_BACK + 1):
            j = k - back
            ok = np.isnan(out) & (j >= 0) & (j < len(a))
            out[ok] = a[j[ok]]
        return out


def _align(values: np.ndarray, values_t0_s: int, t0_s: int, n: int) -> np.ndarray:
    out = np.full(n, np.nan)
    off = (values_t0_s - t0_s) // 60
    lo, hi = max(0, off), min(n, off + len(values))
    if hi > lo:
        out[lo:hi] = values[lo - off:hi - off]
    return out


def build_market(o, h, l, c, v, tb, t0_s, premium, premium_t0_s, bid, ask, book_t0_s, metrics, m_t0_s) -> Market:
    dead = ~(v > 0)
    px = [np.where(dead, np.nan, a.astype(float)) for a in (o, h, l, c)]
    n = len(c)
    book_z = _align(ML.book_features(bid, ask), book_t0_s, t0_s, n)
    return Market(t0_s, *px, _align(premium, premium_t0_s, t0_s, n), book_z, taker_share(v, tb), volume_ratio(v),
                  m_t0_s, metrics)


def load_market() -> Market:
    with np.load(ORB_CACHE / "BTCUSDT_perp_1m.npz") as z:
        cols = {k: z[k] for k in ("open", "high", "low", "close", "volume", "taker_buy_volume")}
        t0 = int(z["t0_ms"][0]) // 1000
    with np.load(CACHE / "BTCUSDT_premium_1m.npz") as z:
        prem, p0 = z["close"], int(z["t0_s"][0])
    with np.load(CACHE / "BTCUSDT_book1pct_1m.npz") as z:
        bid, ask, b0 = z["bid_notional_1pct"], z["ask_notional_1pct"], int(z["t0_s"][0])
    with np.load(CACHE / "BTCUSDT_metrics_5m.npz") as z:
        metrics = {k: z[k] for k in ("oi", "top_position_lsr", "account_lsr")}
        m0 = int(z["t0_s"][0])
    return build_market(cols["open"], cols["high"], cols["low"], cols["close"], cols["volume"], cols["taker_buy_volume"],
                        t0, prem, p0, bid, ask, b0, metrics, m0)


def load_case_market() -> Market:
    with np.load(CACHE / "BTCUSDT_case_SJ-4250.npz") as z:
        t0 = int(z["t0_s"][0])
        metrics = {k: z[f"m_{k}"] for k in ("oi", "top_position_lsr", "account_lsr")}
        return build_market(z["open"], z["high"], z["low"], z["close"], z["volume"], z["taker_buy_volume"], t0,
                            z["premium"], t0, z["bid_notional_1pct"], z["ask_notional_1pct"], t0, metrics, int(z["m_t0_s"][0]))


# --- fires ---------------------------------------------------------------------------------------------

def load_fires(mkt: Market) -> pd.DataFrame:
    d = pd.read_csv(LEDGER)
    b = d[d["regime_backonly"] == "bull_30d"].copy()
    b["bar_ts"] = [int(pd.Timestamp(x).timestamp()) for x in b["ts"]]
    b = b.sort_values("bar_ts", kind="mergesort").reset_index(drop=True)
    return pd.DataFrame([fire_row(mkt, f"BTC:{int(r.bar_ts)}", int(r.bar_ts), float(r.entry), float(r.px_chg_4h),
                                  float(r.oi_chg_4h)) for r in b.itertuples(index=False)])


def fire_row(mkt: Market, fid: str, bar_ts: int, entry: float, px_chg_4h: float = np.nan, oi_chg_4h: float = np.nan) -> dict:
    entry_ts = bar_ts + 3600
    base_ts = bar_ts - 3 * 3600                                  # close of hourly bar i-4
    i0 = mkt.index(entry_ts)
    return {"fid": fid, "bar_ts": bar_ts, "entry_ts": entry_ts, "i0": i0, "entry": entry,
            "entry_close_on_path": float(mkt.close[i0 - 1]), "P0": float(mkt.close[mkt.index(base_ts) - 1]),
            "OI_E": mkt.metric_stamped("oi", entry_ts), "OI_0": mkt.metric_stamped("oi", base_ts),
            "ledger_px_chg_4h": px_chg_4h, "ledger_oi_chg_4h": oi_chg_4h}


# --- shapes (section 4) --------------------------------------------------------------------------------

def bounce_shape(high: np.ndarray, low: np.ndarray, entry: float) -> dict:
    """Relative minute indices (0 = entry minute) of spike start S, top T and reversal C, and the shape."""
    with np.errstate(invalid="ignore"):
        s = first_true(high >= entry * (1 + SPIKE))
    if s < 0:
        return {"shape": "no_spike", "S": -1, "T": -1, "C": -1}
    run = np.fmax.accumulate(high)
    with np.errstate(invalid="ignore"):
        rev = low <= (1 - REVERSAL) * run
    rev[:s] = False
    c = first_true(rev)
    if c < 0:
        return {"shape": "spike_no_reversal", "S": s, "T": int(np.nanargmax(high)), "C": -1}
    peak = run[c]
    t = first_true(high[:c + 1] == peak)
    return {"shape": "spike_reversal", "S": s, "T": t, "C": c}


def new_running_high(high: np.ndarray) -> np.ndarray:
    prev = np.concatenate([[-np.inf], np.fmax.accumulate(high)[:-1]])
    with np.errstate(invalid="ignore"):
        return high > np.where(np.isnan(prev), -np.inf, prev)


def false_tops(high: np.ndarray, s: int, t: int, pause: int = PAUSE_MIN) -> np.ndarray:
    """New running highs in [s, t) with no higher high over the next `pause` minutes."""
    new = new_running_high(high)
    out = []
    for m in range(s, t):
        if not new[m]:
            continue
        ahead = high[m + 1:m + 1 + pause]
        if not np.any(ahead > high[m]):
            out.append(m)
    return np.asarray(out, dtype=np.int64)


def stall_minutes(high: np.ndarray) -> np.ndarray:
    """Minutes since the last new running high (0 on the minute that sets one); NaN before the first valid high."""
    new = new_running_high(high)
    idx = np.where(new, np.arange(len(high)), -1)
    last = np.maximum.accumulate(idx)
    return np.where(last >= 0, np.arange(len(high)) - last, np.nan).astype(float)


# --- features (section 2) -------------------------------------------------------------------------------

def fire_features(mkt: Market, fire, rel_from: int, rel_to: int) -> pd.DataFrame:
    """Every feature at relative minutes [rel_from, rel_to) of one fire; stall and hours only from entry on."""
    i0 = int(fire.i0)
    a, b = i0 + rel_from, i0 + rel_to
    close, high = mkt.close[a:b], mkt.high[a:b]
    rel = np.arange(rel_from, rel_to)
    oi = mkt.metric_minutes("oi", a, b)
    top = mkt.metric_minutes("top_position_lsr", a, b)
    acct = mkt.metric_minutes("account_lsr", a, b)
    top_ref = mkt.metric_minutes("top_position_lsr", i0, i0 + 1)[0]
    acct_ref = mkt.metric_minutes("account_lsr", i0, i0 + 1)[0]
    from_entry = rel >= 0
    stall = np.full(len(rel), np.nan)
    if from_entry.any():
        k = first_true(from_entry)
        stall[k:] = stall_minutes(mkt.high[i0:b])[-(len(rel) - k):]
    with np.errstate(invalid="ignore", divide="ignore"):
        df = pd.DataFrame({
            "rel_min": rel,
            "close": close, "high": high,
            "PR": (close - fire.entry) / (fire.P0 - fire.entry),
            "OR": (oi - fire.OI_E) / (fire.OI_0 - fire.OI_E) if fire.OI_0 > fire.OI_E else np.full(len(rel), np.nan),
            "premium_bp": mkt.premium[a:b] * 1e4,
            "top_position_lsr_chg_pct": (top / top_ref - 1) * 100,
            "account_lsr_chg_pct": (acct / acct_ref - 1) * 100,
            "taker_buy_share_60": mkt.taker_buy_share_60[a:b],
            "volume_ratio_60": mkt.volume_ratio_60[a:b],
            "book_z": mkt.book_z[a:b],
            "stall_min": stall,
            "hours_since_entry": np.where(from_entry, rel / 60, np.nan),
        })
    return df


# --- stage A pieces (section 5) -------------------------------------------------------------------------

def shapes(mkt: Market, fires: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for f in fires.itertuples(index=False):
        i0 = int(f.i0)
        high, low, close = mkt.high[i0:i0 + HORIZON_MIN], mkt.low[i0:i0 + HORIZON_MIN], mkt.close[i0:i0 + HORIZON_MIN]
        sh = bounce_shape(high, low, f.entry)
        row = {"fid": f.fid, "shape": sh["shape"], "S": sh["S"], "T": sh["T"], "C": sh["C"]}
        if sh["S"] >= 0:
            t = sh["T"]
            row.update({"hours_to_spike": sh["S"] / 60, "hours_to_top": t / 60, "hours_spike_to_top": (t - sh["S"]) / 60,
                        "top_runup_pct": (high[t] / f.entry - 1) * 100,
                        "top_after_48h": t >= 48 * 60,
                        "fall_24h_after_top_pct": (np.nanmin(mkt.low[i0 + t:i0 + t + 1440]) / high[t] - 1) * 100,
                        "n_false_tops": len(false_tops(high, sh["S"], t)) if sh["shape"] == "spike_reversal" else np.nan})
        with np.errstate(invalid="ignore"):
            row["max_pct_7d"] = (np.nanmax(high) / f.entry - 1) * 100
            row["min_pct_7d"] = (np.nanmin(low) / f.entry - 1) * 100
            pr = (close - f.entry) / (f.P0 - f.entry)
        row["first_PR_ge_1_min"] = first_true(pr >= 1)
        rows.append(row)
    return fires.merge(pd.DataFrame(rows), on="fid", validate="one_to_one")


def top_vs_false(mkt: Market, sh: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Per fire: every feature at T minus the mean over its false tops (fires with at least one false top)."""
    rows = []
    for f in sh[sh["shape"] == "spike_reversal"].itertuples(index=False):
        high = mkt.high[int(f.i0):int(f.i0) + HORIZON_MIN]
        ft = false_tops(high, int(f.S), int(f.T))
        if len(ft) == 0:
            continue
        feats = fire_features(mkt, f, 0, int(f.T) + 1).set_index("rel_min")
        row = {"fid": f.fid, "n_false_tops": len(ft)}
        for name in FEATURES:
            at_t = feats.at[int(f.T), name]
            at_ft = np.nanmean(feats.loc[ft, name].to_numpy(float)) if np.isfinite(feats.loc[ft, name]).any() else np.nan
            row[f"{name}_top"], row[f"{name}_false"] = at_t, at_ft
            row[f"{name}_diff"] = at_t - at_ft
        rows.append(row)
    per_fire = pd.DataFrame(rows)
    rng = np.random.default_rng(SEED)
    summary = {}
    for name in FEATURES:
        d = per_fire[f"{name}_diff"].to_numpy(float)
        d = d[np.isfinite(d)]
        if len(d) == 0:
            summary[name] = {"fires": 0}
            continue
        boots = np.median(d[rng.integers(0, len(d), size=(N_BOOT, len(d)))], axis=1)
        summary[name] = {"fires": int(len(d)), "median_diff": float(np.median(d)), "mean_diff": float(d.mean()),
                         "ci95_median": [float(np.quantile(boots, 0.025)), float(np.quantile(boots, 0.975))],
                         "share_top_higher": float((d > 0).mean()),
                         "median_at_top": float(np.nanmedian(per_fire[f"{name}_top"])),
                         "median_at_false_tops": float(np.nanmedian(per_fire[f"{name}_false"]))}
    return per_fire, summary


def profiles(mkt: Market, sh: pd.DataFrame, step: int = 5) -> dict:
    """Median of every feature from 12 h before to 12 h after the top, and around false tops (fire means first)."""
    lags = np.arange(-PROFILE_MIN, PROFILE_MIN + 1, step)
    at_top = {name: [] for name in FEATURES}
    at_false = {name: [] for name in FEATURES}
    for f in sh[sh["shape"] == "spike_reversal"].itertuples(index=False):
        t = int(f.T)
        high = mkt.high[int(f.i0):int(f.i0) + HORIZON_MIN]
        ft = false_tops(high, int(f.S), t)
        lo = min(t, *(ft.tolist() or [t])) - PROFILE_MIN
        hi = max(t, *(ft.tolist() or [t])) + PROFILE_MIN + 1
        feats = fire_features(mkt, f, lo, hi).set_index("rel_min")
        for name in FEATURES:
            col = feats[name]
            at_top[name].append(col.reindex(t + lags).to_numpy(float))
            if len(ft):
                at_false[name].append(np.nanmean(np.vstack([col.reindex(m + lags).to_numpy(float) for m in ft]), axis=0))
    out = {"lags_min": lags.tolist()}
    for name in FEATURES:
        with np.errstate(all="ignore"):
            out[name] = {"top_median": np.nanmedian(np.vstack(at_top[name]), axis=0).tolist(),
                         "false_median": np.nanmedian(np.vstack(at_false[name]), axis=0).tolist() if at_false[name] else None,
                         "top_fires": len(at_top[name]), "false_fires": len(at_false[name])}
    return out


def repair(mkt: Market, sh: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """First reach of PR >= 1 and OR >= 1 within 7 days: where it sits against the top, and the move to +24 h."""
    rows = []
    for f in sh.itertuples(index=False):
        feats = fire_features(mkt, f, 0, HORIZON_MIN)
        row = {"fid": f.fid, "shape": f.shape}
        for name in ("PR", "OR"):
            k = first_true(feats[name].to_numpy(float) >= 1)
            row[f"{name}_reached"] = k >= 0
            if k < 0:
                continue
            i = int(f.i0) + k
            c_now, c_fwd = mkt.close[i], mkt.close[i + FORWARD_MIN]
            row[f"{name}_first_hours"] = k / 60
            row[f"{name}_fwd24_pct"] = (c_fwd / c_now - 1) * 100
            row[f"{name}_pct_from_entry"] = (c_now / f.entry - 1) * 100
            if f.shape == "spike_reversal":
                row[f"{name}_minutes_before_top"] = int(f.T) - k
                row[f"{name}_pct_below_top"] = (c_now / mkt.high[int(f.i0) + int(f.T)] - 1) * 100
        if f.shape == "spike_reversal":
            top = feats.iloc[int(f.T)]
            row["PR_at_top"], row["OR_at_top"] = float(top["PR"]), float(top["OR"])
        rows.append(row)
    per = pd.DataFrame(rows)
    summary = {"fires": len(per)}
    for name in ("PR", "OR"):
        r = per[per[f"{name}_reached"]]
        summary[name] = {"reached": int(len(r)), "median_first_hours": float(r[f"{name}_first_hours"].median()),
                         "mean_fwd24_pct": float(r[f"{name}_fwd24_pct"].mean()),
                         "median_fwd24_pct": float(r[f"{name}_fwd24_pct"].median()),
                         "share_fwd24_negative": float((r[f"{name}_fwd24_pct"] < 0).mean()),
                         "median_pct_from_entry": float(r[f"{name}_pct_from_entry"].median())}
        sr = r[r["shape"] == "spike_reversal"]
        if len(sr):
            summary[name].update({"spike_reversal_reached": int(len(sr)),
                                  "share_before_top": float((sr[f"{name}_minutes_before_top"] > 0).mean()),
                                  "median_pct_below_top": float(sr[f"{name}_pct_below_top"].median())})
    sr = per[per["shape"] == "spike_reversal"]
    summary["at_top"] = {"fires": int(len(sr)), "PR_median": float(sr["PR_at_top"].median()),
                         "PR_quartiles": [float(sr["PR_at_top"].quantile(q)) for q in (0.25, 0.75)],
                         "share_PR_ge_1": float((sr["PR_at_top"] >= 1).mean()),
                         "OR_median": float(sr["OR_at_top"].median()),
                         "OR_quartiles": [float(sr["OR_at_top"].quantile(q)) for q in (0.25, 0.75)],
                         "share_OR_ge_1": float((sr["OR_at_top"] >= 1).mean())}
    return per, summary


def state_map(mkt: Market, fires: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Hourly states over 7 days and the move to +24 h, binned by PR, OR and stall, with fire-bootstrap intervals."""
    rows = []
    for f in fires.itertuples(index=False):
        feats = fire_features(mkt, f, 0, HORIZON_MIN)
        k = np.arange(0, HORIZON_MIN, STATE_STEP_MIN)
        i = int(f.i0) + k
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = (mkt.close[i + FORWARD_MIN] / mkt.close[i] - 1) * 100
        sub = feats.iloc[k][["PR", "OR", "premium_bp", "stall_min", "hours_since_entry"]].reset_index(drop=True)
        sub.insert(0, "fid", f.fid)
        sub["fwd24_pct"] = fwd
        rows.append(sub)
    states = pd.concat(rows, ignore_index=True)
    summary = {"states": int(len(states)), "fires": int(states["fid"].nunique()),
               "bull_regime_drift_24h_pct": json.loads(DRIFT.read_text())["by_horizon_h"]["24"]["mean_pct"]}
    fid_codes, fids = pd.factorize(states["fid"])
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(fids), size=(N_BOOT, len(fids)))
    for name, edges in (("PR", PR_EDGES), ("OR", OR_EDGES), ("stall_min", STALL_EDGES)):
        ok = np.isfinite(states[name]) & np.isfinite(states["fwd24_pct"])
        b = pd.cut(states.loc[ok, name], edges, right=False, labels=False).to_numpy()
        codes, vals = fid_codes[ok.to_numpy()], states.loc[ok, "fwd24_pct"].to_numpy()
        nb = len(edges) - 1
        sums, counts = np.zeros((len(fids), nb)), np.zeros((len(fids), nb))
        np.add.at(sums, (codes, b), vals)
        np.add.at(counts, (codes, b), 1)
        bs, bc = sums[draws].sum(axis=1), counts[draws].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot = bs / bc
        bins = []
        for j in range(nb):
            n_states = int(counts[:, j].sum())
            bins.append({"bin": f"[{edges[j]}, {edges[j + 1]})", "states": n_states, "fires": int((counts[:, j] > 0).sum()),
                         "mean_fwd24_pct": float(sums[:, j].sum() / n_states) if n_states else None,
                         "ci95": [float(np.nanquantile(boot[:, j], 0.025)), float(np.nanquantile(boot[:, j], 0.975))]
                         if n_states else None})
        summary[name] = bins
    return states, summary


# --- data checks (section 5.7) ----------------------------------------------------------------------------

def data_checks(mkt: Market, fires: pd.DataFrame) -> dict:
    out = {}
    px = fires["entry"] / fires["P0"] - 1
    out["P0_vs_ledger_px_chg_4h_max_abs"] = float(np.nanmax(np.abs(px - fires["ledger_px_chg_4h"])))
    out["entry_vs_path_close_max_rel"] = float(np.nanmax(np.abs(fires["entry_close_on_path"] / fires["entry"] - 1)))
    oi = fires["OI_E"] / fires["OI_0"] - 1
    diff = oi - fires["ledger_oi_chg_4h"]
    out["archive_oi_chg_vs_ledger"] = {"fires_with_archive_oi": int(np.isfinite(oi).sum()),
                                       "median_abs_diff": float(np.nanmedian(np.abs(diff))),
                                       "max_abs_diff": float(np.nanmax(np.abs(diff))),
                                       "archive_also_le_minus_2pct": int((oi <= -0.02).sum())}
    with np.load(HOURLY) as z:
        ts, oi_close = z["ts"].astype(np.int64), z["oi_close"].astype(float)
    lags = {"stamp_minus_5m": -300, "stamp": 0, "stamp_plus_55m": 3300, "stamp_plus_60m": 3600}
    rows = []
    for t, v in zip(ts, oi_close):
        rec = {"month": datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m")}
        for name, lag in lags.items():
            k = (int(t) + lag - mkt.m_t0_s) // 300
            a = mkt.metrics["oi"]
            rec[name] = abs(a[k] / v - 1) if 0 <= k < len(a) and (int(t) + lag - mkt.m_t0_s) % 300 == 0 and np.isfinite(a[k]) and v > 0 else np.nan
        rows.append(rec)
    by_month = pd.DataFrame(rows).groupby("month").median(numeric_only=True)
    by_month = by_month.dropna(how="all")
    out["ledger_oi_timestamp_lag_by_month"] = {
        m: {"best": str(r.idxmin()), "median_rel_diff_best": float(r.min()),
            **{k: float(r[k]) for k in lags}} for m, r in by_month.iterrows()}
    return out
