"""QUESTION (a) -- PDO regime threshold: live -10% vs predecessor -7%.

Frozen rules are in README.md section 1.  Nothing here is tuned: the only
thing that varies between the two runs is the regime constant.

Read-only on prod.db.  Writes only into ./results/.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from studies.notebooks.pdo_adjacents import common as C  # noqa: E402
from studies.lib.validation import dsr_pbo, bootstrap  # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

THRESHOLDS = {"m10": -10.0, "m7": -7.0}


# --- the engine --------------------------------------------------------------

def scan_fires(asset: str, bars: "C.AssetBars", regime: "C.BtcRegime",
               hours: np.ndarray) -> pd.DataFrame:
    """Walk the hourly grid once and record every bar where the PDO gates
    pass *before* the regime test, together with the regime number.

    Recording gap/touch/regime independently of the threshold means the two
    threshold runs are literally the same scan -- no chance of an accidental
    asymmetry between them.  Position/one-per-day bookkeeping still has to be
    replayed per threshold (a blocked trade frees the next day's slot), so
    that lives in ``simulate``.
    """
    rows = []
    for t in hours:
        t = int(t)
        sig = bars.pdo_cdo_at(t)
        if sig is None:
            continue
        if sig["gap_pct"] < C.GAP_THRESHOLD_PCT:
            continue
        hb = bars.hour_bar_closing_at(t)
        if hb is None:
            continue
        lo, hi, close = hb
        pdo_hi = sig["pdo"] * (1 + C.TOUCH_TOL_PCT / 100)
        pdo_lo = sig["pdo"] * (1 - C.TOUCH_TOL_PCT / 100)
        if not (lo <= pdo_hi and hi >= pdo_lo):
            continue
        r = regime.at(t)
        rows.append({
            "asset": asset, "ts": t, "iso": C.iso(t),
            "bar_day": sig["bar_day"],
            "bar_day_str": C.dstr(sig["bar_day"] * C.DAY),
            "pdo": sig["pdo"], "today_open": sig["today_open"],
            "gap_pct": sig["gap_pct"],
            "hour_low": lo, "hour_high": hi, "hour_close": close,
            "btc_30d_pct": np.nan if r is None else r,
            "regime_available": r is not None,
        })
    return pd.DataFrame(rows)


def simulate(asset: str, bars: "C.AssetBars", fires: pd.DataFrame,
             threshold: float, cost_bp: float) -> pd.DataFrame:
    """Replay one-trade-per-bar-day / no-overlap bookkeeping at one threshold.

    Production semantics (``try_decide_for_variant``): a hold-expiry exit is
    processed before the entry gates on the same tick, so an exit and an entry
    can share an hour.  ``same_hour_exit_entry`` counts those.
    """
    hold_h = C.HOLD_BARS_BY_ASSET[asset]
    trades = []
    open_trade = None
    fired_bar_day = None
    same_hour = 0

    for _, f in fires.iterrows():
        t = int(f["ts"])
        # exits first (all exits land on hour boundaries)
        exited_here = False
        if open_trade is not None and t >= open_trade["exit_ts"]:
            exited_here = (t == open_trade["exit_ts"])
            open_trade = None
        # a trade whose exit is still ahead blocks any entry
        if open_trade is not None:
            continue
        bar_day = int(f["bar_day"])
        if fired_bar_day == bar_day:
            continue
        r = f["btc_30d_pct"]
        if not f["regime_available"] or not (r >= threshold):
            continue
        entry_price = float(f["hour_close"])
        day_start = bar_day * C.DAY
        exit_ts = min(t + hold_h * C.HOUR, day_start + C.DAY + C.HOUR)
        exit_price = bars.hour_close_at(exit_ts)
        if exit_price is None:
            continue
        gross_bp = (exit_price / entry_price - 1.0) * 10000.0
        trades.append({
            "asset": asset, "threshold": threshold,
            "entry_ts": t, "entry_iso": C.iso(t), "entry_date": C.dstr(t),
            "bar_day_str": f["bar_day_str"],
            "exit_ts": exit_ts, "exit_iso": C.iso(exit_ts),
            "hold_hours_scheduled": hold_h,
            "hold_hours_actual": (exit_ts - t) / C.HOUR,
            "pdo": f["pdo"], "today_open": f["today_open"],
            "gap_pct": f["gap_pct"], "btc_30d_pct": r,
            "entry_price": entry_price, "exit_price": exit_price,
            "gross_bp": gross_bp,
            "net_bp": gross_bp - cost_bp,
            "in_band": bool(-10.0 <= r < -7.0),
        })
        open_trade = {"exit_ts": exit_ts}
        fired_bar_day = bar_day
        if exited_here:
            same_hour += 1

    df = pd.DataFrame(trades)
    df.attrs["same_hour_exit_entry"] = same_hour
    return df


# --- period slicing ----------------------------------------------------------

def slice_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    if df.empty:
        return df
    d = df["entry_date"]
    if period == "pooled":
        return df
    if period == "oos":
        return df[d >= C.OOS_START]
    if period == "is":
        return df[d < C.OOS_START]
    if period == "pre_etf":
        return df[d < C.ETF_DATE]
    if period == "post_etf":
        return df[d >= C.ETF_DATE]
    raise ValueError(period)


PERIODS = ["oos", "is", "pre_etf", "post_etf", "pooled"]


def main() -> None:
    cfg = C.assert_config_parity()
    print("live sleeve config:", cfg)

    with C.ro_conn() as con:
        last_ts = int(con.execute(
            "SELECT MIN(m) FROM (SELECT MAX(open_time)/1000 AS m FROM btc_1m "
            "UNION ALL SELECT MAX(open_time)/1000 FROM eth_1m)").fetchone()[0])
    end_ts = last_ts - (last_ts % C.HOUR)          # last complete hour boundary
    hours = C.hour_range(C.STUDY_START, end_ts)
    print(f"hourly grid: {C.iso(int(hours[0]))} .. {C.iso(int(hours[-1]))} "
          f"({len(hours)} hours)")

    regime = C.BtcRegime()
    all_fires, all_trades = [], []
    bars_by_asset = {}
    same_hour_counts = {}

    for asset in ("BTC", "ETH"):
        bars = C.AssetBars(asset)
        bars_by_asset[asset] = bars
        fires = scan_fires(asset, bars, regime, hours)
        print(f"{asset}: {len(fires)} gate-passing touch bars (pre-regime)")
        all_fires.append(fires)
        for name, thr in THRESHOLDS.items():
            tr = simulate(asset, bars, fires, thr, C.COST_BP_RT)
            tr["config"] = name
            same_hour_counts[f"{asset}_{name}"] = tr.attrs["same_hour_exit_entry"]
            print(f"  {name} (thr={thr}): {len(tr)} trades")
            all_trades.append(tr)

    fires_df = pd.concat(all_fires, ignore_index=True)
    trades_df = pd.concat(all_trades, ignore_index=True)
    fires_df.to_csv(RESULTS / "qa_fires.csv", index=False)
    trades_df.to_csv(RESULTS / "qa_trades.csv", index=False)

    # --- summary table -------------------------------------------------------
    rows = []
    for asset in ("BTC", "ETH"):
        for period in PERIODS:
            for name, thr in THRESHOLDS.items():
                sub = slice_period(
                    trades_df[(trades_df.asset == asset) & (trades_df.config == name)],
                    period)
                s = C.summarize(sub["net_bp"].to_numpy() if len(sub) else [])
                s10 = C.summarize(
                    (sub["gross_bp"] - C.COST_BP_RT_LIVE).to_numpy() if len(sub) else [])
                rows.append({
                    "asset": asset, "period": period, "config": name,
                    "threshold": thr, "n": s["n"],
                    "mean_bp_18": s["mean"], "median_bp_18": s["median"],
                    "sd_bp": s["sd"], "win_rate": s["win_rate"],
                    "sum_bp_18": s["sum"], "per_trade_sharpe": s["sharpe"],
                    "mean_bp_10": s10["mean"],
                })
    summary = pd.DataFrame(rows)
    summary.to_csv(RESULTS / "qa_summary.csv", index=False)

    # --- clause arithmetic ---------------------------------------------------
    def mean_bp(asset, period, name):
        r = summary[(summary.asset == asset) & (summary.period == period)
                    & (summary.config == name)]
        return float(r["mean_bp_18"].iloc[0]), int(r["n"].iloc[0])

    deltas = {}
    for period in PERIODS:
        for asset in ("BTC", "ETH"):
            m7, n7 = mean_bp(asset, period, "m7")
            m10, n10 = mean_bp(asset, period, "m10")
            d = (m7 - m10) if (np.isfinite(m7) and np.isfinite(m10)) else float("nan")
            deltas[f"{period}_{asset}"] = {
                "mean_bp_m7": m7, "n_m7": n7,
                "mean_bp_m10": m10, "n_m10": n10,
                "delta_bp": d,
            }

    # discriminating trades: taken at -10 but blocked at -7
    band = trades_df[(trades_df.config == "m10") & (trades_df.in_band)]
    band_rows = []
    for asset in ("BTC", "ETH"):
        for period in PERIODS:
            sub = slice_period(band[band.asset == asset], period)
            s = C.summarize(sub["net_bp"].to_numpy() if len(sub) else [])
            band_rows.append({"asset": asset, "period": period, "n": s["n"],
                              "mean_bp_18": s["mean"], "median_bp_18": s["median"],
                              "win_rate": s["win_rate"], "sum_bp_18": s["sum"]})
    band_df = pd.DataFrame(band_rows)
    band_df.to_csv(RESULTS / "qa_band_trades.csv", index=False)
    band.to_csv(RESULTS / "qa_band_trade_list.csv", index=False)

    # --- regime occupancy context (reported, not decision-bearing) ----------
    reg_vals = np.array([regime.at(int(t)) if regime.at(int(t)) is not None else np.nan
                         for t in hours], dtype=float)
    hour_dates = np.array([C.dstr(int(t)) for t in hours])
    occ = {}
    for label, mask in (("pooled", np.ones(len(hours), bool)),
                        ("oos", hour_dates >= C.OOS_START),
                        ("is", hour_dates < C.OOS_START),
                        ("pre_etf", hour_dates < C.ETF_DATE),
                        ("post_etf", hour_dates >= C.ETF_DATE)):
        v = reg_vals[mask]
        fin = np.isfinite(v)
        occ[label] = {
            "hours": int(mask.sum()),
            "hours_with_regime": int(fin.sum()),
            "pct_below_-10": float(np.mean(v[fin] < -10.0)) if fin.any() else float("nan"),
            "pct_in_band_-10_to_-7": float(np.mean((v[fin] >= -10.0) & (v[fin] < -7.0)))
                if fin.any() else float("nan"),
            "min_regime": float(np.nanmin(v)) if fin.any() else float("nan"),
        }
    occ_df = pd.DataFrame(occ).T.reset_index().rename(columns={"index": "period"})
    occ_df.to_csv(RESULTS / "qa_regime_occupancy.csv", index=False)

    oos_trades = slice_period(trades_df, "oos")
    oos_trades.to_csv(RESULTS / "qa_oos_trade_list.csv", index=False)

    a1_btc = deltas["oos_BTC"]["delta_bp"]
    a1_eth = deltas["oos_ETH"]["delta_bp"]
    a2_btc = deltas["is_BTC"]["delta_bp"]
    a2_eth = deltas["is_ETH"]["delta_bp"]

    def ge(x, v):
        return bool(np.isfinite(x) and x >= v)

    A1 = ge(a1_btc, 5.0) and ge(a1_eth, 5.0)
    A2 = ge(a2_btc, 0.0) and ge(a2_eth, 0.0)
    verdict = "ADOPT_-7" if (A1 and A2) else "KEEP_-10"

    # supporting: DSR at N_TRIALS=2 for each pooled config
    dsr_rows = []
    for asset in ("BTC", "ETH"):
        for name in THRESHOLDS:
            sub = trades_df[(trades_df.asset == asset) & (trades_df.config == name)]
            r = dsr_pbo.dsr_from_returns(sub["net_bp"].to_numpy() / 10000.0, n_trials=2)
            dsr_rows.append({"asset": asset, "config": name,
                             "n": 0 if r is None else r["n"],
                             "sr_per_trade": None if r is None else r["sr_per_obs"],
                             "dsr": None if r is None else r["dsr"]})
    dsr_df = pd.DataFrame(dsr_rows)
    dsr_df.to_csv(RESULTS / "qa_dsr.csv", index=False)

    # supporting: bootstrap on the OOS difference is not defined (paired sets
    # differ only by removals), so bootstrap each config's pooled net_bp.
    boot_rows = []
    for asset in ("BTC", "ETH"):
        for name in THRESHOLDS:
            sub = trades_df[(trades_df.asset == asset) & (trades_df.config == name)]
            b = bootstrap.bootstrap_sharpe((sub["net_bp"] / 10000.0).tolist(),
                                           n_iter=10000, seed=42)
            boot_rows.append({"asset": asset, "config": name,
                              "n": None if b is None else b["n"],
                              "sr_point": None if b is None else b["sr_point"],
                              "sr_p05": None if b is None else b["sr_p05"],
                              "sr_p95": None if b is None else b["sr_p95"]})
    pd.DataFrame(boot_rows).to_csv(RESULTS / "qa_bootstrap.csv", index=False)

    out = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "live_config": cfg,
        "cost_bp_rt_primary": C.COST_BP_RT,
        "cost_bp_rt_secondary": C.COST_BP_RT_LIVE,
        "grid_first_hour": C.iso(int(hours[0])),
        "grid_last_hour": C.iso(int(hours[-1])),
        "same_hour_exit_entry_counts": same_hour_counts,
        "deltas": deltas,
        "clauses": {
            "A1_oos_delta_btc_bp": a1_btc,
            "A1_oos_delta_eth_bp": a1_eth,
            "A1_required_bp": 5.0,
            "A1_fired": A1,
            "A2_is_delta_btc_bp": a2_btc,
            "A2_is_delta_eth_bp": a2_eth,
            "A2_required_bp": 0.0,
            "A2_fired": A2,
        },
        "verdict": verdict,
        "regime_occupancy": occ,
        "oos_trade_count_by_config": {
            f"{a}_{n}": int(len(oos_trades[(oos_trades.asset == a) & (oos_trades.config == n)]))
            for a in ("BTC", "ETH") for n in THRESHOLDS},
        "band_trades_total": int(len(band)),
        "band_trades_oos": int(len(slice_period(band, "oos"))),
    }
    (RESULTS / "qa_clauses.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["clauses"], indent=2))
    print("verdict:", verdict)
    print(summary.to_string(index=False))
    print(band_df.to_string(index=False))


if __name__ == "__main__":
    main()
