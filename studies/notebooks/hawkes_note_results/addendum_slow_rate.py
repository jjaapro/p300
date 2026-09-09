"""POST-HOC diagnostic, added AFTER the pre-registered ladder was computed.

Labelled exploratory. It does not change any pre-registered clause; it answers the
follow-up question the ladder raises: if F(w) grows monotonically with bin width,
is the over-dispersion fully explained by a SLOWLY VARYING Poisson rate (volatility
regimes), or is there residual fast clustering that only a self-exciting kernel
would produce?

N3 null: inhomogeneous Poisson. Estimate lambda(t) as a centred rolling mean of the
observed hourly counts over W hours, then draw counts ~ Poisson(lambda_h) per hour
and re-aggregate. If the observed F(w) sits inside this null at every w, a
time-varying-rate Poisson model is a sufficient description and a Hawkes term adds
nothing. Residual excess at small w = genuine fast self-excitation.

Also fits the log-log slope of the hourly-count ACF over lags 1..24 (single
exponential kernel => fast/curved decay; long memory => straight line, slope -beta).

Run: python studies/notebooks/hawkes_note_results/addendum_slow_rate.py
"""
from __future__ import annotations

import json
import sqlite3
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from strategies.support import db  # noqa: E402

OUT = Path(__file__).resolve().parent
N_REP = 200
BIN_WIDTHS_H = (1, 4, 12, 24, 72)
SMOOTH_W = (24, 168)   # hours: absorb rate variation slower than 1 day / 1 week


def hourly_counts_btc(threshold: float):
    c = sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)
    b = pd.read_sql("select open_time, close from btc_1m order by open_time", c)
    c.close()
    ts = b.open_time.values.astype(float) / 1000.0
    px = b.close.values.astype(float)
    r = np.zeros(len(px))
    r[1:] = np.log(px[1:] / px[:-1])
    ok = np.zeros(len(px), dtype=bool)
    ok[1:] = (ts[1:] - ts[:-1]) == 60.0
    ev = ts[ok & (np.abs(r) >= threshold)]
    t0, t1 = ts[0], ts[-1]
    nb = int(np.floor((t1 - t0) / 3600.0))
    edges = t0 + 3600.0 * np.arange(nb + 1)
    return np.histogram(ev, bins=edges)[0].astype(float), len(ev)


def fano_at(counts_h: np.ndarray, wh: int) -> float:
    if wh == 1:
        x = counts_h
    else:
        n = (len(counts_h) // wh) * wh
        x = counts_h[:n].reshape(-1, wh).sum(axis=1)
    return float(x.var(ddof=1) / x.mean())


def main():
    rng = np.random.default_rng(20260908)
    res = {}
    for label, thr in (("S4_BTC_1M", 0.005), ("S4b_BTC_1M_p999", None)):
        if thr is None:
            c = sqlite3.connect("file:" + str(db.PROD_DB) + "?mode=ro", uri=True)
            b = pd.read_sql("select open_time, close from btc_1m order by open_time", c)
            c.close()
            ts = b.open_time.values.astype(float) / 1000.0
            px = b.close.values.astype(float)
            r = np.zeros(len(px)); r[1:] = np.log(px[1:] / px[:-1])
            ok = np.zeros(len(px), dtype=bool); ok[1:] = (ts[1:] - ts[:-1]) == 60.0
            thr = float(np.percentile(np.abs(r)[ok], 99.9))
        ch, n_ev = hourly_counts_btc(thr)
        entry = {"threshold": thr, "n_events": int(n_ev), "obs": {}, "null_N3": {}}
        for wh in BIN_WIDTHS_H:
            entry["obs"][f"{wh}h"] = round(fano_at(ch, wh), 4)

        s = pd.Series(ch)
        for W in SMOOTH_W:
            lam = s.rolling(W, center=True, min_periods=max(2, W // 4)).mean().values
            lam = np.nan_to_num(lam, nan=float(np.nanmean(lam)))
            lam = np.clip(lam, 1e-9, None)
            sims = {f"{wh}h": [] for wh in BIN_WIDTHS_H}
            for _ in range(N_REP):
                sim = rng.poisson(lam).astype(float)
                for wh in BIN_WIDTHS_H:
                    sims[f"{wh}h"].append(fano_at(sim, wh))
            entry["null_N3"][f"smooth_{W}h"] = {
                k: dict(mean=round(float(np.mean(v)), 4),
                        p99=round(float(np.percentile(v, 99)), 4),
                        obs_over_null_mean=round(entry["obs"][k] / float(np.mean(v)), 4))
                for k, v in sims.items()}

        # ACF log-log slope over lags 1..24
        x = ch - ch.mean()
        d = np.dot(x, x)
        a = np.array([np.dot(x[:-k], x[k:]) / d for k in range(1, 25)])
        lags = np.arange(1, 25.0)
        m = a > 0
        slope, intercept = np.polyfit(np.log(lags[m]), np.log(a[m]), 1)
        pred = np.exp(intercept) * lags[m] ** slope
        ss_res = float(((a[m] - pred) ** 2).sum())
        ss_tot = float(((a[m] - a[m].mean()) ** 2).sum())
        entry["acf_powerlaw"] = dict(slope=round(float(slope), 4),
                                     r2=round(1 - ss_res / ss_tot, 4),
                                     acf_lag1=round(float(a[0]), 4),
                                     acf_lag24=round(float(a[23]), 4))
        # exponential fit for comparison
        slope_e, icept_e = np.polyfit(lags[m], np.log(a[m]), 1)
        pred_e = np.exp(icept_e) * np.exp(slope_e * lags[m])
        ss_res_e = float(((a[m] - pred_e) ** 2).sum())
        entry["acf_exponential"] = dict(rate_per_hour=round(float(slope_e), 5),
                                        r2=round(1 - ss_res_e / ss_tot, 4),
                                        halflife_h=round(float(np.log(2) / -slope_e), 2)
                                        if slope_e < 0 else None)
        res[label] = entry
        print(f"== {label} thr={thr:.6f} n={n_ev}")
        print("   obs F:", entry["obs"])
        for W, v in entry["null_N3"].items():
            print(f"   N3[{W}]:", {k: (vv['mean'], vv['p99'], vv['obs_over_null_mean'])
                                   for k, vv in v.items()})
        print("   acf power-law", entry["acf_powerlaw"], " exp", entry["acf_exponential"])

    (OUT / "addendum_slow_rate.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("wrote", OUT / "addendum_slow_rate.json")


if __name__ == "__main__":
    main()
