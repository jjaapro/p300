"""SQUEEZE_BULL on ETH: the hourly frame from on-disk panels, the frozen June engine unchanged, the fidelity gate
and the decision rule (README.md is the pre-registration)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, ROOT / "studies" / "notebooks" / "short_squeeze_eth_2026_09",
          ROOT / "studies" / "notebooks" / "squeeze_bull_revalidation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import ss_eth_lib as SS  # noqa: E402  (the panel -> table builders of the SHORT_SQUEEZE-on-ETH study)
import squeeze_bull_lib as SB  # noqa: E402  (imports the frozen June rule functions)
from studies.lib.validation.dsr_pbo import dsr_from_returns  # noqa: E402

RESULTS = HERE / "results"
REF_LEDGER = ROOT / "studies" / "notebooks" / "squeeze_bull_revalidation" / "results" / "full_oi_flush_ledger.csv"

# --- frozen numbers (README) --------------------------------------------------------------------------
FULL_SAMPLE_START = SB.FULL_SAMPLE_START          # 2022-01-30, the BTC revalidation's
OOS_START = SB.OOS_START                          # 2026-04-14
JUNE_COST_BP = float(SB.OI_COST_BP)               # 18, charged inside r_outcome by June's replay
LIVE_COST_BP = 10.0                               # ETHUSDT's measured taker round trip (chento's entries)
RECOST_R = ((JUNE_COST_BP - LIVE_COST_BP) / 1e4) / SB.OI_STOP_PCT     # +0.04 R per trade at a 2 % stop
BUILD_MIN_N, BUILD_MIN_MEAN_R, BUILD_MIN_MAR = 10, 0.10, 1.5
JACCARD_MIN = 0.80
BULL = "bull_30d"


# --- the frame ---------------------------------------------------------------------------------------------

def hourly_frame(asset: str) -> pd.DataFrame:
    """squeeze_bull_lib.load_oi_frame's recipe on panel-built tables: hourly OHLC from the perpetual 1-minute panel
    joined (inner) with close-of-hour open interest from the 5-minute archive, then the June features."""
    perp, t0 = SS.load_perp_panel(asset)
    bars = SS.bars_from_minutes(perp, t0, 60)[["open", "high", "low", "close"]]
    oi, m0 = SS.load_metrics(asset)
    oi_h = SS.oi_hourly(oi, m0)[["oi_close"]]
    df = bars.join(oi_h, how="inner")
    df.index.name = "ts"
    return add_features(df)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["oi_chg_4h"] = df["oi_close"].pct_change(4)
    df["px_chg_4h"] = df["close"].pct_change(4)
    daily = df["close"].resample("1D").last()
    r30 = daily.pct_change(30)
    df["ret_30d"] = r30.reindex(df.index, method="ffill")
    df["ret_30d_backonly"] = r30.shift(1).reindex(df.index, method="ffill")
    return df


def frame_facts(df: pd.DataFrame) -> dict:
    hours = pd.date_range(df.index.min(), df.index.max(), freq="h")
    return {"first": str(df.index.min()), "last": str(df.index.max()), "rows": int(len(df)),
            "grid_hours": int(len(hours)), "missing_hours": int(len(hours) - len(df)),
            "oi_finite_share": float(np.isfinite(df["oi_close"]).mean())}


# --- the ledger --------------------------------------------------------------------------------------------

def ledger(df: pd.DataFrame, start: pd.Timestamp = FULL_SAMPLE_START) -> pd.DataFrame:
    """June's detector and replay, unchanged (18 bp inside r_outcome); fires before `start` dropped; r_10bp added."""
    led = SB.oi_ledger(df)
    led = led[led["ts"] >= start].reset_index(drop=True)
    led["r_10bp"] = led["r_outcome"] + RECOST_R
    return led


def bull_gated(led: pd.DataFrame, resolved_only: bool = True) -> pd.DataFrame:
    out = led[led["regime_backonly"] == BULL]
    if resolved_only:
        out = out[out["resolved"]]
    return out.reset_index(drop=True)


def stats(led: pd.DataFrame, col: str = "r_outcome") -> dict:
    """June's stats form: n, mean, win rate, cumulative R, max drawdown, annual R, MAR — plus halves, per year,
    exit mix and the one-trial DSR."""
    if led.empty:
        return {"n": 0}
    led = led.sort_values("ts").reset_index(drop=True)
    r = led[col].to_numpy(float)
    cum = np.cumsum(r)
    dd = cum - np.maximum.accumulate(cum)
    span_y = max((led["ts"].max() - led["ts"].min()).total_seconds() / (365.25 * 86400), 0.05)
    annual = float(r.mean() * len(r) / span_y)
    n = len(r)
    half = -(-n // 2)
    out = {"n": n, "mean_R": float(r.mean()), "WR": float((r > 0).mean()), "cum_R": float(cum[-1]),
           "maxDD": float(dd.min()), "annual_R": annual,
           "MAR": (annual / abs(float(dd.min()))) if dd.min() < 0 else None,
           "span_y": float(span_y), "first_fire": str(led["ts"].min()), "last_fire": str(led["ts"].max()),
           "first_half_mean_R": float(r[:half].mean()), "second_half_mean_R": float(r[half:].mean()) if n > half else None,
           "exit_mix": led["exit_kind"].value_counts().to_dict(),
           "per_year": {int(y): {"n": int(len(g)), "mean_R": float(g[col].mean()), "sum_R": float(g[col].sum())}
                        for y, g in led.groupby(led["ts"].dt.year)}}
    if n > 2 and r.std(ddof=1) > 0:
        d = dsr_from_returns(r, n_trials=1)
        out["dsr"] = None if d is None else float(d["dsr"])
    else:
        out["dsr"] = None
    return out


def oos(led: pd.DataFrame) -> pd.DataFrame:
    return led[led["ts"] >= OOS_START].reset_index(drop=True)


# --- fidelity ------------------------------------------------------------------------------------------------

def load_reference() -> pd.DataFrame:
    ref = pd.read_csv(REF_LEDGER)
    ref["ts"] = pd.to_datetime(ref["ts"], utc=True)
    return ref


def fidelity(panel_led: pd.DataFrame, ref: pd.DataFrame) -> dict:
    """Bull-gated fire sets of the panel-built BTC run and the reference ledger over their common span, and the
    replay's agreement on the shared fires."""
    lo = max(panel_led["ts"].min(), ref["ts"].min())
    hi = min(panel_led["ts"].max(), ref["ts"].max())
    a = panel_led[(panel_led["ts"] >= lo) & (panel_led["ts"] <= hi) & (panel_led["regime_backonly"] == BULL)]
    b = ref[(ref["ts"] >= lo) & (ref["ts"] <= hi) & (ref["regime_backonly"] == BULL)]
    sa, sb = set(a["ts"]), set(b["ts"])
    shared = sorted(sa & sb)
    ra = a.set_index("ts").loc[shared, "r_outcome"].to_numpy(float) if shared else np.zeros(0)
    rb = b.set_index("ts").loc[shared, "r_outcome"].to_numpy(float) if shared else np.zeros(0)
    max_diff = float(np.abs(ra - rb).max()) if len(shared) else None
    jac = (len(shared) / len(sa | sb)) if (sa | sb) else None
    all_a, all_b = set(panel_led[(panel_led["ts"] >= lo) & (panel_led["ts"] <= hi)]["ts"]), \
        set(ref[(ref["ts"] >= lo) & (ref["ts"] <= hi)]["ts"])
    return {"common_span": [str(lo), str(hi)], "panel_bull": len(sa), "ref_bull": len(sb), "shared_bull": len(shared),
            "jaccard_bull": jac, "max_abs_r_diff_shared": max_diff,
            "only_panel": [str(t) for t in sorted(sa - sb)][:30], "only_ref": [str(t) for t in sorted(sb - sa)][:30],
            "all_regimes": {"panel": len(all_a), "ref": len(all_b), "shared": len(all_a & all_b),
                            "jaccard": (len(all_a & all_b) / len(all_a | all_b)) if (all_a | all_b) else None},
            "pass": bool(jac is not None and jac >= JACCARD_MIN and max_diff is not None and max_diff < 1e-9)}


# --- decision ------------------------------------------------------------------------------------------------

def projection(led_bull: pd.DataFrame, df: pd.DataFrame, need: int, have: int) -> dict:
    """When OOS n < 10: the date at which n >= 10 is expected at ETH's own bull-gated firing rate per bull day."""
    bull_days = df["ret_30d_backonly"].resample("1D").last() > SB.BULL_THRESHOLD
    n_bull_days = int(bull_days.sum())
    rate = (len(led_bull) / n_bull_days) if n_bull_days else None
    return {"bull_days_in_sample": n_bull_days, "fires_per_bull_day": rate,
            "bull_days_needed": (None if not rate else math.ceil((need - have) / rate)),
            "note": "calendar date depends on how many of the coming days are bull days; not a forecast"}


def decide(oos_s: dict, full_s: dict, fid: dict, proj: dict | None) -> dict:
    d = {"a_oos_n": oos_s.get("n", 0), "a_oos_mean_R": oos_s.get("mean_R"), "b_full_MAR": full_s.get("MAR"),
         "c_halves": [full_s.get("first_half_mean_R"), full_s.get("second_half_mean_R")], "d_fidelity": fid["pass"]}
    if not fid["pass"]:
        d["verdict"], d["reason"] = "DESCRIPTIVE", "fidelity gate failed: the builder does not reproduce the BTC run"
        return d
    n, m = oos_s.get("n", 0), oos_s.get("mean_R")
    fn, fm = full_s.get("n", 0), full_s.get("mean_R")
    a = n >= BUILD_MIN_N and m is not None and m >= BUILD_MIN_MEAN_R
    b = full_s.get("MAR") is not None and full_s["MAR"] >= BUILD_MIN_MAR
    c = (full_s.get("first_half_mean_R") or 0) > 0 and (full_s.get("second_half_mean_R") or 0) > 0
    kill = (n >= BUILD_MIN_N and m is not None and m <= 0) or (fn >= BUILD_MIN_N and fm is not None and fm <= 0)
    if kill:
        d["verdict"], d["reason"] = "KILL", (f"OOS mean R {m:+.4f} <= 0 at n = {n}" if n >= BUILD_MIN_N and m is not None and m <= 0
                                            else f"full-sample mean R {fm:+.4f} <= 0 at n = {fn}")
    elif a and b and c:
        d["verdict"], d["reason"] = "BUILD", f"OOS n = {n}, mean R {m:+.4f}; full MAR {full_s['MAR']:.2f}; both halves > 0"
    else:
        failing = [k for k, v in (("a_oos", a), ("b_mar", b), ("c_halves", c)) if not v]
        d["verdict"], d["reason"] = "INCONCLUSIVE", "failing: " + ", ".join(failing) + (f" (OOS n = {n} < {BUILD_MIN_N})" if n < BUILD_MIN_N else "")
        if n < BUILD_MIN_N and proj:
            d["projection"] = proj
    d["clauses"] = {"a": a, "b": b, "c": c, "kill": kill}
    return d
