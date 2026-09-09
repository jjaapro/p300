#!/usr/bin/env python3
"""A1 anchor-allocator study — Step 3: run the variants, write results, print
the pre-registered verdict.

Writes  results/summary.csv        one row per variant (+ btc_bh reference)
        results/per_year.csv       calendar-year returns per variant
        results/sleeve_contrib.csv Σ weight × unit return per sleeve per variant
        results/equity_<variant>.csv
        results/verdict.json       the three clauses with their measured numbers
        results/bootstrap_delta.csv paired block-bootstrap CI of ΔSharpe (diagnostic)

Run from the repo root:  venv\\Scripts\\python studies/notebooks/anchor_allocator_study/run.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):          # plain console only; ipykernel streams lack it
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from studies.notebooks.anchor_allocator_study import simulator as sim   # noqa: E402

RESULTS = HERE / "results"
PANEL = RESULTS / "daily_panel.csv"
POST_ETF = "2024-01-11"

# Pre-registered thresholds (README "Verdict procedure")
CONFIRM_SHARPE = 0.20
CONFIRM_MDD_PP = 3.0
KILL_SHARPE = 0.10
KILL_MDD_PP = 5.0
KILL_NOGOLD_SHARPE = 0.10


def summarize(res: dict, group: str) -> dict:
    d, r = res["dates"], res["daily_ret"]
    m = sim.metrics(r)
    m22 = sim.slice_metrics(d, r, "2022-01-01", "2022-12-31")
    mpe = sim.slice_metrics(d, r, POST_ETF)
    n = len(r)
    return {
        "variant": res["variant"].name, "group": group,
        "sharpe": m["sharpe"], "cagr": m["ann_ret"], "mdd": m["mdd"], "calmar": m["calmar"],
        "ret_2022": m22["total_ret"], "sharpe_post_etf": mpe["sharpe"],
        "mean_idle_pct": float(res["idle"].mean() * 100),
        "mean_anchor_pct": float((res["w_gold"] + res["w_ema"] + res["w_cash"]).mean() * 100),
        "mean_gross": float(res["gross"].mean()),
        "ann_vol": m["ann_vol"], "total_ret": m["total_ret"],
        "sharpe_2022": m22["sharpe"], "mdd_2022": m22["mdd"],
        "cagr_post_etf": mpe["ann_ret"], "mdd_post_etf": mpe["mdd"],
        "mean_tactical_pct": float(res["w_tactical"].mean() * 100),
        "mean_routed_pct": float(res["routed"].mean() * 100),
        "mean_gold_pct": float(res["w_gold"].mean() * 100),
        "mean_ema_pct": float(res["w_ema"].mean() * 100),
        "mean_cash_pct": float(res["w_cash"].mean() * 100),
        "mean_net_btc": float(res["net_btc"].mean()), "max_net_btc": float(res["net_btc"].max()),
        "max_gross": float(res["gross"].max()),
        "days_net_cap_bound": int(res["net_bound"].sum()),
        "days_gross_cap_bound": int(res["gross_bound"].sum()),
        "mean_freed_by_net_cap_pct": float(res["freed_net"].mean() * 100),
        "turnover_cost_pa_pct": float(res["turnover_cost"].sum() / n * 365 * 100),
        "n_days": n,
    }


def paired_block_bootstrap(ra: np.ndarray, rb: np.ndarray, block: int = 20, n_iter: int = 2000,
                           seed: int = 42) -> dict:
    """Paired circular-block bootstrap of ΔSharpe = Sharpe(a) − Sharpe(b) using
    the predecessor's Sharpe definition on identical resampled day sets.
    Diagnostic only — not part of the pre-registered verdict."""
    from studies.lib.validation.bootstrap import circular_block_indices
    T = len(ra)
    rng = np.random.default_rng(seed)
    out = np.empty(n_iter)
    for k in range(n_iter):
        idx = circular_block_indices(T, T, block, rng)
        out[k] = sim.metrics(ra[idx])["sharpe"] - sim.metrics(rb[idx])["sharpe"]
    return dict(point=sim.metrics(ra)["sharpe"] - sim.metrics(rb)["sharpe"],
                p05=float(np.quantile(out, 0.05)), p50=float(np.quantile(out, 0.50)),
                p95=float(np.quantile(out, 0.95)), p_gt_0=float((out > 0).mean()),
                p_ge_010=float((out >= KILL_SHARPE).mean()), p_ge_020=float((out >= CONFIRM_SHARPE).mean()),
                block=block, n_iter=n_iter)


def main() -> int:
    panel = sim.load_panel(PANEL)
    print(f"panel {len(panel)} days {panel.index[0]} → {panel.index[-1]}")

    runs: dict[str, dict] = {}
    rows = []
    for v in sim.CORE_VARIANTS + sim.SENSITIVITY_VARIANTS + sim.DIAGNOSTIC_VARIANTS:
        res = sim.simulate(panel, v)
        runs[v.name] = res
        rows.append(summarize(res, v.group))
        eq = pd.DataFrame({
            "date": res["dates"], "nav": res["nav"], "daily_ret": res["daily_ret"],
            "w_gold": res["w_gold"], "w_ema": res["w_ema"], "w_cash": res["w_cash"],
            "w_tactical": res["w_tactical"], "idle": res["idle"], "routed": res["routed"],
            "gross": res["gross"], "net_btc": res["net_btc"], "n_active": res["n_active"],
            "turnover_cost": res["turnover_cost"],
        })
        eq.to_csv(RESULTS / f"equity_{v.name}.csv", index=False, float_format="%.8g")

    # BTC buy-and-hold reference row
    btc = panel["btc_bh"].to_numpy()
    dates = list(panel.index)
    mb, mb22, mbpe = sim.metrics(btc), sim.slice_metrics(dates, btc, "2022-01-01", "2022-12-31"), \
        sim.slice_metrics(dates, btc, POST_ETF)
    rows.append({"variant": "btc_bh (reference)", "group": "reference", "sharpe": mb["sharpe"],
                 "cagr": mb["ann_ret"], "mdd": mb["mdd"], "calmar": mb["calmar"], "ret_2022": mb22["total_ret"],
                 "sharpe_post_etf": mbpe["sharpe"], "ann_vol": mb["ann_vol"], "total_ret": mb["total_ret"],
                 "sharpe_2022": mb22["sharpe"], "mdd_2022": mb22["mdd"], "cagr_post_etf": mbpe["ann_ret"],
                 "mdd_post_etf": mbpe["mdd"], "n_days": len(btc)})

    df = pd.DataFrame(rows)
    base = df[df.variant == "baseline"].iloc[0]
    df["d_sharpe_vs_baseline"] = df["sharpe"] - base["sharpe"]
    df["d_mdd_pp_vs_baseline"] = (df["mdd"] - base["mdd"]) * 100
    df["d_sharpe_post_etf_vs_baseline"] = df["sharpe_post_etf"] - base["sharpe_post_etf"]
    df.to_csv(RESULTS / "summary.csv", index=False, float_format="%.6g")

    # per-year returns
    py = {}
    for name, res in runs.items():
        py[name] = sim.per_year_returns(res["dates"], res["daily_ret"])
    py["btc_bh (reference)"] = sim.per_year_returns(dates, btc)
    pd.DataFrame(py).T.rename_axis("variant").to_csv(RESULTS / "per_year.csv", float_format="%.5g")

    # per-sleeve contributions
    sc = []
    for name, res in runs.items():
        for s in sim.TACTICAL + sim.ANCHOR:
            sc.append({"variant": name, "sleeve": s, "sum_contrib_pct": res["sleeve_contrib"][s] * 100,
                       "avg_weight_when_held_pct": res["sleeve_avg_weight"][s] * 100,
                       "days_held": res["sleeve_days"][s]})
    pd.DataFrame(sc).to_csv(RESULTS / "sleeve_contrib.csv", index=False, float_format="%.5g")

    # ── verdict (README "Verdict procedure", verbatim) ──
    S = {r["variant"]: r for r in rows}
    d_sharpe = S["overflow"]["sharpe"] - S["baseline"]["sharpe"]
    d_mdd_pp = (S["overflow"]["mdd"] - S["baseline"]["mdd"]) * 100
    d_sharpe_nogold = S["overflow_no_gold"]["sharpe"] - S["baseline"]["sharpe"]
    d_sharpe_cash = S["overflow_cash_only"]["sharpe"] - S["baseline"]["sharpe"]
    d_sharpe_bgold = S["baseline_gold"]["sharpe"] - S["baseline"]["sharpe"]
    clauses = {
        "KILL: Sharpe uplift < +0.10": {"measured": d_sharpe, "threshold": KILL_SHARPE, "fires": d_sharpe < KILL_SHARPE},
        "KILL: MDD worse by > 5 pp": {"measured": d_mdd_pp, "threshold": KILL_MDD_PP, "fires": d_mdd_pp > KILL_MDD_PP},
        "KILL: uplift disappears without gold (ΔSharpe_nogold < +0.10)": {
            "measured": d_sharpe_nogold, "threshold": KILL_NOGOLD_SHARPE, "fires": d_sharpe_nogold < KILL_NOGOLD_SHARPE},
        "CONFIRM: Sharpe uplift ≥ +0.20": {"measured": d_sharpe, "threshold": CONFIRM_SHARPE, "fires": d_sharpe >= CONFIRM_SHARPE},
        "CONFIRM: MDD ≤ baseline + 3 pp": {"measured": d_mdd_pp, "threshold": CONFIRM_MDD_PP, "fires": d_mdd_pp <= CONFIRM_MDD_PP},
    }
    killed = any(c["fires"] for k, c in clauses.items() if k.startswith("KILL"))
    confirmed = (not killed) and all(c["fires"] for k, c in clauses.items() if k.startswith("CONFIRM"))
    verdict = "KILL" if killed else ("CONFIRMED" if confirmed else "WEAK")

    # ── diagnostic: paired block bootstrap of ΔSharpe (not pre-registered) ──
    boot_rows = []
    for a, b in (("overflow", "baseline"), ("overflow_no_gold", "baseline"), ("overflow_cash_only", "baseline"),
                 ("baseline_gold", "baseline"), ("overflow", "baseline_gold")):
        bb = paired_block_bootstrap(runs[a]["daily_ret"], runs[b]["daily_ret"])
        boot_rows.append({"a": a, "b": b, **bb})
    pd.DataFrame(boot_rows).to_csv(RESULTS / "bootstrap_delta.csv", index=False, float_format="%.4g")

    (RESULTS / "verdict.json").write_text(json.dumps({
        "verdict": verdict,
        "d_sharpe": d_sharpe, "d_mdd_pp": d_mdd_pp, "d_sharpe_nogold": d_sharpe_nogold,
        "d_sharpe_cash_only": d_sharpe_cash, "d_sharpe_baseline_gold": d_sharpe_bgold,
        "clauses": {k: {kk: (bool(vv) if isinstance(vv, (bool, np.bool_)) else float(vv)) for kk, vv in c.items()}
                    for k, c in clauses.items()},
        "period": [dates[0], dates[-1]], "n_days": len(dates),
        "bootstrap": boot_rows,
    }, indent=2, default=float), encoding="utf-8")

    # ── print ──
    cols = ["variant", "sharpe", "cagr", "mdd", "calmar", "ret_2022", "sharpe_post_etf",
            "mean_idle_pct", "mean_anchor_pct", "mean_gross", "d_sharpe_vs_baseline", "d_mdd_pp_vs_baseline"]
    pd.set_option("display.width", 250)
    print("\n=== summary (full period %s → %s) ===" % (dates[0], dates[-1]))
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n=== per-year returns ===")
    print((pd.DataFrame(py).T * 100).to_string(float_format=lambda x: f"{x:+.1f}"))
    print("\n=== paired block-bootstrap ΔSharpe (diagnostic, block=20, 2000 iter) ===")
    print(pd.DataFrame(boot_rows)[["a", "b", "point", "p05", "p50", "p95", "p_gt_0", "p_ge_010", "p_ge_020"]]
          .to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n=== pre-registered verdict ===")
    for k, c in clauses.items():
        print(f"  {'FIRES ' if c['fires'] else 'no    '} {k}: measured {c['measured']:+.3f} vs {c['threshold']:+.2f}")
    print(f"  ΔSharpe(overflow_cash_only) = {d_sharpe_cash:+.3f}; ΔSharpe(baseline_gold) = {d_sharpe_bgold:+.3f}")
    print(f"\nVERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
