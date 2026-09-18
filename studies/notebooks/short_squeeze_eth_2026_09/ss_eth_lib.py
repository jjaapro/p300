"""SHORT_SQUEEZE on ETH: the five tables the engine reads, built from on-disk panels; the engine itself is the
execution study's port with its loads replaced by arguments (README.md is the pre-registration)."""
from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in (ROOT, ROOT / "studies" / "notebooks" / "execution_2026_09", ROOT / "studies" / "notebooks" / "exit_policy_2026_09"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import exec_lib as ex  # noqa: E402
import micro_lib as M  # noqa: E402
from studies.lib.validation.dsr_pbo import dsr_from_returns  # noqa: E402

RESULTS = HERE / "results"
PROD_DB = ROOT / "data" / "databases" / "prod.db"
ORB_CACHE = ROOT / "studies" / "notebooks" / "orb_study" / "cache"
EP_CACHE = ROOT / "studies" / "notebooks" / "exit_policy_2026_09" / "cache"
SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
FUNDING_TABLE = {"BTC": "cd_funding_rate", "ETH": "cd_funding_rate_eth"}

# --- frozen numbers (README) --------------------------------------------------------------------------
COST_BP_RT = 10.0                    # the sleeve's COST_BP_RT and the measured taker round trip
MIN_TRIGGERS = 30
MIN_NET_R = 0.20
DSR_BAR = 0.95
JACCARD_MIN = 0.90
N_BOOT, SEED = 5_000, 42
E0_EXPECT = dict(n=70, win=0.443, mean_r=0.40, pf=1.65, end="2026-05-18")   # the BTC port's anchor, P0


# --- panels -> tables ------------------------------------------------------------------------------------

def load_perp_panel(asset: str) -> tuple[dict, int]:
    with np.load(ORB_CACHE / f"{SYMBOL[asset]}_perp_1m.npz") as z:
        a = {k: z[k].astype(float) for k in ("open", "high", "low", "close", "volume", "taker_buy_volume")}
        t0 = int(z["t0_ms"][0]) // 1000
    return a, t0


def load_spot_panel(asset: str) -> tuple[dict, int]:
    with np.load(EP_CACHE / f"{SYMBOL[asset]}_spot_1m_panel.npz") as z:
        a = {k: z[k].astype(float) for k in ("open", "high", "low", "close", "volume", "taker_buy_volume")}
        t0 = int(z["t0_s"][0])
    return a, t0


def load_metrics(asset: str) -> tuple[np.ndarray, int]:
    with np.load(EP_CACHE / f"{SYMBOL[asset]}_metrics_5m_full.npz") as z:
        return z["oi"].astype(float), int(z["t0_s"][0])


def bars_from_minutes(a: dict, t0: int, width: int) -> pd.DataFrame:
    """Aggregate a minute panel to `width`-minute bars in the prod tables' shape, indexed by UTC bar open.
    A minute with zero volume is dead (NaN); a bar with no present minute is dropped."""
    n = len(a["close"]) // width * width
    r = {k: a[k][:n].reshape(-1, width) for k in a}
    present = np.isfinite(r["close"]) & (r["volume"] > 0)
    has = present.any(axis=1)
    first = np.argmax(present, axis=1)
    last = width - 1 - np.argmax(present[:, ::-1], axis=1)
    rows = np.arange(len(has))
    with np.errstate(invalid="ignore"), np.testing.suppress_warnings() as sup:
        sup.filter(RuntimeWarning)
        o = r["open"][rows, first]
        c = r["close"][rows, last]
        h = np.nanmax(np.where(present, r["high"], np.nan), axis=1)
        lo = np.nanmin(np.where(present, r["low"], np.nan), axis=1)
        v = np.where(present, r["volume"], 0.0).sum(axis=1)
        vb = np.where(present, r["taker_buy_volume"], 0.0).sum(axis=1)
    ts = t0 + np.arange(len(has)) * width * 60
    df = pd.DataFrame({"timestamp": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v,
                       "volume_buy": vb, "volume_sell": v - vb})[has]
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("ts")


def oi_hourly(oi: np.ndarray, m0: int) -> pd.DataFrame:
    """Close-of-hour open interest: the 5-minute snapshot stamped H + 1 h, for every hour H the archive covers."""
    assert m0 % 3600 == 0
    hours = np.arange(m0, m0 + len(oi) * 300 - 3600, 3600, dtype=np.int64)
    j = (hours + 3600 - m0) // 300
    valid = j < len(oi)
    hours, j = hours[valid], j[valid]
    close = oi[j]
    open_ = oi[(hours - m0) // 300]
    df = pd.DataFrame({"timestamp": hours, "oi_open": open_, "oi_close": close})
    df = df[np.isfinite(df["oi_close"])]
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    return df.set_index("ts")


def funding_table(asset: str) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql(f"SELECT timestamp, fr_open, fr_high, fr_low, fr_close FROM {FUNDING_TABLE[asset]} "
                         "ORDER BY timestamp", con)
    finally:
        con.close()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("ts")
    return df[~df.index.duplicated(keep="last")]


def tables_from_panels(asset: str) -> dict:
    perp, t0 = load_perp_panel(asset)
    spot, s0 = load_spot_panel(asset)
    assert t0 == s0, "perp and spot panels must share a grid"
    oi, m0 = load_metrics(asset)
    return {"perp15": bars_from_minutes(perp, t0, 15), "spot15": bars_from_minutes(spot, s0, 15),
            "perp_h": bars_from_minutes(perp, t0, 60), "oi_h": oi_hourly(oi, m0), "fund_h": funding_table(asset)}


def tables_from_prod() -> dict:
    """The engine's own loads, for the P0 parity run (BTC only)."""
    return {"perp15": ex._load_ts_table("cd_futures_15m"), "spot15": ex._load_ts_table("cd_spot_15m"),
            "perp_h": ex._load_ts_table("cd_futures_ohlcv"), "oi_h": ex._load_ts_table("cd_open_interest"),
            "fund_h": ex._load_ts_table("cd_funding_rate")}


# --- the engine, verbatim with injected tables -------------------------------------------------------------

def frame(t: dict) -> tuple[pd.DataFrame, pd.Series]:
    """exec_lib.short_squeeze_frame with the five loads replaced by `t`; nothing else changed."""
    perp15, spot15, perp_h, oi_h, fund_h = t["perp15"], t["spot15"], t["perp_h"], t["oi_h"], t["fund_h"]
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
        for name, (lo, hi) in ex.SS_SESSIONS.items():
            if lo <= h < hi:
                return name
        return None

    b15["session"] = b15.index.hour.map(_session_of)
    b15 = b15.dropna(subset=["session"]).copy()
    b15["date"] = b15.index.date
    b15["prior_low_24"] = b15["l"].rolling(ex.SS_LOOKBACK_BARS + 1).min().shift(1)
    bar_range = (b15["h"] - b15["l"]).clip(lower=1e-9)
    b15["close_in_range"] = (b15["c"] - b15["l"]) / bar_range
    b15["divergence"] = b15["spot_cvd"] - b15["perp_cvd"]
    univ = b15[b15["session"].isin(["london", "ny"])].copy()
    univ["perp_cvd_pct"] = ex._rolling_percentile(univ["perp_cvd"], ex.SS_WINDOW_BARS)
    univ["divergence_pct"] = ex._rolling_percentile(univ["divergence"], ex.SS_WINDOW_BARS)
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
                    & (b15["perp_cvd_pct"] < ex.SS_PARAMS["perp_cvd_pct_max"])
                    & (b15["divergence_pct"] > ex.SS_PARAMS["divergence_pct_min"])
                    & (b15["close_in_range"] >= ex.SS_PARAMS["close_in_range_min"]))
    out = pd.Series(False, index=long_trigger.index)
    last = None
    for ts, v in long_trigger.items():
        if not v:
            continue
        if last is None or (ts - last) >= pd.Timedelta(minutes=15 * ex.SS_COOLDOWN_BARS):
            out.loc[ts] = True
            last = ts
    return b15, out


def simulate(b15: pd.DataFrame, trig: pd.Series, m1: ex.PricePath, slip_bp: float, tp_R: float = 3.0) -> pd.DataFrame:
    """exec_lib.short_squeeze_notebook_simulate with the per-leg slippage as a parameter."""
    rows = []
    for ts in trig.index[trig]:
        bar = b15.loc[ts]
        entry = float(bar["c"]); stop = float(bar["l"]) * (1 - 0.001); risk = entry - stop
        if risk <= 0:
            continue
        target = entry + tp_R * risk
        t0 = int(ts.timestamp()) + 900
        i0, i1 = m1.idx_ge(t0), m1.idx_gt(t0 + ex.SS_TIME_STOP_HOURS * 3600)
        if i1 <= i0:
            continue
        w = ex.walk(m1, i0, i1, 1, stop, target)
        exit_price = w["price"]
        slip = slip_bp / 1e4
        eff_e, eff_x = entry * (1 + slip), exit_price * (1 - slip)
        rows.append(dict(trigger_ts=ts, entry=entry, stop=stop, target=target, exit_price=exit_price,
                         exit_reason={"stop": "stop", "target": "target", "tif": "time"}[w["kind"]],
                         pnl_R=(eff_x - eff_e) / risk, risk_pct=risk / entry))
    return pd.DataFrame(rows, columns=["trigger_ts", "entry", "stop", "target", "exit_price", "exit_reason",
                                       "pnl_R", "risk_pct"])


def with_net(sim: pd.DataFrame, cost_bp_rt: float = COST_BP_RT) -> pd.DataFrame:
    """Gross pnl_R (0 bp slippage) minus the round-trip cost as a fraction of the trade's own risk distance."""
    out = sim.copy()
    out["cost_R"] = (cost_bp_rt / 1e4) / out["risk_pct"]
    out["net_R"] = out["pnl_R"] - out["cost_R"]
    out["day"] = pd.to_datetime(out["trigger_ts"], utc=True).dt.strftime("%Y-%m-%d")
    return out


# --- statistics ------------------------------------------------------------------------------------------

def max_drawdown(cum: np.ndarray) -> float:
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))
    return float((peak[1:] - cum).max()) if len(cum) else 0.0


def profit_factor(r: np.ndarray) -> float | None:
    g, l = r[r > 0].sum(), -r[r < 0].sum()
    return float(g / l) if l > 0 else None


def describe(sim: pd.DataFrame, col: str) -> dict:
    if not len(sim):
        return {"n": 0}
    r = sim[col].to_numpy(float)
    days = pd.to_datetime(sim["trigger_ts"], utc=True)
    years = max((days.max() - days.min()).days / 365.25, 1e-9)
    n = len(r)
    half = -(-n // 2)
    out = {"n": n, "mean_R": float(r.mean()), "win_rate": float((r > 0).mean()), "pf": profit_factor(r),
           "annual_R": float(r.sum() / years), "max_dd_R": max_drawdown(np.cumsum(r)), "years": float(years),
           "first_half_mean_R": float(r[:half].mean()), "second_half_mean_R": float(r[half:].mean()) if n > half else None,
           "exit_mix": sim["exit_reason"].value_counts().to_dict(), "median_risk_pct": float(sim["risk_pct"].median() * 100),
           "per_year": {y: {"n": int(len(g)), "mean_R": float(g[col].mean()), "sum_R": float(g[col].sum())}
                        for y, g in sim.groupby(days.dt.year)}}
    out["mar"] = (out["annual_R"] / out["max_dd_R"]) if out["max_dd_R"] > 0 else None
    if n > 2 and r.std(ddof=1) > 0:
        d = dsr_from_returns(r, n_trials=1)
        out["dsr"] = None if d is None else float(d["dsr"])
        out["sharpe_per_trade"] = float(r.mean() / r.std(ddof=1))
    else:
        out["dsr"], out["sharpe_per_trade"] = None, None
    return out


def block_bootstrap_mean(sim: pd.DataFrame, col: str, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    """30-day-block circular bootstrap of the mean (trades assigned to trigger days), CI90."""
    if len(sim) < 2:
        return {"ci90": [None, None]}
    days = pd.to_datetime(sim["trigger_ts"], utc=True).dt.strftime("%Y-%m-%d")
    axis = M.day_axis(days)
    idx = M.block_indices(len(axis), n_boot=n_boot, seed=seed)
    boot = M.boot_means(days, sim[col].to_numpy(float), axis, idx)
    return {"ci90": [float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))], "mean": float(sim[col].mean())}


def jaccard(a: pd.Series, b: pd.Series, end=None) -> dict:
    """Trigger-set agreement of two runs over their common span (both indices truncated to `end`)."""
    ta, tb = set(a.index[a]), set(b.index[b])
    lo = max(min(a.index), min(b.index))
    hi = min(max(a.index), max(b.index)) if end is None else min(max(a.index), max(b.index), end)
    ta = {t for t in ta if lo <= t <= hi}
    tb = {t for t in tb if lo <= t <= hi}
    inter, union = ta & tb, ta | tb
    return {"common_span": [str(lo), str(hi)], "a": len(ta), "b": len(tb), "shared": len(inter),
            "jaccard": (len(inter) / len(union)) if union else None,
            "only_a": sorted(str(t) for t in ta - tb)[:20], "only_b": sorted(str(t) for t in tb - ta)[:20]}


def decide(eth: dict, boot: dict, fidelity: dict) -> dict:
    a = eth["n"] >= MIN_TRIGGERS
    b = eth["n"] > 0 and eth["mean_R"] >= MIN_NET_R and boot["ci90"][0] is not None and boot["ci90"][0] > 0
    c = eth["n"] > 1 and eth["first_half_mean_R"] > 0 and (eth["second_half_mean_R"] or 0) > 0
    d = eth.get("dsr") is not None and eth["dsr"] >= DSR_BAR
    e = fidelity.get("jaccard") is not None and fidelity["jaccard"] >= JACCARD_MIN and fidelity.get("pnl_agree", False)
    if not e:
        verdict = "DESCRIPTIVE — the builder does not reproduce the BTC run"
    elif a and b and c and d:
        verdict = "RECOMMEND an ETH paper twin"
    else:
        verdict = "KILL for ETH"
    return {"a_n": a, "b_net_and_ci": b, "c_both_halves": c, "d_dsr": d, "e_fidelity": e,
            "failing": [k for k, v in (("a_n", a), ("b_net_and_ci", b), ("c_both_halves", c), ("d_dsr", d),
                                       ("e_fidelity", e)) if not v], "verdict": verdict}
