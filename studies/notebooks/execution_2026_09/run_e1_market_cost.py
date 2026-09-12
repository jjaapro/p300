"""E1 — what a market order costs: realised spread and decision-to-fill drift (README §E1)."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import exec_lib as ex

if sys.platform == "win32":
    getattr(sys.stdout, "reconfigure", lambda **k: None)(encoding="utf-8")


def spread_tables() -> dict:
    out = {}
    p5 = ex.load_path("spot_5s")
    hours = p5.ts // 3600
    uniq, first = np.unique(hours, return_index=True)
    ends = np.append(first[1:], len(p5.ts))
    rows = []
    for h0, a, b in zip(uniq, first, ends):
        if b - a < 300:
            continue
        rows.append(dict(hour_ts=int(h0 * 3600), roll_half_bp=ex.roll_halfspread_bp(p5.c[a:b]),
                         cs_half_bp=ex.corwin_schultz_halfspread_bp(p5.h[a:b], p5.l[a:b]),
                         range_bp=float((p5.h[a:b].max() / p5.l[a:b].min() - 1) * 1e4)))
    r5 = pd.DataFrame(rows)
    r5["hod"] = (r5.hour_ts // 3600) % 24
    r5.to_csv(ex.RESULTS / "e1_roll_5s_hourly.csv", index=False)
    out["roll_5s"] = dict(n_hours=int(len(r5)), share_defined=float(r5.roll_half_bp.notna().mean()),
                          median_half_bp=float(r5.roll_half_bp.median()), mean_half_bp=float(r5.roll_half_bp.mean()),
                          p90_half_bp=float(r5.roll_half_bp.quantile(0.9)),
                          by_hod_median=r5.groupby("hod").roll_half_bp.median().round(3).to_dict(),
                          cs_5s_median_half_bp=float(r5.cs_half_bp.median()))
    # Corwin-Schultz per day on 1 m high/low, 2020 -> now, both assets
    for name in ("btc_1m", "eth_1m"):
        p = ex.load_path(name)
        days = p.ts // 86400
        u, f = np.unique(days, return_index=True)
        e = np.append(f[1:], len(p.ts))
        cs = np.array([ex.corwin_schultz_halfspread_bp(p.h[a:b], p.l[a:b]) if b - a > 600 else np.nan for a, b in zip(f, e)])
        d = pd.DataFrame(dict(day=pd.to_datetime(u * 86400, unit="s"), cs_half_bp=cs))
        d.to_csv(ex.RESULTS / f"e1_cs_{name}_daily.csv", index=False)
        out[f"cs_{name}"] = dict(median_half_bp=float(np.nanmedian(cs)),
                                 by_year={int(k): round(float(v.median()), 3) for k, v in d.groupby(d.day.dt.year).cs_half_bp})
    return out


def entry_drift(sleeve: str, events: list, res: str) -> dict:
    path = ex.path_for(events[0].asset, res)
    rows = []
    for ev in events:
        cx = ex.context(ev, path, need_tif=False)
        if cx is None:
            continue
        i0, paper, d = cx["i0"], cx["paper"], ev.direction
        rec = dict(ts=ev.signal_ts, paper=paper)
        if res == "1m":
            rec["gap_open"] = float(path.o[i0])          # first trade after the close (0 s)
            rec["at_60s"] = float(path.c[i0])            # price when a 60 s tick would act
            rec["at_120s"] = float(path.o[i0 + 1]) if i0 + 1 < len(path.ts) else np.nan
            rng = (path.h[i0] - path.l[i0]) / path.c[i0]
            rec["range_bp"] = float(rng * 1e4)
        else:
            rec["at_0s"] = float(path.o[i0])
            rec["at_30s"] = float(path.o[i0 + 6]) if i0 + 6 < len(path.ts) else np.nan
            rec["at_60s"] = float(path.o[i0 + 12]) if i0 + 12 < len(path.ts) else np.nan
        for k in list(rec):
            if k not in ("ts", "paper", "range_bp"):
                rec[k + "_cost_bp"] = d * (rec[k] - paper) / paper * 1e4
        rows.append(rec)
    df = pd.DataFrame(rows)
    if df.empty:
        return dict(n=0)
    out = dict(n=int(len(df)))
    g = ex.day_groups(df.ts.to_numpy())
    for k in [c for c in df.columns if c.endswith("_cost_bp")]:
        v = df[k].to_numpy(float); ok = np.isfinite(v)
        b = ex.block_boot_mean(v[ok], g[ok])
        out[k] = dict(mean=b["mean"], ci90=b["ci90"], median=float(np.median(v[ok])), p90=float(np.quantile(v[ok], 0.9)),
                      share_adverse=float((v[ok] > 0).mean()))
    if res == "1m":
        thr = np.quantile(df.range_bp, 0.95)
        hot = df.range_bp >= thr
        out["stressed_top5pct_at_60s_cost_bp"] = dict(n=int(hot.sum()), mean=float(df.loc[hot, "at_60s_cost_bp"].mean()),
                                                      calm_mean=float(df.loc[~hot, "at_60s_cost_bp"].mean()))
    return out


def tif_exit_drift(sleeve: str, events: list, res: str) -> dict:
    """For trades that reach the time stop: the scheduled close vs the next bar's open (the bot's market exit)."""
    path = ex.path_for(events[0].asset, res)
    rows = []
    for ev in events:
        cx = ex.context(ev, path)
        if cx is None:
            continue
        mt = ex.market_trade(ev, path, cx)
        if mt["kind"] != "tif":
            continue
        j = mt["idx"]
        if j + 1 >= len(path.ts):
            continue
        sched, nxt = float(path.c[j]), float(path.o[j + 1])
        rows.append(dict(ts=ev.signal_ts, cost_bp=-ev.direction * (nxt - sched) / sched * 1e4))
    if not rows:
        return dict(n=0)
    df = pd.DataFrame(rows)
    b = ex.block_boot_mean(df.cost_bp.to_numpy(), ex.day_groups(df.ts.to_numpy()))
    return dict(n=int(len(df)), mean=b["mean"], ci90=b["ci90"], median=float(df.cost_bp.median()))


def main() -> None:
    out = {"env": ex.env_info(), "spreads": spread_tables()}
    print("spreads:", {k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if not isinstance(vv, dict)}) for k, v in out["spreads"].items()})
    out["entry_drift"], out["tif_exit_drift"] = {}, {}
    for sl in ex.WALK_SLEEVES:
        ev = ex.load_events(sl)
        out["entry_drift"][sl] = {"1m": entry_drift(sl, ev, "1m")}
        out["tif_exit_drift"][sl] = {"1m": tif_exit_drift(sl, ev, "1m")}
        if ev[0].asset == "BTC":
            out["entry_drift"][sl]["5s"] = entry_drift(sl, ev, "5s")
            out["tif_exit_drift"][sl]["5s"] = tif_exit_drift(sl, ev, "5s")
        for res, d in out["entry_drift"][sl].items():
            print(f"{sl} entry drift {res}: n={d.get('n')}",
                  {k: (round(v["mean"], 2), [round(x, 2) for x in v["ci90"]]) for k, v in d.items() if isinstance(v, dict) and "ci90" in v})
        print(f"{sl} tif-exit drift:", {res: (d.get("n"), round(d.get("mean", float("nan")), 2)) for res, d in out["tif_exit_drift"][sl].items()})
    # ADX: entry at 00:00 UTC (daily close) and signal exits at 00:00; the bot ticks every 60 s
    adx = ex.load_adx()
    p1 = ex.load_path("btc_1m")
    rows = []
    for a in adx:
        i0 = p1.idx_ge(a["signal_ts"])
        if i0 <= 0 or p1.ts[i0] != a["signal_ts"]:
            continue
        paper = float(p1.c[i0 - 1]); d = a["direction"]
        rows.append(dict(ts=a["signal_ts"], gap_open_cost_bp=d * (p1.o[i0] - paper) / paper * 1e4,
                         at_60s_cost_bp=d * (p1.c[i0] - paper) / paper * 1e4))
        if a["reason"] != "SL":
            j0 = p1.idx_ge(a["exit_ts"])
            if 0 < j0 < len(p1.ts) and p1.ts[j0] == a["exit_ts"]:
                pe = float(p1.c[j0 - 1])
                rows[-1]["exit_at_60s_cost_bp"] = -d * (p1.c[j0] - pe) / pe * 1e4
    df = pd.DataFrame(rows)
    out["entry_drift"]["ADX"] = {"1m": {k: dict(n=int(df[k].notna().sum()), mean=float(df[k].mean()), median=float(df[k].median()))
                                        for k in df.columns if k.endswith("_cost_bp")}}
    print("ADX drift:", out["entry_drift"]["ADX"])
    ex.jdump(out, "e1_market_cost.json")


if __name__ == "__main__":
    main()
