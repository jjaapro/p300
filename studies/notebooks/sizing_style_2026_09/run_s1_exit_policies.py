"""S1 — exit policy on identical entries: shipped stop vs no-stop (time / target only) vs catastrophe stop."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import sizing_lib as sl

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex, bv = sl.ex, sl.bv


def walk_sleeves() -> tuple[dict, pd.DataFrame]:
    out, frames = {}, []
    for sleeve in ex.WALK_SLEEVES:
        events = ex.load_events(sleeve)
        path = ex.path_for(events[0].asset, "1m")
        per_policy = {p: [] for p in sl.POLICIES}
        for ev in events:
            cx = ex.context(ev, path)
            if cx is None:
                continue
            for p in sl.POLICIES:
                per_policy[p].append(sl.run_policy(sleeve, ev, path, cx, p))
        res = {p: sl.policy_summary(tr, path) for p, tr in per_policy.items()}
        base = np.array([t["r"] for t in per_policy["P0_shipped"]])
        ts = np.array([t["ts"] for t in per_policy["P0_shipped"]])
        for p in sl.POLICIES[1:]:
            diff = np.array([t["r"] for t in per_policy[p]]) - base
            b = ex.block_boot_mean(diff, ex.day_groups(ts))
            res[p]["delta_r_vs_P0"] = dict(mean=b["mean"], ci90=b["ci90"])
        out[sleeve] = res
        for p, tr in per_policy.items():
            frames.append(pd.DataFrame(tr))
        print(f"\n{sleeve} (cost {sl.cost_bp(sleeve):.1f} bp, sizing {sl.SIZING[sleeve]})")
        tab = pd.DataFrame({p: dict(n=r["n"], mean_r=r["mean_r"], win=r["win"], worst_r=r["worst_r"], h1_r=r["first_half_r"], h2_r=r["second_half_r"],
                                    mean_pct=r["mean_pnl_pct"], mtm_maxdd_pct=r["maxdd_pct"], pct_per_yr=r["pct_per_year"], mar=r["mar"],
                                    notional_x=r["mean_notional_x"], d_r=r.get("delta_r_vs_P0", {}).get("mean", 0.0))
                            for p, r in res.items()}).T
        print(tab.round(3).to_string())
    return out, pd.concat(frames)


def adx_policies() -> dict:
    hz = bv.import_adx_harness()
    candles = bv.df_to_candles(bv.load_daily_btc(0))
    closes = [c["close"] for c in candles]
    e150 = hz.ema(closes, 150)

    def short_gate(ctx, _e=e150):
        return ctx["new_dir"] != "short" or ctx["close"] < _e[ctx["i"]]

    cfgs = {"P0_shipped_T2": dict(sl_pct=10.0, exit_mode="adx_or_atr", atr_mult=4.0, entry_gate=short_gate),
            "P1_signal_only": dict(sl_pct=0.0, exit_mode="adx", entry_gate=short_gate),
            "P2_catastrophe_30": dict(sl_pct=30.0, exit_mode="adx", entry_gate=short_gate)}
    out = {}
    for name, kw in cfgs.items():
        m = hz.run(candles, "2018-01-01", **kw)
        tr = [t for t in m["trades"] if not t.get("still_open")]
        r = bv.ledger_to_daily_returns(tr, candles, cost_bp_rt=15.0)
        eq = np.cumprod(1 + r); peak = np.maximum.accumulate(eq); mdd = float(((eq - peak) / peak).min() * 100)
        cagr = bv.cagr_of(r) * 100
        net = np.array([t["net_pct"] for t in tr]); ets = np.array([int(pd.Timestamp(t["entry_dt"], tz="UTC").timestamp()) for t in tr])
        h2 = ex.halves(ets)
        out[name] = dict(n=len(tr), mean_net_pct=float(net.mean()), worst_pct=float(net.min()), win=float((net > 0).mean()),
                         first_half_pct=float(net[~h2].mean()), second_half_pct=float(net[h2].mean()),
                         cagr_pct=float(cagr), mtm_maxdd_pct=mdd, mar=float(cagr / abs(mdd)) if mdd < 0 else float("inf"),
                         sharpe=float(bv.sharpe_of(r)), reasons=pd.Series([t["reason"] for t in tr]).value_counts().to_dict())
        print("ADX", name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out[name].items()})
    return out


def main() -> None:
    out = {"env": ex.env_info(), "policies": sl.POLICIES, "cat_mult": sl.CAT_MULT, "capital": sl.CAPITAL}
    out["sleeves"], df = walk_sleeves()
    df.to_csv(sl.RESULTS / "s1_trades.csv", index=False)
    out["adx"] = adx_policies()
    sl.jdump(out, "s1_exit_policies.json")


if __name__ == "__main__":
    main()
