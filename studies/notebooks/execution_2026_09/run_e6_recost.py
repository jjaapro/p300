"""E6 — re-cost every sleeve under the measured execution models and apply the pre-registered rules (README §E6)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

HALF_SPREAD_BP = 0.3          # E1: Roll on 5 s 0.26 bp median when defined, Corwin-Schultz on 1 m 0.86 bp = upper bound
MAKER_T = 900                 # rule 2: best T <= 15 min; the touch-rule fill rate is ~1 at any T, so the longest allowed
FEE = ex.FEES_BP
AS = ex.AS_BP["base"]


def per_trade_costs(kind: str, drift_bp: float, stop_slip_bp: float, entry: str, tif_exit: str) -> float:
    """Round-trip cost in bp for one trade given its exit kind and the leg policies."""
    c = (FEE["perp_maker"] + AS) if entry == "maker" else (FEE["perp_taker"] + HALF_SPREAD_BP + drift_bp)
    if kind == "target":
        c += FEE["perp_maker"] if entry != "taker_all" else FEE["perp_taker"] + HALF_SPREAD_BP
    elif kind == "stop":
        c += FEE["perp_taker"] + HALF_SPREAD_BP + stop_slip_bp
    else:  # tif / late
        c += (FEE["perp_maker"] + AS) if tif_exit == "maker" else (FEE["perp_taker"] + HALF_SPREAD_BP)
    return c


def run_sleeve(sl: str, e1: dict, e4: dict) -> tuple[dict, pd.DataFrame]:
    events = ex.load_events(sl)
    path = ex.path_for(events[0].asset, "1m")
    drift = float(e1["entry_drift"][sl]["1m"]["at_60s_cost_bp"]["mean"])
    drift_ci = e1["entry_drift"][sl]["1m"]["at_60s_cost_bp"]["ci90"]
    stop_slip = float(e4["sleeves"][sl]["1m"]["slip_pathsem_bp"]["mean"])
    rows = []
    for k, ev in enumerate(events):
        cx = ex.context(ev, path)
        if cx is None:
            continue
        mt = ex.market_trade(ev, path, cx)                     # paper-convention fill, gross R
        bp_to_r = mt["fill"] / mt["risk"] / 1e4
        rec = dict(event=k, ts=ev.signal_ts, kind=mt["kind"], gross_r=mt["r"], bp_to_r=bp_to_r,
                   m0_sleeve_bp=ex.CODED_COST_BP[sl]["sleeve"], m0_research_bp=ex.CODED_COST_BP[sl]["research"],
                   m1_bp=per_trade_costs(mt["kind"], drift, stop_slip, "taker", "taker"))
        # M2/M3: maker entry, fill-weighted (cancel->skip at MAKER_T, touch and through brackets)
        for rule in ("touch", "through"):
            j, f = ex.limit_fill(path, cx["i0"], min(path.idx_ge(ev.signal_ts + MAKER_T), cx["i1"]), ev.direction, cx["paper"], rule)
            if j >= 0:
                t = ex.market_trade(ev, path, cx, fill=f, start=j)
                rec[f"m2_{rule}_filled"] = True
                rec[f"m2_{rule}_gross_r"] = t["r"]
                rec[f"m2_{rule}_bp"] = per_trade_costs(t["kind"], 0.0, stop_slip, "maker", "taker")
                rec[f"m3_{rule}_bp"] = per_trade_costs(t["kind"], 0.0, stop_slip, "maker", "maker")
                rec[f"m2_{rule}_bp_to_r"] = t["fill"] / t["risk"] / 1e4
            else:
                rec[f"m2_{rule}_filled"] = False
                rec[f"m2_{rule}_gross_r"] = 0.0
                rec[f"m2_{rule}_bp"] = 0.0
                rec[f"m3_{rule}_bp"] = 0.0
                rec[f"m2_{rule}_bp_to_r"] = 0.0
        rows.append(rec)
    df = pd.DataFrame(rows)
    ts = df.ts.to_numpy()
    out = dict(n=int(len(df)), exit_kinds=df.kind.value_counts().to_dict(),
               gross=ex.summarize_r(df.gross_r.to_numpy(), ts), drift_bp=drift, drift_ci90=drift_ci, stop_slip_bp=stop_slip)
    models = {}
    for name, bp_col, r_col, bpr_col in (("M0_sleeve", "m0_sleeve_bp", "gross_r", "bp_to_r"),
                                          ("M0_research", "m0_research_bp", "gross_r", "bp_to_r"),
                                          ("M1_measured_taker", "m1_bp", "gross_r", "bp_to_r"),
                                          ("M2_hybrid_touch", "m2_touch_bp", "m2_touch_gross_r", "m2_touch_bp_to_r"),
                                          ("M2_hybrid_through", "m2_through_bp", "m2_through_gross_r", "m2_through_bp_to_r"),
                                          ("M3_maker_touch", "m3_touch_bp", "m2_touch_gross_r", "m2_touch_bp_to_r"),
                                          ("M3_maker_through", "m3_through_bp", "m2_through_gross_r", "m2_through_bp_to_r")):
        cost_r = df[bp_col] * df[bpr_col]
        net = df[r_col] - cost_r
        s = ex.summarize_r(net.to_numpy(), ts)
        gross_mean = float(df[r_col].mean())
        models[name] = dict(mean_bp_per_trade=float(df[bp_col].mean()), mean_cost_r=float(cost_r.mean()), gross_mean_r=gross_mean,
                            net_mean_r=s["mean_r"], net_ci90=s["ci90"], cost_share_of_gross=float(cost_r.mean() / gross_mean) if gross_mean > 0 else float("inf"),
                            net_mar=s["mar"], net_first_half=s["first_half_mean"], net_second_half=s["second_half_mean"],
                            fill_rate=float(df[bp_col.replace("_bp", "_filled").replace("m3_", "m2_")].mean()) if "m2" in bp_col or "m3" in bp_col else 1.0)
    out["models"] = models
    # measured taker round trip vs the coded lump (rule 1): the drift CI is the only random term
    m1_mean = models["M1_measured_taker"]["mean_bp_per_trade"]
    fixed = m1_mean - drift
    m1_ci = [fixed + drift_ci[0], fixed + drift_ci[1]]
    coded = ex.CODED_COST_BP[sl]["sleeve"]
    out["rule1_cost_constant"] = dict(coded_sleeve_bp=coded, measured_taker_bp=m1_mean, measured_ci90=m1_ci,
                                      differs_by_gt_3bp=bool(abs(m1_mean - coded) > 3), ci_excludes_coded=bool(coded < m1_ci[0] or coded > m1_ci[1]),
                                      recommend_change=bool(abs(m1_mean - coded) > 3 and (coded < m1_ci[0] or coded > m1_ci[1])))
    # rule 4 viability
    share1 = models["M1_measured_taker"]["cost_share_of_gross"]
    share2 = models["M2_hybrid_through"]["cost_share_of_gross"]      # the conservative bracket decides
    if share1 <= 0.50:
        v = "VIABLE"
    elif share2 < 0.33:
        v = "MAKER-POLICY CANDIDATE"
    else:
        v = "RETIRE PENDING USER DECISION"
    out["rule4_viability"] = dict(m1_cost_share=share1, m2_through_cost_share=share2, m2_touch_cost_share=models["M2_hybrid_touch"]["cost_share_of_gross"], verdict=v)
    return out, df


def main() -> None:
    e1, e2, e3, e4, e5 = (ex.jload(n) for n in ("e1_market_cost.json", "e2_passive_entry.json", "e3_tp_fills.json",
                                                 "e4_stop_slippage.json", "e5_conditional_as.json"))
    out = {"env": ex.env_info(), "assumptions": dict(half_spread_bp=HALF_SPREAD_BP, maker_T_s=MAKER_T, fees_bp=FEE, as_bp=AS),
           "sleeves": {}, "decisions": {}}
    frames = []
    for sl in ex.WALK_SLEEVES:
        res, df = run_sleeve(sl, e1, e4)
        df["sleeve"] = sl
        frames.append(df)
        out["sleeves"][sl] = res
        out["decisions"][sl] = dict(rule1=res["rule1_cost_constant"], rule2_entry=e2["decision"][sl], rule3_tp=e3["decision"][sl],
                                    rule4=res["rule4_viability"])
        print(f"\n{sl}: n={res['n']} exits={res['exit_kinds']} gross {res['gross']['mean_r']:+.3f} R | drift {res['drift_bp']:+.2f} bp | stop slip {res['stop_slip_bp']:.2f} bp")
        tab = pd.DataFrame(res["models"]).T[["mean_bp_per_trade", "mean_cost_r", "net_mean_r", "cost_share_of_gross", "net_mar", "net_first_half", "net_second_half", "fill_rate"]]
        print(tab.round(3).to_string())
        print("rule1:", res["rule1_cost_constant"]); print("rule4:", res["rule4_viability"])
    pd.concat(frames).to_csv(ex.RESULTS / "e6_per_trade.csv", index=False)
    # ADX and CARRY: fee arithmetic only (no R units)
    adx = ex.load_adx()
    d_in = e1["entry_drift"]["ADX"]["1m"]
    adx_bp = dict(coded_sleeve_bp=15.0, harness_bp=10.0,
                  measured_taker_bp=2 * FEE["perp_taker"] + 2 * HALF_SPREAD_BP + d_in["at_60s_cost_bp"]["mean"] + d_in["exit_at_60s_cost_bp"]["mean"],
                  mean_trade_pct=float(np.mean([a["net_pct"] for a in adx])), trades_per_year=len(adx) / 8.7)
    adx_bp["cost_share_of_mean_trade"] = adx_bp["measured_taker_bp"] / 1e4 / (adx_bp["mean_trade_pct"] / 100)
    out["ADX"] = adx_bp
    out["CARRY"] = dict(coded_pct_per_toggle=0.24,
                        taker_all_vip0_bp=2 * FEE["spot_taker"] + 2 * FEE["perp_taker"],
                        taker_all_bnb_bp=2 * 7.5 + 2 * FEE["perp_taker_bnb"],
                        maker_perp_legs_bnb_bp=2 * 7.5 + 2 * (FEE["perp_maker_bnb"] + AS),
                        toggles_per_year=4.8, note="price P&L is zero by construction; cost = fees on 4 fills per toggle")
    print("\nADX:", adx_bp); print("CARRY:", out["CARRY"])
    ex.jdump(out, "e6_recost.json")


if __name__ == "__main__":
    main()
