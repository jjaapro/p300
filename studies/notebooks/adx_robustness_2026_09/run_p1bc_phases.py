"""P1(b)(c) — live-semantics machine across the 24 day boundaries; MTM drawdown sizing; phase ensembles."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import adx_lib as al

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex, bv = al.ex, al.bv
DD_BUDGET_PCT = 25.0
ENSEMBLES = {"k2_0_12": (0, 12), "k3_0_8_16": (0, 8, 16), "k24_all": tuple(range(24))}


def main() -> None:
    path = ex.load_path("btc_1m")
    series, rows = {}, []
    for ph in range(24):
        tr = al.live_walk(al.daily_candles(ph), path)
        r = al.mtm_returns(tr, path)
        series[ph] = r
        st = al.curve_stats(r); ls = al.ledger_stats(tr)
        rows.append(dict(phase_hour=ph, n=ls["n"], mean_net_pct=ls["mean_net_pct"], worst_pct=ls["worst_pct"],
                         median_init_stop_pct=ls["median_init_stop_pct"], **st))
        print(f"phase {ph:2d}: n={ls['n']} mean {ls['mean_net_pct']:+.2f}% CAGR {st['cagr_pct']:.1f}% MTM maxDD {st['mtm_maxdd_pct']:.1f}% Sharpe {st['sharpe']:.2f} MAR {st['mar']:.2f}")
    ph_df = pd.DataFrame(rows)
    ph_df.to_csv(al.RESULTS / "p1b_phases.csv", index=False)
    out = {"env": ex.env_info(), "phases": ph_df.to_dict("records"),
           "phase_ranges": {k: [float(ph_df[k].min()), float(ph_df[k].median()), float(ph_df[k].max())] for k in ("cagr_pct", "mtm_maxdd_pct", "sharpe", "mar", "n")}}
    # ── (b) sizing off the MTM drawdown ────────────────────────────────────
    worst_dd, med_dd = float(ph_df.mtm_maxdd_pct.min()), float(ph_df.mtm_maxdd_pct.median())
    med_stop = float(ph_df.median_init_stop_pct.median())
    shipped_nx = min(2.0 / med_stop, 3.0)                       # RISK_PCT 2 % over the initial stop, 3x cap
    out["sizing"] = dict(dd_budget_pct=DD_BUDGET_PCT, worst_phase_mtm_maxdd_pct=worst_dd, median_phase_mtm_maxdd_pct=med_dd,
                         median_initial_stop_pct=med_stop, shipped_notional_x_typical=shipped_nx,
                         shipped_account_maxdd_worst_phase_pct=shipped_nx * worst_dd, shipped_account_maxdd_live_phase_pct=shipped_nx * float(ph_df.loc[ph_df.phase_hour == 0, "mtm_maxdd_pct"].iloc[0]),
                         notional_x_for_budget_worst_phase=DD_BUDGET_PCT / abs(worst_dd), notional_x_for_budget_median_phase=DD_BUDGET_PCT / abs(med_dd),
                         risk_pct_for_budget_worst_phase=DD_BUDGET_PCT / abs(worst_dd) * med_stop)
    print("sizing:", out["sizing"])
    # ── (c) ensembles ──────────────────────────────────────────────────────
    idx = sorted(set().union(*[set(s.index) for s in series.values()]))
    M = pd.DataFrame({ph: s.reindex(idx).fillna(0.0) for ph, s in series.items()})
    base = M[0]
    ens = {}
    for name, phases in ENSEMBLES.items():
        r = M[list(phases)].mean(axis=1)
        st = al.curve_stats(r)
        boot_sh = bv.paired_block_boot(r.to_numpy(), base.to_numpy(), bv.sharpe_of, block=30)
        boot_mar = bv.paired_block_boot(r.to_numpy(), base.to_numpy(), lambda x: (bv.cagr_of(x) / abs(bv.maxdd_of(x))) if bv.maxdd_of(x) < 0 else float("inf"), block=30)
        ens[name] = dict(phases=list(phases), **st, delta_sharpe_vs_phase0=boot_sh, delta_mar_vs_phase0=boot_mar,
                         rule=dict(maxdd_5pp_better_than_median=bool(st["mtm_maxdd_pct"] >= med_dd + 5.0),
                                   sharpe_ge_median=bool(st["sharpe"] >= float(ph_df.sharpe.median())), k_le_3=len(phases) <= 3))
        ens[name]["verdict"] = "BUILD-CANDIDATE" if all(ens[name]["rule"].values()) else "NO CHANGE"
        print(name, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in ens[name].items() if k not in ("delta_sharpe_vs_phase0", "delta_mar_vs_phase0")},
              "dSharpe", boot_sh, "dMAR", boot_mar)
    out["ensembles"] = ens
    M.to_csv(al.RESULTS / "p1c_phase_daily_returns.csv")
    al.jdump(out, "p1bc_phases.json")


if __name__ == "__main__":
    main()
