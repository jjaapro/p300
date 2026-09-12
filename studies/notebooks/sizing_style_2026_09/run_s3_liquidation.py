"""S3 — minute-by-minute liquidation walk per bot and pooled, under P0 and P1; then the pre-registered decision."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import sizing_lib as sl

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

ex = sl.ex


def to_trades(df: pd.DataFrame) -> list[dict]:
    cols = ["i_fill", "i_exit", "d", "notional_usd", "fill_spot", "pnl_usd", "mae", "asset"]
    return [dict(zip(cols, row)) for row in df[cols].itertuples(index=False)]


def main() -> None:
    df = pd.read_csv(sl.RESULTS / "s1_trades.csv")
    s1 = sl.jload("s1_exit_policies.json")
    out = {"env": ex.env_info(), "per_bot": {}, "pooled": {}, "decision": {}}
    for policy in sl.POLICIES:
        out["per_bot"][policy] = {}
        pooled = {}
        for sleeve in ex.WALK_SLEEVES:
            t = df[(df.sleeve == sleeve) & (df.policy == policy)]
            trades = to_trades(t)
            asset = trades[0]["asset"]
            res = sl.liquidation_walk({asset: trades}, sl.CAPITAL)
            out["per_bot"][policy][sleeve] = res
            pooled.setdefault(asset, []).extend(trades)
            print(policy, sleeve, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in res.items()})
        out["pooled"][policy] = sl.liquidation_walk(pooled, 5 * sl.CAPITAL)
        print(policy, "POOLED $50k", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out["pooled"][policy].items()})
    # ── decision rule 1 (README): vs P0, both halves higher, MAR higher, MTM maxDD not worse by > 5 pp,
    #    zero liquidation episodes and min distance >= 2 x the policy's worst in-hold adverse excursion
    for sleeve in ex.WALK_SLEEVES:
        p0 = s1["sleeves"][sleeve]["P0_shipped"]
        out["decision"][sleeve] = {}
        for policy in sl.POLICIES[1:]:
            r = s1["sleeves"][sleeve][policy]
            w = out["per_bot"][policy][sleeve]
            clauses = dict(halves_higher=bool(r["first_half_r"] > p0["first_half_r"] and r["second_half_r"] > p0["second_half_r"]),
                           mar_higher=bool(r["mar"] > p0["mar"]),
                           maxdd_ok=bool(r["maxdd_pct"] >= p0["maxdd_pct"] - 5.0),
                           no_liquidation=bool(w["episodes"] == 0),
                           safety_2x=bool(w["min_distance"] >= 2 * w["worst_mae"]))
            verdict = "BUILD-CANDIDATE" if all(clauses.values()) else "KEEP THE STOP"
            out["decision"][sleeve][policy] = dict(**clauses, verdict=verdict, min_distance=w["min_distance"], worst_mae=w["worst_mae"])
            print(f"DECISION {sleeve} {policy}: {clauses} -> {verdict}")
    # ADX: same clauses on the harness curves (no liquidation walk: 0.2-0.5x notional)
    a0 = s1["adx"]["P0_shipped_T2"]
    out["decision"]["ADX"] = {}
    for name in ("P1_signal_only", "P2_catastrophe_30"):
        a = s1["adx"][name]
        clauses = dict(halves_higher=bool(a["first_half_pct"] > a0["first_half_pct"] and a["second_half_pct"] > a0["second_half_pct"]),
                       mar_higher=bool(a["mar"] > a0["mar"]), maxdd_ok=bool(a["mtm_maxdd_pct"] >= a0["mtm_maxdd_pct"] - 5.0))
        out["decision"]["ADX"][name] = dict(**clauses, verdict="BUILD-CANDIDATE" if all(clauses.values()) else "KEEP THE STOP")
        print(f"DECISION ADX {name}: {clauses} -> {out['decision']['ADX'][name]['verdict']}")
    sl.jdump(out, "s3_liquidation.json")


if __name__ == "__main__":
    main()
