"""CARRY on ETH — the shipped CUM-30D rule on ETHUSDT settlement prints, and the combined BTC+ETH book.

Pre-registration: README.md (frozen before this produced a number). Read-only against prod.db.
Writes results/carry_eth.json, results/per_year.csv, results/combined_daily.csv.
"""
from __future__ import annotations

import json
import sqlite3
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
import run_c2_carry_timing as c2  # noqa: E402  (Agg backend; its main() is guarded)
from strategies.support import db  # noqa: E402

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
PPY = c2.PRINTS_PER_YEAR
CUM30_EXIT = -0.005
# The BTC study's rows (carry_exit_rule_2026_09/findings.md), which this code path must reproduce.
PARITY = dict(CUM30D=dict(net_ann_pct=11.58, rt_per_year=0.74),
              ALWAYS_ON=dict(net_ann_pct=11.69), LIVE=dict(net_ann_pct=10.85, rt_per_year=4.75))
# README decision bars.
MIN_NET_ANN_PCT = 5.0
MIN_WORST_YEAR_PCT = -2.0


# ── copied verbatim from carry_exit_rule_2026_09/run_p2_exit_rules.py (a script; not importable safely) ──

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
    state, held, neg = {}, False, 0
    for d in daily.index:
        v = daily[d]
        neg = neg + 1 if v < 0 else 0
        if kind == "LIVE":
            ex = neg >= 3
        elif kind == "CUM30D":
            ex = pd.notna(cum30[d]) and cum30[d] < CUM30_EXIT
        else:
            raise ValueError(kind)
        if held and ex:
            held = False
        elif not held and pd.notna(avg7[d]) and avg7[d] > 0:
            held = True
        state[d] = held
    return state

# ── end of the copied helpers ─────────────────────────────────────────────────


def c0_gate(ts: np.ndarray, f: np.ndarray, table: str) -> dict:
    """The fetched prints must equal prod.db's settlement rows over the overlap."""
    con = sqlite3.connect(f"file:{db.PROD_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(f"SELECT timestamp, fr_close FROM {table} ORDER BY timestamp").fetchall()
    finally:
        con.close()
    tab = {int(t): float(v) for t, v in rows}
    fetched = {int(t): float(v) for t, v in zip(ts, f)}
    common = sorted(set(tab) & set(fetched))
    diffs = np.array([abs(tab[t] - fetched[t]) for t in common]) if common else np.array([])
    return dict(table=table, table_rows=len(tab), fetched_prints=len(fetched), common=len(common),
                only_in_table=len(set(tab) - set(fetched)), only_fetched=len(set(fetched) - set(tab)),
                max_abs_diff=float(diffs.max()) if len(diffs) else None,
                pass_=bool(common and diffs.max() < 1e-9 and len(set(tab) - set(fetched)) == 0))


def evaluate(symbol: str) -> dict:
    fr = bv.fetch_binance_funding(symbol)
    ts = np.array([t // 1000 for t, _ in fr], dtype=np.int64)
    f = np.array([r for _, r in fr], dtype=float)
    n = len(f)
    q50 = pd.Series(f).rolling(c2.TRAIL, min_periods=c2.MIN_TRAIL).quantile(0.50).to_numpy()
    valid = np.isfinite(q50)
    first = int(np.flatnonzero(valid)[0])
    days, daily = daily_frame(ts, f)
    live = c2.s078_hold(ts, f) & valid
    assert np.array_equal(live, hold_from_states(days, daily, rule_states(daily, "LIVE")) & valid), \
        "generic LIVE state machine must equal c2.s078_hold"
    rules = {"CUM30D": hold_from_states(days, daily, rule_states(daily, "CUM30D")) & valid,
             "ALWAYS_ON": valid.copy(), "LIVE": live}
    res = {k: c2.run_rule(f, h) for k, h in rules.items()}
    span_years = (n - 1 - first) / PPY
    ts_int = ts[1:]
    yrs_int = np.array([bv.utc(t).year for t in ts_int])
    d_int = np.array([bv.iso(t) for t in ts_int])
    table, per_year = {}, []
    for k, r in res.items():
        pnl = r["pnl"][first:]
        table[k] = dict(share_held=float(r["hold"][first:].mean()),
                        round_trips=int(r["entries"][first:].sum()),
                        rt_per_year=float(r["entries"][first:].sum() / span_years),
                        gross_ann_pct=float(np.where(r["hold"][first:], f[1:][first:], 0.0).sum() / span_years * 100),
                        net_ann_pct=float(pnl.sum() / span_years * 100))
        for y in range(2019, 2027):
            m = (yrs_int == y); m[:first] = False
            if m.sum() < 100:
                continue
            per_year.append(dict(symbol=symbol, rule=k, year=y, n=int(m.sum()),
                                 share_held=float(r["hold"][m].mean()), round_trips=int(r["entries"][m].sum()),
                                 net_ann_pct=float(r["pnl"][m].sum() / (m.sum() / PPY) * 100)))
    era = {}
    for k, r in res.items():
        for name, sel in (("pre_etf", d_int < bv.ETF_CUTOFF), ("post_etf", d_int >= bv.ETF_CUTOFF)):
            sel = sel.copy(); sel[:first] = False
            era.setdefault(k, {})[name] = dict(
                net_ann_pct=float(r["pnl"][sel].sum() / (sel.sum() / PPY) * 100) if sel.sum() else None,
                round_trips=int(r["entries"][sel].sum()))
    stress = {}
    for w in (30, 90, 180):
        cum = daily.rolling(w).sum()
        end = cum.idxmin()
        pos = list(daily.index).index(end)
        start = list(daily.index)[max(0, pos - w + 1)]
        win = (d_int >= start) & (d_int <= end); win[:first] = False
        stress[f"worst_{w}d"] = dict(cum_funding_pct=float(cum.min() * 100), window=[start, end],
                                     net_pct_in_window={k: float(r["pnl"][win].sum() * 100) for k, r in res.items()})
    b = bv.boot_mean_ci(res["CUM30D"]["pnl"][first:], block=90)
    boot = dict(net_ann_pct=b["mean"] * PPY * 100, ci90_ann_pct=[x * PPY * 100 for x in b["ci"]][::2],
                p_gt_0=b["p_gt_0"])
    # Daily net pnl of the candidate, for the combined book (day of the print collected).
    daily_net = pd.Series(res["CUM30D"]["pnl"][first:], index=d_int[first:]).groupby(level=0).sum()
    daily_raw = daily[daily.index >= d_int[first]]
    return dict(symbol=symbol, n_prints=n, eval_span=[bv.iso(ts[first]), bv.iso(ts[-1])], years=span_years,
                rules=table, per_year=per_year, era=era, tail_stress=stress, bootstrap_cum30d_vs_zero=boot,
                _daily_net=daily_net, _daily_raw=daily_raw, _ts=ts, _f=f)


def book(daily_net: pd.Series) -> dict:
    cum = daily_net.cumsum() * 100
    dd = cum - cum.cummax()
    years = len(daily_net) / 365.25
    net = float(daily_net.sum() * 100 / years)
    maxdd = float(dd.min())
    return dict(days=int(len(daily_net)), net_ann_pct=net, max_dd_pct=maxdd,
                net_over_dd=(net / abs(maxdd)) if maxdd < 0 else None)


def main() -> None:
    eth, btc = evaluate("ETHUSDT"), evaluate("BTCUSDT")
    out = {"env": bv.env_info(), "pre_registration": "README.md"}

    gate = c0_gate(eth["_ts"], eth["_f"], "cd_funding_rate_eth")
    out["c0_gate"] = gate
    print("C0 gate (fetched ETH prints vs cd_funding_rate_eth):", gate)
    if not gate["pass_"]:
        print("STOP: the C0 gate failed; no ETH number is decision-bearing.")
        out["verdict"] = "STOP — C0 gate failed"
        (RESULTS / "carry_eth.json").write_text(json.dumps(bv._jsonable(out), indent=1, default=str), encoding="utf-8")
        return

    got = {k: {kk: btc["rules"][k][kk] for kk in v} for k, v in PARITY.items()}
    parity_ok = all(abs(got[k]["net_ann_pct"] - v["net_ann_pct"]) < 0.02 for k, v in PARITY.items()) and \
        all(abs(got[k]["rt_per_year"] - v["rt_per_year"]) < 0.1 for k, v in PARITY.items() if "rt_per_year" in v)
    out["parity"] = dict(got=got, expected=PARITY, pass_=bool(parity_ok))
    print("parity vs the BTC study:", out["parity"])

    for name, r in (("ETH", eth), ("BTC", btc)):
        print(f"\n{name} rules over {r['eval_span'][0]}..{r['eval_span'][1]} ({r['years']:.2f} y), {r['n_prints']} prints:")
        print(pd.DataFrame(r["rules"]).T.round(3).to_string())
    pdf = pd.DataFrame(eth["per_year"] + btc["per_year"])
    pdf.to_csv(RESULTS / "per_year.csv", index=False)
    print("\nnet %/yr by year (ETH):\n", pdf[pdf.symbol == "ETHUSDT"].pivot_table(index="rule", columns="year", values="net_ann_pct").round(2).to_string())
    print("ETH tail stress:", eth["tail_stress"])
    print("ETH CUM30D bootstrap vs zero:", eth["bootstrap_cum30d_vs_zero"])

    # The combined book on common days.
    common = eth["_daily_net"].index.intersection(btc["_daily_net"].index)
    e, b_ = eth["_daily_net"].reindex(common).fillna(0.0), btc["_daily_net"].reindex(common).fillna(0.0)
    combined = dict(common_span=[str(common[0]), str(common[-1])],
                    btc_alone=book(b_), eth_alone=book(e), equal_weight=book(0.5 * b_ + 0.5 * e),
                    corr_daily_funding=float(eth["_daily_raw"].reindex(common).corr(btc["_daily_raw"].reindex(common))),
                    corr_daily_net_cum30d=float(e.corr(b_)))
    pd.DataFrame(dict(btc_net=b_, eth_net=e)).to_csv(RESULTS / "combined_daily.csv")
    out["combined"] = combined
    print("\ncombined book:", json.dumps(combined, indent=1))

    # Decision, per README.
    cand = eth["rules"]["CUM30D"]
    worst = float(pdf[(pdf.symbol == "ETHUSDT") & (pdf.rule == "CUM30D")].net_ann_pct.min())
    a = bool(cand["net_ann_pct"] >= MIN_NET_ANN_PCT and eth["bootstrap_cum30d_vs_zero"]["ci90_ann_pct"][0] > 0)
    bb = bool(worst >= MIN_WORST_YEAR_PCT)
    ew, alone = combined["equal_weight"]["net_over_dd"], combined["btc_alone"]["net_over_dd"]
    c = bool(ew is not None and alone is not None and ew >= alone)
    if not parity_ok:
        verdict = "INCONCLUSIVE — parity with the BTC study failed"
    elif a and bb and c:
        verdict = "RECOMMEND an ETH paper twin"
    elif a and bb:
        verdict = "NO BUILD — ETH carry is BTC carry again"
    else:
        verdict = "KILL for ETH"
    out["decision"] = dict(a_net_and_ci=a, a_net_ann_pct=cand["net_ann_pct"], a_bar=MIN_NET_ANN_PCT,
                           a_ci90_low=eth["bootstrap_cum30d_vs_zero"]["ci90_ann_pct"][0],
                           b_worst_year_ok=bb, b_worst_year=worst, b_bar=MIN_WORST_YEAR_PCT,
                           c_equal_weight_not_worse=c, c_equal_weight_net_over_dd=ew, c_btc_alone_net_over_dd=alone,
                           verdict=verdict)
    print("\nDECISION:", json.dumps(out["decision"], indent=1))
    for r in (eth, btc):
        for k in list(r):
            if k.startswith("_"):
                del r[k]
    out["eth"], out["btc"] = eth, btc
    (RESULTS / "carry_eth.json").write_text(json.dumps(bv._jsonable(out), indent=1, default=str), encoding="utf-8")
    print("wrote", RESULTS / "carry_eth.json")


if __name__ == "__main__":
    main()
