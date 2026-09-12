"""P2 — S-078 exit rules on the full settlement history, net of toggles (README)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BV = ROOT / "studies" / "notebooks" / "brainstorm_validation_2026_09"
for p in (str(ROOT), str(BV)):
    if p not in sys.path:
        sys.path.insert(0, p)
import bv_lib as bv  # noqa: E402
import run_c2_carry_timing as c2  # noqa: E402  (imports matplotlib with the Agg backend; main() is guarded)

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
PPY = c2.PRINTS_PER_YEAR
EXPECT = dict(LIVE=dict(net_ann_pct=10.85, rt_per_year=4.8), ALWAYS_ON=dict(net_ann_pct=11.69))
MINHOLD_DAYS = 21
CUM30_EXIT = -0.005


def daily_frame(ts: np.ndarray, f: np.ndarray):
    days = np.array([bv.iso(t) for t in ts])
    daily = pd.DataFrame(dict(day=days, f=f)).groupby("day")["f"].sum()
    return days, daily


def hold_from_states(days: np.ndarray, daily: pd.Series, state: dict) -> np.ndarray:
    """Same print mapping as c2.s078_hold: the state at the end of the previous day governs a print."""
    day_list = list(daily.index)
    day_pos = {d: k for k, d in enumerate(day_list)}
    hold = np.zeros(len(days), bool)
    for i, d in enumerate(days):
        k = day_pos[d]
        hold[i] = state[day_list[k - 1]] if k > 0 else False
    return np.r_[hold[1:], False]


def rule_states(daily: pd.Series, kind: str) -> dict:
    avg7 = daily.rolling(7).mean()
    cum30 = daily.rolling(30).sum()
    state, held, neg, held_days = {}, False, 0, 0
    for d in daily.index:
        v = daily[d]
        neg = neg + 1 if v < 0 else 0
        if held:
            held_days += 1
        if kind == "LIVE":
            ex = neg >= 3
        elif kind == "SYMMETRIC_7D":
            ex = pd.notna(avg7[d]) and avg7[d] < 0
        elif kind == "LIVE_MINHOLD21":
            ex = neg >= 3 and held_days >= MINHOLD_DAYS
        elif kind == "CUM30D":
            ex = pd.notna(cum30[d]) and cum30[d] < CUM30_EXIT
        else:
            raise ValueError(kind)
        if held and ex:
            held, held_days = False, 0
        elif not held and pd.notna(avg7[d]) and avg7[d] > 0:
            held, held_days = True, 0
        state[d] = held
    return state


def main() -> None:
    fr = bv.fetch_binance_funding()
    ts = np.array([t // 1000 for t, _ in fr], dtype=np.int64)
    f = np.array([r for _, r in fr], dtype=float)
    n = len(f)
    s = pd.Series(f)
    q50 = s.rolling(c2.TRAIL, min_periods=c2.MIN_TRAIL).quantile(0.50).to_numpy()
    valid = np.isfinite(q50)
    first = int(np.flatnonzero(valid)[0])
    days, daily = daily_frame(ts, f)
    live = c2.s078_hold(ts, f) & valid
    live_generic = hold_from_states(days, daily, rule_states(daily, "LIVE")) & valid
    assert np.array_equal(live, live_generic), "generic LIVE state machine must equal c2.s078_hold"
    rules = {"LIVE": live, "ALWAYS_ON": valid.copy(),
             "SYMMETRIC_7D": hold_from_states(days, daily, rule_states(daily, "SYMMETRIC_7D")) & valid,
             "LIVE_MINHOLD21": hold_from_states(days, daily, rule_states(daily, "LIVE_MINHOLD21")) & valid,
             "CUM30D": hold_from_states(days, daily, rule_states(daily, "CUM30D")) & valid}
    res = {k: c2.run_rule(f, h) for k, h in rules.items()}
    span_years = (n - 1 - first) / PPY
    ts_int = ts[1:]
    yrs_int = np.array([bv.utc(t).year for t in ts_int])
    table, per_year = {}, []
    for k, r in res.items():
        pnl = r["pnl"][first:]
        table[k] = dict(share_held=float(r["hold"][first:].mean()), round_trips=int(r["entries"][first:].sum()),
                        rt_per_year=float(r["entries"][first:].sum() / span_years),
                        gross_ann_pct=float(np.where(r["hold"][first:], f[1:][first:], 0.0).sum() / span_years * 100),
                        net_ann_pct=float(pnl.sum() / span_years * 100))
        for y in range(2020, 2027):
            m = (yrs_int == y); m[:first] = False
            if m.sum() < 100:
                continue
            per_year.append(dict(rule=k, year=y, n=int(m.sum()), share_held=float(r["hold"][m].mean()), round_trips=int(r["entries"][m].sum()),
                                 net_ann_pct=float(r["pnl"][m].sum() / (m.sum() / PPY) * 100)))
    pdf = pd.DataFrame(per_year)
    pdf.to_csv(RESULTS / "p2_per_year.csv", index=False)
    out = {"env": bv.env_info(), "eval_span": [bv.iso(ts[first]), bv.iso(ts[-1])], "years": span_years, "n_prints": n, "rules": table}
    got = dict(LIVE=dict(net_ann_pct=table["LIVE"]["net_ann_pct"], rt_per_year=table["LIVE"]["rt_per_year"]), ALWAYS_ON=dict(net_ann_pct=table["ALWAYS_ON"]["net_ann_pct"]))
    out["parity"] = dict(got=got, expected=EXPECT,
                         pass_=bool(abs(got["LIVE"]["net_ann_pct"] - 10.85) < 0.02 and abs(got["ALWAYS_ON"]["net_ann_pct"] - 11.69) < 0.02 and abs(got["LIVE"]["rt_per_year"] - 4.8) < 0.1))
    print("parity vs C2:", out["parity"])
    print("rules over %s..%s (%.2f y):" % (out["eval_span"][0], out["eval_span"][1], span_years))
    print(pd.DataFrame(table).T.round(3).to_string())
    piv = pdf.pivot_table(index="rule", columns="year", values="net_ann_pct").round(2)
    print("net %/yr by year:\n", piv.to_string())
    # era split
    d_int = np.array([bv.iso(t) for t in ts_int])
    era = {}
    for k, r in res.items():
        for name, sel in (("pre_etf", d_int < bv.ETF_CUTOFF), ("post_etf", d_int >= bv.ETF_CUTOFF)):
            sel = sel.copy(); sel[:first] = False
            era.setdefault(k, {})[name] = dict(net_ann_pct=float(r["pnl"][sel].sum() / (sel.sum() / PPY) * 100), round_trips=int(r["entries"][sel].sum()))
    out["era"] = era
    # tail stress: worst cumulative funding windows and how each rule fared through them
    stress = {}
    for w in (30, 90, 180):
        cum = daily.rolling(w).sum()
        end = cum.idxmin()
        pos = list(daily.index).index(end)
        start = list(daily.index)[max(0, pos - w + 1)]
        win = (d_int >= start) & (d_int <= end); win[:first] = False
        stress[f"worst_{w}d"] = dict(cum_funding_pct=float(cum.min() * 100), window=[start, end],
                                     net_pct_in_window={k: float(r["pnl"][win].sum() * 100) for k, r in res.items()})
    out["tail_stress"] = stress
    print("tail stress:", stress)
    # bootstrap vs LIVE
    base = res["LIVE"]["pnl"][first:]
    boots = {}
    for k in rules:
        if k == "LIVE":
            continue
        b = bv.boot_mean_ci(res[k]["pnl"][first:] - base, block=90)
        boots[k] = dict(diff_ann_pct=b["mean"] * PPY * 100, ci90_ann_pct=[x * PPY * 100 for x in b["ci"]][::2], p_gt_0=b["p_gt_0"])
    out["bootstrap_vs_live"] = boots
    for k, v in boots.items():
        print("   %-16s diff vs LIVE %+6.2f %%/yr  CI90 [%+6.2f, %+6.2f]  P(>0) %.3f" % (k, v["diff_ann_pct"], v["ci90_ann_pct"][0], v["ci90_ann_pct"][1], v["p_gt_0"]))
    # decision
    worst = pdf.groupby("rule").net_ann_pct.min()
    dec = {}
    for k, v in boots.items():
        clauses = dict(beats_by_half_pct=bool(v["diff_ann_pct"] >= 0.5), ci_excludes_zero=bool(v["ci90_ann_pct"][0] > 0),
                       worst_year_ok=bool(worst[k] >= worst["LIVE"] - 1.0), worst_year=float(worst[k]), live_worst_year=float(worst["LIVE"]))
        dec[k] = dict(**clauses, verdict="RECOMMEND" if all(x for x in (clauses["beats_by_half_pct"], clauses["ci_excludes_zero"], clauses["worst_year_ok"])) else "NO CHANGE")
    out["decision"] = dict(per_rule=dec, tail_insurance_premium_pct_per_year=table["LIVE"]["net_ann_pct"] - table["ALWAYS_ON"]["net_ann_pct"],
                           verdict=("RECOMMEND " + ", ".join(k for k, v in dec.items() if v["verdict"] == "RECOMMEND")) if any(v["verdict"] == "RECOMMEND" for v in dec.values()) else "NO CHANGE")
    print("decision:", out["decision"])
    (RESULTS / "p2_exit_rules.json").write_text(pd.io.json.ujson_dumps(bv._jsonable(out), indent=1) if hasattr(pd.io.json, "ujson_dumps") else __import__("json").dumps(bv._jsonable(out), indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
