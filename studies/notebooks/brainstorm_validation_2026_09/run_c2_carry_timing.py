"""C2 — funding-carry quartile timing, net of its own turnover.  README §C2."""
from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import bv_lib as bv

PRINTS_PER_YEAR = 1095.0
COST_RT = 0.002          # p300 S-078 ENTRY_EXIT_COST_PCT = 0.20 % of notional per round trip
TRAIL = 1095             # causal quantile window (one year of prints)
MIN_TRAIL = 300


def autocorr(x: np.ndarray, k: int) -> float:
    if len(x) <= k + 2:
        return float("nan")
    a, b = x[:-k], x[k:]
    if a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def quartile_table(cur: np.ndarray, nxt: np.ndarray) -> list[dict]:
    """Their construction: sort by the current print, four equal chunks, next print annualised."""
    o = np.argsort(cur, kind="stable")
    n = len(o)
    step = n // 4
    rows = []
    for b in range(4):
        sl = o[b * step:(b + 1) * step] if b < 3 else o[b * step:]
        rows.append(dict(q=b + 1, lo=float(cur[sl].min()) * 100, hi=float(cur[sl].max()) * 100, n=int(len(sl)),
                         next_ann_pct=float(nxt[sl].mean()) * PRINTS_PER_YEAR * 100))
    return rows


def run_rule(f: np.ndarray, hold: np.ndarray) -> dict:
    """hold[t] = hold the pair through (t, t+1]; collect f[t+1].  Returns per-interval pnl
    (funding minus toggle costs) aligned to t = 0..n-2."""
    n = len(f) - 1
    h = hold[:n].astype(bool)
    prev = np.r_[False, h[:-1]]
    entries = h & ~prev
    exits = ~h & prev
    pnl = np.where(h, f[1:], 0.0) - COST_RT / 2 * entries - COST_RT / 2 * exits
    if h[-1]:
        pnl[-1] -= COST_RT / 2      # close the open pair at the end
    return dict(pnl=pnl, hold=h, entries=entries, n_round_trips=int(entries.sum()),
                gross=float(np.where(h, f[1:], 0.0).sum()), net=float(pnl.sum()), share_held=float(h.mean()))


def s078_hold(ts: np.ndarray, f: np.ndarray) -> np.ndarray:
    """p300 S-078: daily sums; enter when the 7-day average > 0; exit after 3 consecutive
    negative days.  Decided at the end of each UTC day, effective from the next print."""
    days = np.array([bv.iso(t) for t in ts])
    df = pd.DataFrame(dict(day=days, f=f))
    daily = df.groupby("day")["f"].sum()
    avg7 = daily.rolling(7).mean()
    state = {}
    held = False
    neg = 0
    for d in daily.index:
        v = daily[d]
        neg = neg + 1 if v < 0 else 0
        if held and neg >= 3:
            held = False
        elif not held and pd.notna(avg7[d]) and avg7[d] > 0:
            held = True
        state[d] = held
    # the decision at the end of day D governs every print whose settlement day is > D
    hold = np.zeros(len(f), bool)
    prev_day = None
    for i, d in enumerate(days):
        if prev_day is not None and prev_day != d:
            pass
        prev_day = d
    day_list = list(daily.index)
    day_pos = {d: k for k, d in enumerate(day_list)}
    for i, d in enumerate(days):
        k = day_pos[d]
        # print i settles on day d; hold if the state at the end of the PREVIOUS day was True
        hold[i] = state[day_list[k - 1]] if k > 0 else False
    # run_rule reads hold[t] as "hold through (t, t+1]" i.e. collecting print t+1:
    return np.r_[hold[1:], False]


def main() -> None:
    fr = bv.fetch_binance_funding()
    ts = np.array([t // 1000 for t, _ in fr], dtype=np.int64)
    f = np.array([r for _, r in fr], dtype=float)
    yrs_all = np.array([bv.utc(t).year for t in ts])
    n = len(f)
    years = (n - 1) / PRINTS_PER_YEAR
    out = {"env": bv.env_info(), "series": dict(n=n, span=[bv.iso(ts[0]), bv.iso(ts[-1])], years=years,
                                                mean_8h_pct=float(f.mean()) * 100, share_positive=float((f > 0).mean()),
                                                untimed_ann_pct=float(f.mean()) * PRINTS_PER_YEAR * 100)}
    print("series: %d prints %s..%s | mean %.4f%%/8h | positive %.1f%% | untimed %.2f%%/yr"
          % (n, out["series"]["span"][0], out["series"]["span"][1], out["series"]["mean_8h_pct"],
             out["series"]["share_positive"] * 100, out["series"]["untimed_ann_pct"]))

    # ── 1. parity: their sample (last 1200 prints = 400 days; also last 500) ──
    par = {}
    for k in (1200, 500):
        x = f[-k:]
        par[f"last_{k}"] = dict(lag1=autocorr(x, 1), mean_8h_pct=float(x.mean()) * 100, share_positive=float((x > 0).mean()),
                                ann_pct=float(x.mean()) * PRINTS_PER_YEAR * 100, quartiles=quartile_table(x[:-1], x[1:]))
    out["parity"] = dict(expected=dict(lag1=0.75, mean_8h_pct=0.0031, share_positive=0.766, ann_pct=3.40,
                                       quartiles_next_ann=[-2.2, 2.5, 5.5, 7.7]), got=par)
    print("1. PARITY last 1200:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in par["last_1200"].items() if k != "quartiles"},
          [round(r["next_ann_pct"], 1) for r in par["last_1200"]["quartiles"]])
    print("          last  500:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in par["last_500"].items() if k != "quartiles"},
          [round(r["next_ann_pct"], 1) for r in par["last_500"]["quartiles"]])

    # ── 2. persistence by year; quartile tables in-sample and causal ───────
    ac = []
    for y in sorted(set(yrs_all.tolist())):
        x = f[yrs_all == y]
        ac.append(dict(year=int(y), n=int(len(x)), lag1=autocorr(x, 1), lag2=autocorr(x, 2), lag3=autocorr(x, 3),
                       mean_8h_pct=float(x.mean()) * 100, ann_pct=float(x.mean()) * PRINTS_PER_YEAR * 100,
                       share_positive=float((x > 0).mean())))
    ac_df = pd.DataFrame(ac)
    out["autocorr_by_year"] = ac
    out["autocorr_full"] = dict(lag1=autocorr(f, 1), lag2=autocorr(f, 2), lag3=autocorr(f, 3), lag6=autocorr(f, 6))
    print("2. autocorr by year:\n", ac_df.round(3).to_string(index=False), "\n   full:", {k: round(v, 3) for k, v in out["autocorr_full"].items()})

    s = pd.Series(f)
    q25 = s.rolling(TRAIL, min_periods=MIN_TRAIL).quantile(0.25).to_numpy()
    q50 = s.rolling(TRAIL, min_periods=MIN_TRAIL).quantile(0.50).to_numpy()
    q75 = s.rolling(TRAIL, min_periods=MIN_TRAIL).quantile(0.75).to_numpy()
    q40 = s.rolling(TRAIL, min_periods=MIN_TRAIL).quantile(0.40).to_numpy()
    q60 = s.rolling(TRAIL, min_periods=MIN_TRAIL).quantile(0.60).to_numpy()
    with np.errstate(invalid="ignore"):
        bucket = np.where(f <= q25, 1, np.where(f <= q50, 2, np.where(f <= q75, 3, 4)))
    bucket = np.where(np.isfinite(q50), bucket, 0)
    rows = []
    for y in sorted(set(yrs_all.tolist())):
        sel = (yrs_all == y)
        cur, nxt = f[:-1], f[1:]
        sely = sel[:-1]
        if sely.sum() < 100:
            continue
        ins = quartile_table(cur[sely], nxt[sely])
        rec = dict(year=int(y), n=int(sely.sum()))
        for r in ins:
            rec[f"insample_q{r['q']}"] = r["next_ann_pct"]
        for b in (1, 2, 3, 4):
            m = sely & (bucket[:-1] == b)
            rec[f"causal_q{b}"] = float(nxt[m].mean()) * PRINTS_PER_YEAR * 100 if m.sum() else float("nan")
            rec[f"causal_n{b}"] = int(m.sum())
        rows.append(rec)
    qt = pd.DataFrame(rows)
    qt.to_csv(bv.RESULTS / "c2_quartiles.csv", index=False)
    full_causal = {}
    for b in (1, 2, 3, 4):
        m = (bucket[:-1] == b)
        full_causal[f"q{b}"] = dict(n=int(m.sum()), next_ann_pct=float(f[1:][m].mean()) * PRINTS_PER_YEAR * 100)
    out["quartiles"] = dict(full_insample=quartile_table(f[:-1], f[1:]), full_causal=full_causal, by_year=rows)
    print("   quartiles full-sample in-sample:", [round(r["next_ann_pct"], 1) for r in out["quartiles"]["full_insample"]],
          "| causal:", {k: round(v["next_ann_pct"], 1) for k, v in full_causal.items()})
    print(qt.round(1).to_string(index=False))

    # ── 3. the trade, net of turnover ──────────────────────────────────────
    valid = np.isfinite(q50)
    with np.errstate(invalid="ignore"):
        upper = valid & (f > q50)
        top = valid & (f > q75)
    hyst = np.zeros(n, bool)
    st = False
    for i in range(n):
        if not valid[i]:
            continue
        if not st and f[i] > q60[i]:
            st = True
        elif st and f[i] < q40[i]:
            st = False
        hyst[i] = st
    minhold = np.zeros(n, bool)
    st, until = False, -1
    for i in range(n):
        if not valid[i]:
            continue
        if not st and f[i] > q50[i]:
            st, until = True, i + 63
        elif st and i >= until and f[i] <= q50[i]:
            st = False
        minhold[i] = st
    rules = {"ALWAYS_ON": valid.copy(), "UPPER_HALF": upper, "TOP_QUARTILE": top,
             "HYSTERESIS_p60_p40": hyst, "UPPER_HALF_minhold21d": minhold, "S078_live_rule": s078_hold(ts, f) & valid}
    # common evaluation span: prints where the causal median exists
    first = int(np.flatnonzero(valid)[0])
    res = {k: run_rule(f, h) for k, h in rules.items()}
    span_years = (n - 1 - first) / PRINTS_PER_YEAR
    table = []
    per_year = []
    ts_int = ts[1:]
    yrs_int = np.array([bv.utc(t).year for t in ts_int])
    for k, r in res.items():
        pnl = r["pnl"][first:]
        table.append(dict(rule=k, share_held=float(r["hold"][first:].mean()), round_trips=int(r["entries"][first:].sum()),
                          rt_per_year=float(r["entries"][first:].sum() / span_years),
                          gross_ann_pct=float(np.where(r["hold"][first:], f[1:][first:], 0.0).sum() / span_years * 100),
                          net_ann_pct=float(pnl.sum() / span_years * 100)))
        for y in range(2020, 2027):
            m = (yrs_int == y)
            m[:first] = False
            if m.sum() < 100:
                continue
            yr_len = m.sum() / PRINTS_PER_YEAR
            per_year.append(dict(rule=k, year=y, n=int(m.sum()), share_held=float(r["hold"][m].mean()),
                                 round_trips=int(r["entries"][m].sum()),
                                 gross_ann_pct=float(np.where(r["hold"][m], f[1:][m], 0.0).sum() / yr_len * 100),
                                 net_ann_pct=float(r["pnl"][m].sum() / yr_len * 100)))
    tdf = pd.DataFrame(table)
    pdf = pd.DataFrame(per_year)
    pdf.to_csv(bv.RESULTS / "c2_per_year.csv", index=False)
    out["rules"] = dict(eval_span=[bv.iso(ts[first]), bv.iso(ts[-1])], years=span_years, table=table)
    print("3. rules over %s..%s (%.2f y):\n" % (bv.iso(ts[first]), bv.iso(ts[-1]), span_years), tdf.round(3).to_string(index=False))
    piv = pdf.pivot_table(index="rule", columns="year", values="net_ann_pct").round(2)
    print("   net %/yr by year:\n", piv.to_string())

    # era split
    era = {}
    for k, r in res.items():
        d = np.array([bv.iso(t) for t in ts_int])
        for name, sel in (("pre_etf", d < bv.ETF_CUTOFF), ("post_etf", d >= bv.ETF_CUTOFF)):
            sel = sel.copy()
            sel[:first] = False
            era.setdefault(k, {})[name] = dict(n=int(sel.sum()), net_ann_pct=float(r["pnl"][sel].sum() / (sel.sum() / PRINTS_PER_YEAR) * 100),
                                                 round_trips=int(r["entries"][sel].sum()))
    out["era"] = era

    # bootstrap of UPPER_HALF − ALWAYS_ON (and the other rules) on the aligned per-interval pnl
    boots = {}
    base = res["ALWAYS_ON"]["pnl"][first:]
    for k in ("UPPER_HALF", "TOP_QUARTILE", "HYSTERESIS_p60_p40", "UPPER_HALF_minhold21d", "S078_live_rule"):
        dlt = res[k]["pnl"][first:] - base
        b = bv.boot_mean_ci(dlt, block=90)
        boots[k] = dict(diff_ann_pct=b["mean"] * PRINTS_PER_YEAR * 100, ci90_ann_pct=[x * PRINTS_PER_YEAR * 100 for x in b["ci"]][::2],
                        p_gt_0=b["p_gt_0"])
    out["bootstrap_vs_always_on"] = boots
    print("4. bootstrap (block 90 prints) of net difference vs ALWAYS_ON, %/yr:")
    for k, v in boots.items():
        print("   %-24s diff %+6.2f  CI90 [%+6.2f, %+6.2f]  P(>0) %.3f" % (k, v["diff_ann_pct"], v["ci90_ann_pct"][0], v["ci90_ann_pct"][1], v["p_gt_0"]))

    # year-count comparison for the decision
    up = pdf[pdf.rule == "UPPER_HALF"].set_index("year")["net_ann_pct"]
    al = pdf[pdf.rule == "ALWAYS_ON"].set_index("year")["net_ann_pct"]
    yrs_common = sorted(set(up.index) & set(al.index))
    wins = int(sum(up[y] > al[y] for y in yrs_common))
    full_win = bool(tdf.set_index("rule").loc["UPPER_HALF", "net_ann_pct"] > tdf.set_index("rule").loc["ALWAYS_ON", "net_ann_pct"])
    ci = boots["UPPER_HALF"]["ci90_ann_pct"]
    ci_excl = bool(ci[0] > 0 or ci[1] < 0)
    verdict = "CONFIRMED" if (full_win and wins >= 5 and ci[0] > 0) else "KILL"
    out["decision"] = dict(upper_half_beats_always_on_full_sample=full_win, years_won=wins, years_compared=len(yrs_common),
                           ci90_excludes_zero=ci_excl, ci90=ci, verdict=verdict)
    print("5. DECISION:", out["decision"])

    # figure: cumulative net yield
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = pd.to_datetime(ts_int[first:], unit="s", utc=True)
    for k in ("ALWAYS_ON", "UPPER_HALF", "TOP_QUARTILE", "S078_live_rule"):
        ax.plot(x, np.cumsum(res[k]["pnl"][first:]) * 100, label=k)
    ax.set_ylabel("cumulative net funding, % of notional")
    ax.set_title("C2 carry timing rules, net of 0.20% per round trip")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(bv.RESULTS / "c2_curves.png", dpi=110)
    plt.close(fig)
    bv.jdump(out, "c2_summary.json")


if __name__ == "__main__":
    main()
