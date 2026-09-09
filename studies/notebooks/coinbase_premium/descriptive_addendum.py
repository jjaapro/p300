"""S-Coinbase-premium — DESCRIPTIVE ADDENDUM.

Written and run AFTER premium_study.py had already fixed every decision number.
Nothing here feeds a clause; nothing here can change the verdict. It exists so
the discussion in findings.md can say honestly *why* the frozen rule fails, and
so the "does the signal decay?" question is answered with numbers instead of
adjectives.

Adds, per asset:
  1. unconditional forward-48h return (the drift baseline the decile table sits on)
  2. the z-decile / z-bucket forward-return tables split pre- vs post-ETF
  3. the z <-> fwd48 correlation split pre- vs post-ETF
  4. calendar 2024+ and 2025+ per-trade R with bootstrap CIs, both assets
  5. ETH hourly-bar completeness (minutes per aggregated hour)
  6. what the frozen 2xATR stop costs: same fires, no stop

Read-only on prod.db. Re-runnable.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from premium_study import (COST_RT, ETF_CUTOFF, HOLD_BARS, OUT, SL_ATR_MULT,
                           bootstrap_mean, build_features, connect, load_btc,
                           load_eth, pearson, simulate, spearman)

pd.set_option("display.width", 200)


def decile_table(d: pd.DataFrame, label: str, asset: str) -> pd.DataFrame:
    if len(d) < 100:
        return pd.DataFrame()
    d = d.copy()
    d["decile"] = pd.qcut(d["z"], 10, labels=False, duplicates="drop")
    g = d.groupby("decile")
    return pd.DataFrame({
        "asset": asset, "era": label,
        "n": g.size(),
        "z_mid": g["z"].median(),
        "mean_fwd48_bp": g["fwd48"].mean() * 1e4,
        "win_rate": g["fwd48"].apply(lambda s: float((s > 0).mean())),
    }).reset_index()


def bucket_table(d: pd.DataFrame, label: str, asset: str) -> pd.DataFrame:
    edges = [-np.inf, -2, -1, 0, 1, 2, np.inf]
    labels = ["<-2", "-2..-1", "-1..0", "0..1", "1..2", ">=2"]
    d = d.copy()
    d["bucket"] = pd.cut(d["z"], edges, labels=labels, right=False)
    g = d.groupby("bucket", observed=False)
    return pd.DataFrame({
        "asset": asset, "era": label,
        "n": g.size(),
        "mean_fwd48_bp": g["fwd48"].mean() * 1e4,
        "excess_vs_uncond_bp": g["fwd48"].mean() * 1e4 - d["fwd48"].mean() * 1e4,
        "win_rate": g["fwd48"].apply(lambda s: float((s > 0).mean()) if len(s) else np.nan),
    }).reset_index()


def main() -> None:
    con = connect()
    raw = {"BTC": load_btc(con), "ETH": load_eth(con)}
    con.close()

    out: dict = {}
    dec_rows, buck_rows = [], []

    for asset, df0 in raw.items():
        n_min = df0["n_min"].to_numpy() if "n_min" in df0 else None
        df = build_features(df0.iloc[:-1].copy())
        cutoff = ETF_CUTOFF[asset]
        a: dict = {"etf_cutoff": cutoff}

        ok = np.isfinite(df["z"]) & np.isfinite(df["fwd48"])
        sub = df.loc[ok, ["date", "z", "fwd48", "atr", "bn_close"]].copy()
        pre = sub[sub["date"] < cutoff]
        post = sub[sub["date"] >= cutoff]

        # 1. unconditional drift baseline
        a["uncond_fwd48_bp"] = {
            "all": float(sub["fwd48"].mean() * 1e4),
            "pre_etf": float(pre["fwd48"].mean() * 1e4),
            "post_etf": float(post["fwd48"].mean() * 1e4),
        }

        # 2/3. tables + correlation by era
        for label, d in (("all", sub), ("pre_etf", pre), ("post_etf", post)):
            dec_rows.append(decile_table(d, label, asset))
            buck_rows.append(bucket_table(d, label, asset))
        a["corr_by_era"] = {
            label: {"n": int(len(d)),
                    "pearson": pearson(d["z"], d["fwd48"]),
                    "spearman": spearman(d["z"], d["fwd48"])}
            for label, d in (("all", sub), ("pre_etf", pre), ("post_etf", post))
        }
        # conditional-on-fire raw forward return, no stop, no cost
        fired = sub[sub["z"] >= 2.0]
        a["raw_fwd48_when_z_ge_2_bp"] = {
            "all": {"n": int(len(fired)), "mean_bp": float(fired["fwd48"].mean() * 1e4)},
            "pre_etf": {"n": int((fired["date"] < cutoff).sum()),
                        "mean_bp": float(fired.loc[fired["date"] < cutoff, "fwd48"].mean() * 1e4)},
            "post_etf": {"n": int((fired["date"] >= cutoff).sum()),
                         "mean_bp": float(fired.loc[fired["date"] >= cutoff, "fwd48"].mean() * 1e4)},
        }

        # 4. modern-era per-trade R, both assets, calendar cuts
        tr = simulate(df)
        a["trades_calendar_cuts"] = {}
        for cut in ("2024-01-01", "2025-01-01", "2026-01-01"):
            t = tr[tr["entry_dt"] >= cut]
            b = bootstrap_mean(t["R"]) if len(t) >= 2 else None
            a["trades_calendar_cuts"][cut + "+"] = {
                "n": int(len(t)),
                "mean_R": float(t["R"].mean()) if len(t) else None,
                "total_R": float(t["R"].sum()) if len(t) else None,
                "ci95": [b["ci95_lo"], b["ci95_hi"]] if b else None,
                "mean_net_ret_pct": float(t["net_ret"].mean() * 100) if len(t) else None,
            }

        # 6. what the frozen stop costs: same fires, exit only at bar i+48
        c = df["bn_close"].to_numpy(float)
        idx = {int(t): i for i, t in enumerate(df["ts"].to_numpy())}
        no_stop = []
        for _, row in tr.iterrows():
            i = idx[int(row["entry_ts"])]
            if i + HOLD_BARS < len(c):
                gross = (c[i + HOLD_BARS] - c[i]) / c[i]
                no_stop.append((gross - COST_RT) / row["risk_frac"])
        a["no_stop_same_fires"] = {
            "n": len(no_stop),
            "mean_R": float(np.mean(no_stop)) if no_stop else None,
            "total_R": float(np.sum(no_stop)) if no_stop else None,
            "with_stop_mean_R": float(tr["R"].mean()),
            "stop_hit_rate": float((tr["reason"] == "SL").mean()),
        }
        b = bootstrap_mean(no_stop) if len(no_stop) >= 2 else None
        a["no_stop_same_fires"]["ci95"] = [b["ci95_lo"], b["ci95_hi"]] if b else None

        # 5. ETH aggregation completeness
        if n_min is not None:
            a["hourly_completeness"] = {
                "hours": int(len(n_min)),
                "full_60_min": int((n_min == 60).sum()),
                "lt_60_min": int((n_min < 60).sum()),
                "lt_30_min": int((n_min < 30).sum()),
                "min_minutes": int(n_min.min()),
            }

        out[asset] = a

    pd.concat([d for d in dec_rows if len(d)]).to_csv(
        OUT / "z_decile_by_era.csv", index=False)
    pd.concat([d for d in buck_rows if len(d)]).to_csv(
        OUT / "z_bucket_by_era.csv", index=False)
    (OUT / "descriptive_addendum.json").write_text(json.dumps(out, indent=2, default=float))

    print("=" * 78)
    print("  DESCRIPTIVE ADDENDUM (post-primary, non-decision-bearing)")
    print("=" * 78)
    for asset, a in out.items():
        print(f"\n  {asset}  (ETF cutoff {a['etf_cutoff']})")
        u = a["uncond_fwd48_bp"]
        print(f"    unconditional fwd48h: all {u['all']:+.1f} bp | pre {u['pre_etf']:+.1f}"
              f" | post {u['post_etf']:+.1f}")
        r = a["raw_fwd48_when_z_ge_2_bp"]
        print(f"    raw fwd48h when z>=2 (no stop/cost): all {r['all']['mean_bp']:+.1f} bp"
              f" (n={r['all']['n']}) | pre {r['pre_etf']['mean_bp']:+.1f} (n={r['pre_etf']['n']})"
              f" | post {r['post_etf']['mean_bp']:+.1f} (n={r['post_etf']['n']})")
        for k, v in a["corr_by_era"].items():
            print(f"    corr {k:9s} n={v['n']:>6,}  pearson {v['pearson']:+.4f}"
                  f"  spearman {v['spearman']:+.4f}")
        for k, v in a["trades_calendar_cuts"].items():
            if v["n"]:
                print(f"    trades {k}: n={v['n']:>3}  mean R={v['mean_R']:+.4f}"
                      f"  CI95 [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]"
                      f"  total R={v['total_R']:+.1f}")
        ns = a["no_stop_same_fires"]
        print(f"    same fires, NO stop: mean R={ns['mean_R']:+.4f} (with stop {ns['with_stop_mean_R']:+.4f});"
              f" stop hit {ns['stop_hit_rate']*100:.0f}% of trades")
        if "hourly_completeness" in a:
            h = a["hourly_completeness"]
            print(f"    hourly completeness: {h['full_60_min']:,}/{h['hours']:,} full 60-min;"
                  f" {h['lt_30_min']} hours under 30 min (min {h['min_minutes']})")
    print(f"\n  artefacts -> {OUT}")


if __name__ == "__main__":
    main()
